"""Copy text: the words in pictures and scanned pages, read by Windows (suiteocr), put on the
clipboard (by app.py, once the job is done: job.text).

A PDF's own text is taken as it is where a page has some - exact, and quicker than reading it -
and only a page without any (a scan) is read as a picture.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QImage

from .. import suiteocr
from ..jobs import Job, UserError
from . import images, pdf

SCAN_DPI = 200           # a scanned page drawn at this for reading: small print stays legible


def _qimage(picture) -> QImage:
    """A Pillow picture as a QImage (suiteocr's), its first frame if it moves."""
    rgba = picture.convert('RGBA')
    return QImage(rgba.tobytes(), rgba.width, rgba.height, rgba.width * 4, QImage.Format_RGBA8888).copy()


def _picture_text(src: Path) -> str:
    picture = images.open_image(src)
    try:
        picture.seek(0)
    except (AttributeError, EOFError):
        pass
    return suiteocr.text_of(suiteocr.recognize(_qimage(picture)))


def _pdf_text(job: Job, src: Path) -> str:
    document = pdf._open_pdfium(src)
    pages = []
    try:
        count = len(document)
        for index in range(count):
            if job.cancelled:
                break
            page = document[index]
            layer = page.get_textpage()
            text = layer.get_text_bounded().replace('\r\n', '\n').strip()
            layer.close()
            if not text:
                text = suiteocr.text_of(suiteocr.recognize(_qimage(page.render(scale=SCAN_DPI / 72).to_pil())))
            page.close()
            pages.append(text)
            job.progress((index + 1) / count)
    finally:
        document.close()
    return '\n\n'.join(page for page in pages if page)


def copy_text(job: Job) -> None:
    found = []

    def read(src: Path) -> None:
        text = _pdf_text(job, src) if src.suffix.lower() == '.pdf' else _picture_text(src)
        if text.strip():
            found.append(text.strip())
        job.progress(1)
    job.for_each(read)
    if not found:
        name = job.files[0].name if len(job.files) == 1 else 'them'
        raise UserError(f'No text found in {name}')
    job.text = '\n\n'.join(found)
