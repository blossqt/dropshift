r"""The media editor and metadata windows, opened inside a running Dropshift.

    %LOCALAPPDATA%\DropshiftBuild\venv\Scripts\python.exe tests\window_test.py [--exe dist\Dropshift\Dropshift.exe]

No mouse or keyboard is touched: the app is asked (through its development
pipe) to act as if a video, a song and a GIF had been dropped on Edit, and the
video on Show metadata, then asked what it has open. Mainly a check of a packaged build,
whose video player depends on plugins PyInstaller has to have collected.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
sys.path.insert(0, str(ROOT / 'tests'))

from drag_test import ask, wait_for_app  # noqa: E402

from dropshift import locate  # noqa: E402


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix='dropshift-windows-'))
    sample = work / 'window test.mp4'
    song, gif = work / 'window test.mp3', work / 'window test.gif'
    for args in (['-f', 'lavfi', '-i', 'testsrc=duration=3:size=320x240:rate=24', '-pix_fmt', 'yuv420p', sample],
                 ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=3', song],
                 ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=120x90:rate=10', gif]):
        subprocess.run([str(locate.ffmpeg()), '-hide_banner', '-loglevel', 'error', *map(str, args)],
                       check=True, creationflags=0x08000000)
    exe = sys.argv[sys.argv.index('--exe') + 1] if '--exe' in sys.argv else ''
    command = [os.path.abspath(exe)] if exe else [sys.executable, str(ROOT / 'app' / 'main.py')]
    app = subprocess.Popen(command, env=dict(os.environ, DROPSHIFT_DEV='1'))
    try:
        wait_for_app()
        for path in (sample, song, gif):
            ask(f'perform tools.edit {path}')
        ask(f'perform tools.info {sample}')
        wanted = ('MediaEditor 320x240 frame', 'MediaEditor audio loaded wave', 'MediaEditor 120x90 frame',
                  'InfoWindow')
        windows, end = '', time.monotonic() + 25
        while not all(w in windows for w in wanted) and time.monotonic() < end:
            time.sleep(0.5)
            windows = ask('windows')
        print('windows:', windows)
        checks = {'the editor opened the video and shows it': wanted[0] in windows,
                  'the editor opened the song, with its waveform': wanted[1] in windows,
                  'the editor opened the GIF and shows it': wanted[2] in windows,
                  'the metadata window opened': wanted[3] in windows}
        for name, ok in checks.items():
            print(('PASS ' if ok else 'FAIL ') + name)
        return 0 if all(checks.values()) else 1
    finally:
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
