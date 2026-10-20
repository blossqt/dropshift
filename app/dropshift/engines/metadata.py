"""What a file says about itself, gathered for the Show metadata window.

describe() returns sections of (field, value) rows: the file itself, then
whatever its kind carries - EXIF and GPS for photos, streams and tags for video
and audio, document properties for PDFs and Office files. Office files are read
straight from their XML, so this needs no Office installed.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import stat
import tarfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from .. import catalog, jobs
from . import media

Section = tuple[str, list[tuple[str, str]]]

FOLDER_TIME_LIMIT = 2.0     # seconds spent adding up a folder before giving up


def human_size(size: float) -> str:
    for unit in ('bytes', 'KB', 'MB', 'GB', 'TB'):
        if size < 1024 or unit == 'TB':
            return f'{size:.0f} {unit}' if unit == 'bytes' else f'{size:.2f} {unit}'
        size /= 1024
    return ''


def _size(size: int) -> str:
    return human_size(size) if size < 1024 else f'{human_size(size)} ({size:,} bytes)'


def _when(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')


def _duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f'{int(h)}:{int(m):02d}:{s:05.2f}' if h else f'{int(m)}:{s:05.2f}'


def describe(path: Path) -> list[Section]:
    sections = [_file_section(path)]
    kind = catalog.kind_of(path)
    readers = {'image': _image, 'video': _media, 'audio': _media, 'pdf': _pdf, 'folder': _folder,
               'archive': _archive, 'text': _text, 'sheet': _sheet, 'data': _json, 'svg': _svg,
               'subtitles': _text}
    reader = readers.get(kind)
    if catalog.ext_of(path) in ('docx', 'docm', 'xlsx', 'xlsm', 'pptx', 'pptm'):
        reader = _office
    if reader:
        try:
            sections += [s for s in reader(path) if s[1]]
        except Exception as error:  # noqa: BLE001 - say so rather than show nothing
            sections.append(('Contents', [('Could not read', str(error) or type(error).__name__)]))
    return sections


def as_text(path: Path, sections: list[Section]) -> str:
    lines = [str(path), '']
    for title, rows in sections:
        lines.append(title)
        lines += [f'  {field}: {value}' for field, value in rows]
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def location_of(sections: list[Section]) -> tuple[float, float] | None:
    for _, rows in sections:
        for field, value in rows:
            if field == 'Location':
                match = re.match(r'(-?\d+\.\d+), (-?\d+\.\d+)', value)
                if match:
                    return float(match.group(1)), float(match.group(2))
    return None


# --------------------------------------------------------------------------- the file


def _file_section(path: Path) -> Section:
    info = path.stat()
    kind = catalog.kind_of(path)
    rows = [('Name', path.name), ('Folder', str(path.parent))]
    if kind != 'folder':
        rows.append(('Size', _size(info.st_size)))
        ext = catalog.ext_of(path).upper()
        what = 'file' if kind == 'other' else catalog.KIND_TITLES[kind].lower()
        rows.append(('Type', f'{ext} {what}'.strip()))
    rows += [('Created', _when(info.st_ctime)), ('Modified', _when(info.st_mtime)),
             ('Accessed', _when(info.st_atime))]
    flags = []
    attributes = getattr(info, 'st_file_attributes', 0)
    if attributes & stat.FILE_ATTRIBUTE_READONLY:
        flags.append('read-only')
    if attributes & stat.FILE_ATTRIBUTE_HIDDEN:
        flags.append('hidden')
    if attributes & stat.FILE_ATTRIBUTE_SYSTEM:
        flags.append('system')
    if flags:
        rows.append(('Attributes', ', '.join(flags)))
    return ('File', rows)


def _folder(path: Path) -> list[Section]:
    files = folders = total = 0
    start, complete = time.monotonic(), True
    for root, dirs, names in os.walk(path):
        folders += len(dirs)
        for name in names:
            files += 1
            try:
                total += os.stat(os.path.join(root, name)).st_size
            except OSError:
                pass
        if time.monotonic() - start > FOLDER_TIME_LIMIT:
            complete = False
            break
    more = '' if complete else ' (stopped counting - more than this)'
    return [('Contents', [('Files', f'{files:,}{more}'), ('Folders', f'{folders:,}{more}'),
                          ('Total size', _size(total) + more)])]


# --------------------------------------------------------------------------- pictures


def _ratio(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _exif_value(tag: str, value) -> str:
    if isinstance(value, bytes):
        text = value.decode('utf-8', 'replace').strip('\x00 ')
        return text if text.isprintable() and text else f'({len(value)} bytes)'
    if tag == 'ExposureTime' and _ratio(value):
        seconds = _ratio(value)
        return f'1/{round(1 / seconds)} s' if seconds < 1 else f'{seconds:g} s'
    if tag == 'FNumber':
        return f'f/{_ratio(value):g}'
    if tag in ('FocalLength', 'FocalLengthIn35mmFilm'):
        return f'{_ratio(value):g} mm'
    if isinstance(value, tuple):
        return ', '.join(_exif_value('', v) for v in value)
    if hasattr(value, 'numerator'):
        return f'{_ratio(value):g}'
    return str(value).strip('\x00 ')


def _gps(gps: dict) -> tuple[float, float] | None:
    def degrees(values, ref):
        d, m, s = (_ratio(v) for v in values)
        sign = -1 if ref in ('S', 'W') else 1
        return sign * (d + m / 60 + s / 3600)
    try:
        return degrees(gps[2], gps.get(1, 'N')), degrees(gps[4], gps.get(3, 'E'))
    except (KeyError, TypeError, ValueError):
        return None


def _image(path: Path) -> list[Section]:
    from PIL import ExifTags, Image, ImageCms
    from . import images

    with Image.open(io.BytesIO(path.read_bytes())) as image:
        rows = [('Dimensions', f'{image.width} × {image.height} pixels'),
                ('Megapixels', f'{image.width * image.height / 1e6:.1f}'),
                ('Format', image.format or ''), ('Colour mode', image.mode)]
        dpi = image.info.get('dpi')
        if dpi:
            rows.append(('Resolution', f'{_ratio(dpi[0]):g} × {_ratio(dpi[1]):g} dpi'))
        if getattr(image, 'n_frames', 1) > 1:
            rows.append(('Frames', str(image.n_frames)))
        icc = image.info.get('icc_profile')
        if icc:
            try:
                profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                rows.append(('Colour profile', ImageCms.getProfileDescription(profile).strip()))
            except Exception:  # noqa: BLE001
                rows.append(('Colour profile', 'embedded'))
        sections = [('Picture', rows)]

        exif = image.getexif()
        camera, details = [], []
        main_tags = {'Make': 'Camera make', 'Model': 'Camera model', 'LensModel': 'Lens',
                     'DateTimeOriginal': 'Taken', 'ExposureTime': 'Exposure', 'FNumber': 'Aperture',
                     'ISOSpeedRatings': 'ISO', 'PhotographicSensitivity': 'ISO',
                     'FocalLength': 'Focal length', 'Software': 'Software', 'Artist': 'Artist',
                     'Copyright': 'Copyright', 'ImageDescription': 'Description'}
        merged = dict(exif)
        try:
            merged.update(exif.get_ifd(ExifTags.IFD.Exif))
        except Exception:  # noqa: BLE001
            pass
        for tag_id, value in merged.items():
            tag = ExifTags.TAGS.get(tag_id, f'Tag {tag_id:#06x}')
            if tag in ('ExifOffset', 'GPSInfo', 'MakerNote', 'UserComment', 'PrintImageMatching'):
                continue
            text = _exif_value(tag, value)
            if not text:
                continue
            if tag in main_tags and main_tags[tag] not in (f for f, _ in camera):
                camera.append((main_tags[tag], text))
            else:
                details.append((tag, text))
        try:
            gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
        except Exception:  # noqa: BLE001
            gps = {}
        place = _gps(gps) if gps else None
        if place:
            camera.append(('Location', f'{place[0]:.6f}, {place[1]:.6f}'))
            if 6 in gps:
                camera.append(('Altitude', f'{_ratio(gps[6]):.0f} m'))
        sections += [('Camera', camera), ('All EXIF', sorted(details))]
    if images.normal_ext(path) in ('jpg', 'png', 'webp', 'tiff', 'heic', 'avif') and not exif:
        sections.append(('Camera', [('EXIF', 'none - no camera or location details')]))
    return sections


def _svg(path: Path) -> list[Section]:
    root = ElementTree.parse(path).getroot()
    rows = [(k, root.get(k)) for k in ('width', 'height', 'viewBox') if root.get(k)]
    title = root.find('{http://www.w3.org/2000/svg}title')
    if title is not None and title.text:
        rows.append(('Title', title.text.strip()))
    return [('Drawing', rows)]


# --------------------------------------------------------------------------- video and audio

_ISO6709 = re.compile(r'([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)')


def _fps(rate: str) -> str:
    try:
        num, _, den = rate.partition('/')
        value = float(num) / float(den or 1)
        return f'{value:.3f}'.rstrip('0').rstrip('.') + ' fps' if value else ''
    except (ValueError, ZeroDivisionError):
        return ''


def _bitrate(value) -> str:
    try:
        return f'{int(value) / 1000:,.0f} kb/s'
    except (TypeError, ValueError):
        return ''


def _media(path: Path) -> list[Section]:
    info = media.probe(path)
    if not info:
        return [('Media', [('Could not read', 'FFmpeg is missing or did not recognise it')])]
    fmt = info.get('format', {})
    rows = [('Duration', _duration(media.duration(info))),
            ('Container', fmt.get('format_long_name', fmt.get('format_name', ''))),
            ('Overall bit rate', _bitrate(fmt.get('bit_rate')))]
    sections: list[Section] = [('Media', [r for r in rows if r[1]])]

    counts: dict[str, int] = {}
    for stream in info.get('streams', []):
        kind = stream.get('codec_type', '')
        if stream.get('disposition', {}).get('attached_pic'):
            sections.append(('Cover art', [('Picture', f'{stream.get("width")} × {stream.get("height")} '
                                                       f'{stream.get("codec_name", "").upper()}')]))
            continue
        counts[kind] = counts.get(kind, 0) + 1
        codec = stream.get('codec_long_name') or stream.get('codec_name', '')
        profile = stream.get('profile')
        rows = [('Codec', f'{codec} ({profile})' if profile and profile != 'unknown' else codec)]
        tags = stream.get('tags', {})
        if kind == 'video':
            rows += [('Resolution', f'{stream.get("width")} × {stream.get("height")}'),
                     ('Display aspect', stream.get('display_aspect_ratio', '')),
                     ('Frame rate', _fps(stream.get('avg_frame_rate', ''))),
                     ('Pixel format', stream.get('pix_fmt', '')),
                     ('Colour', ' / '.join(filter(None, (stream.get('color_space'), stream.get('color_primaries'),
                                                          stream.get('color_transfer')))))]
            for side in stream.get('side_data_list', []):
                if 'rotation' in side:
                    rows.append(('Rotation', f'{side["rotation"]}°'))
        elif kind == 'audio':
            layout = stream.get('channel_layout') or f'{stream.get("channels")} channels'
            rows += [('Sample rate', f'{int(stream.get("sample_rate", 0)):,} Hz' if stream.get('sample_rate') else ''),
                     ('Channels', layout)]
        rows += [('Bit rate', _bitrate(stream.get('bit_rate'))), ('Language', tags.get('language', '')),
                 ('Title', tags.get('title', ''))]
        title = {'video': 'Video', 'audio': 'Audio', 'subtitle': 'Subtitles'}.get(kind, kind.title() or 'Stream')
        number = f' {counts[kind]}' if counts[kind] > 1 else ''
        sections.append((title + number, [r for r in rows if r[1] and r[1] != 'und']))

    tags = []
    for key, value in fmt.get('tags', {}).items():
        lower = key.lower()
        if lower.endswith('location.iso6709') or lower == 'location':
            match = _ISO6709.match(value)
            if match:
                tags.append(('Location', f'{float(match.group(1)):.6f}, {float(match.group(2)):.6f}'))
                continue
        name = key.split('.')[-1] if key.startswith('com.apple.quicktime.') else key
        tags.append((name.replace('_', ' ').capitalize(), str(value)))
    sections.append(('Tags', tags))
    return sections


# --------------------------------------------------------------------------- documents


def _pdf_date(value: str) -> str:
    match = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?", value or '')
    if not match:
        return value or ''
    parts = [p or '01' for p in match.groups()]
    return f'{parts[0]}-{parts[1]}-{parts[2]} {parts[3] or "00"}:{parts[4] or "00"}:{parts[5] or "00"}'


_PAPER = {(210, 297): 'A4', (148, 210): 'A5', (297, 420): 'A3', (216, 279): 'Letter', (216, 356): 'Legal'}


def _pdf(path: Path) -> list[Section]:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    rows = []
    if reader.is_encrypted:
        rows.append(('Encrypted', 'yes'))
        if not reader.decrypt(''):
            return [('Document', rows + [('Pages', 'locked with a password')])]
    rows.append(('Pages', str(len(reader.pages))))
    if reader.pages:
        box = reader.pages[0].mediabox
        w_mm, h_mm = float(box.width) / 72 * 25.4, float(box.height) / 72 * 25.4
        paper = _PAPER.get((round(min(w_mm, h_mm)), round(max(w_mm, h_mm))), '')
        rows.append(('Page size', f'{w_mm:.0f} × {h_mm:.0f} mm' + (f' ({paper})' if paper else '')))
    rows.append(('PDF version', reader.pdf_header.replace('%PDF-', '')))
    if '/AcroForm' in reader.trailer['/Root']:
        rows.append(('Form fields', str(len(reader.get_fields() or {}))))
    meta = reader.metadata or {}
    names = {'/Title': 'Title', '/Author': 'Author', '/Subject': 'Subject', '/Keywords': 'Keywords',
             '/Creator': 'Made with', '/Producer': 'Converted by', '/CreationDate': 'Created',
             '/ModDate': 'Modified'}
    props = []
    for key, value in meta.items():
        text = str(value).strip()
        if not text:
            continue
        if key in ('/CreationDate', '/ModDate'):
            text = _pdf_date(text)
        props.append((names.get(key, key.lstrip('/')), text))
    return [('Document', rows), ('Properties', props)]


_OOXML = {'dc': 'http://purl.org/dc/elements/1.1/',
          'cp': 'http://schemas.openxmlformats.org/package/2006/metadata/core-properties',
          'dcterms': 'http://purl.org/dc/terms/',
          'ep': 'http://schemas.openxmlformats.org/officeDocument/2006/extended-properties'}


def _office(path: Path) -> list[Section]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        core = ElementTree.fromstring(archive.read('docProps/core.xml')) if 'docProps/core.xml' in names else None
        app = ElementTree.fromstring(archive.read('docProps/app.xml')) if 'docProps/app.xml' in names else None
    props = []
    fields = [('dc:title', 'Title'), ('dc:subject', 'Subject'), ('dc:creator', 'Author'),
              ('cp:keywords', 'Keywords'), ('dc:description', 'Comments'),
              ('cp:lastModifiedBy', 'Last saved by'), ('cp:revision', 'Revision'),
              ('dcterms:created', 'Created'), ('dcterms:modified', 'Modified'),
              ('cp:lastPrinted', 'Last printed')]
    if core is not None:
        for tag, label in fields:
            element = core.find(tag, _OOXML)
            if element is not None and (element.text or '').strip():
                text = element.text.strip()
                if tag.startswith('dcterms') or tag == 'cp:lastPrinted':
                    text = text.replace('T', ' ').rstrip('Z')
                props.append((label, text))
    stats = []
    if app is not None:
        for tag, label in (('Application', 'Made with'), ('AppVersion', 'App version'),
                           ('Company', 'Company'), ('Pages', 'Pages'), ('Words', 'Words'),
                           ('Characters', 'Characters'), ('Slides', 'Slides'), ('Notes', 'Notes'),
                           ('HiddenSlides', 'Hidden slides'), ('TotalTime', 'Editing time (min)')):
            element = app.find(f'ep:{tag}', _OOXML)
            if element is not None and (element.text or '').strip():
                stats.append((label, element.text.strip()))
    return [('Properties', props), ('Statistics', stats)]


def _text(path: Path) -> list[Section]:
    raw = path.read_bytes()
    encoding = 'UTF-8 with BOM' if raw.startswith(b'\xef\xbb\xbf') else 'UTF-16' if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else ''
    if not encoding:
        try:
            raw.decode('utf-8')
            encoding = 'UTF-8' if any(b > 127 for b in raw) else 'ASCII'
        except UnicodeDecodeError:
            encoding = 'Windows (ANSI) or other'
    text = raw.decode('utf-8', 'replace')
    endings = 'Windows (CRLF)' if '\r\n' in text else 'Unix (LF)' if '\n' in text else ''
    rows = [('Encoding', encoding), ('Lines', f'{text.count(chr(10)) + (1 if text and not text.endswith(chr(10)) else 0):,}'),
            ('Words', f'{len(text.split()):,}'), ('Characters', f'{len(text):,}')]
    if endings:
        rows.append(('Line endings', endings))
    return [('Text', rows)]


def _sheet(path: Path) -> list[Section]:
    if catalog.ext_of(path) not in ('csv', 'tsv'):
        return []
    text = path.read_bytes().decode('utf-8-sig', 'replace')
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;\t|')
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    delimiter = {',': 'comma', ';': 'semicolon', '\t': 'tab', '|': 'bar'}.get(dialect.delimiter, dialect.delimiter)
    info = [('Rows', f'{max(len(rows) - 1, 0):,} plus a header'), ('Columns', str(len(rows[0]) if rows else 0)),
            ('Separator', delimiter)]
    if rows:
        info.append(('Header', ', '.join(rows[0][:12]) + (' …' if len(rows[0]) > 12 else '')))
    return [('Table', info)] + _text(path)


def _json(path: Path) -> list[Section]:
    data = json.loads(path.read_bytes().decode('utf-8-sig'))
    if isinstance(data, list):
        rows = [('Top level', f'a list of {len(data):,} item{"" if len(data) == 1 else "s"}')]
    elif isinstance(data, dict):
        keys = list(data)
        rows = [('Top level', f'an object with {len(keys):,} key{"" if len(keys) == 1 else "s"}'),
                ('Keys', ', '.join(keys[:12]) + (' …' if len(keys) > 12 else ''))]
    else:
        rows = [('Top level', type(data).__name__)]
    return [('JSON', rows)]


def _archive(path: Path) -> list[Section]:
    ext = jobs.split_name(path)[1].lower()
    if ext == '.zip':
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            packed = sum(e.compress_size for e in entries)
            unpacked = sum(e.file_size for e in entries)
            rows = [('Files', f'{sum(1 for e in entries if not e.is_dir()):,}'),
                    ('Unpacked size', _size(unpacked)),
                    ('Compression', f'{100 - packed / unpacked * 100:.0f}% smaller' if unpacked else '')]
            if any(e.flag_bits & 1 for e in entries):
                rows.append(('Encrypted', 'yes'))
            if archive.comment:
                rows.append(('Comment', archive.comment.decode('utf-8', 'replace')))
            return [('Archive', [r for r in rows if r[1]])]
    if ext in ('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz'):
        with tarfile.open(path) as archive:
            members = archive.getmembers()
            return [('Archive', [('Files', f'{sum(1 for m in members if m.isfile()):,}'),
                                 ('Unpacked size', _size(sum(m.size for m in members)))])]
    return []
