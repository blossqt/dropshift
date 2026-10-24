"""Drop targets of the person's own: a command line run on what was dropped (set up in Settings).

The command runs through cmd, in the dropped file's folder, with no console
window. In it, {file} is the file's path, {folder} the folder it is in, {name}
its name, {stem} its name without the extension and {ext} the extension -
each as it is, for the command to put in quotes - and {files} every file
dropped, each in quotes. A command with none of these gets the file (or the
files, run once for all) on the end, in quotes. Whatever new appears in the
folder is what it made.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .. import jobs, win32
from ..jobs import Job, UserError

PLACEHOLDERS = ('{file}', '{files}', '{folder}', '{name}', '{stem}', '{ext}')


def command_line(command: str, files: list[Path]) -> str:
    """The command with what was dropped put in."""
    first = files[0]
    quoted = ' '.join(f'"{f}"' for f in files)
    if not any(p in command for p in PLACEHOLDERS):
        return f'{command} {quoted}'
    stem, ext = jobs.split_name(first)
    for key, value in (('{files}', quoted), ('{file}', str(first)), ('{folder}', str(first.parent)),
                       ('{name}', first.name), ('{stem}', stem), ('{ext}', ext.lstrip('.'))):
        command = command.replace(key, value)
    return command


def runner(name: str, command: str, each: bool):
    """What a drop target of one's own runs: the command, once for each file or once for all."""
    def run(job: Job) -> None:
        if each:
            job.for_each(lambda src: _run(job, name, command, [src]))
        else:
            _run(job, name, command, job.files)
    return run


def _listing(folder: Path) -> set:
    try:
        return {p.name for p in folder.iterdir()}
    except OSError:
        return set()


def _run(job: Job, name: str, command: str, files: list[Path]) -> None:
    folder = files[0].parent
    before = _listing(folder)
    result = subprocess.run(command_line(command, files), shell=True, cwd=folder, capture_output=True,
                            text=True, errors='replace', creationflags=win32.CREATE_NO_WINDOW)
    if result.returncode:
        said = next((line.strip() for line in reversed((result.stderr or result.stdout or '').splitlines())
                     if line.strip()), '')
        raise UserError(f'{name} stopped with code {result.returncode}' + (f': {said}' if said else ''))
    for made in sorted(_listing(folder) - before):
        job.add(folder / made)
