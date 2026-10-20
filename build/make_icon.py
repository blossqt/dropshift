"""Render Dropshift's icon into the .ico the packaged exe carries.

    python build/make_icon.py

It comes from the same drawing the running app uses for its window, tray and
toasts (dropshift.ui.icons.render_app_icon), so the icon in the Start menu, on
the taskbar and in the tray is one icon.
"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtWidgets import QApplication     # noqa: E402

HERE = Path(__file__).resolve().parent
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def png_bytes(pixmap) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not pixmap.save(buffer, 'PNG'):
        raise RuntimeError(f'could not encode the {pixmap.width()}px icon')
    data = bytes(buffer.data())
    buffer.close()
    return data


def write_ico(render, out: Path) -> None:
    # Each size drawn at that size, rather than one large image shrunk: the
    # small ones stay crisp.
    images = [(size, png_bytes(render(size))) for size in ICO_SIZES]
    header = struct.pack('<HHH', 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b'', b''
    for size, data in images:
        entries += struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    out.write_bytes(header + entries + blobs)
    print(f'wrote {out} ({out.stat().st_size} bytes, {len(images)} sizes)')


def main():
    app = QApplication(sys.argv)
    from dropshift.ui.icons import render_app_icon
    write_ico(render_app_icon, HERE / 'dropshift.ico')
    app.quit()


if __name__ == '__main__':
    main()
