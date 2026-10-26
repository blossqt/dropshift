"""Every conversion, tool and action, run for real on freshly made sample files.

    %LOCALAPPDATA%\\DropshiftBuild\\venv\\Scripts\\python.exe -m unittest discover -s tests -v

Office conversions drive the real Word, Excel and PowerPoint (unseen) and are
skipped where those aren't installed; set DROPSHIFT_SKIP_OFFICE=1 to skip them
anyway, since they take a while.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PIL import Image  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from dropshift import catalog, locate, win32  # noqa: E402
from dropshift.engines import office  # noqa: E402
from dropshift.jobs import Job  # noqa: E402

APP = QApplication.instance() or QApplication([])
SKIP_OFFICE = os.environ.get('DROPSHIFT_SKIP_OFFICE') == '1'


def ffmpeg(*args) -> None:
    result = win32.run_hidden([locate.ffmpeg(), '-hide_banner', '-loglevel', 'error', '-y', *args],
                              capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def make_samples(folder: Path) -> dict[str, Path]:
    s: dict[str, Path] = {}
    picture = Image.new('RGBA', (320, 200), (40, 90, 200, 255))
    for x in range(0, 320, 20):
        picture.paste((255, 200, 0, 128), (x, 0, x + 10, 200))
    s['png'] = folder / 'alpha picture.png'
    picture.save(s['png'])
    s['jpg'] = folder / 'camera.jpg'
    exif = Image.Exif()
    exif[0x010F] = 'TestCam'          # Make
    picture.convert('RGB').save(s['jpg'], quality=90, exif=exif)
    s['gif'] = folder / 'spinner.gif'
    frames = [Image.new('RGB', (64, 64), (i * 40, 0, 255 - i * 40)) for i in range(5)]
    frames[0].save(s['gif'], save_all=True, append_images=frames[1:], duration=100, loop=0)
    s['svg'] = folder / 'drawing.svg'
    s['svg'].write_text('<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80">'
                        '<rect width="120" height="80" rx="12" fill="#3b5bdb"/>'
                        '<circle cx="60" cy="40" r="24" fill="#fff"/></svg>', encoding='utf-8')

    s['mp4'] = folder / 'clip.mp4'
    ffmpeg('-f', 'lavfi', '-i', 'testsrc=duration=2:size=320x240:rate=24', '-f', 'lavfi',
           '-i', 'sine=frequency=440:duration=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
           '-c:a', 'aac', '-shortest', s['mp4'])
    s['silent'] = folder / 'silent.webm'
    ffmpeg('-f', 'lavfi', '-i', 'testsrc=duration=1:size=160x120:rate=12', '-c:v', 'libvpx-vp9',
           '-b:v', '200k', s['silent'])
    s['wav'] = folder / 'tone.wav'
    ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=330:duration=2', s['wav'])
    s['mp3'] = folder / 'tone two.mp3'
    ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=550:duration=1:sample_rate=22050', '-ac', '1', s['mp3'])
    s['srt'] = folder / 'subs.srt'
    s['srt'].write_text('1\n00:00:00,500 --> 00:00:02,000\nHello there\n\n'
                        '2\n00:00:02,500 --> 00:00:04,000\nSecond line\n', encoding='utf-8')

    s['pdf'] = folder / 'three pages.pdf'
    pages = [Image.new('RGB', (850, 1100), color) for color in ('white', 'lightblue', 'lightyellow')]
    pages[0].save(s['pdf'], save_all=True, append_images=pages[1:], resolution=100)
    s['csv'] = folder / 'people.csv'
    with open(s['csv'], 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerows([['name', 'city'], ['Ada', 'London'], ['Zoë', 'Zürich']])
    s['json'] = folder / 'records.json'
    s['json'].write_text(json.dumps({'items': [{'id': 1, 'tags': ['a']}, {'id': 2, 'extra': True}]}),
                         encoding='utf-8')
    s['txt'] = folder / 'notes.txt'
    s['txt'].write_text('Dropshift test document\n\nA second paragraph.', encoding='utf-8')

    s['folder'] = folder / 'project'
    (s['folder'] / 'inner').mkdir(parents=True)
    (s['folder'] / 'a.txt').write_text('a')
    (s['folder'] / 'inner' / 'b.txt').write_text('b')
    s['zip'] = folder / 'bundle.zip'
    with zipfile.ZipFile(s['zip'], 'w') as archive:
        archive.writestr('bundle/readme.txt', 'hello')
        archive.writestr('bundle/data/x.bin', b'\0' * 100)
    s['tgz'] = folder / 'loose.tar.gz'
    with tarfile.open(s['tgz'], 'w:gz') as archive:
        archive.add(s['folder'] / 'a.txt', arcname='a.txt')
        archive.add(s['folder'] / 'inner' / 'b.txt', arcname='b.txt')
    s['evil'] = folder / 'evil.zip'
    with zipfile.ZipFile(s['evil'], 'w') as archive:
        archive.writestr('../escaped.txt', 'should never be written')
    return s


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix='dropshift-test-')
        cls.folder = Path(cls._tmp.name)
        cls.s = make_samples(cls.folder)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_op(self, op_id: str, *files: Path, destination: Path | None = None, expect_error=False) -> Job:
        op = catalog.BY_ID[op_id]
        taken, skipped = catalog.split(op, list(files))
        self.assertTrue(taken, f'{op_id} takes none of {[f.name for f in files]}')
        job = Job(op, taken, skipped, destination=destination)
        error = job.execute()
        if expect_error:
            self.assertTrue(error, f'{op_id} should have failed')
        else:
            self.assertEqual(error, '', f'{op_id} on {[f.name for f in files]}')
            for out in job.outputs:
                self.assertTrue(out.exists(), f'{op_id}: {out} missing')
                if out.is_file():
                    self.assertGreater(out.stat().st_size, 0, f'{op_id}: {out} is empty')
        return job

    def probe(self, path: Path) -> dict:
        from dropshift.engines.media import probe
        return probe(path)


class Images(Base):
    def test_every_image_target(self):
        for target in ('jpg', 'webp', 'avif', 'gif', 'bmp', 'tiff', 'ico', 'pdf'):
            with self.subTest(target=target):
                job = self.run_op(f'convert.image.{target}', self.s['png'])
                out = job.outputs[0]
                self.assertEqual(out.name, f'alpha picture.{target}')
                if target != 'pdf':
                    Image.open(out).load()

    def test_jpg_has_no_alpha_and_png_is_not_offered_to_itself(self):
        job = self.run_op('convert.image.jpg', self.s['png'])
        self.assertEqual(Image.open(job.outputs[0]).mode, 'RGB')
        _, skipped = catalog.split(catalog.BY_ID['convert.image.png'], [self.s['png']])
        self.assertEqual(skipped, [self.s['png']])

    def test_second_conversion_gets_a_free_name(self):
        first = self.run_op('convert.image.webp', self.s['jpg']).outputs[0]
        second = self.run_op('convert.image.webp', self.s['jpg']).outputs[0]
        self.assertNotEqual(first, second)
        self.assertEqual(second.name, 'camera (2).webp')

    def test_animated_gif_stays_animated(self):
        out = self.run_op('convert.image.webp', self.s['gif']).outputs[0]
        self.assertTrue(getattr(Image.open(out), 'is_animated', False))

    def test_svg(self):
        for target in ('png', 'pdf', 'ico'):
            with self.subTest(target=target):
                self.run_op(f'convert.svg.{target}', self.s['svg'])
        png = self.run_op('convert.svg.png', self.s['svg']).outputs[0]
        self.assertEqual(max(Image.open(png).size), 2048)

    def test_tools(self):
        for op_id in ('tools.half', 'tools.rotate_right', 'tools.rotate_left', 'tools.flip',
                      'tools.grayscale', 'tools.compress'):
            with self.subTest(op=op_id):
                self.run_op(op_id, self.s['png'])
        half = self.run_op('tools.half', self.s['png']).outputs[0]
        self.assertEqual(Image.open(half).size, (160, 100))
        turned = self.run_op('tools.rotate_right', self.s['png']).outputs[0]
        self.assertEqual(Image.open(turned).size, (200, 320))

    def test_metadata_is_removed(self):
        self.assertIn(0x010F, Image.open(self.s['jpg']).getexif())
        out = self.run_op('tools.strip', self.s['jpg']).outputs[0]
        self.assertNotIn(0x010F, Image.open(out).getexif())


@unittest.skipUnless(locate.ffmpeg(), 'FFmpeg not found')
class Media(Base):
    def test_video_targets(self):
        for target in ('mov', 'webm', 'mkv', 'avi', 'gif', 'mp3', 'm4a', 'wav'):
            with self.subTest(target=target):
                out = self.run_op(f'convert.video.{target}', self.s['mp4']).outputs[0]
                self.assertTrue(self.probe(out).get('streams'), out)

    def test_mov_copies_h264_rather_than_re_encoding(self):
        out = self.run_op('convert.video.mov', self.s['mp4']).outputs[0]
        codecs = {s['codec_type']: s['codec_name'] for s in self.probe(out)['streams']}
        self.assertEqual(codecs, {'video': 'h264', 'audio': 'aac'})

    def test_webm_to_mp4_re_encodes(self):
        out = self.run_op('convert.video.mp4', self.s['silent']).outputs[0]
        self.assertEqual(self.probe(out)['streams'][0]['codec_name'], 'h264')

    def test_audio_from_a_silent_video_says_so(self):
        job = Job(catalog.BY_ID['convert.video.mp3'], [self.s['silent']], [])
        self.assertIn('no sound', job.execute())

    def test_audio_targets(self):
        for target in ('mp3', 'm4a', 'flac', 'ogg', 'opus', 'aiff'):
            with self.subTest(target=target):
                self.run_op(f'convert.audio.{target}', self.s['wav'])

    def test_subtitles(self):
        out = self.run_op('convert.subtitles.vtt', self.s['srt']).outputs[0]
        self.assertIn('WEBVTT', out.read_text(encoding='utf-8'))
        styled = self.run_op('convert.subtitles.ass', out).outputs[0]
        self.assertIn('Second line', styled.read_text(encoding='utf-8'))

    def test_tools(self):
        for op_id in ('tools.half', 'tools.rotate_right', 'tools.mute', 'tools.strip', 'tools.frame'):
            with self.subTest(op=op_id):
                self.run_op(op_id, self.s['mp4'])
        muted = self.run_op('tools.mute', self.s['mp4']).outputs[0]
        self.assertEqual([s['codec_type'] for s in self.probe(muted)['streams']], ['video'])

    def test_join_audio_of_different_rates(self):
        out = self.run_op('tools.join_audio', self.s['wav'], self.s['mp3']).outputs[0]
        self.assertAlmostEqual(float(self.probe(out)['format']['duration']), 3.0, delta=0.2)

    def test_join_videos_of_different_sizes_one_silent(self):
        out = self.run_op('tools.join_video', self.s['mp4'], self.s['silent']).outputs[0]
        self.assertEqual(out.name, 'Joined.mp4')
        info = self.probe(out)
        self.assertAlmostEqual(float(info['format']['duration']), 3.0, delta=0.3)
        video = next(s for s in info['streams'] if s['codec_type'] == 'video')
        self.assertEqual((video['width'], video['height']), (320, 240), "the first one's size")
        self.assertIn('audio', [s['codec_type'] for s in info['streams']], 'sound throughout, silent where none')

    def test_join_videos_needs_two(self):
        self.assertFalse(catalog.usable(catalog.BY_ID['tools.join_video'], [self.s['mp4']]))

    def test_progress_is_reported(self):
        seen = []
        op = catalog.BY_ID['convert.video.webm']
        job = Job(op, [self.s['mp4']], [])
        job.on_progress = seen.append
        self.assertEqual(job.execute(), '')
        self.assertTrue(seen and max(seen) > 0.3, seen)


class Pdf(Base):
    def test_pages_to_pictures(self):
        job = self.run_op('convert.pdf.png', self.s['pdf'])
        folder = job.outputs[0]
        self.assertTrue(folder.is_dir())
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         ['three pages 1.png', 'three pages 2.png', 'three pages 3.png'])

    def test_no_text_says_so(self):
        job = Job(catalog.BY_ID['convert.pdf.txt'], [self.s['pdf']], [])
        self.assertIn('no text', job.execute())

    def test_combine_split_rotate(self):
        from pypdf import PdfReader
        combined = self.run_op('tools.combine', self.s['pdf'], self.s['png'], self.s['jpg']).outputs[0]
        self.assertEqual(len(PdfReader(combined).pages), 5)
        parts = self.run_op('tools.split', self.s['pdf']).outputs[0]
        self.assertEqual(len(list(parts.iterdir())), 3)
        turned = self.run_op('tools.rotate_right', self.s['pdf']).outputs[0]
        self.assertEqual(PdfReader(turned).pages[0].rotation, 90)
        self.run_op('tools.strip', self.s['pdf'])

    @unittest.skipUnless(office.available('Word') and not SKIP_OFFICE, 'Word not installed')
    def test_combine_takes_office_files_too(self):
        from pypdf import PdfReader
        combined = self.run_op('tools.combine', self.s['txt'], self.s['pdf']).outputs[0]
        reader = PdfReader(combined)
        self.assertEqual(len(reader.pages), 4, 'the document as a page, then the PDF')
        self.assertIn('Dropshift test document', reader.pages[0].extract_text())
        self.assertEqual(sorted(p.name for p in self.folder.glob('notes*.pdf')), [], 'nothing left over beside it')

    def test_compress_shrinks_or_says_why(self):
        job = Job(catalog.BY_ID['tools.compress'], [self.s['pdf']], [])
        self.assertEqual(job.execute(), '')
        self.assertTrue(job.outputs or job.notes)


class DataAndArchives(Base):
    def test_csv_json_round_trip(self):
        out = self.run_op('convert.sheet.json', self.s['csv']).outputs[0]
        self.assertEqual(json.loads(out.read_text(encoding='utf-8'))[1]['city'], 'Zürich')
        back = self.run_op('convert.data.csv', self.s['json']).outputs[0]
        rows = list(csv.DictReader(back.read_text(encoding='utf-8-sig').splitlines()))
        self.assertEqual(rows[0]['tags'], '["a"]')
        self.assertEqual(set(rows[0]), {'id', 'tags', 'extra'})

    def test_zip_a_folder_and_a_file(self):
        out = self.run_op('tools.zip', self.s['folder'], self.s['txt']).outputs[0]
        with zipfile.ZipFile(out) as archive:
            self.assertEqual(sorted(archive.namelist()),
                             ['notes.txt', 'project/a.txt', 'project/inner/b.txt'])

    def test_extract_one_folder_is_not_doubled(self):
        out = self.run_op('tools.extract', self.s['zip']).outputs[0]
        self.assertEqual(out.name, 'bundle')
        self.assertTrue((out / 'readme.txt').exists())

    def test_extract_loose_files_get_a_folder(self):
        out = self.run_op('tools.extract', self.s['tgz']).outputs[0]
        self.assertEqual(out.name, 'loose')
        self.assertEqual(sorted(p.name for p in out.iterdir()), ['a.txt', 'b.txt'])

    def test_zip_slip_is_refused(self):
        self.run_op('tools.extract', self.s['evil'], expect_error=True)
        self.assertFalse((self.folder.parent / 'escaped.txt').exists())
        self.assertFalse(any(p.name.startswith('.dropshift-') for p in self.folder.iterdir()))

    def test_repack(self):
        out = self.run_op('convert.archive.tar.gz', self.s['zip']).outputs[0]
        self.assertEqual(out.name, 'bundle.tar.gz')
        if locate.seven_zip():
            self.run_op('convert.archive.7z', self.s['tgz'])
            seven = self.folder / 'bundle.7z'
            self.run_op('convert.archive.7z', self.s['zip'])
            self.assertTrue(seven.exists())


class Actions(Base):
    def test_copy_and_move_to_a_folder(self):
        dest = self.folder / 'destination'
        dest.mkdir()
        copied = self.run_op('actions.copy_to', self.s['folder'], self.s['txt'], destination=dest)
        self.assertEqual(sorted(p.name for p in copied.outputs), ['notes.txt', 'project'])
        self.assertTrue(self.s['txt'].exists())
        loose = self.folder / 'to move.txt'
        loose.write_text('x')
        self.run_op('actions.move_to', loose, destination=dest)
        self.assertFalse(loose.exists())
        self.assertTrue((dest / 'to move.txt').exists())

    def test_clipboard_holds_files(self):
        import win32clipboard
        from dropshift.engines import actions
        actions.copy_to_clipboard([self.s['png'], self.s['folder']])
        APP.processEvents()
        win32clipboard.OpenClipboard()
        try:
            files = win32clipboard.GetClipboardData(win32clipboard.CF_HDROP)
        finally:
            win32clipboard.CloseClipboard()
        self.assertEqual([Path(f) for f in files], [self.s['png'], self.s['folder']])

    def test_menu_offers_actions_for_folders_but_no_conversions(self):
        self.assertEqual(catalog.ops_for('convert', [self.s['folder']]), [])
        labels = [op.label for op in catalog.ops_for('actions', [self.s['folder']])[0][1]]
        self.assertIn('Terminal', labels)
        self.assertNotIn('Open with…', catalog.ops_for('actions', [self.s['png'], self.s['jpg']])[0][1])


@unittest.skipIf(SKIP_OFFICE, 'DROPSHIFT_SKIP_OFFICE=1')
class Custom(Base):
    """Drop targets of one's own, run for real through cmd."""

    def tearDown(self):
        catalog.set_custom([])

    def target(self, command: str, exts: str = '', each: bool = True, mode: str = 'tools'):
        catalog.set_custom([{'name': 'Mine', 'command': command, 'mode': mode, 'exts': exts, 'each': each}])
        return catalog.BY_ID['custom.0']

    def test_a_command_on_each_file_and_what_it_made(self):
        self.target('copy "{file}" "{folder}\\{stem} copy.{ext}"', exts='txt')
        job = self.run_op('custom.0', self.s['txt'])
        self.assertEqual([p.name for p in job.outputs], ['notes copy.txt'])
        self.assertEqual(job.outputs[0].read_text(encoding='utf-8'), self.s['txt'].read_text(encoding='utf-8'))

    def test_once_for_all_of_them(self):
        self.target('type {files} > "{folder}\\together.txt"', each=False)
        job = self.run_op('custom.0', self.s['txt'], self.s['srt'])
        text = (self.folder / 'together.txt').read_text(encoding='utf-8', errors='replace')
        self.assertIn('second paragraph', text)
        self.assertIn('Hello there', text)
        self.assertEqual([p.name for p in job.outputs], ['together.txt'])

    def test_only_for_its_kinds_and_in_its_menu(self):
        op = self.target('echo', exts='png, jpeg', mode='actions')
        self.assertTrue(op.takes(self.s['png']))
        self.assertTrue(op.takes(self.s['jpg']), 'jpeg is jpg')
        self.assertFalse(op.takes(self.s['txt']))
        actions = [o for _, ops in catalog.ops_for('actions', [self.s['png']]) for o in ops]
        tools = [o for _, ops in catalog.ops_for('tools', [self.s['png']]) for o in ops]
        self.assertIn(op, actions)
        self.assertNotIn(op, tools)

    def test_a_failing_command_says_so(self):
        op = self.target('echo it went wrong 1>&2 & exit 3')
        job = Job(op, [self.s['txt']], [])
        error = job.execute()
        self.assertIn('Mine stopped with code 3', error)
        self.assertIn('it went wrong', error)
        self.assertEqual(job.outputs, [])

    def test_the_file_goes_on_the_end_without_placeholders(self):
        from dropshift.engines import custom
        self.assertEqual(custom.command_line('notepad', [self.s['txt']]), f'notepad "{self.s["txt"]}"')

    def test_kept_in_settings(self):
        from dropshift.settings import Settings
        settings = Settings()
        before = settings.custom
        try:
            settings.custom = [{'name': 'Mine', 'command': 'echo', 'mode': 'tools', 'exts': '', 'each': True}]
            self.assertEqual(Settings().custom[0]['name'], 'Mine')
        finally:
            settings.custom = before


