"""Things dragged out of a web page: a picture, a link, a file a page links to.

A browser hands over a web address, not a file - with, from Chrome and Edge,
the name it would save it under. While the drag is under way that is all there
is: what kind of file it is comes from that name or from the address's own
ending, and anything else is a link to a page. So the menu is offered a stand-in
for each, a path in Dropshift's own temporary folder where the file will be, and
Convert and Tools treat it as the file it will be.

Dropping on a choice downloads first, then does what was chosen. Results go
where they go for a file from Explorer, except that "beside the original" means
the Downloads folder: a web page has no folder of its own. Download alone saves
the file as it is; Grabbit, when it is installed, is handed the address instead,
for a video page or a post of several pictures.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import struct
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import winreg
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import suiteweb
from .jobs import UserError

ROOT = Path(tempfile.gettempdir()) / 'Dropshift' / 'web'
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36')
TIMEOUT = 30
CHUNK = 256 * 1024

# What a server says it sent, as the extension it is saved with. Anything not
# here (application/octet-stream, say) keeps the name the drag suggested.
TYPES = {
    'image/jpeg': 'jpg', 'image/png': 'png', 'image/gif': 'gif', 'image/webp': 'webp',
    'image/avif': 'avif', 'image/svg+xml': 'svg', 'image/bmp': 'bmp', 'image/tiff': 'tiff',
    'image/x-icon': 'ico', 'image/vnd.microsoft.icon': 'ico', 'image/heic': 'heic',
    'video/mp4': 'mp4', 'video/webm': 'webm', 'video/quicktime': 'mov',
    'audio/mpeg': 'mp3', 'audio/mp4': 'm4a', 'audio/ogg': 'ogg', 'audio/wav': 'wav',
    'audio/x-wav': 'wav', 'audio/flac': 'flac', 'application/pdf': 'pdf',
    'application/zip': 'zip', 'application/json': 'json', 'text/plain': 'txt', 'text/html': 'html',
}
_FILE_GROUP = 'application/x-qt-windows-mime;value="FileGroupDescriptorW"'
_HTML_FORMAT = 'application/x-qt-windows-mime;value="HTML Format"'
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class Source:
    url: str
    page: str = ''          # the page it was dragged from, sent as the Referer
    link: bool = False      # a page rather than a file


_SOURCES: dict[Path, Source] = {}


def source(path: Path) -> Source | None:
    return _SOURCES.get(path)


def is_web(path: Path) -> bool:
    return path in _SOURCES


def is_link(path: Path) -> bool:
    found = _SOURCES.get(path)
    return bool(found and found.link)


def dragged(paths: list[Path]) -> bool:
    return bool(paths) and all(path in _SOURCES for path in paths)


def host(path: Path) -> str:
    found = _SOURCES.get(path)
    if not found or found.url.startswith('data:'):
        return ''
    name = urllib.parse.urlsplit(found.url).hostname or ''
    return name[4:] if name.startswith('www.') else name


# ------------------------------------------------------------------ the drag

def from_mime(mime) -> list[Path]:
    """Stand-ins for the web things in a drag, or [] when it holds none."""
    if not mime:
        return []
    urls = [url.toString() for url in mime.urls()
            if url.scheme() in ('http', 'https', 'data')] if mime.hasUrls() else []
    if not urls and mime.hasText():
        text = mime.text().strip()
        if re.fullmatch(r'https?://\S+', text):
            urls = [text]
    if not urls:
        return []
    names = suggested_names(mime)
    page = _page_of(mime)
    picture = len(urls) == 1 and mime.hasHtml() and '<img' in mime.html().lower()
    paths = []
    for index, url in enumerate(urls):
        name, link = name_for(url, names[index] if index < len(names) else '', picture)
        path = ROOT / hashlib.sha1(url.encode('utf-8')).hexdigest()[:12] / name
        _SOURCES[path] = Source(url, page, link)
        paths.append(path)
    return paths


def suggested_names(mime) -> list[str]:
    """The names Chrome and Edge would save a drag's files under (FILEGROUPDESCRIPTORW)."""
    if not mime.hasFormat(_FILE_GROUP):
        return []
    raw = bytes(mime.data(_FILE_GROUP))
    names = []
    try:
        count = struct.unpack_from('<I', raw)[0]
        for index in range(min(count, 64)):
            start = 4 + index * 592 + 72          # each FILEDESCRIPTORW is 592 bytes; cFileName at 72
            name = raw[start:start + 520].decode('utf-16-le').split('\x00', 1)[0]
            names.append(name)
    except (struct.error, UnicodeDecodeError):
        return []
    return names


