"""Text in a picture, read by Windows' own OCR (Windows.Media.Ocr): nothing leaves the PC.

Shared by the suite's apps (Hanami's copy is the original; tests/check_copies.py keeps the others
the same): Hanami reads the screen with it, Dropshift dropped pictures and scans.

    lines = recognize(image)        # off the UI thread: a screen takes a few hundred ms
    text = text_of(lines)

- Small text, as on a screen, reads better at twice the size: at its own size Windows misread
  "mic" and dropped a capital T in 9 pt Segoe UI. So a picture is enlarged first, as far as
  Windows allows (OcrEngine.max_image_dimension, 10,000 px), and what is found is scaled back.
- Windows reads one language at a time, set up in Windows' language settings. Its English reader
  skips Chinese, Japanese and Korean outright, and its Chinese one garbles English ("Fnday",
  "d ue"). So when one of those is set up besides the first language, both read the picture and
  their words are put together (_together): the first reader's words, and the CJK reader's where
  the first found nothing.
- The Chinese reader also takes big, bold or narrow capitals for Chinese characters: a game's
  "GRAVE MATTERS" came out as "鼐 MATTERS", "VICTORY" as "训昶". Over Chinese the English reader
  finds nothing at all (not a word on 180 lines of it, 12 to 56 px, in four fonts), so where it
  did find a word that one stays, unless Chinese characters cover most of it (TRUSTED).
- The Chinese reader splits digits and punctuation apart ("27", "：", "18"), and reads some the
  English one misses: a timer, "27:18:51:47", or the "：3" in "下载完成：3 个文件". Touching
  pieces are joined again, and a colon or comma between digits or letters made plain (_glued).
"""

from __future__ import annotations

import asyncio
import threading
import unicodedata
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

ENLARGE = 2.0
TRUSTED = 0.75          # a first-language word gives way only to CJK characters covering this much of it
TOUCHING = 0.3          # pieces closer than this many heights apart are one word (_glued),
LEANING = 0.5           # and punctuation this close leans on what is before it
LINE_GAP = 2.5          # heights: words further apart than this, though level, are in different columns
_CJK_LANGUAGES = ('zh', 'ja', 'ko')


@dataclass(frozen=True)
class Word:
    text: str
    x: float           # where it is, in the picture's own pixels
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def overlap(self, other: Word) -> float:
        """How much of the smaller of the two the other covers, 0 to 1."""
        width = min(self.x + self.w, other.x + other.w) - max(self.x, other.x)
        height = min(self.y + self.h, other.y + other.h) - max(self.y, other.y)
        if width <= 0 or height <= 0:
            return 0.0
        return width * height / max(1e-6, min(self.w * self.h, other.w * other.h))


@dataclass(frozen=True)
class Line:
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        return join(self.words)

    @property
    def top(self) -> float:
        return min(word.y for word in self.words)

    @property
    def bottom(self) -> float:
        return max(word.y + word.h for word in self.words)


def is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (0x3000 <= code <= 0x30FF or 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF or
            0xAC00 <= code <= 0xD7AF or 0xF900 <= code <= 0xFAFF or 0xFF00 <= code <= 0xFFEF or
            0x20000 <= code <= 0x2FA1F)


def is_ideograph(ch: str) -> bool:
    """A Chinese character, kana or hangul - not CJK punctuation, which the Chinese reader also
    finds in English (a colon read as '：')."""
    code = ord(ch)
    return (0x3040 <= code <= 0x30FF or 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF or
            0xAC00 <= code <= 0xD7AF or 0xF900 <= code <= 0xFAFF or 0x20000 <= code <= 0x2FA1F)


def has_cjk(text: str) -> bool:
    return any(is_ideograph(ch) for ch in text)


def _spaced(ch: str) -> bool:
    """Chinese punctuation ('：', '。') and full-width forms carry their own space."""
    return is_cjk(ch) and not is_ideograph(ch)


