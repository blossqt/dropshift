"""Everything the drop menu can offer, and which of it suits a set of files.

Three modes, one per trigger key: Convert (make a copy in another format),
Tools (change the file - compress, rotate, split...) and Actions (do something
with the file itself - copy it, float it, open a terminal there). Drop targets
of the person's own (a command line, set up in Settings) join Tools or Actions
(set_custom).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Callable

from . import jobs, locate, suiteocr, web
from .engines import actions, archives, custom, data, images, media, ocr, office, pdf

MODES = ('convert', 'tools', 'actions')
MODE_TITLES = {'convert': 'Convert', 'tools': 'Tools', 'actions': 'Actions'}

KINDS = {
    'image': {'png', 'jpg', 'jpeg', 'jpe', 'jfif', 'webp', 'bmp', 'gif', 'tif', 'tiff', 'ico',
              'heic', 'heif', 'avif', 'jxl', 'tga', 'psd', 'dds', 'pcx', 'ppm', 'qoi'},
    'svg': {'svg'},
    'video': {'mp4', 'mov', 'mkv', 'webm', 'avi', 'm4v', 'wmv', 'flv', 'mpg', 'mpeg', '3gp',
              'ts', 'mts', 'm2ts', 'ogv', 'vob'},
    'audio': {'mp3', 'wav', 'flac', 'm4a', 'aac', 'ogg', 'oga', 'opus', 'wma', 'aiff', 'aif',
              'amr', 'mka', 'ac3'},
    'pdf': {'pdf'},
    'document': {'doc', 'docx', 'docm', 'odt', 'rtf', 'wpd'},
    'text': {'txt', 'md', 'markdown', 'log'},
    'sheet': {'xls', 'xlsx', 'xlsm', 'xlsb', 'ods', 'csv', 'tsv'},
    'slides': {'ppt', 'pptx', 'pptm', 'pps', 'ppsx', 'odp'},
    'data': {'json'},
    'subtitles': {'srt', 'vtt', 'ass', 'ssa'},
    'archive': {'zip', '7z', 'rar', 'tar', 'gz', 'tgz', 'bz2', 'tbz2', 'xz', 'txz', 'cab', 'iso',
                'tar.gz', 'tar.bz2', 'tar.xz'},
}
KIND_TITLES = {'image': 'Image', 'svg': 'SVG drawing', 'video': 'Video', 'audio': 'Audio',
               'pdf': 'PDF', 'document': 'Document', 'text': 'Text', 'sheet': 'Spreadsheet',
               'slides': 'Slides', 'data': 'JSON', 'subtitles': 'Subtitles', 'archive': 'Archive',
               'folder': 'Folder', 'other': 'File'}
_BY_EXT = {ext: kind for kind, exts in KINDS.items() for ext in exts}
# Spellings of one format, so a .jpeg isn't offered "JPG" as something new.
SAME = {'jpeg': 'jpg', 'jpe': 'jpg', 'jfif': 'jpg', 'tif': 'tiff', 'aif': 'aiff', 'heif': 'heic',
        'htm': 'html', 'tgz': 'tar.gz', 'oga': 'ogg', 'markdown': 'md'}

ANY = frozenset({'*'})


_kinds: dict[Path, str] = {}        # kind_of's answers, for the drag under way (fresh() at each)


def kind_of(path: Path) -> str:
    """'folder', or the kind of file by its extension - each path looked at once a drag: each
    choice of the menu asks of every file, and asking the disk each time took 1.5 s for 400."""
    kind = _kinds.get(path)
    if kind is None:
        kind = 'folder' if path.is_dir() else _BY_EXT.get(ext_of(path), 'other')
        if len(_kinds) > 20000:
            _kinds.clear()
        _kinds[path] = kind
    return kind


def fresh() -> None:
    """A new drag: files are looked at again, as they are now."""
    _kinds.clear()


def ext_of(path: Path) -> str:
    return jobs.split_name(path)[1].lower().lstrip('.')


def same_format(path: Path) -> str:
    ext = ext_of(path)
    return SAME.get(ext, ext)


@dataclass(frozen=True)
class Op:
    id: str
    label: str
    mode: str
    kinds: frozenset
    run: Callable = field(compare=False, repr=False)
    hint: str = ''               # the menu's footer line while hovering it
    icon: str = ''               # glyph for Tools and Actions tiles
    target: str = ''             # Convert: the extension made
    exts: frozenset = frozenset()   # narrower than kinds: only these extensions
    also: frozenset = frozenset()   # wider than kinds: these extensions too, whatever their kind
    named: tuple = ()            # ((kind, label), ...): the label when every file is that kind
    min_files: int = 1
    max_files: int = 0
    needs: Callable[[], bool] = field(default=lambda: True, compare=False, repr=False)
    gui: bool = False            # runs right away on the GUI thread (clipboard, windows)
    gui_thread: bool = False     # runs as a job, but on the GUI thread (Qt drawing)
    ask_folder: str = ''         # asks for a destination folder first
    verb: str = 'Working on'     # "Converting photo.png"

    def takes(self, path: Path) -> bool:
        if self.also and same_format(path) in self.also and kind_of(path) != 'folder':
            return True
        if '*' not in self.kinds and kind_of(path) not in self.kinds:
            return False
        if self.exts and same_format(path) not in self.exts:
            return False
        return True

    def available(self) -> bool:
        return self.needs()

    def label_for(self, paths: list[Path]) -> str:
        kinds = {kind_of(p) for p in paths if self.takes(p)}
        if len(kinds) == 1:
            return dict(self.named).get(kinds.pop(), self.label)
        return self.label


def each(work: Callable) -> Callable:
    return lambda job: job.for_each(partial(work, job))


def _has_ffmpeg() -> bool:
    return locate.ffmpeg() is not None


def _has(app: str) -> Callable[[], bool]:
    return lambda: office.available(app)


def _convert(kind: str, target: str, work: Callable, *, hint: str = '', needs=None,
             exts: frozenset = frozenset(), label: str = '', gui_thread: bool = False) -> Op:
    return Op(id=f'convert.{kind}.{target}', label=label or target.upper(), mode='convert',
              kinds=frozenset({kind}), target=target, exts=exts, gui_thread=gui_thread,
              run=each(lambda job, src: work(job, src, target)),
              hint=hint or f'Save a {target.upper()} copy', needs=needs or (lambda: True),
              verb='Converting')


def _build() -> list[Op]:
    ops: list[Op] = []
    add = ops.append

    # ------------------------------------------------------------------ Convert
    for target in ('png', 'jpg', 'webp', 'avif', 'gif', 'bmp', 'tiff', 'ico', 'pdf'):
        hint = {'ico': 'Save a Windows icon (16 to 256 px)',
                'pdf': 'Save as a PDF page'}.get(target, '')
        add(_convert('image', target, images.convert, hint=hint))
    for target in ('png', 'jpg', 'webp', 'ico', 'pdf'):
        add(_convert('svg', target, images.convert, gui_thread=True,
                     hint='Save as a PDF, still sharp at any size' if target == 'pdf' else ''))

    for target in ('mp4', 'mov', 'webm', 'mkv', 'avi', 'gif'):
        hint = {'gif': 'Make an animated GIF (15 fps, up to 640 px wide)',
                'mkv': 'Re-wrap as MKV without re-encoding'}.get(target, '')
        add(_convert('video', target, media.convert_video, hint=hint, needs=_has_ffmpeg))
    for target in ('mp3', 'm4a', 'wav'):
        add(_convert('video', target, media.convert_video, needs=_has_ffmpeg,
                     hint=f'Save just the sound, as {target.upper()}'))
    for target in ('mp3', 'm4a', 'wav', 'flac', 'ogg', 'opus', 'aiff'):
        add(_convert('audio', target, media.convert_audio, needs=_has_ffmpeg))
    for target in ('srt', 'vtt', 'ass'):
        add(_convert('subtitles', target, media.convert_subtitles, needs=_has_ffmpeg))

    add(_convert('pdf', 'png', pdf.to_images, hint='Save each page as a PNG'))
    add(_convert('pdf', 'jpg', pdf.to_images, hint='Save each page as a JPG'))
    add(_convert('pdf', 'txt', pdf.to_text, hint='Save the text it contains'))
    add(_convert('pdf', 'docx', office.word, needs=_has('Word'), label='Word',
                 hint='Turn it into an editable Word document'))

    for target in ('pdf', 'docx', 'odt', 'rtf', 'txt', 'html'):
        add(_convert('document', target, office.word, needs=_has('Word')))
    for target in ('pdf', 'docx'):
        add(_convert('text', target, office.word, needs=_has('Word')))

    for target in ('pdf', 'xlsx', 'csv', 'ods', 'xls'):
        add(_convert('sheet', target, office.excel, needs=_has('Excel')))
    add(_convert('sheet', 'json', data.csv_to_json, exts=frozenset({'csv', 'tsv'}),
                 hint='Save the rows as a JSON list'))
    add(_convert('data', 'csv', data.json_to_csv, hint='Save the records as CSV rows'))

    for target in ('pdf', 'pptx', 'odp', 'ppt'):
        add(_convert('slides', target, office.powerpoint, needs=_has('PowerPoint')))
    add(_convert('slides', 'png', office.powerpoint, needs=_has('PowerPoint'),
                 hint='Save each slide as a PNG'))

    for target in ('zip', '7z', 'tar.gz'):
        add(_convert('archive', target, archives.repack,
                     needs=(lambda: locate.seven_zip() is not None) if target == '7z' else None,
                     hint=f'Repack as {target.upper()}'))

    # ------------------------------------------------------------------ Tools
    def tool(id, label, kinds, run, hint, icon, **extra):
        add(Op(id=f'tools.{id}', label=label, mode='tools', kinds=frozenset(kinds), run=run,
               hint=hint, icon=icon, **extra))

    def by_kind(table: dict) -> Callable:
        def work(job, src):
            table[kind_of(src)](job, src)
        return each(work)

    # Edit and Show metadata open windows; app.py's Controller opens them.
    tool('edit', 'Edit media', {'video', 'audio'}, None,
         'Trim, change speed, volume or fades - and crop or rotate pictures - then export', 'scissors',
         needs=_has_ffmpeg, gui=True, max_files=4, also=frozenset({'gif'}),
         named=(('video', 'Edit video'), ('audio', 'Edit audio'), ('image', 'Edit GIF')))
    tool('compress', 'Compress', {'image', 'video', 'audio', 'pdf'},
         by_kind({'image': images.compress, 'video': media.compress_video,
                  'audio': media.compress_audio, 'pdf': pdf.compress}),
         'Make a smaller copy', 'compress', verb='Compressing')
    tool('half', 'Half size', {'image', 'video'},
         by_kind({'image': images.half_size, 'video': media.half_size_video}),
         'Make a copy at half the width and height', 'shrink', verb='Resizing')
    tool('rotate_right', 'Rotate right', {'image', 'video', 'pdf'},
         by_kind({'image': partial(images.rotate, clockwise=True),
                  'video': partial(media.rotate_video, clockwise=True),
                  'pdf': partial(pdf.rotate, clockwise=True)}),
         'Turn a copy a quarter turn clockwise', 'rotate_right', verb='Rotating')
    tool('rotate_left', 'Rotate left', {'image', 'pdf'},
         by_kind({'image': partial(images.rotate, clockwise=False),
                  'pdf': partial(pdf.rotate, clockwise=False)}),
         'Turn a copy a quarter turn anticlockwise', 'rotate_left', verb='Rotating')
    tool('flip', 'Flip', {'image'}, each(images.flip), 'Mirror a copy left to right', 'flip',
         verb='Flipping')
    tool('grayscale', 'Grayscale', {'image'}, each(images.grayscale), 'Make a black-and-white copy',
         'grayscale', verb='Converting')
    tool('ocr', 'Copy text', {'image', 'pdf'}, ocr.copy_text,
         'Read the words in it and copy them - Windows reads them, on this PC', 'scan',
         needs=suiteocr.available, verb='Reading')
    tool('info', 'Show metadata', ANY, None,
         'See what it says about itself: camera, location, author, codecs...', 'tag', gui=True)
    tool('strip', 'Remove metadata', {'image', 'video', 'audio', 'pdf'},
         by_kind({'image': images.strip_metadata, 'video': media.strip_metadata,
                  'audio': media.strip_metadata, 'pdf': pdf.strip_metadata}),
         'Copy without camera, location and author details', 'shield', verb='Cleaning')
    tool('combine', 'Combine PDF', {'image', 'pdf'} | set(pdf.BY_OFFICE), pdf.combine,
         'Join them, in this order, into one PDF', 'combine', min_files=2, verb='Combining')
    tool('split', 'Split pages', {'pdf'}, each(pdf.split), 'Save every page as its own PDF',
         'split', verb='Splitting')
    tool('join_audio', 'Join audio', {'audio'}, media.join_audio, 'Play them one after another, as one file',
         'combine', min_files=2, needs=_has_ffmpeg, verb='Joining')
    tool('join_video', 'Join videos', {'video'}, media.join_videos,
         'Play them one after another, as one MP4', 'combine', min_files=2, needs=_has_ffmpeg, verb='Joining')
    tool('mute', 'Remove sound', {'video'}, each(media.mute), 'Make a silent copy', 'mute',
         needs=_has_ffmpeg, verb='Muting')
    tool('frame', 'Grab frame', {'video'}, each(media.snapshot), 'Save a still from a third of the way in',
         'image', needs=_has_ffmpeg, verb='Grabbing a frame from')
    tool('zip', 'Zip', ANY, archives.zip_items, 'Pack everything into one ZIP', 'zip', verb='Zipping')
    tool('extract', 'Extract', {'archive'}, each(archives.extract), 'Unpack it beside the archive',
         'unzip', verb='Extracting')

    # ------------------------------------------------------------------ Actions
    def action(id, label, run, hint, icon, **extra):
        add(Op(id=f'actions.{id}', label=label, mode='actions', kinds=ANY, run=run, hint=hint,
               icon=icon, **extra))

    action('copy', 'Copy', actions.copy_to_clipboard, 'Put it on the clipboard, ready to paste',
           'copy', gui=True)
    action('copy_path', 'Copy path', actions.copy_paths, 'Copy where it is, as text', 'link', gui=True)
    action('float', 'Float', None, 'Keep it in a small window on top, to drag from later', 'pin',
           gui=True)
    action('move_to', 'Move to…', actions.move_to, 'Choose a folder to move it to', 'move',
           ask_folder='Move to', verb='Moving')
    action('copy_to', 'Copy to…', actions.copy_to, 'Choose a folder to copy it to', 'duplicate',
           ask_folder='Copy to', verb='Copying')
    action('terminal', 'Terminal', actions.terminal, 'Open a terminal in its folder', 'terminal',
           gui=True)
    action('open_with', 'Open with…', actions.open_with, 'Pick an app to open it in', 'open',
           gui=True, max_files=1)
    action('properties', 'Properties', actions.properties, "Show Windows' Properties for it", 'info',
           gui=True)
    return ops


OPS: list[Op] = _build()
BY_ID = {op.id: op for op in OPS}
CUSTOM: list[Op] = []           # the person's own, from Settings (set_custom)


def set_custom(entries: list[dict]) -> None:
    """Make the drop targets of the person's own, as Settings describes them: {'name', 'command',
    'mode' ('tools' or 'actions'), 'exts' (as 'png, jpg'; empty for any file), 'each' (once for
    each file, else once for all)}."""
    for op in CUSTOM:
        BY_ID.pop(op.id, None)
    CUSTOM.clear()
    for index, entry in enumerate(entries):
        name, command = str(entry.get('name', '')).strip(), str(entry.get('command', '')).strip()
        if not name or not command:
            continue
        exts = frozenset(SAME.get(e, e) for e in (part.strip().lower().lstrip('.')
                                                  for part in str(entry.get('exts', '')).split(',')) if e)
        each_file = bool(entry.get('each', True))
        op = Op(id=f'custom.{index}', label=name, mode='actions' if entry.get('mode') == 'actions' else 'tools',
                kinds=ANY, exts=exts, run=custom.runner(name, command, each_file), hint=command, icon='terminal',
                verb='Running')
        CUSTOM.append(op)
        BY_ID[op.id] = op

# Opened as windows or done on the spot, so they need the file on disk the
# moment they are chosen: not offered for something still on a web page.
NEEDS_A_FILE_NOW = frozenset({'actions.float', 'tools.info', 'tools.edit'})


def _web_ops(mode: str) -> list[Op]:
    """What heads every menu for something dragged out of a web page."""
    return [
        Op(id='web.download', label='Download', mode=mode, kinds=ANY, run=web.save,
           hint='Save it as it is', icon='download', verb='Downloading'),
        Op(id='web.grabbit', label='Grabbit', mode=mode, kinds=ANY, run=web.to_grabbit,
           hint='Hand it to Grabbit, for a video, a post or a playlist', icon='video', gui=True,
           needs=lambda: web.grabbit() is not None),
    ]


WEB_OPS = {mode: _web_ops(mode) for mode in MODES}


def split(op: Op, paths: list[Path]) -> tuple[list[Path], list[Path]]:
    """The files op works on, and the ones it would skip."""
    taken = [p for p in paths if op.takes(p)]
    if op.id == 'tools.combine':
        # Office files only where Office can make PDFs of them.
        taken = [p for p in taken if kind_of(p) not in pdf.BY_OFFICE or office.available(pdf.BY_OFFICE[kind_of(p)])]
    if op.mode == 'convert':
        # Converting to what it already is would only make a duplicate.
        taken = [p for p in taken if same_format(p) != op.target]
    if op.id == 'convert.archive.' + op.target or op.id == 'tools.extract':
        taken = [p for p in taken if archives.readable(p)]
    kept = set(taken)
    skipped = [p for p in paths if p not in kept]
    return taken, skipped


def usable(op: Op, paths: list[Path]) -> bool:
    if not op.available():
        return False
    taken, _ = split(op, paths)
    if len(taken) < op.min_files:
        return False
    if op.max_files and len(paths) > op.max_files:
        return False
    return True


def ops_for(mode: str, paths: list[Path]) -> list[tuple[str, list[Op]]]:
    """[(group title, ops)] for the menu. Convert groups by kind of file;
    Tools and Actions are one group each. Something from a web page gets
    Download first, then what suits the file it will be (web.py)."""
    if web.dragged(paths):
        groups = [('From the web', [op for op in WEB_OPS[mode] if usable(op, paths)])]
        if mode != 'actions' and not any(web.is_link(path) for path in paths):
            for title, ops in _groups(mode, paths):
                ops = [op for op in ops if not op.gui and op.id not in NEEDS_A_FILE_NOW]
                if ops:
                    groups.append((title, ops))
        return groups
    return _groups(mode, paths)


def _groups(mode: str, paths: list[Path]) -> list[tuple[str, list[Op]]]:
    candidates = [op for op in OPS + CUSTOM if op.mode == mode and usable(op, paths)]
    if mode != 'convert':
        return [('', candidates)] if candidates else []
    present = []
    for path in paths:
        kind = kind_of(path)
        if kind not in present:
            present.append(kind)
    groups = []
    for kind in present:
        ops = [op for op in candidates if kind in op.kinds]
        if ops:
            groups.append((KIND_TITLES[kind], ops))
    return groups
