"""File actions: things done with the files themselves rather than made from
them. They work on folders and mixed selections too."""

from __future__ import annotations

import shutil
import struct
from pathlib import Path

from .. import jobs, win32
from ..jobs import Job, UserError

DROPEFFECT_COPY = 1


def copy_to_clipboard(paths: list[Path]) -> None:
    """Put the files on the clipboard, ready to paste into a folder."""
    from PySide6.QtCore import QMimeData, QUrl
    from PySide6.QtWidgets import QApplication
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    # Explorer pastes as a copy, never a move, when this says so.
    mime.setData('application/x-qt-windows-mime;value="Preferred DropEffect"',
                 struct.pack('<I', DROPEFFECT_COPY))
    QApplication.clipboard().setMimeData(mime)


def copy_paths(paths: list[Path]) -> None:
    from PySide6.QtWidgets import QApplication
    QApplication.clipboard().setText('\r\n'.join(str(p) for p in paths))


def terminal(paths: list[Path]) -> None:
    first = paths[0]
    win32.open_terminal(first if first.is_dir() else first.parent)


def open_with(paths: list[Path]) -> None:
    win32.open_with(paths[0])


def properties(paths: list[Path]) -> None:
    for path in paths[:8]:
        win32.show_properties(path)


def _transfer(job: Job, move: bool) -> None:
    dest = job.destination
    if not dest or not dest.is_dir():
        raise UserError('That folder is no longer there')
    for index, src in enumerate(job.files):
        if job.cancelled:
            return
        if src.parent.resolve() == dest.resolve() and move:
            continue
        if dest.resolve().is_relative_to(src.resolve()):
            raise UserError(f"Can't put {src.name} inside itself")
        # Not claimed by the job on purpose: a failed move must never be
        # "cleaned up" by deleting what already reached the new folder.
        target = jobs.free_path(dest, src.name)
        try:
            if move:
                shutil.move(str(src), str(target))
            elif src.is_dir():
                shutil.copytree(src, target)
            else:
                shutil.copy2(src, target)
        finally:
            jobs.release(target)
        job.add(target)
        job.progress((index + 1) / len(job.files))


def move_to(job: Job) -> None:
    _transfer(job, move=True)


def copy_to(job: Job) -> None:
    _transfer(job, move=False)
