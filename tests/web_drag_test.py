"""A real drag out of a web page in Edge onto Dropshift, as a person would do it.

    %LOCALAPPDATA%\\DropshiftBuild\\venv\\Scripts\\python.exe tests\\web_drag_test.py [--exe dist\\Dropshift\\Dropshift.exe]

Serves a page of its own on 127.0.0.1 - a picture and a link - and opens it in
a new InPrivate Edge window with a profile of its own, so nothing of yours is
touched.
Then it starts Dropshift and, with genuine input, drags:

  1. the picture, with Shift, onto Download;
  2. the picture, with Shift, onto PNG;
  3. the link, with Alt, onto Download.

Each passes if its file turns up in Downloads, where Dropshift saves what comes
from the web. The files are named for this run, and the test deletes them
afterwards.

It takes over the mouse and keyboard for about fifteen seconds; run it only
when nobody is using the computer. Safety: the button only goes down over the
test's own Edge window, and a drop is only let go over Dropshift - anything
else is cancelled with Esc first. The cursor is put back where it was.
"""

from __future__ import annotations

import ctypes
import io
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Before anything reads or sets a screen position: two monitors at different
# scalings would otherwise put every coordinate somewhere else.
ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

import win32gui  # noqa: E402
import win32process  # noqa: E402
from PIL import Image  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from drag_test import INPUT, KEYBDINPUT, MOUSEINPUT, ask, cursor, left, owner, user32, wait_for_app  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EDGE = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe'
DOWNLOADS = Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'Downloads'
TOKEN = secrets.token_hex(3)
TITLE = f'Dropshift web drag test {TOKEN}'
PICTURE = f'dropshift-web-test-{TOKEN}.jpg'
VK = {'shift': (0xA0, 0x2A), 'alt': (0xA4, 0x38), 'escape': (0x1B, 0x01)}

_jpeg = io.BytesIO()
Image.new('RGB', (320, 240), (255, 209, 220)).save(_jpeg, 'JPEG')
JPEG = _jpeg.getvalue()

PAGE = f'''<!doctype html><html><head><title>{TITLE}</title></head>
<body style="margin:0; font-family:Segoe UI">
<img src="/{PICTURE}" style="position:absolute; left:40px; top:40px; width:320px; height:240px">
<a href="/article-{TOKEN}" style="position:absolute; left:40px; top:320px; font-size:32px">Article {TOKEN}</a>
</body></html>'''.encode()


class Site(BaseHTTPRequestHandler):
    def do_GET(self):
        pages = {'/': (PAGE, 'text/html; charset=utf-8'), f'/{PICTURE}': (JPEG, 'image/jpeg'),
                 f'/article-{TOKEN}': (f'<html><title>Article {TOKEN}</title>Hello</html>'.encode(), 'text/html')}
        body, kind = pages.get(self.path, (b'', 'text/plain'))
        self.send_response(200 if body else 404)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def press(name: str, down: bool) -> None:
    vk, scan = VK[name]
    item = INPUT(type=1)
    item.u.ki = KEYBDINPUT(vk, scan, 0 if down else 0x0002, 0, 0)
    if user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT)) != 1:
        raise OSError('SendInput failed')


def slide(to: tuple[int, int], steps: int = 12, pause: float = 0.02) -> None:
    """Move the mouse there as a hand would: real movement, which a browser's own drag
    detection sees (it can miss a cursor merely set somewhere)."""
    vx, vy, vw, vh = (user32.GetSystemMetrics(n) for n in (76, 77, 78, 79))
    x0, y0 = cursor()
    for n in range(1, steps + 1):
        x, y = x0 + (to[0] - x0) * n / steps, y0 + (to[1] - y0) * n / steps
        item = INPUT(type=0)
        item.u.mi = MOUSEINPUT(round((x - vx) * 65535 / (vw - 1)), round((y - vy) * 65535 / (vh - 1)), 0,
                               0x0001 | 0x8000 | 0x4000, 0, 0)       # move, absolute, whole virtual desktop
        user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT))
        time.sleep(pause)


def top_title(point: tuple[int, int]) -> str:
    return win32gui.GetWindowText(win32gui.GetAncestor(win32gui.WindowFromPoint(point), 2))


def edge_window(timeout: float = 30) -> int:
    """The test page's Edge window, once its title says the page has loaded."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        found = []
        win32gui.EnumWindows(lambda hwnd, _: found.append(hwnd) if win32gui.IsWindowVisible(hwnd)
                             and TITLE in win32gui.GetWindowText(hwnd) else None, None)
        if found:
            return found[0]
        time.sleep(0.3)
    raise SystemExit('the test page did not open in Edge')


def page_origin(window: int) -> tuple[int, int, float]:
    """Where the page itself starts on screen, and its CSS pixel's size in screen pixels."""
    views = []
    win32gui.EnumChildWindows(window, lambda hwnd, _: views.append(hwnd)
                              if win32gui.GetClassName(hwnd) == 'Chrome_RenderWidgetHostHWND' else None, None)
    if not views:
        raise SystemExit("could not find the page inside Edge's window")
    left_edge, top_edge, _, _ = win32gui.GetWindowRect(views[0])
    return left_edge, top_edge, user32.GetDpiForWindow(views[0]) / 96


