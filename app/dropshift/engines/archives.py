"""Zipping, unzipping and repacking archives.

ZIP and TAR are handled by Python itself; 7Z, RAR, ISO and the rest need an
installed 7-Zip. Extracting never writes outside the folder it makes: entries
named like ../../evil.exe are refused.
"""

from __future__ import annotations

import gzip
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path

from .. import jobs, locate, win32
from ..jobs import Job, UserError

PYTHON_READS = ('.zip', '.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz', '.gz')


def _ext(path: Path) -> str:
    return jobs.split_name(path)[1].lower()


def readable(path: Path) -> bool:
    return _ext(path) in PYTHON_READS or locate.seven_zip() is not None


def _items(paths: list[Path]):
    """(file on disk, name inside the archive) for files and whole folders."""
    for path in paths:
        if path.is_dir():
            for child in sorted(path.rglob('*')):
                if child.is_file():
                    yield child, child.relative_to(path.parent).as_posix()
        elif path.is_file():
            yield path, path.name


def _write_zip(job: Job, paths: list[Path], out: Path) -> None:
    entries = list(_items(paths))
    total = sum(f.stat().st_size for f, _ in entries) or 1
    done = 0
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file, name in entries:
            if job.cancelled:
                return
            archive.write(file, name)
            done += file.stat().st_size
            job.progress(done / total)


def zip_items(job: Job) -> None:
    paths = job.files
    if len(paths) == 1:
        stem = paths[0].name if paths[0].is_dir() else jobs.split_name(paths[0])[0]
        out = job.output_named(paths[0], f'{stem}.zip')
    else:
        out = job.output_named(paths[0], 'Archive.zip')
    _write_zip(job, paths, out)
    job.add(out)


def _inside(root: Path, name: str) -> Path:
    target = (root / name).resolve()
    if not target.is_relative_to(root.resolve()):
        raise UserError(f'The archive tries to write outside its folder ({name}); not extracted')
    return target


def _unpack(job: Job, src: Path, dest: Path) -> None:
    ext = _ext(src)
    if ext == '.zip':
        with zipfile.ZipFile(src) as archive:
            members = archive.infolist()
            for index, member in enumerate(members):
                _inside(dest, member.filename)
                archive.extract(member, dest)
                job.progress((index + 1) / max(len(members), 1))
    elif ext == '.gz':
        with gzip.open(src, 'rb') as packed, open(dest / jobs.split_name(src)[0], 'wb') as plain:
            shutil.copyfileobj(packed, plain)
    elif ext in PYTHON_READS:
        with tarfile.open(src) as archive:
            archive.extractall(dest, filter='data')
    else:
        seven = locate.seven_zip()
        if not seven:
            raise UserError(f'7-Zip is needed to open {src.name}')
        result = win32.run_hidden([seven, 'x', '-y', '-bso0', '-bsp0', f'-o{dest}', src],
                                  capture_output=True, text=True, errors='replace')
        if result.returncode:
            raise UserError((result.stderr or 'Extracting failed').strip().splitlines()[-1])


def extract(job: Job, src: Path) -> None:
    """Unpack beside the archive. One folder (or file) inside takes the
    archive's place; anything else goes into a folder named after it."""
    stem, _ = jobs.split_name(src)
    parent = job.folder_for(src)
    work = Path(tempfile.mkdtemp(prefix='.dropshift-', dir=parent))
    try:
        _unpack(job, src, work)
        children = list(work.iterdir())
        if len(children) == 1:
            target = job.claim(jobs.free_path(parent, children[0].name))
            children[0].rename(target)
        else:
            target = job.claim(jobs.free_path(parent, stem))
            work.rename(target)
        job.add(target)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def repack(job: Job, src: Path, target: str) -> None:
    out = job.output_for(src, target)
    with tempfile.TemporaryDirectory(prefix='dropshift-') as tmp:
        root = Path(tmp)
        _unpack(job, src, root)
        contents = sorted(root.iterdir())
        if target == 'zip':
            _write_zip(job, contents, out)
        elif target == 'tar.gz':
            with tarfile.open(out, 'w:gz') as archive:
                for item in contents:
                    archive.add(item, arcname=item.name)
        elif target == '7z':
            seven = locate.seven_zip()
            if not seven:
                raise UserError('7-Zip is needed to make 7Z archives')
            result = win32.run_hidden([seven, 'a', '-t7z', '-y', '-bso0', '-bsp0', out,
                                       *[item.name for item in contents]],
                                      cwd=root, capture_output=True, text=True, errors='replace')
            if result.returncode:
                raise UserError((result.stderr or '7-Zip failed').strip().splitlines()[-1])
    job.add(out)