def _page_of(mime) -> str:
    """The page the drag started on: CF_HTML's SourceURL line, when the browser gives one."""
    if not mime.hasFormat(_HTML_FORMAT):
        return ''
    head = bytes(mime.data(_HTML_FORMAT))[:2048].decode('utf-8', 'replace')
    found = re.search(r'^SourceURL:(\S+)', head, re.MULTILINE)
    return found.group(1) if found and found.group(1).startswith(('http://', 'https://')) else ''


def name_for(url: str, suggested: str, picture: bool) -> tuple[str, bool]:
    """(file name, is a link to a page) for an address dragged out of a page."""
    from . import catalog

    def known(name: str) -> bool:
        return catalog.kind_of(Path(name)) not in ('other', 'folder')

    if suggested and suggested.lower().endswith('.url'):
        # Chrome's name for a dragged link: the page's title, as an Internet shortcut.
        return clean(suggested[:-4]) + '.url', True
    if suggested and known(suggested):
        return clean(suggested), False
    if url.startswith('data:'):
        kind = url[5:].split(';', 1)[0].split(',', 1)[0].lower()
        return f'image.{TYPES.get(kind, "png")}' if kind.startswith('image/') else 'file.bin', False
    parts = urllib.parse.urlsplit(url)
    last = urllib.parse.unquote(parts.path.rstrip('/').rsplit('/', 1)[-1])
    if last and known(last):
        return clean(last), False
    if picture:
        # A picture whose address does not say what it is (x.com/media/a?format=jpg):
        # a JPG to begin with, put right once the server says what it sent.
        stem = clean(last.rsplit('.', 1)[0]) if last else 'image'
        return f'{stem or "image"}.jpg', False
    title = parts.hostname or 'link'
    return clean(title[4:] if title.startswith('www.') else title) + '.url', True


def clean(name: str) -> str:
    name = _BAD_NAME.sub('_', name).strip(' .')
    if len(name) > 120:
        stem, dot, ext = name.rpartition('.')
        name = (stem[:110] + dot + ext) if dot and len(ext) <= 8 else name[:120]
    return name or 'download'


# ------------------------------------------------------------------ fetching

def fetch(path: Path, progress: Callable[[float], None] = lambda fraction: None) -> Path:
    """Download a stand-in's file to where it stands. Returns where it went: the
    name changes if the server sent another kind of file than the name says."""
    found = _SOURCES[path]
    path.parent.mkdir(parents=True, exist_ok=True)
    if found.url.startswith('data:'):
        head, _, body = found.url.partition(',')
        data = base64.b64decode(body) if head.endswith(';base64') else urllib.parse.unquote_to_bytes(body)
        path.write_bytes(data)
        progress(1.0)
        return path
    with _reply(found, path) as reply:
        target = renamed_for(path, reply.headers.get_content_type(), found.link,
                             reply.headers.get_filename() or '')
        total = int(reply.headers.get('Content-Length') or 0)
        done = 0
        with open(target, 'wb') as out:
            while True:
                chunk = reply.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if total:
                    progress(done / total)
    progress(1.0)
    return target


