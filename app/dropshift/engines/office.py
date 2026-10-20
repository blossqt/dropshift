"""Documents, spreadsheets and slides, converted by Microsoft Office itself.

Word and Excel are started as a private copy (DispatchEx) that nobody sees and
that is closed again afterwards. PowerPoint only ever runs once per person, so
if it was already open the copy is the person's own: Dropshift then closes
just the file it opened and leaves PowerPoint running.

One Office job runs at a time; two Words starting together tend to trip over
each other's settings files.
"""

from __future__ import annotations

import contextlib
import functools
import tempfile
import threading
from pathlib import Path

from .. import jobs, win32
from ..jobs import Job, UserError

_lock = threading.Lock()

WORD_FORMATS = {'docx': 16, 'doc': 0, 'rtf': 6, 'odt': 23, 'txt': 2, 'html': 10}
EXCEL_FORMATS = {'xlsx': 51, 'xls': 56, 'ods': 60, 'csv': 62}      # 62: CSV UTF-8
POWERPOINT_FORMATS = {'pptx': 24, 'ppt': 1, 'odp': 35, 'pdf': 32}
UTF8 = 65001


@functools.cache
def available(app: str) -> bool:
    return win32.com_class_registered(f'{app}.Application')


@contextlib.contextmanager
def _com():
    import pythoncom
    with _lock:
        pythoncom.CoInitialize()
        try:
            yield
        finally:
            pythoncom.CoUninitialize()


def _describe(error: Exception) -> str:
    # COM errors carry Office's own message deep inside their arguments.
    details = getattr(error, 'excepinfo', None) or (error.args[2] if len(error.args) > 2 else None)
    if isinstance(details, tuple) and len(details) > 2 and details[2]:
        return str(details[2]).strip()
    return str(error)


def word(job: Job, src: Path, target: str) -> None:
    if not available('Word'):
        raise UserError('Microsoft Word is needed for this')
    out = job.output_for(src, target)
    import win32com.client
    with _com():
        app = win32com.client.DispatchEx('Word.Application')
        try:
            app.Visible = False
            app.DisplayAlerts = 0
            try:
                document = app.Documents.Open(str(src), ConfirmConversions=False, ReadOnly=True,
                                              AddToRecentFiles=False, Visible=False,
                                              NoEncodingDialog=True)
            except Exception as error:  # noqa: BLE001
                raise UserError(f"Word couldn't open {src.name}: {_describe(error)}") from None
            try:
                job.progress(0.5)
                if target == 'pdf':
                    document.ExportAsFixedFormat(str(out), 17)
                elif target == 'txt':
                    document.SaveAs2(str(out), FileFormat=WORD_FORMATS['txt'], Encoding=UTF8,
                                     AddToRecentFiles=False)
                else:
                    document.SaveAs2(str(out), FileFormat=WORD_FORMATS[target], AddToRecentFiles=False)
            except Exception as error:  # noqa: BLE001
                raise UserError(f"Word couldn't save {out.name}: {_describe(error)}") from None
            finally:
                document.Close(SaveChanges=0)
        finally:
            if app.Documents.Count == 0:
                app.Quit(SaveChanges=0)
    job.add(out)


XL_WINDOWS_ANSI, XL_DELIMITED, XL_DOUBLE_QUOTE = 2, 1, 1