def drag(start: tuple[int, int], trigger: str, choice: str, app_pid: int) -> str:
    """One drag from the page onto a choice in Dropshift's menu. Returns what happened."""
    slide(start)
    time.sleep(0.4)
    if TITLE not in top_title(start):
        return f'not started: under the cursor is {top_title(start)!r}, not the test page'
    left(True)
    try:
        slide((start[0] + 30, start[1] + 14), steps=8)      # past the drag threshold: the browser's drag begins
        time.sleep(0.3)
        press(trigger, True)
        time.sleep(0.9)                                      # the menu opens, catches the drag and fades in
        reply = ask(f'where {choice}')
        if not reply or reply == 'none':
            press('escape', True)
            press('escape', False)
            return f'cancelled: the menu had no {choice} ({reply!r})'
        x, y = (int(v) for v in reply.split())
        slide((x, y))
        time.sleep(0.3)
        if owner(cursor()) != app_pid:
            press('escape', True)
            press('escape', False)
            return f'cancelled: under the cursor is {top_title(cursor())!r}, not Dropshift'
        return 'dropped'
    finally:
        left(False)
        time.sleep(0.05)
        press(trigger, False)
        time.sleep(1.2)


def wait_for(pick, timeout: float = 20) -> Path | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        found = pick()
        if found:
            return found
        time.sleep(0.3)
    return None


def main() -> int:
    if user32.GetSystemMetrics(23):
        raise SystemExit('mouse buttons are swapped; this test assumes they are not')
    home = cursor()
    started = time.time()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    profile = Path(tempfile.mkdtemp(prefix='dropshift-edge-'))
    # InPrivate as well as a profile of its own: a fresh profile signs in with
    # the Windows account and covers the page with a "now syncing" dialog.
    edge = subprocess.Popen([str(EDGE), f'--user-data-dir={profile}', '--inprivate', '--no-first-run',
                             '--no-default-browser-check', '--window-position=120,120', '--window-size=900,700',
                             f'http://127.0.0.1:{server.server_address[1]}/'])
    exe = sys.argv[sys.argv.index('--exe') + 1] if '--exe' in sys.argv else ''
    app = subprocess.Popen([os.path.abspath(exe)] if exe else [sys.executable, str(ROOT / 'app' / 'main.py')],
                           env=dict(os.environ, DROPSHIFT_DEV='1'))
    made: list[Path] = []
    try:
        wait_for_app()
        app_pid = int(ask('pid'))
        window = edge_window()
        win32gui.ShowWindow(window, 9)                       # restored, in case Edge opened it minimised
        try:
            win32gui.SetForegroundWindow(window)
        except Exception:  # noqa: BLE001 - the first click on the page brings it forward anyway
            pass
        time.sleep(1.5)
        x0, y0, scale = page_origin(window)
        picture = (x0 + round(200 * scale), y0 + round(160 * scale))
        link = (x0 + round(80 * scale), y0 + round(342 * scale))

        def new_file(test) -> Path | None:
            for path in DOWNLOADS.glob(f'*{TOKEN}*'):
                if path.stat().st_mtime >= started - 1 and test(path) and path not in made:
                    return path
            return None

        checks = {}
        outcome = drag(picture, 'shift', 'web.download', app_pid)
        print('1. picture, Shift, Download:', outcome)
        saved = wait_for(lambda: new_file(lambda p: p.name == PICTURE))
        made += [saved] if saved else []
        checks['the picture was downloaded as it is'] = bool(saved) and saved.read_bytes() == JPEG

        outcome = drag(picture, 'shift', 'convert.image.png', app_pid)
        print('2. picture, Shift, PNG:', outcome)
        png = wait_for(lambda: new_file(lambda p: p.suffix == '.png'))
        made += [png] if png else []
        checks['the picture was downloaded and made a PNG'] = bool(png) and Image.open(png).format == 'PNG'

        outcome = drag(link, 'alt', 'web.download', app_pid)
        print('3. link, Alt, Download:', outcome)
        page = wait_for(lambda: new_file(lambda p: p.suffix == '.html'))
        made += [page] if page else []
        checks['the link was saved as its page'] = bool(page) and b'Hello' in page.read_bytes()

        for path in made:
            print('   made', path.name)
        for name, ok in checks.items():
            print(('PASS ' if ok else 'FAIL ') + name)
        return 0 if all(checks.values()) else 1
    finally:
        for name in ('shift', 'alt'):
            press(name, False)
        user32.SetCursorPos(*home)
        for path in made:
            path.unlink(missing_ok=True)
        try:
            ask('quit')
        except OSError:
            pass
        try:
            app.wait(10)
        except subprocess.TimeoutExpired:
            app.kill()
        subprocess.run(['taskkill', '/PID', str(edge.pid), '/T', '/F'], capture_output=True,
                       creationflags=0x08000000)
        server.shutdown()
        server.server_close()
        time.sleep(1)
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
