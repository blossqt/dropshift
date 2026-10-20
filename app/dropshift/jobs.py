"""One operation on a set of dropped files, run off the GUI thread.

Engines see a Job: the files it accepts, where to put what they make
(output_for and friends pick a free name beside the original), and progress()
to report how far along they are. Whatever an engine started writing is
removed again if it fails, so a failed conversion leaves nothing half-made.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from . import win32


class UserError(Exception):
    """A failure worth showing as it is, such as "This video has no sound"."""


# Names handed out but perhaps not written yet, so two jobs finishing at once
# never pick the same "photo (2).jpg".
_reserved: set[str] = set()
_reserved_lock = threading.Lock()

_COMPOUND = ('.tar.gz', '.tar.bz2', '.tar.xz')


def split_name(path: Path) -> tuple[str, str]:
    """('photo', '.jpg'), keeping '.tar.gz' together as one extension."""
    lower = path.name.lower()
    for ext in _COMPOUND:
        if lower.endswith(ext) and len(lower) > len(ext):
            return path.name[:-len(ext)], path.name[-len(ext):]
    return path.stem, path.suffix


def free_path(folder: Path, name: str) -> Path:
    stem, ext = split_name(Path(name))
    with _reserved_lock:
        candidate, n = folder / name, 2
        while candidate.exists() or str(candidate).lower() in _reserved:
            candidate = folder / f'{stem} ({n}){ext}'
            n += 1
        _reserved.add(str(candidate).lower())
    return candidate


def release(path: Path) -> None:
    """Forget a name free_path handed out; Job does this for what it claims."""
    with _reserved_lock:
        _reserved.discard(str(path).lower())


_writable: dict[str, bool] = {}


def writable(folder: Path) -> bool:
    key = str(folder).lower()
    if key not in _writable:
        try:
            with tempfile.TemporaryFile(dir=folder):
                pass
            _writable[key] = True
        except OSError:
            _writable[key] = False
    return _writable[key]


class Job:
    def __init__(self, op, files: list[Path], skipped: list[Path], output_folder: str = '',
                 destination: Path | None = None):
        self.op = op
        self.files = files
        self.skipped = skipped
        self.destination = destination      # for Move to / Copy to
        self.outputs: list[Path] = []
        self.notes: list[str] = []
        self._output_folder = Path(output_folder) if output_folder else None
        self._claimed: list[Path] = []
        self._made_dirs: list[Path] = []
        self._base, self._span = 0.0, 1.0
        self._last_report = 0.0
        self.cancelled = False
        self.on_progress: Callable[[float], None] = lambda fraction: None

    # ----------------------------------------------------------------- outputs

    def folder_for(self, src: Path) -> Path:
        folder = self._output_folder or src.parent
        if not writable(folder):
            folder = win32.downloads_folder()
            note = f'{src.parent} is read-only, so the result went to Downloads'
            if note not in self.notes:
                self.notes.append(note)
        return folder

    def output_for(self, src: Path, ext: str, tag: str = '') -> Path:
        """A free path beside src: 'photo.png' -> 'photo.jpg' or 'photo (small).png'."""
        stem, _ = split_name(src)
        name = f'{stem} ({tag})' if tag else stem
        return self.claim(free_path(self.folder_for(src), f'{name}.{ext.lstrip(".")}'))

    def output_named(self, src: Path, name: str) -> Path:
        return self.claim(free_path(self.folder_for(src), name))

    def output_folder(self, src: Path, name: str) -> Path:
        folder = free_path(self.folder_for(src), name)
        folder.mkdir(parents=True)
        self._made_dirs.append(folder)
        release(folder)
        return folder

    def claim(self, path: Path) -> Path:
        self._claimed.append(path)
        return path

    def add(self, path: Path) -> None:
        self.outputs.append(path)

    # ----------------------------------------------------------------- progress

    def progress(self, fraction: float) -> None:
        now = time.monotonic()
        if now - self._last_report < 0.08 and fraction < 1:
            return
        self._last_report = now
        overall = self._base + self._span * max(0.0, min(1.0, fraction))
        self.on_progress(overall)

    def for_each(self, work: Callable[[Path], None]) -> None:
        """Run work(src) on every file, spreading progress across them."""
        count = len(self.files)
        for index, src in enumerate(self.files):
            if self.cancelled:
                break
            self._base, self._span = index / count, 1 / count
            self.progress(0)
            work(src)
        self._base, self._span = 0.0, 1.0

    # ----------------------------------------------------------------- running

    def execute(self) -> str:
        """Run the operation; returns an error message, or '' on success."""
        try:
            self.op.run(self)
            return ''
        except UserError as error:
            self._discard_partial()
            return str(error)
        except Exception as error:  # noqa: BLE001 - shown to the person, logged for us
            traceback.print_exc()
            self._discard_partial()
            return f'{type(error).__name__}: {error}'
        finally:
            for path in self._claimed:
                release(path)

    def _discard_partial(self) -> None:
        kept = {str(p).lower() for p in self.outputs}
        for path in self._claimed:
            if str(path).lower() not in kept:
                try:
                    if path.is_dir():
                        shutil.rmtree(path, ignore_errors=True)
                    elif path.exists():
                        path.unlink()
                except OSError:
                    pass
        for folder in self._made_dirs:
            if str(folder).lower() not in kept:
                shutil.rmtree(folder, ignore_errors=True)


class _Signals(QObject):
    progress = Signal(float)
    finished = Signal(str)


class _Runnable(QRunnable):
    def __init__(self, job: Job, signals: _Signals):
        super().__init__()
        self.job, self.signals = job, signals
        job.on_progress = signals.progress.emit

    def run(self):
        self.signals.finished.emit(self.job.execute())


class JobRunner(QObject):
    """Runs jobs two at a time; Office and SVG work goes through the GUI thread."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._live: set = set()

    def start(self, job: Job, on_progress: Callable[[float], None],
              on_finished: Callable[[Job, str], None]) -> None:
        signals = _Signals()
        signals.progress.connect(on_progress)
        holder = (job, signals)
        self._live.add(holder)

        def finished(error: str):
            self._live.discard(holder)
            on_finished(job, error)

        signals.finished.connect(finished)
        if getattr(job.op, 'gui_thread', False):
            job.on_progress = signals.progress.emit
            QTimer.singleShot(0, lambda: signals.finished.emit(job.execute()))
        else:
            self._pool.start(_Runnable(job, signals))

    def busy(self) -> bool:
        return bool(self._live)
