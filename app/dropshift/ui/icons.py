"""The app icon, and the small line glyphs on the menu's tiles."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF

from . import suiteicons

_SHAPES = suiteicons.SHAPES      # the suite's one set of line icons


def glyph(name: str, color: QColor | str, size: int = 20, width: float = suiteicons.STROKE) -> QPixmap:
    """One of the suite's line icons as a pixmap (a plain file's, for a name it hasn't), sharp on
    high-DPI screens."""
    return suiteicons.pixmap(name if name in _SHAPES else 'other', color, size, width)


def glyph_icon(name: str, color: QColor | str, size: int = 20) -> QIcon:
    return QIcon(glyph(name, color, size))


def clear_cache() -> None:
    suiteicons.clear_cache()


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
