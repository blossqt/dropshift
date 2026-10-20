"""A real Windows drag and drop, end to end, as a person would do it.

    %LOCALAPPDATA%\\DropshiftBuild\\venv\\Scripts\\python.exe tests\\drag_test.py [--exe dist\\Dropshift\\Dropshift.exe]

Starts Dropshift, then drags a sample PNG with genuine input: the left button
goes down on a small test window under the cursor, a shell drag begins (the
same data object Explorer hands out), the cursor moves, Shift is pressed and
held - Dropshift's own watcher has to notice - the cursor moves onto the menu's
JPG choice, and the button comes up. It passes if Windows was told "copy"
(Shift alone would have meant "move"), the original is still there, and a JPG
was made.

It takes over the mouse and Shift key for about three seconds. Safety: the
button only goes down if the cursor is over the test's own window, and the
drop is only let go if the window under the cursor is Dropshift's - anything
else cancels the drag, so the sample can't land anywhere else. The cursor is
put back where it was.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path

import pythoncom
import win32gui
import win32process
from PIL import Image
from win32com.server.util import wrap
from win32com.shell import shell

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
PIPE = rf'\\.\pipe\dropshift-{os.environ.get("USERNAME", "user")}'
S_OK, DRAGDROP_S_DROP, DRAGDROP_S_CANCEL, DRAGDROP_S_USEDEFAULTCURSORS = 0, 0x40100, 0x40101, 0x40102
DROPEFFECT_COPY, DROPEFFECT_MOVE, DROPEFFECT_LINK = 1, 2, 4
MK_LBUTTON = 1
VK_LSHIFT, SHIFT_SCAN = 0xA0, 0x2A
VK_LCONTROL, CONTROL_SCAN = 0xA2, 0x1D

user32 = ctypes.WinDLL('user32', use_last_error=True)
# --shot FILE saves a picture of the menu mid-drag; --exe PATH tests a built Dropshift.exe;
# --editor drags a video with Ctrl onto Edit instead of a PNG with Shift onto JPG.
SHOT = sys.argv[sys.argv.index('--shot') + 1] if '--shot' in sys.argv else ''
EDITOR = '--editor' in sys.argv


# --------------------------------------------------------------------------- input


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('mouseData', wintypes.DWORD),
                ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD), ('dwFlags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class _UNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('pad', ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [('type', wintypes.DWORD), ('u', _UNION)]


def _send(item: INPUT) -> None:
    if user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT)) != 1:
        raise OSError(f'SendInput failed ({ctypes.get_last_error()})')


def mouse(flags: int) -> None:
    item = INPUT(type=0)
    item.u.mi = MOUSEINPUT(0, 0, 0, flags, 0, 0)
    _send(item)


def left(down: bool) -> None:
    mouse(0x0002 if down else 0x0004)


def key(down: bool) -> None:
    """The trigger: Ctrl for the editor run, Shift otherwise."""
    vk, scan = (VK_LCONTROL, CONTROL_SCAN) if EDITOR else (VK_LSHIFT, SHIFT_SCAN)
    item = INPUT(type=1)
    item.u.ki = KEYBDINPUT(vk, scan, 0 if down else 0x0002, 0, 0)
    _send(item)


def cursor() -> tuple[int, int]:
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def glide(to: tuple[int, int], steps: int = 12, pause: float = 0.02) -> None:
    x0, y0 = cursor()
    for n in range(1, steps + 1):
        user32.SetCursorPos(round(x0 + (to[0] - x0) * n / steps), round(y0 + (to[1] - y0) * n / steps))
        time.sleep(pause)


def owner(point: tuple[int, int]) -> int:
    return win32process.GetWindowThreadProcessId(win32gui.WindowFromPoint(point))[1]


# --------------------------------------------------------------------------- Dropshift


def ask(message: str) -> str:
    with open(PIPE, 'r+b', buffering=0) as pipe:
        pipe.write(message.encode('utf-8'))
        time.sleep(0.15)
        try:
            return pipe.read(256).decode('utf-8', 'replace')
        except OSError:
            return ''


def wait_for_app(timeout: float = 20) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            ask('noop')
            return
        except OSError:
            time.sleep(0.2)
    raise SystemExit('Dropshift did not start')


def shell_data_object(path: Path):
    """The IDataObject Explorer itself would give for dragging this file."""
    desktop = shell.SHGetDesktopFolder()
    _, folder_pidl, _ = desktop.ParseDisplayName(0, None, str(path.parent))
    folder = desktop.BindToObject(folder_pidl, None, shell.IID_IShellFolder)
    _, child, _ = folder.ParseDisplayName(0, None, path.name)
    result = folder.GetUIObjectOf(0, [child], pythoncom.IID_IDataObject, 0)
    return result[1] if isinstance(result, tuple) else result


class DropSource:
    _com_interfaces_ = [pythoncom.IID_IDropSource]
    _public_methods_ = ['QueryContinueDrag', 'GiveFeedback']

    def __init__(self, app_pid: int):
        self.app_pid = app_pid
        self.started = time.monotonic()
        self.outcome = ''

    def QueryContinueDrag(self, escape_pressed, key_state):  # noqa: N802 - COM names
        if escape_pressed or time.monotonic() - self.started > 10:
            self.outcome = 'cancelled: escape or timeout'
            return DRAGDROP_S_CANCEL
        if key_state & MK_LBUTTON:
            return S_OK
        if owner(cursor()) != self.app_pid:
            hwnd = win32gui.WindowFromPoint(cursor())
            self.outcome = f'cancelled: under the cursor is {win32gui.GetClassName(hwnd)!r}, not Dropshift'
            return DRAGDROP_S_CANCEL
        self.outcome = 'dropped'
        return DRAGDROP_S_DROP

    def GiveFeedback(self, effect):  # noqa: N802
        return DRAGDROP_S_USEDEFAULTCURSORS


# --------------------------------------------------------------------------- the test


def main() -> int:
    if user32.GetSystemMetrics(23):
        raise SystemExit('mouse buttons are swapped; this test assumes they are not')
    work = Path(tempfile.mkdtemp(prefix='dropshift-drag-'))
    if EDITOR:
        sample = work / 'dragged.mp4'
        from dropshift import locate
        subprocess.run([str(locate.ffmpeg()), '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                        'testsrc=duration=3:size=320x240:rate=24', '-pix_fmt', 'yuv420p', str(sample)],
                       check=True, creationflags=0x08000000)
    else:
        sample = work / 'dragged.png'
        Image.new('RGBA', (200, 120), (59, 91, 219, 200)).save(sample)
    choice = 'tools.edit' if EDITOR else 'convert.image.jpg'
    home = cursor()

    env = dict(os.environ, DROPSHIFT_DEV='1')
    exe = sys.argv[sys.argv.index('--exe') + 1] if '--exe' in sys.argv else ''
    app = subprocess.Popen([os.path.abspath(exe)] if exe else [sys.executable, str(ROOT / 'app' / 'main.py')], env=env)
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QLabel
    qt = QApplication([])
    pad = QLabel('Dropshift\ndrag test')
    pad.setAlignment(Qt.AlignCenter)
    pad.setStyleSheet('background:#3b5bdb; color:white; font:600 10pt "Segoe UI";')
    pad.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
    pad.resize(140, 140)
    ratio = pad.devicePixelRatioF()
    pad.move(round(home[0] / ratio) - 70, round(home[1] / ratio) - 70)
    pad.show()

    stage = {'step': 'starting'}
    finished = threading.Event()
    try:
        wait_for_app()
        for _ in range(20):
            qt.processEvents()
            time.sleep(0.02)
        if owner(cursor()) != os.getpid():
            print('ABORT: the cursor is not over the test window, so no click was sent')
            return 2

        pythoncom.OleInitialize()
        data = shell_data_object(sample)
        # Not app.pid: a venv's python.exe runs the real interpreter as a child.
        source = DropSource(int(ask('pid')))

        def drive():
            try:
                stage['step'] = 'moving off the start point'
                glide((home[0] + 24, home[1] + 6), steps=6)
                time.sleep(0.15)
                stage['step'] = 'pressing the trigger key'
                key(True)
                time.sleep(0.7)        # the menu opens, catches the drag and fades in
                reply = ask(f'where {choice}') if not finished.is_set() else ''
                stage['step'] = f'menu says {choice} is at {reply!r}'
                if reply and reply != 'none':
                    x, y = (int(v) for v in reply.split())
                    glide((x, y))
                    time.sleep(0.25)
                    if SHOT:
                        # Just the menu's own rectangle, for checking how it looks.
                        from PIL import ImageGrab
                        box = tuple(int(v) for v in ask('rect').split())
                        ImageGrab.grab(bbox=box, all_screens=True).save(SHOT)
            finally:
                left(False)
                time.sleep(0.05)
                key(False)

        left(True)
        driver = threading.Thread(target=drive, daemon=True)
        driver.start()
        try:
            effect = pythoncom.DoDragDrop(data, wrap(source, pythoncom.IID_IDropSource),
                                          DROPEFFECT_COPY | DROPEFFECT_MOVE | DROPEFFECT_LINK)
        except pythoncom.com_error as error:
            effect = None
            print('DoDragDrop:', error)
        finished.set()
        driver.join(5)
        print('last step:', stage['step'])
        print('outcome:', source.outcome, '| effect:', effect)

        checks = {
            'dropped on Dropshift': source.outcome == 'dropped',
            'Windows was told "copy", not "move", with the key held': effect == DROPEFFECT_COPY,
            'the original is still there': sample.exists(),
        }
        if EDITOR:
            windows, end = '', time.monotonic() + 15
            while 'frame' not in windows and time.monotonic() < end:
                time.sleep(0.5)
                windows = ask('windows')
            print('windows:', windows)
            checks['the editor opened and is showing the video'] = 'MediaEditor 320x240 frame' in windows
        else:
            out = work / 'dragged.jpg'
            end = time.monotonic() + 10
            while not out.exists() and time.monotonic() < end:
                time.sleep(0.2)
            checks['dragged.jpg was made'] = out.exists() and Image.open(out).format == 'JPEG'
        for name, ok in checks.items():
            print(('PASS ' if ok else 'FAIL ') + name)
        return 0 if all(checks.values()) else 1
    finally:
        key(False)
        user32.SetCursorPos(*home)
        pad.close()
        try:
            ask('quit')
        except OSError:
            pass
        try:
            app.wait(10)
        except subprocess.TimeoutExpired:
            app.kill()


if __name__ == '__main__':
    sys.exit(main())