def _open_book(app, src: Path, scratch: Path):
    """Open a workbook - or a CSV, saying how it is encoded.

    Left to guess, Excel reads a CSV without a byte-order mark in the old
    Windows code page, and UTF-8 'Zürich' comes back as 'ZÃ¼rich'. OpenText
    can be told the encoding, but ignores what it is told for a file named
    .csv - so it is given a .txt copy of the same name instead.
    """
    if src.suffix.lower() not in ('.csv', '.tsv'):
        return app.Workbooks.Open(str(src), UpdateLinks=0, ReadOnly=True, AddToMru=False)
    raw = src.read_bytes()
    try:
        raw.decode('utf-8')
        origin = UTF8
    except UnicodeDecodeError:
        origin = XL_WINDOWS_ANSI
    sample = raw[:8192].decode('utf-8', 'replace')
    tab = src.suffix.lower() == '.tsv' or sample.count('\t') > sample.count(',')
    semicolon = not tab and sample.count(';') > sample.count(',')
    copy = scratch / f'{src.stem}.txt'
    copy.write_bytes(raw)
    app.Workbooks.OpenText(str(copy), Origin=origin, DataType=XL_DELIMITED,
                           TextQualifier=XL_DOUBLE_QUOTE, Tab=tab, Semicolon=semicolon,
                           Comma=not tab and not semicolon, Local=False)
    return app.ActiveWorkbook


def excel(job: Job, src: Path, target: str) -> None:
    if not available('Excel'):
        raise UserError('Microsoft Excel is needed for this')
    out = job.output_for(src, target)
    import win32com.client
    with _com(), tempfile.TemporaryDirectory(prefix='dropshift-') as scratch:
        app = win32com.client.DispatchEx('Excel.Application')
        try:
            app.Visible = False
            app.DisplayAlerts = False
            app.AskToUpdateLinks = False
            try:
                book = _open_book(app, src, Path(scratch))
            except Exception as error:  # noqa: BLE001
                raise UserError(f"Excel couldn't open {src.name}: {_describe(error)}") from None
            try:
                job.progress(0.5)
                if target == 'pdf':
                    book.ExportAsFixedFormat(0, str(out))
                else:
                    if target == 'csv' and book.Worksheets.Count > 1:
                        job.notes.append(f'{src.name}: CSV holds one sheet, so only the first was saved')
                        book.Worksheets(1).Activate()
                    book.SaveAs(str(out), FileFormat=EXCEL_FORMATS[target])
            except Exception as error:  # noqa: BLE001
                raise UserError(f"Excel couldn't save {out.name}: {_describe(error)}") from None
            finally:
                book.Close(SaveChanges=False)
        finally:
            if app.Workbooks.Count == 0:
                app.Quit()
    job.add(out)


def powerpoint(job: Job, src: Path, target: str) -> None:
    if not available('PowerPoint'):
        raise UserError('Microsoft PowerPoint is needed for this')
    stem, _ = jobs.split_name(src)
    if target == 'png':
        out = job.output_folder(src, f'{stem} slides')
    else:
        out = job.output_for(src, target)
    import win32com.client
    with _com():
        # Checked inside the lock, so another Dropshift job's PowerPoint
        # isn't mistaken for one the person opened.
        already_open = win32.process_running('POWERPNT.EXE')
        app = win32com.client.DispatchEx('PowerPoint.Application')
        try:
            try:
                deck = app.Presentations.Open(str(src), ReadOnly=True, Untitled=False, WithWindow=False)
            except Exception as error:  # noqa: BLE001
                raise UserError(f"PowerPoint couldn't open {src.name}: {_describe(error)}") from None
            try:
                if target == 'png':
                    slides = deck.Slides.Count
                    width = 1920
                    height = round(width * deck.PageSetup.SlideHeight / deck.PageSetup.SlideWidth)
                    digits = len(str(slides))
                    for n in range(1, slides + 1):
                        deck.Slides(n).Export(str(out / f'{stem} {n:0{digits}d}.png'), 'PNG', width, height)
                        job.progress(n / slides)
                else:
                    job.progress(0.5)
                    deck.SaveAs(str(out), POWERPOINT_FORMATS[target])
            except Exception as error:  # noqa: BLE001
                raise UserError(f"PowerPoint couldn't save {out.name}: {_describe(error)}") from None
            finally:
                deck.Close()
        finally:
            if not already_open and app.Presentations.Count == 0:
                app.Quit()
    job.add(out)