class Office(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.docx = cls.folder / 'notes.docx'
        cls.xlsx = cls.folder / 'people.xlsx'
        cls.pptx = cls.folder / 'deck.pptx'

    @unittest.skipUnless(office.available('Word'), 'Word not installed')
    def test_word(self):
        docx = self.run_op('convert.text.docx', self.s['txt']).outputs[0]
        for target in ('pdf', 'odt', 'rtf', 'txt', 'html'):
            with self.subTest(target=target):
                self.run_op(f'convert.document.{target}', docx)
        rtf = self.folder / 'notes.rtf'
        self.run_op('convert.document.docx', rtf)
        text = self.run_op('convert.document.txt', docx).outputs[0]
        self.assertIn('second paragraph', text.read_text(encoding='utf-8-sig'))
        pdf = self.run_op('convert.document.pdf', docx).outputs[0]
        self.run_op('convert.pdf.docx', pdf)
        words = self.run_op('convert.pdf.txt', pdf).outputs[0]
        self.assertIn('Dropshift test document', words.read_text(encoding='utf-8'))

    @unittest.skipUnless(office.available('Excel'), 'Excel not installed')
    def test_excel(self):
        xlsx = self.run_op('convert.sheet.xlsx', self.s['csv']).outputs[0]
        for target in ('pdf', 'csv', 'ods', 'xls'):
            with self.subTest(target=target):
                self.run_op(f'convert.sheet.{target}', xlsx)
        back = self.run_op('convert.sheet.csv', xlsx).outputs[0]
        self.assertIn('Zürich', back.read_text(encoding='utf-8-sig'))

    @unittest.skipUnless(office.available('PowerPoint'), 'PowerPoint not installed')
    def test_powerpoint(self):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        was_running = win32.process_running('POWERPNT.EXE')
        app = win32com.client.DispatchEx('PowerPoint.Application')
        deck = app.Presentations.Add(WithWindow=False)
        slide = deck.Slides.Add(1, 1)
        slide.Shapes(1).TextFrame.TextRange.Text = 'Dropshift'
        deck.Slides.Add(2, 2)
        deck.SaveAs(str(self.pptx), 24)
        deck.Close()
        if not was_running and app.Presentations.Count == 0:
            app.Quit()
        pythoncom.CoUninitialize()
        for target in ('pdf', 'odp', 'ppt'):
            with self.subTest(target=target):
                self.run_op(f'convert.slides.{target}', self.pptx)
        self.run_op('convert.slides.pptx', self.folder / 'deck.odp')
        slides = self.run_op('convert.slides.png', self.pptx).outputs[0]
        self.assertEqual(len(list(slides.glob('*.png'))), 2)
        self.assertEqual(Image.open(next(slides.glob('*.png'))).size[0], 1920)


class Metadata(Base):
    def rows(self, path: Path) -> dict[str, dict[str, str]]:
        from dropshift.engines import metadata
        return {title: dict(rows) for title, rows in metadata.describe(path)}

    def test_photo_camera_and_location(self):
        from PIL.TiffImagePlugin import IFDRational
        photo = self.folder / 'located.jpg'
        exif = Image.Exif()
        exif[0x010F], exif[0x0110] = 'TestCam', 'Model 7'
        gps = exif.get_ifd(0x8825)
        gps[1], gps[2] = 'N', (IFDRational(47), IFDRational(22), IFDRational(30))
        gps[3], gps[4] = 'E', (IFDRational(8), IFDRational(32), IFDRational(0))
        Image.new('RGB', (64, 48)).save(photo, exif=exif)
        info = self.rows(photo)
        self.assertEqual(info['Picture']['Dimensions'], '64 × 48 pixels')
        self.assertEqual(info['Camera']['Camera make'], 'TestCam')
        self.assertEqual(info['Camera']['Location'], '47.375000, 8.533333')
        from dropshift.engines import metadata
        self.assertEqual(metadata.location_of(metadata.describe(photo)), (47.375, 8.533333))

    def test_photo_without_exif_says_so(self):
        self.assertIn('none', self.rows(self.s['png'])['Camera']['EXIF'])

    @unittest.skipUnless(locate.ffmpeg(), 'FFmpeg not found')
    def test_video(self):
        info = self.rows(self.s['mp4'])
        self.assertEqual(info['Media']['Duration'], '0:02.00')
        self.assertEqual(info['Video']['Resolution'], '320 × 240')
        self.assertEqual(info['Video']['Frame rate'], '24 fps')
        self.assertIn('AAC', info['Audio']['Codec'])

    def test_pdf(self):
        info = self.rows(self.s['pdf'])
        self.assertEqual(info['Document']['Pages'], '3')
        self.assertIn('mm', info['Document']['Page size'])

    def test_office_properties_without_office(self):
        docx = self.folder / 'props.docx'
        core = ('<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/">'
                '<dc:creator>Ada Lovelace</dc:creator><dc:title>Notes</dc:title>'
                '<dcterms:created>2026-01-02T03:04:05Z</dcterms:created></cp:coreProperties>')
        app = ('<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
               '<Application>Microsoft Office Word</Application><Words>42</Words></Properties>')
        with zipfile.ZipFile(docx, 'w') as archive:
            archive.writestr('docProps/core.xml', core)
            archive.writestr('docProps/app.xml', app)
        info = self.rows(docx)
        self.assertEqual(info['Properties']['Author'], 'Ada Lovelace')
        self.assertEqual(info['Properties']['Created'], '2026-01-02 03:04:05')
        self.assertEqual(info['Statistics']['Words'], '42')

    def test_folder_csv_json_archive(self):
        self.assertEqual(self.rows(self.s['folder'])['Contents']['Files'], '2')
        self.assertEqual(self.rows(self.s['csv'])['Table']['Rows'], '2 plus a header')
        self.assertEqual(self.rows(self.s['json'])['JSON']['Top level'], 'an object with 1 key')
        self.assertEqual(self.rows(self.s['zip'])['Archive']['Files'], '2')

    def test_every_sample_reads_cleanly(self):
        from dropshift.engines import metadata
        for name, path in self.s.items():
            with self.subTest(sample=name):
                text = metadata.as_text(path, metadata.describe(path))
                self.assertNotIn('Could not read', text)

    def test_menu_offers_it_for_anything(self):
        for paths in ([self.s['folder']], [self.s['png'], self.s['mp4']]):
            labels = [op.label for _, ops in catalog.ops_for('tools', paths) for op in ops]
            self.assertIn('Show metadata', labels)


@unittest.skipUnless(locate.ffmpeg(), 'FFmpeg not found')
class MediaEditing(Base):
    def export(self, source: str = 'mp4', **settings) -> tuple[Path, dict]:
        from dropshift.engines import media
        edit = media.MediaEdit(**settings)
        op = catalog.Op(id='tools.edit.export', label='Export', mode='tools', kinds=frozenset({'video', 'audio'}),
                        run=lambda job: media.edit_media(job, job.files[0], edit))
        job = Job(op, [self.s[source]], [])
        self.assertEqual(job.execute(), '', settings)
        out = job.outputs[0]
        return out, self.probe(out)

    def video(self, info: dict) -> dict:
        return next(s for s in info['streams'] if s['codec_type'] == 'video')

    def test_trim_is_exact(self):
        out, info = self.export(start=0.5, end=1.5)
        self.assertTrue(out.name.startswith('clip (edited)'), out.name)
        self.assertAlmostEqual(float(info['format']['duration']), 1.0, delta=0.08)

    def test_fast_trim_copies_the_streams(self):
        from dropshift.engines import media
        args = media.edit_args(self.s['mp4'], media.MediaEdit(start=0.5, fast=True), Path('x.mp4'))
        self.assertIn('copy', args)
        self.assertNotIn('-vf', args)
        out, info = self.export(start=0.5, end=1.8, fast=True, target='webm')
        self.assertEqual(out.suffix, '.mp4')       # fast keeps the original format
        self.assertEqual(self.video(info)['codec_name'], 'h264')

    def test_crop_rotate_and_speed(self):
        _, info = self.export(crop=(0.0, 0.0, 0.5, 0.5), rotate=90, speed=2.0)
        video = self.video(info)
        self.assertEqual((video['width'], video['height']), (120, 160))
        self.assertAlmostEqual(float(info['format']['duration']), 1.0, delta=0.1)
        self.assertIn('audio', [s['codec_type'] for s in info['streams']])

    def test_formats_and_size(self):
        _, gif = self.export(target='gif', end=1.0)
        self.assertEqual(gif['format']['format_name'], 'gif')
        _, webm = self.export(target='webm', height=120, quality='small', end=1.0)
        video = self.video(webm)
        self.assertEqual((video['codec_name'], video['width'], video['height']), ('vp9', 160, 120))

    def test_remove_sound_and_flip(self):
        _, info = self.export(mute=True, flip=True, end=1.0)
        self.assertEqual([s['codec_type'] for s in info['streams']], ['video'])

    def test_slow_motion_chains_atempo(self):
        from dropshift.engines import media
        self.assertEqual(media._atempo(0.25), ['atempo=0.5', 'atempo=0.5'])
        _, info = self.export(speed=0.5, end=1.0)
        self.assertAlmostEqual(float(info['format']['duration']), 2.0, delta=0.15)

    def test_compress_video(self):
        job = self.run_op('tools.compress', self.s['mp4'])
        self.assertTrue(job.outputs or job.notes)

    def test_audio_trim_fade_volume_and_formats(self):
        for target in ('mp3', 'm4a', 'wav', 'flac', 'ogg', 'opus', 'aiff'):
            with self.subTest(target=target):
                out, info = self.export('wav', target=target, start=0.5, end=1.5, fade_in=0.2,
                                        fade_out=0.3, volume=0.5, quality='small')
                self.assertEqual(out.suffix, f'.{target}')
                self.assertAlmostEqual(float(info['format']['duration']), 1.0, delta=0.1)
        _, loud = self.export('mp3', target='m4a', normalize=True)
        self.assertEqual(int(loud['streams'][0]['sample_rate']), 22050)   # back at its own rate

    def test_sound_from_a_video_and_fades_on_pictures(self):
        _, sound = self.export(target='mp3', end=1.0)
        self.assertEqual([s['codec_type'] for s in sound['streams']], ['audio'])
        _, faded = self.export(fade_in=0.5, fade_out=0.5)
        self.assertEqual(sorted(s['codec_type'] for s in faded['streams']), ['audio', 'video'])

    def test_gif_in_and_audio_cannot_become_video(self):
        _, gif = self.export('gif', target='mp4', speed=2.0, crop=(0.25, 0.25, 0.5, 0.5))
        self.assertEqual(self.video(gif)['codec_name'], 'h264')
        from dropshift.engines import media
        edit = media.MediaEdit(target='mp4')
        op = catalog.Op(id='x', label='Export', mode='tools', kinds=frozenset({'audio'}),
                        run=lambda job: media.edit_media(job, job.files[0], edit))
        self.assertIn('no picture', Job(op, [self.s['wav']], []).execute())

    def test_fast_trim_of_audio(self):
        out, info = self.export('mp3', start=0.2, fast=True, target='wav')
        self.assertEqual((out.suffix, info['streams'][0]['codec_name']), ('.mp3', 'mp3'))

    def test_one_edit_tile_named_for_the_files(self):
        op = catalog.BY_ID['tools.edit']
        self.assertEqual(op.label_for([self.s['mp4']]), 'Edit video')
        self.assertEqual(op.label_for([self.s['wav']]), 'Edit audio')
        self.assertEqual(op.label_for([self.s['gif']]), 'Edit GIF')
        self.assertEqual(op.label_for([self.s['mp4'], self.s['wav']]), 'Edit media')
        for name in ('mp4', 'wav', 'mp3', 'gif'):
            self.assertTrue(catalog.usable(op, [self.s[name]]), name)
        self.assertFalse(catalog.usable(op, [self.s['png']]))

    def test_audio_editor_window(self):
        import time as clock
        from dropshift.ui.media_editor import MediaEditor
        exported = []
        editor = MediaEditor(self.s['mp3'], lambda path, edit: exported.append(edit))
        self.assertFalse(editor.has_video)
        self.assertEqual([f for _, f in editor.formats][:2], ['mp3', 'm4a'])
        end = clock.monotonic() + 10
        while not editor._sound.wave and clock.monotonic() < end:
            APP.processEvents()
            clock.sleep(0.02)
        self.assertIsNotNone(editor._sound.wave)
        editor._timeline.set_range(200, 800)
        editor._volume.setCurrentIndex(6)          # even out
        editor._fade_out.setCurrentIndex(1)
        self.assertFalse(editor._fast.isEnabled())
        editor._export_now()
        edit = exported[-1]
        self.assertEqual((edit.target, edit.start, edit.end, edit.normalize, edit.fade_out),
                         ('mp3', 0.2, 0.8, True, 0.5))
        editor.close()
        APP.processEvents()

    def test_editor_window_builds_the_edit(self):
        import time as clock
        from dropshift.ui.media_editor import CROPS, MediaEditor
        exported = []
        editor = MediaEditor(self.s['mp4'], lambda path, edit: exported.append(edit))
        end = clock.monotonic() + 8
        while editor._native.isEmpty() and clock.monotonic() < end:
            APP.processEvents()
            clock.sleep(0.02)
        self.assertEqual((editor._native.width(), editor._native.height()), (320, 240))
        editor._timeline.set_range(500, 1500)
        editor._crop_choice.setCurrentIndex([name for name, _ in CROPS].index('1:1'))
        editor._turn(-90)
        editor._mute.setChecked(True)
        self.assertFalse(editor._fast.isEnabled())       # cropping rules out a fast trim
        editor._export_now()
        edit = exported[-1]
        self.assertEqual((edit.start, edit.end, edit.rotate, edit.mute), (0.5, 1.5, 270, True))
        self.assertAlmostEqual(edit.crop[2] * 320, edit.crop[3] * 240, delta=1)   # square
        editor.close()
        APP.processEvents()
        moved = self.s['mp4'].rename(self.s['mp4'].with_name('clip moved.mp4'))   # not held open
        moved.rename(self.s['mp4'])


class Coverage(unittest.TestCase):
    def test_every_op_is_exercised_somewhere(self):
        source = Path(__file__).read_text(encoding='utf-8')
        untested = []
        for op in catalog.OPS:
            prefix, _, rest = op.id.rpartition('.')
            if op.id in source or op.gui:
                continue
            # Ops built in loops are named like f'convert.image.{target}'.
            if op.mode == 'convert' and f"'{op.target}'" in source and prefix in source:
                continue
            untested.append(op.id)
        self.assertEqual(untested, [])



class MenuTests(unittest.TestCase):
    def test_many_files_are_quick_to_offer_choices_for(self):
        # The menu comes up mid-drag, the program the drag came from waiting on it: 400 files took
        # 1.5 s (each file's kind asked of the disk again for each choice), and 25-50 ms each repaint
        # while a choice was hovered (catalog.split, which was quadratic too).
        import time
        folder = Path(tempfile.mkdtemp(prefix='dropshift-menu-'))
        paths = []
        for i in range(400):
            path = folder / f'f{i}.{["png", "mp4", "pdf", "docx", "zip", "txt"][i % 6]}'
            path.write_bytes(b'x')
            paths.append(path)
        catalog.fresh()
        started = time.perf_counter()
        for mode in catalog.MODES:
            groups = catalog.ops_for(mode, paths)
            for _, ops in groups:
                for op in ops:
                    catalog.split(op, paths)
        self.assertLess(time.perf_counter() - started, 0.5)
        self.assertEqual(catalog.kind_of(folder), 'folder')
        self.assertEqual(catalog.kind_of(paths[0]), 'image')


if __name__ == '__main__':
    unittest.main()
