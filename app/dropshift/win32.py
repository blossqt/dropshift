"""The few Win32 calls Dropshift needs, made through ctypes."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import winreg
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.WinDLL('user32', use_last_error=True)
shell32 = ctypes.WinDLL('shell32', use_last_error=True)

user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12      # Alt
VK_ESCAPE = 0x1B

SM_SWAPBUTTON = 23
SM_CXDRAG = 68
SM_CYDRAG = 69

# The keys a person can choose as triggers, by the name settings store.
TRIGGER_KEYS = {'shift': VK_SHIFT, 'ctrl': VK_CONTROL, 'alt': VK_MENU}
KEY_LABELS = {'shift': 'Shift', 'ctrl': 'Ctrl', 'alt': 'Alt', 'off': ''}

CREATE_NO_WINDOW = 0x08000000


def key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def primary_button() -> int:
    # GetAsyncKeyState reads the physical buttons, so a left-handed mouse with
    # its buttons swapped drags with what Windows calls the right one.
    return VK_RBUTTON if user32.GetSystemMetrics(SM_SWAPBUTTON) else VK_LBUTTON


def drag_threshold() -> int:
    return max(user32.GetSystemMetrics(SM_CXDRAG), user32.GetSystemMetrics(SM_CYDRAG), 4)


def nudge_cursor() -> None:
    """Move the cursor a pixel and back.

    Windows' drag loop only looks for a new drop target when the mouse moves.
    A window that appears under a still cursor is not seen until then, and a
    drop made before that would land on whatever was underneath it.
    """
    point = wintypes.POINT()
    if user32.GetCursorPos(ctypes.byref(point)):
        user32.SetCursorPos(point.x + 1, point.y)
        user32.SetCursorPos(point.x, point.y)


def run_hidden(args: list, **kwargs) -> subprocess.CompletedProcess:
    """Run a command-line tool without a console window flashing up."""
    kwargs.setdefault('creationflags', CREATE_NO_WINDOW)
    return subprocess.run([str(a) for a in args], **kwargs)


def popen_hidden(args: list, **kwargs) -> subprocess.Popen:
    kwargs.setdefault('creationflags', CREATE_NO_WINDOW)
    return subprocess.Popen([str(a) for a in args], **kwargs)


# --------------------------------------------------------------------------- shell


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.DWORD),
        ('fMask', ctypes.c_ulong),
        ('hwnd', wintypes.HWND),
        ('lpVerb', wintypes.LPCWSTR),
        ('lpFile', wintypes.LPCWSTR),
        ('lpParameters', wintypes.LPCWSTR),
        ('lpDirectory', wintypes.LPCWSTR),
        ('nShow', ctypes.c_int),
        ('hInstApp', wintypes.HINSTANCE),
        ('lpIDList', ctypes.c_void_p),
        ('lpClass', wintypes.LPCWSTR),
        ('hkeyClass', wintypes.HKEY),
        ('dwHotKey', wintypes.DWORD),
        ('hIconOrMonitor', wintypes.HANDLE),
        ('hProcess', wintypes.HANDLE),
    ]


shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
shell32.ShellExecuteExW.restype = wintypes.BOOL

SEE_MASK_INVOKEIDLIST = 0x0000000C
SW_SHOWNORMAL = 1


def shell_verb(verb: str, path: Path) -> bool:
    """Ask Explorer to do `verb` to a file, as its right-click menu would."""
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_INVOKEIDLIST
    info.lpVerb = verb
    info.lpFile = str(path)
    info.nShow = SW_SHOWNORMAL
    return bool(shell32.ShellExecuteExW(ctypes.byref(info)))


def show_in_explorer(path: Path) -> None:
    # One string, not a list: explorer wants /select,"path" with no space,
    # which subprocess's own quoting would not produce.
    subprocess.Popen(f'explorer.exe /select,"{path}"')


def open_with(path: Path) -> None:
    if not shell_verb('openas', path):
        subprocess.Popen(f'rundll32.exe shell32.dll,OpenAs_RunDLL {path}')


def show_properties(path: Path) -> None:
    shell_verb('properties', path)


def open_terminal(folder: Path) -> None:
    wt = shutil.which('wt')
    if wt:
        subprocess.Popen([wt, '-d', str(folder)])
        return
    subprocess.Popen(['powershell.exe', '-NoExit', '-Command',
                      f"Set-Location -LiteralPath '{str(folder).replace(chr(39), chr(39) * 2)}'"],
                     creationflags=subprocess.CREATE_NEW_CONSOLE)


# --------------------------------------------------------------------------- start with Windows

_RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
_RUN_NAME = 'Dropshift'


def launch_command() -> str:
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    main = Path(__file__).resolve().parents[1] / 'main.py'
    return f'"{pythonw}" "{main}"'


def starts_with_windows() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _RUN_NAME)
            return True
    except OSError:
        return False


def set_start_with_windows(enabled: bool) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, _RUN_NAME, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, _RUN_NAME)
            except FileNotFoundError:
                pass


def com_class_registered(prog_id: str) -> bool:
    """Whether an app such as Word.Application can be automated here."""
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id + r'\CLSID'):
            return True
    except OSError:
        return False


def process_running(image_name: str) -> bool:
    result = run_hidden(['tasklist', '/FI', f'IMAGENAME eq {image_name}', '/NH', '/FO', 'CSV'],
                        capture_output=True, text=True)
    return image_name.lower() in (result.stdout or '').lower()


def downloads_folder() -> Path:
    return Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'Downloads'