def _reply(found: Source, path: Path):
    """The server's answer for a stand-in: for a picture a site shows smaller than it keeps
    it (suiteweb.fuller), the full-size one if that answers with a picture."""
    full = '' if found.link else suiteweb.fuller(found.url)
    headers = {'User-Agent': USER_AGENT, 'Accept': '*/*'}
    if found.page:
        headers['Referer'] = found.page
    for url in ([full] if full else []) + [found.url]:
        try:
            reply = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=TIMEOUT)
        except urllib.error.HTTPError as error:
            if url == full:
                continue
            if suiteweb.robot_check(error.code, error.headers or {}):
                raise UserError(_robot_check(found)) from None
            raise
        except OSError:
            if url == full:
                continue
            raise
        if url == full and not reply.headers.get_content_type().startswith('image/'):
            reply.close()
            continue
        if suiteweb.robot_check(reply.status, reply.headers):
            reply.close()
            raise UserError(_robot_check(found))
        return reply
    raise UserError(f'Could not download {path.name}')


def _robot_check(found: Source) -> str:
    site = urllib.parse.urlsplit(found.url).hostname or 'That site'
    site = site[4:] if site.startswith('www.') else site
    if found.link:
        return f"{site} checks for robots before it shows this page, so it can't be downloaded. Drag the picture itself instead."
    return f"{site} checks for robots before it gives out this file, so it can't be downloaded."


def renamed_for(path: Path, content_type: str, link: bool, offered: str = '') -> Path:
    """Where the download goes, given what the server says it is sending."""
    ext = TYPES.get(content_type, '')
    if link:
        # A link is a page, or a file the page links to: named by the server, then by type.
        if offered and '.' in offered:
            return path.with_name(clean(offered))
        return path.with_suffix('.' + (ext or 'html'))
    if ext and path.suffix.lower().lstrip('.') not in (ext, 'jpeg' if ext == 'jpg' else ext):
        return path.with_suffix('.' + ext)
    return path


def fetch_all(job) -> None:
    """Download every stand-in among a job's files, in its place in job.files."""
    count = len(job.files)
    for index, path in enumerate(list(job.files)):
        if path in _SOURCES:
            job.files[index] = fetch(path, lambda fraction, i=index: job.on_progress((i + fraction) / count))


def forget(paths: list[Path]) -> None:
    """Remove what was downloaded for a job, now that it is done with."""
    for path in paths:
        if path.parent.parent == ROOT:
            shutil.rmtree(path.parent, ignore_errors=True)


def after_download(run: Callable) -> Callable:
    """An operation's work, done once its web files are on disk."""
    def work(job):
        stand_ins = list(job.files)
        try:
            fetch_all(job)
            run(job)
        finally:
            forget(stand_ins)
    return work


def save(job) -> None:
    """Download: each file, as it is, where results go."""
    stand_ins = list(job.files)
    try:
        fetch_all(job)
        for src in job.files:
            target = job.output_named(src, src.name)
            shutil.move(str(src), str(target))
            job.add(target)
    finally:
        forget(stand_ins)


# ------------------------------------------------------------------ Grabbit

def grabbit() -> Path | None:
    """Grabbit's exe: where Hanami installs it, where it says it is, or a build of it."""
    local = Path(os.environ.get('LOCALAPPDATA', str(Path.home())))
    candidates = [local / 'Programs' / 'Grabbit' / 'Grabbit.exe']
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Classes\Grabbit.torrent\shell\open\command') as key:
            command = winreg.QueryValue(key, None)
        found = re.match(r'\s*"([^"]+\.exe)"', command or '')
        if found:
            candidates.append(Path(found.group(1)))
    except OSError:
        pass
    candidates.append(Path.home() / 'Documents' / 'Grabbit' / 'dist' / 'Grabbit' / 'Grabbit.exe')
    return next((path for path in candidates if path.is_file()), None)


def to_grabbit(paths: list[Path]) -> None:
    """Hand the addresses to Grabbit, which asks what to download from them."""
    exe = grabbit()
    if exe is None:
        raise RuntimeError('Grabbit is not installed')
    urls = [_SOURCES[path].url for path in paths if path in _SOURCES]
    subprocess.Popen([str(exe), *urls], cwd=str(exe.parent), close_fds=True)
