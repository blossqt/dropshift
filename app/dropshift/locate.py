"""Where the command-line tools Dropshift drives are found.

A packaged build carries FFmpeg in tools\\ffmpeg\\ beside Dropshift.exe. Run
from source, the copy build\\bootstrap.ps1 fetched is used, and failing both,
whatever is on PATH. 7-Zip is never bundled; an installed one is used if found.
"""

from __future__ import annotations

import functools
import os
import shutil
import sys
from pathlib import Path


def app_folder() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def build_cache() -> Path:
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'DropshiftBuild'


def _find(exe: str, subfolder: str, extra: tuple[Path, ...] = ()) -> Path | None:
    for candidate in (app_folder() / 'tools' / subfolder / exe,
                      build_cache() / 'components' / subfolder / exe,
                      *extra):
        if candidate.is_file():
            return candidate
    found = shutil.which(exe)
    return Path(found) if found else None


@functools.cache
def ffmpeg() -> Path | None:
    return _find('ffmpeg.exe', 'ffmpeg')


@functools.cache
def ffprobe() -> Path | None:
    return _find('ffprobe.exe', 'ffmpeg')


@functools.cache
def seven_zip() -> Path | None:
    roots = [os.environ.get('ProgramFiles', r'C:\Program Files'),
             os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')]
    return _find('7z.exe', '7-zip', tuple(Path(root) / '7-Zip' / '7z.exe' for root in roots))
