"""PDFs: pages to pictures and text (pypdfium2), and combining, splitting,
rotating and slimming them down (pypdf)."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

from .. import jobs
from ..jobs import Job, UserError
from . import images

RENDER_DPI = 150
LARGEST_IMAGE = 2000     # px; bigger pictures inside a PDF are scaled down by Compress


def _open_pdfium(src: Path):
    import pypdfium2 as pdfium
    try:
        return pdfium.PdfDocument(str(src))
    except pdfium.PdfiumError as error:
        raise UserError(f"Can't open {src.name}: {error}") from None


def to_images(job: Job, src: Path, target: str) -> None:
    document = _open_pdfium(src)
    try:
        count = len(document)
        stem, _ = jobs.split_name(src)
        if count == 1:
            outs = [job.output_for(src, target)]
        else:
            folder = job.output_folder(src, f'{stem} pages')
            width = len(str(count))
            outs = [folder / f'{stem} {n:0{width}d}.{target}' for n in range(1, count + 1)]
        for index, out in enumerate(outs):
            if job.cancelled:
                return
            page = document[index]
            picture = page.render(scale=RENDER_DPI / 72).to_pil()
            images.save(picture, out, target)
            page.close()
            job.progress((index + 1) / count)
        job.add(outs[0] if count == 1 else outs[0].parent)
    finally:
        document.close()


def to_text(job: Job, src: Path, target: str = 'txt') -> None:
    document = _open_pdfium(src)
    try:
        pages = []
        for index in range(len(document)):
            page = document[index]
            text = page.get_textpage()
            pages.append(text.get_text_bounded().replace('\r\n', '\n'))
            text.close()
            page.close()
            job.progress((index + 1) / len(document))
    finally:
        document.close()
    if not any(p.strip() for p in pages):
        raise UserError(f'{src.name} has no text in it - it may be scanned pictures of pages')
    out = job.output_for(src, 'txt')
    out.write_text('\n\n'.join(pages), encoding='utf-8')
    job.add(out)


def _reader(src: Path):
    from pypdf import PdfReader
    reader = PdfReader(str(src))
    if reader.is_encrypted:
        try:
            if not reader.decrypt(''):
                raise UserError(f'{src.name} is password-protected')
        except Exception as error:  # noqa: BLE001
            raise UserError(f'{src.name} is password-protected') from error
    return reader


# Kinds of file Combine PDF makes PDFs of first, and the Office app that does.
BY_OFFICE = {'document': 'Word', 'text': 'Word', 'sheet': 'Excel', 'slides': 'PowerPoint'}


def combine(job: Job) -> None:
    """PDFs, pictures and Office files, in the order they were dropped, as one PDF. Office files
    are made into PDFs by Office first, in a folder of their own that goes afterwards."""
    from pypdf import PdfWriter

    from ..catalog import kind_of
    from . import office
    makers = {'Word': office.word, 'Excel': office.excel, 'PowerPoint': office.powerpoint}
    writer = PdfWriter()
    with tempfile.TemporaryDirectory(prefix='dropshift-') as scratch:
        for index, src in enumerate(job.files):
            kind = kind_of(src)
            if images.normal_ext(src) == 'pdf':
                writer.append(_reader(src))
            elif kind in BY_OFFICE:
                made = Job(job.op, [src], [], output_folder=scratch)
                makers[BY_OFFICE[kind]](made, src, 'pdf')
                writer.append(_reader(made.outputs[0]))
            else:
                writer.append(io.BytesIO(images.pdf_page(src)))
            job.progress((index + 1) / len(job.files))
        out = job.output_named(job.files[0], 'Combined.pdf')
        with open(out, 'wb') as handle:
            writer.write(handle)
    job.add(out)


def split(job: Job, src: Path) -> None:
    from pypdf import PdfWriter
    reader = _reader(src)
    count = len(reader.pages)
    if count < 2:
        raise UserError(f'{src.name} has only one page')
    stem, _ = jobs.split_name(src)
    folder = job.output_folder(src, f'{stem} pages')
    width = len(str(count))
    for index, page in enumerate(reader.pages):
        writer = PdfWriter()
        writer.add_page(page)
        with open(folder / f'{stem} {index + 1:0{width}d}.pdf', 'wb') as handle:
            writer.write(handle)
        job.progress((index + 1) / count)
    job.add(folder)


def rotate(job: Job, src: Path, clockwise: bool) -> None:
    from pypdf import PdfWriter
    writer = PdfWriter(clone_from=_reader(src))
    for page in writer.pages:
        page.rotate(90 if clockwise else -90)
    out = job.output_for(src, 'pdf', 'rotated')
    with open(out, 'wb') as handle:
        writer.write(handle)
    job.add(out)


def strip_metadata(job: Job, src: Path) -> None:
    from pypdf import PdfWriter
    writer = PdfWriter(clone_from=_reader(src))
    writer.metadata = None
    root = writer.root_object
    if '/Metadata' in root:
        del root['/Metadata']
    out = job.output_for(src, 'pdf', 'no metadata')
    with open(out, 'wb') as handle:
        writer.write(handle)
    job.add(out)


def compress(job: Job, src: Path) -> None:
    """Re-encode the pictures inside (scaled to at most 2000px) and pack the rest tighter."""
    from PIL import Image
    from pypdf import PdfWriter
    writer = PdfWriter(clone_from=_reader(src))
    pages = len(writer.pages)
    for index, page in enumerate(writer.pages):
        for picture in page.images:
            try:
                image = picture.image
                if image.mode not in ('RGB', 'L'):
                    continue
                if max(image.size) > LARGEST_IMAGE:
                    image.thumbnail((LARGEST_IMAGE, LARGEST_IMAGE), Image.Resampling.LANCZOS)
                picture.replace(image, quality=65)
            except Exception:  # noqa: BLE001 - an odd picture is left as it was
                continue
        page.compress_content_streams()
        job.progress((index + 1) / pages * 0.9)
    writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
    out = job.output_for(src, 'pdf', 'compressed')
    with open(out, 'wb') as handle:
        writer.write(handle)
    if out.stat().st_size >= src.stat().st_size:
        out.unlink()
        job.notes.append(f'{src.name} is already as small as it gets')
        return
    job.add(out)