def join(words) -> str:
    """Words as they are written: with spaces between, except between Chinese or Japanese
    characters, which have none, and round Chinese punctuation."""
    out = ''
    for word in words:
        after = word.text[:1] or ' '
        if out and not (is_cjk(out[-1]) and is_cjk(after) or _spaced(out[-1]) or _spaced(after)):
            out += ' '
        out += word.text
    return out


def text_of(lines) -> str:
    return '\n'.join(line.text for line in lines if line.words)


# --------------------------------------------------------------------------- Windows' readers

_lock = threading.Lock()
_reading = threading.Lock()     # one picture at a time: a reader refuses a second while busy
_readers: list | None = None
_apartments = threading.local()


def _apartment() -> None:
    """WinRT on this thread (a worker's, as a rule): its multi-threaded apartment, once."""
    if getattr(_apartments, 'ready', False):
        return
    _apartments.ready = True
    try:
        from winrt.runtime import MTA, init_apartment
        init_apartment(MTA)
    except Exception:  # noqa: BLE001 - already set up, as the UI thread is (Qt makes it single-threaded)
        pass


def _make_readers() -> list:
    """The first language's reader, then one for each Chinese, Japanese or Korean set up too."""
    from winrt.windows.globalization import Language
    from winrt.windows.media.ocr import OcrEngine
    first = OcrEngine.try_create_from_user_profile_languages()
    readers = [first] if first is not None else []
    tags = [first.recognizer_language.language_tag.lower()] if first is not None else []
    for language in OcrEngine.available_recognizer_languages:
        tag = language.language_tag.lower()
        if tag.split('-')[0] in _CJK_LANGUAGES and tag not in tags:
            reader = OcrEngine.try_create_from_language(Language(language.language_tag))
            if reader is not None:
                readers.append(reader)
                tags.append(tag)
    return readers


def readers() -> list:
    global _readers
    with _lock:
        if _readers is None:
            _apartment()
            try:
                _readers = _make_readers()
            except Exception:  # noqa: BLE001 - no WinRT here (Windows 10 before 1809, say), or no reader
                _readers = []
        return _readers


def available() -> bool:
    """Whether Windows can read text here: a reader for at least one of its languages."""
    return bool(readers())


def languages() -> list[str]:
    return [reader.recognizer_language.display_name for reader in readers()]


def _read(reader, bitmap, scale: float) -> list[list[Word]]:
    async def run():
        return await reader.recognize_async(bitmap)
    result = asyncio.run(run())
    lines = []
    for line in result.lines:
        words = []
        for word in line.words:
            box = word.bounding_rect
            words.append(Word(word.text, box.x / scale, box.y / scale, box.width / scale, box.height / scale))
        if words:
            lines.append(words)
    return lines


def recognize(image: QImage, early=None) -> list[Line]:
    """The lines of text in image, in reading order. Off the UI thread: it waits for Windows - and
    for any other picture being read, since a reader takes one at a time ("Another RecognizeAsync
    operation is already running!" when two screens were read at once).

    early(lines), if given, hears the first reader's lines as soon as it has them, before the CJK
    readers: Chinese took 600 ms a screen, English 180."""
    found = readers()
    if image.isNull() or not found:
        return []
    _apartment()
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    longest = max(image.width(), image.height())
    scale = min(ENLARGE, OcrEngine.max_image_dimension / longest)
    if abs(scale - 1) > 0.01:
        image = image.scaled(max(1, round(image.width() * scale)), max(1, round(image.height() * scale)),
                             Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    image = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)      # BGRA, byte by byte
    pixels = memoryview(image.constBits())[:image.sizeInBytes()]
    bitmap = SoftwareBitmap.create_copy_with_alpha_from_buffer(
        pixels, BitmapPixelFormat.BGRA8, image.width(), image.height(), BitmapAlphaMode.PREMULTIPLIED)
    try:
        with _reading:
            read = [_read(found[0], bitmap, scale)]
            if early is not None and len(found) > 1:
                early([Line(tuple(words)) for words in read[0]])
            read += [_read(reader, bitmap, scale) for reader in found[1:]]
    finally:
        bitmap.close()
    if len(read) == 1:
        return [Line(tuple(words)) for words in read[0]]
    return _together(read)


