"""Things dragged out of a web page (web.py): what the menu makes of them, and the download.

    %LOCALAPPDATA%\\DropshiftBuild\\venv\\Scripts\\python.exe -m unittest tests.test_web -v

The drags are built as Chrome and Edge build theirs - the address, the page's
HTML, the name the browser would save it under and the page it came from - and
the downloads come from a web server of the test's own on 127.0.0.1.
"""

from __future__ import annotations

import base64
import io
import struct
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PIL import Image  # noqa: E402
from PySide6.QtCore import QMimeData, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from dropshift import catalog, web  # noqa: E402
from dropshift.jobs import Job  # noqa: E402
from dropshift.menu import describe  # noqa: E402

APP = QApplication.instance() or QApplication([])


def picture(fmt: str) -> bytes:
    out = io.BytesIO()
    Image.new('RGB', (40, 30), (255, 209, 220)).save(out, fmt)
    return out.getvalue()


PNG, JPEG = picture('PNG'), picture('JPEG')


def file_group(*names: str) -> bytes:
    """FILEGROUPDESCRIPTORW, as Chrome puts it on a drag: a count, then 592 bytes per file."""
    out = struct.pack('<I', len(names))
    for name in names:
        descriptor = bytearray(592)
        encoded = name.encode('utf-16-le')
        descriptor[72:72 + len(encoded)] = encoded
        out += bytes(descriptor)
    return out


def browser_drag(url: str, name: str = '', page: str = '', html: str = '') -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl(url)])
    if html:
        mime.setHtml(html)
    if name:
        mime.setData('application/x-qt-windows-mime;value="FileGroupDescriptorW"', file_group(name))
    if page:
        mime.setData('application/x-qt-windows-mime;value="HTML Format"',
                     f'Version:0.9\r\nStartHTML:0\r\nSourceURL:{page}\r\n<html>{html}</html>'.encode())
    return mime


