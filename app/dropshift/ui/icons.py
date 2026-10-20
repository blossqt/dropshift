"""The app icon, and the small line glyphs on the menu's tiles."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

# 24x24 stroke drawings. '{c}' is filled in with the requested colour.
_STROKE = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
           'stroke="{c}" stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')

_FILE = 'M14 3.5H6.5A1.5 1.5 0 0 0 5 5v14a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 19V8.5z'
_FOLDER = ('M3.5 7.5A1.5 1.5 0 0 1 5 6h4l2 2.5h6.5a1.5 1.5 0 0 1 1.5 1.5v7.5A1.5 1.5 0 0 1 '
           '17.5 19h-13A1.5 1.5 0 0 1 3 17.5z')

_SHAPES = {
    # tools
    'compress': '<path d="M4 14h6v6"/><path d="M20 10h-6V4"/><path d="M14 10l6.5-6.5"/><path d="M3.5 20.5 10 14"/>',
    'shrink': '<path d="M20.5 9V5A1.5 1.5 0 0 0 19 3.5H5A1.5 1.5 0 0 0 3.5 5v14A1.5 1.5 0 0 0 5 20.5h4"/>'
              '<rect x="12" y="12" width="8.5" height="8.5" rx="1.5"/>',
    'rotate_right': '<path d="M20 12a8 8 0 1 1-2.3-5.6"/><path d="M20 4v5h-5"/>',
    'rotate_left': '<path d="M4 12a8 8 0 1 0 2.3-5.6"/><path d="M4 4v5h5"/>',
    'flip': '<path d="M12 3v18"/><path d="M9 7.5 4 17h5z"/><path d="M15 7.5l5 9.5h-5z"/>',
    'grayscale': '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5a8.5 8.5 0 0 1 0 17z" fill="{c}"/>',
    'shield': '<path d="M12 3.5 5 6v5.5c0 4.5 3 7.7 7 9 4-1.3 7-4.5 7-9V6z"/><path d="M9 12l2 2 4-4"/>',
    'combine': '<rect x="4" y="3.5" width="10" height="13" rx="1.5"/><rect x="10" y="7.5" width="10" height="13" rx="1.5"/>',
    'split': '<rect x="3.5" y="4" width="7" height="16" rx="1.5"/><rect x="13.5" y="4" width="7" height="16" rx="1.5"/>',
    'mute': '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/>',
    'zip': f'<path d="{_FILE}"/><path d="M11 3.5v2M13 6.5v2M11 9.5v2M13 12.5v2"/>'
           '<rect x="10.5" y="15.5" width="3" height="3" rx=".8"/>',
    'unzip': '<path d="M4 8.5h16v10a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5z"/>'
             '<path d="M4 8.5 6.5 4h11L20 8.5"/><path d="M12 11.5v5M9.5 14l2.5 2.5 2.5-2.5"/>',
    # actions
    'copy': '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2"/>'
            '<path d="M15.5 8.5V6A1.5 1.5 0 0 0 14 4.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5"/>',
    'link': '<path d="M9.5 14.5 14.5 9.5"/><path d="M11 7l1.5-1.5a4 4 0 0 1 5.7 5.7L16.7 12.7"/>'
            '<path d="M13 17l-1.5 1.5a4 4 0 0 1-5.7-5.7L7.3 11.3"/>',
    'pin': '<path d="M9 3.5h6l-1 6 3.5 3.5h-11L10 9.5z"/><path d="M12 13v7.5"/>',
    'move': f'<path d="{_FOLDER}"/><path d="M8.5 13.5h6.5M12.5 11l2.5 2.5-2.5 2.5"/>',
    'duplicate': f'<path d="{_FOLDER}"/><path d="M11.5 11v5M9 13.5h5"/>',
    'terminal': '<rect x="3" y="4.5" width="18" height="15" rx="2"/><path d="M7 9.5l3 2.5-3 2.5M12.5 15h4.5"/>',
    'open': '<path d="M13.5 4.5h6v6"/><path d="M19.5 4.5 11 13"/>'
            '<path d="M17 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 4 18.5v-10A1.5 1.5 0 0 1 5.5 7H10"/>',
    'info': '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.8v.3"/>',
    'close_x': '<path d="M6 6l12 12M18 6 6 18"/>',
    'tag': '<path d="M3.5 12.2V4.5a1 1 0 0 1 1-1h7.7l8.3 8.3a1.4 1.4 0 0 1 0 2l-6.2 6.2a1.4 1.4 0 0 1-2 0z"/>'
           '<circle cx="8" cy="8" r="1.5"/>',
    'scissors': '<circle cx="6.5" cy="7" r="2.5"/><circle cx="6.5" cy="17" r="2.5"/>'
                '<path d="M8.6 8.4 20 17M8.6 15.6 20 7"/>',
    'play': '<path d="M8 5.5v13l11-6.5z" fill="{c}"/>',
    'pause': '<path d="M9 5.5v13M15 5.5v13" stroke-width="3"/>',
    # kinds of file
    'image': '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.6"/>'
             '<path d="M5 17l4.5-4.5 3 3L16 12l3 3.5"/>',
    'svg': '<path d="M12 3.5 18 10l-3 8.5H9L6 10z"/><circle cx="12" cy="12" r="1.5"/><path d="M12 3.5v7"/>',
    'video': '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 11l5-3v8l-5-3z"/>',
    'audio': '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
    'pdf': f'<path d="{_FILE}"/><path d="M14 3.5V8.5h5"/><path d="M8.5 13h7M8.5 16.5h5"/>',
    'document': f'<path d="{_FILE}"/><path d="M14 3.5V8.5h5"/><path d="M8.5 12.5h7M8.5 15.5h7M8.5 18h4"/>',
    'text': f'<path d="{_FILE}"/><path d="M8.5 9h4M8.5 12.5h7M8.5 16h7"/>',
    'sheet': '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 9.5h16M4 15h16M10 4v16"/>',
    'slides': '<rect x="3.5" y="4.5" width="17" height="11" rx="1.5"/><path d="M12 15.5v4M8.5 19.5h7"/>',
    'data': '<path d="M9 4.5H8a2 2 0 0 0-2 2v3a2.5 2.5 0 0 1-2 2.5 2.5 2.5 0 0 1 2 2.5v3a2 2 0 0 0 2 2h1"/>'
            '<path d="M15 4.5h1a2 2 0 0 1 2 2v3a2.5 2.5 0 0 0 2 2.5 2.5 2.5 0 0 0-2 2.5v3a2 2 0 0 1-2 2h-1"/>',
    'subtitles': '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 15h4M13 15h4M7 11.5h10"/>',
    'archive': '<path d="M4 8.5h16v10a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5z"/>'
               '<path d="M4 8.5 6.5 4h11L20 8.5"/><path d="M10 12.5h4"/>',
    'folder': f'<path d="{_FOLDER}"/>',
    'other': f'<path d="{_FILE}"/><path d="M14 3.5V8.5h5"/>',
    'files': '<path d="M15.5 7.5V5A1.5 1.5 0 0 0 14 3.5H6A1.5 1.5 0 0 0 4.5 5v11A1.5 1.5 0 0 0 6 17.5h2"/>'
             '<rect x="9.5" y="7.5" width="10" height="13" rx="1.5"/>',
}

_cache: dict = {}


def glyph(name: str, color: QColor | str, size: int = 20, width: float = 1.8) -> QPixmap:
    """One of the drawings above as a pixmap, sharp on high-DPI screens."""
    # SVG wants #rrggbb; any transparency goes in as the drawing's opacity.
    color = QColor(color)
    hex_color, alpha = color.name(), color.alphaF()
    ratio = QApplication.primaryScreen().devicePixelRatio() if QApplication.primaryScreen() else 1.0
    key = (name, hex_color, alpha, size, width, ratio)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    body = _SHAPES.get(name, _SHAPES['other']).replace('{c}', hex_color)
    svg = _STROKE.format(c=hex_color, w=width, body=f'<g opacity="{alpha:.3f}">{body}</g>')
    renderer = QSvgRenderer(QByteArray(svg.encode('utf-8')))
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    _cache[key] = pixmap
    return pixmap


def glyph_icon(name: str, color: QColor | str, size: int = 20) -> QIcon:
    return QIcon(glyph(name, color, size))


def clear_cache() -> None:
    _cache.clear()


# The icon follows the suite's shared shape, so it sits beside Homework Hub's,
# Grabbit's, Keytap's and Hanami's as one of a set: a tile in blossom pink
# filling the
# whole square, corners rounded to 42/192 of its width, and one mark in deep
# rose on it - white would vanish on the pale pink.
#
# The colours are constants on purpose: make_icon.py bakes this icon into the
# exe at build time, and a colour taken from the palette there would be
# whatever accent Windows happened to have that day.
APP_ICON_COLOR = '#ffd1dc'
APP_ICON_MARK = '#c2255c'
APP_ICON_CORNER = 42 / 192

# The mark is two arrows passing each other, ⇄ - "this becomes that". In units
# of a 64-wide tile: margin m either side, shaft thickness t, arrowhead
# half-height h and length l, and the two shafts' centre lines y1 and y2.
# Small icons get their own numbers, chosen so the shafts sit on whole pixels
# and the gaps between the arrows stay at least two pixels wide.
_MARK_LARGE = dict(m=13, t=8, h=11, l=13, y1=23, y2=41)
_MARK_MEDIUM = dict(m=12, t=9.333, h=11.333, l=13.333, y1=22.667, y2=41.333)   # 24px: whole 3/8 px
_MARK_SMALL = dict(m=12, t=8, h=10, l=14, y1=20, y2=44)                        # 16-20px


def _mark(size: int) -> dict:
    if size <= 20:
        return _MARK_SMALL
    if size <= 24:
        return _MARK_MEDIUM
    return _MARK_LARGE


def render_app_icon(size: int) -> QPixmap:
    """The app icon at one size, drawn for that size rather than scaled."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(size / 64.0, size / 64.0)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(APP_ICON_COLOR))
    corner = 64 * APP_ICON_CORNER
    painter.drawRoundedRect(QRectF(0, 0, 64, 64), corner, corner)

    k = _mark(size)
    m, t, h, l, y1, y2 = k['m'], k['t'], k['h'], k['l'], k['y1'], k['y2']
    right, left = 64 - m, m
    painter.setBrush(QColor(APP_ICON_MARK))
    # Top arrow, pointing right: a shaft with a rounded tail under its head.
    painter.drawRoundedRect(QRectF(left, y1 - t / 2, right - l - left + 1, t), t / 2, t / 2)
    painter.drawPolygon(QPolygonF([QPointF(right - l, y1 - h), QPointF(right, y1),
                                   QPointF(right - l, y1 + h)]))
    # Bottom arrow, pointing left.
    painter.drawRoundedRect(QRectF(left + l - 1, y2 - t / 2, right - (left + l - 1), t), t / 2, t / 2)
    painter.drawPolygon(QPolygonF([QPointF(left + l, y2 - h), QPointF(left, y2),
                                   QPointF(left + l, y2 + h)]))
    painter.end()
    return pixmap


ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def app_icon() -> QIcon:
    """The window, tray and notification icon - and the one in the exe."""
    result = QIcon()
    for size in ICON_SIZES:
        result.addPixmap(render_app_icon(size))
    return result