def _together(read: list[list[list[Word]]]) -> list[Line]:
    """The first reader's words, unless CJK characters cover most of one (TRUSTED), then each CJK
    reader's words where nothing is yet.

    The lines are Windows' own: it keeps columns apart, where grouping words by height alone put a
    sidebar's item, a message's line and a side panel's link level with it into one line, and a
    drag down the message took in the sidebar too. A CJK reader's word joins a line it sits in or
    beside (_row_for), as in "混合 text 和中文"; the rest stay in their own reader's lines."""
    later = [[_glued(words) for words in lines] for lines in read[1:]]
    cjk = [word for lines in later for words in lines for word in words if has_cjk(word.text)]
    rows: list[_Row] = []
    for words in read[0]:
        kept = [word for word in words if not (under := [c for c in cjk if word.overlap(c) > 0.3])
                or _covered(word, under) < TRUSTED]
        if kept:
            rows.append(_Row(kept))
    taken = [word for row in rows for word in row.words]
    for lines in later:
        found, alone = [], []
        for words in lines:
            free = [word for word in words if not any(word.overlap(t) > 0.3 for t in taken)]
            own = [word for word in free if not _join(word, rows)]
            if own:
                alone.append(_Row(own))
            found += free
        taken += found
        rows += alone
    lines = [Line(tuple(sorted(row.words, key=lambda w: w.x))) for row in rows]
    return sorted(lines, key=lambda line: (line.top, line.words[0].x))


class _Row:
    """A line being put together, with its bounds."""

    def __init__(self, words: list[Word]):
        self.words = list(words)
        self.top = min(w.y for w in words)
        self.bottom = max(w.y + w.h for w in words)
        self.left = min(w.x for w in words)
        self.right = max(w.x + w.w for w in words)

    def add(self, word: Word) -> None:
        self.words.append(word)
        self.top, self.bottom = min(self.top, word.y), max(self.bottom, word.y + word.h)
        self.left, self.right = min(self.left, word.x), max(self.right, word.x + word.w)


def _join(word: Word, rows: list[_Row]) -> bool:
    """Put word into the line it sits in, or beside - level with it, and no further off its end
    than LINE_GAP heights (further is another column). False if there is none."""
    best, off = None, 0.0
    for row in rows:
        middle = (row.top + row.bottom) / 2
        if not (row.top <= word.cy <= row.bottom and abs(middle - word.cy) < max(word.h, row.bottom - row.top) * 0.5):
            continue
        apart = max(0.0, row.left - (word.x + word.w), word.x - row.right)
        if apart <= LINE_GAP * word.h and (best is None or apart < off):
            best, off = row, apart
    if best is not None:
        best.add(word)
    return best is not None


def _covered(word: Word, others: list[Word]) -> float:
    """How much of word's width the others cover together, 0 to 1."""
    total, end = 0.0, word.x
    for left, right in sorted((max(word.x, o.x), min(word.x + word.w, o.x + o.w)) for o in others):
        left = max(left, end)
        if right > left:
            total += right - left
            end = right
    return total / max(1e-6, word.w)


def _glued(words: list[Word]) -> list[Word]:
    """A CJK reader's line with its touching pieces that aren't CJK characters made one word
    ('27', '：', '18' as '27:18'), and their full-width punctuation made plain."""
    out: list[Word] = []
    for word in sorted(words, key=lambda w: w.x):
        last = out[-1] if out else None
        reach = TOUCHING if word.text[:1].isalnum() else LEANING
        if (last is not None and not has_cjk(last.text) and not has_cjk(word.text)
                and word.x - (last.x + last.w) < reach * max(last.h, word.h)):
            top, bottom = min(last.y, word.y), max(last.y + last.h, word.y + word.h)
            out[-1] = Word(last.text + word.text, last.x, top, word.x + word.w - last.x, bottom - top)
        else:
            out.append(word)
    return [Word(unicodedata.normalize('NFKC', w.text), w.x, w.y, w.w, w.h)
            if not has_cjk(w.text) and any(ch.isalnum() for ch in w.text) else w for w in out]