class Site(BaseHTTPRequestHandler):
    """/a.jpg and /media/abc are pictures; /article is a page. Remembers each request's Referer."""

    seen: list[tuple[str, str]] = []

    def do_GET(self):
        Site.seen.append((self.path, self.headers.get('Referer', '')))
        if self.path == '/checked':                     # as Cloudflare answers a program
            body = b'<html><title>Just a moment...</title></html>'
            self.send_response(403)
            self.send_header('Content-Type', 'text/html; charset=UTF-8')
            self.send_header('Server', 'cloudflare')
            self.send_header('cf-mitigated', 'challenge')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body, kind = {'/a.jpg': (JPEG, 'image/jpeg'), '/a_large.jpg': (PNG, 'image/png'), '/media/abc?format=jpg': (PNG, 'image/png'),
                      '/article': (b'<html><body>Hello</body></html>', 'text/html; charset=utf-8')}.get(
            self.path, (b'', 'text/plain'))
        self.send_response(200 if body else 404)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class WebDragTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Site)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.out = Path(tempfile.mkdtemp(prefix='dropshift-web-'))
        Site.seen.clear()

    # -------------------------------------------------------------- the drag

    def test_a_picture_from_chrome(self):
        url = 'https://pbs.example.com/media/Gx7?format=jpg&name=large'
        paths = web.from_mime(browser_drag(url, 'Gx7.jpg', 'https://x.example.com/post/1', f'<img src="{url}">'))
        self.assertEqual(len(paths), 1)
        path = paths[0]
        self.assertEqual(path.name, 'Gx7.jpg')
        self.assertTrue(web.dragged(paths))
        self.assertFalse(web.is_link(path))
        self.assertEqual(web.source(path).page, 'https://x.example.com/post/1')
        self.assertEqual(describe(paths), ('Gx7.jpg', 'JPG image from pbs.example.com', 'image'))

    def test_a_picture_whose_address_does_not_say(self):
        url = 'https://cdn.example.com/media/Gx7?format=webp'
        paths = web.from_mime(browser_drag(url, html=f'<img src="{url}">'))
        self.assertEqual(paths[0].name, 'Gx7.jpg')          # put right once the server answers
        self.assertEqual(catalog.kind_of(paths[0]), 'image')

    def test_a_link(self):
        paths = web.from_mime(browser_drag('https://www.youtube.com/watch?v=abc', 'A video - YouTube.url'))
        self.assertTrue(web.is_link(paths[0]))
        self.assertEqual(describe(paths), ('A video - YouTube', 'Link · youtube.com', 'link'))

    def test_an_address_dragged_as_text(self):
        mime = QMimeData()
        mime.setText('https://example.com/report.pdf')
        paths = web.from_mime(mime)
        self.assertEqual(paths[0].name, 'report.pdf')
        self.assertEqual(catalog.kind_of(paths[0]), 'pdf')

    def test_selected_words_are_not_a_web_file(self):
        mime = QMimeData()
        mime.setText('some words picked out of a page')
        self.assertEqual(web.from_mime(mime), [])

    def test_a_file_from_explorer_is_not_a_web_file(self):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.out / 'photo.png'))])
        self.assertEqual(web.from_mime(mime), [])

    # -------------------------------------------------------------- the menu

    def test_the_menus_for_a_picture(self):
        paths = web.from_mime(browser_drag('https://example.com/photo.jpg'))
        convert = catalog.ops_for('convert', paths)
        self.assertEqual(convert[0][0], 'From the web')
        self.assertEqual(convert[0][1][0].label, 'Download')
        targets = [op.target for title, ops in convert[1:] for op in ops]
        self.assertIn('png', targets)
        self.assertNotIn('jpg', targets)                     # it is one already
        tools = [op.id for title, ops in catalog.ops_for('tools', paths) for op in ops]
        self.assertIn('tools.compress', tools)
        for needs_a_file in catalog.NEEDS_A_FILE_NOW:
            self.assertNotIn(needs_a_file, tools)
        actions = catalog.ops_for('actions', paths)
        self.assertEqual([title for title, ops in actions], ['From the web'])

    def test_a_link_can_only_be_downloaded(self):
        paths = web.from_mime(browser_drag('https://example.com/story', 'A story.url'))
        for mode in catalog.MODES:
            groups = catalog.ops_for(mode, paths)
            self.assertEqual([title for title, ops in groups], ['From the web'])

    # -------------------------------------------------------------- downloading

    def run_job(self, op, paths):
        job = Job(op, paths, [], str(self.out))
        error = job.execute()
        self.assertEqual(error, '')
        return job

    def test_download_saves_it_as_it_is(self):
        paths = web.from_mime(browser_drag(f'{self.base}/a.jpg', page='https://blog.example.com/p'))
        job = self.run_job(catalog.WEB_OPS['actions'][0], paths)
        self.assertEqual([p.name for p in job.outputs], ['a.jpg'])
        self.assertEqual(job.outputs[0].read_bytes(), JPEG)
        self.assertEqual(Site.seen, [('/a.jpg', 'https://blog.example.com/p')])   # the page as Referer
        self.assertFalse(paths[0].parent.exists())                                  # nothing left behind

    def test_named_for_what_the_server_sent(self):
        # Called .jpg by its address, sent as a PNG: saved as what it is.
        url = f'{self.base}/media/abc?format=jpg'
        paths = web.from_mime(browser_drag(url, html=f'<img src="{url}">'))
        job = self.run_job(catalog.WEB_OPS['convert'][0], paths)
        self.assertEqual([p.name for p in job.outputs], ['abc.png'])

    def test_a_link_is_saved_as_the_page(self):
        paths = web.from_mime(browser_drag(f'{self.base}/article', 'An article.url'))
        job = self.run_job(catalog.WEB_OPS['tools'][0], paths)
        self.assertEqual([p.name for p in job.outputs], ['An article.html'])

    def test_converting_downloads_first(self):
        paths = web.from_mime(browser_drag(f'{self.base}/a.jpg'))
        op = catalog.BY_ID['convert.image.png']
        from dataclasses import replace
        job = self.run_job(replace(op, run=web.after_download(op.run)), paths)
        self.assertEqual([p.name for p in job.outputs], ['a.png'])
        with Image.open(job.outputs[0]) as made:
            self.assertEqual((made.format, made.size), ('PNG', (40, 30)))
        self.assertFalse(paths[0].parent.exists())

    def test_a_picture_inside_the_page_itself(self):
        url = 'data:image/png;base64,' + base64.b64encode(PNG).decode()
        paths = web.from_mime(browser_drag(url))
        self.assertEqual(paths[0].name, 'image.png')
        job = self.run_job(catalog.WEB_OPS['actions'][0], paths)
        self.assertEqual(job.outputs[0].read_bytes(), PNG)

    def test_a_page_behind_a_robot_check(self):
        paths = web.from_mime(browser_drag(f'{self.base}/checked', 'Crystalline Pink Blossom.url'))
        job = Job(catalog.WEB_OPS['actions'][0], paths, [], str(self.out))
        error = job.execute()
        self.assertIn('checks for robots', error)
        self.assertIn('Drag the picture itself instead', error)
        self.assertEqual(list(self.out.iterdir()), [], 'the check is not saved as the page')

    def test_the_full_size_picture(self):
        # As StockCake's page shows .../<id>_large/<name>.jpg: the full one is fetched instead.
        from unittest import mock
        small, full = f'{self.base}/a_large.jpg', f'{self.base}/a.jpg'
        with mock.patch.object(web.suiteweb, 'fuller', lambda url: full if url == small else ''):
            paths = web.from_mime(browser_drag(small, html=f'<img src="{small}">'))
            job = self.run_job(catalog.WEB_OPS['actions'][0], paths)
        self.assertEqual(job.outputs[0].read_bytes(), JPEG)
        self.assertEqual([path for path, _ in Site.seen], ['/a.jpg'])

    def test_the_shown_picture_when_there_is_no_fuller_one(self):
        from unittest import mock
        small = f'{self.base}/a_large.jpg'
        with mock.patch.object(web.suiteweb, 'fuller', lambda url: f'{self.base}/gone.jpg'):
            paths = web.from_mime(browser_drag(small, html=f'<img src="{small}">'))
            job = self.run_job(catalog.WEB_OPS['actions'][0], paths)
        self.assertEqual(job.outputs[0].read_bytes(), PNG)
        self.assertEqual([path for path, _ in Site.seen], ['/gone.jpg', '/a_large.jpg'])


class SuiteWebTests(unittest.TestCase):
    def test_stockcakes_sizes(self):
        base = 'https://images.stockcake.com/public/d/d/f/ddf7aab9-167f-4728-bc3c-46252abde868'
        for size in ('_large', '_medium', '_small'):
            self.assertEqual(web.suiteweb.fuller(f'{base}{size}/crystalline-pink-blossom-stockcake.jpg'),
                             f'{base}/crystalline-pink-blossom-stockcake.jpg')
        self.assertEqual(web.suiteweb.fuller(f'{base}/crystalline-pink-blossom-stockcake.jpg'), '')
        self.assertEqual(web.suiteweb.fuller('https://example.com/a_large/b.jpg'), '')

    def test_robot_checks(self):
        self.assertTrue(web.suiteweb.robot_check(403, {'cf-mitigated': 'challenge', 'server': 'cloudflare'}))
        self.assertTrue(web.suiteweb.robot_check(503, {'server': 'cloudflare'}))
        self.assertFalse(web.suiteweb.robot_check(200, {'server': 'cloudflare'}))
        self.assertFalse(web.suiteweb.robot_check(403, {'server': 'nginx'}))


if __name__ == '__main__':
    unittest.main(verbosity=2)
