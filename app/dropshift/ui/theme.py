"""Colours for Dropshift's own painted windows, following Windows' light or dark mode.

The highlight is the suite's blossom pink, ACCENT - the colour of every suite
app's icon tile - rather than the Windows accent, which on this kind of
machine follows the wallpaper and could be any colour at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QProxyStyle, QStyle

ACCENT = '#ffd1dc'         # the suite's blossom pink: buttons, ticks, highlights
ACCENT_HOVER = '#ffe0e8'
ON_ACCENT = '#a61e4d'      # text and ticks drawn on ACCENT, which is too pale for white


class SuiteStyle(QProxyStyle):
    """Windows' own style, with check boxes drawn in the suite's pink.

    Windows 11's style loses a ticked box's fill once a style sheet is in
    play, leaving a bare tick that looks cut off. Drawing the box here keeps
    it whole, in any mode, whatever the accent colour of the day.
    """

    SIZE = 18

    def pixelMetric(self, metric, option=None, widget=None):
        if metric in (QStyle.PM_IndicatorWidth, QStyle.PM_IndicatorHeight):
            return self.SIZE
        return super().pixelMetric(metric, option, widget)

    def drawPrimitive(self, element, option, painter, widget=None):
        if element != QStyle.PE_IndicatorCheckBox:
            super().drawPrimitive(element, option, painter, widget)
            return
        c = colors()
        state = option.state
        ticked = bool(state & QStyle.State_On)
        partly = bool(state & QStyle.State_NoChange)
        side = min(option.rect.width(), option.rect.height(), self.SIZE) - 1
        box = QRectF(0, 0, side, side)
        box.moveCenter(QRectF(option.rect).center())
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if not state & QStyle.State_Enabled:
            painter.setOpacity(0.45)
        if ticked or partly:
            painter.setPen(QPen(c.edge, 1))
            painter.setBrush(QColor(ACCENT))
        else:
            hover = state & QStyle.State_MouseOver and state & QStyle.State_Enabled
            painter.setPen(QPen(c.edge if hover else c.dim, 1))
            painter.setBrush(c.chip)
        painter.drawRoundedRect(box, side * 0.22, side * 0.22)
        pen = QPen(QColor(ON_ACCENT), max(1.6, side * 0.12), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        x, y = box.x(), box.y()
        if ticked:
            tick = QPainterPath(QPointF(x + side * 0.26, y + side * 0.52))
            tick.lineTo(x + side * 0.43, y + side * 0.69)
            tick.lineTo(x + side * 0.75, y + side * 0.33)
            painter.drawPath(tick)
        elif partly:
            painter.drawLine(QPointF(x + side * 0.28, y + side / 2), QPointF(x + side * 0.72, y + side / 2))
        painter.restore()


_style = None


def install(app) -> None:
    """Give the app the suite's style. Kept here so Python never frees it under Qt."""
    global _style
    _style = SuiteStyle()
    app.setStyle(_style)


@dataclass(frozen=True)
class Colors:
    card: QColor
    border: QColor
    text: QColor
    dim: QColor
    chip: QColor
    chip_text: QColor
    hover: QColor
    hover_text: QColor
    tab_on: QColor
    shadow: QColor
    track: QColor
    error: QColor
    edge: QColor        # ACCENT as an outline: deeper on white, where the pale pink would vanish


def is_dark() -> bool:
    hints = QGuiApplication.styleHints()
    return hints.colorScheme() == Qt.ColorScheme.Dark


def colors() -> Colors:
    if is_dark():
        return Colors(card=QColor('#202127'), border=QColor(255, 255, 255, 30), text=QColor('#ececf1'),
                      dim=QColor('#9a9aa6'), chip=QColor('#2c2d35'), chip_text=QColor('#ececf1'),
                      hover=QColor(ACCENT), hover_text=QColor(ON_ACCENT), tab_on=QColor('#34353f'),
                      shadow=QColor(0, 0, 0, 120), track=QColor('#34353f'), error=QColor('#ff8a80'),
                      edge=QColor(ACCENT))
    return Colors(card=QColor('#ffffff'), border=QColor(0, 0, 0, 28), text=QColor('#1d1d22'),
                  dim=QColor('#6e6e7a'), chip=QColor('#f1f2f6'), chip_text=QColor('#1d1d22'),
                  hover=QColor(ACCENT), hover_text=QColor(ON_ACCENT), tab_on=QColor('#e6e8f0'),
                  shadow=QColor(20, 24, 40, 70), track=QColor('#e6e8f0'), error=QColor('#c62828'),
                  edge=QColor('#ee9fb5'))


def ink() -> str:
    """The pink as text or a line on the window: pale on dark, deep rose on white."""
    return ACCENT if is_dark() else ON_ACCENT


def font(point_size: float, weight: QFont.Weight = QFont.Normal) -> QFont:
    f = QFont('Segoe UI Variable Text')
    if not f.exactMatch():
        f = QFont('Segoe UI')
    f.setPointSizeF(point_size)
    f.setWeight(weight)
    return f


def paint_shadow(painter, rect, radius: float, depth: int, color: QColor) -> None:
    """A soft shadow, as rings of fading rounded rectangles around rect."""
    from PySide6.QtCore import QRectF
    painter.save()
    painter.setPen(Qt.NoPen)
    for i in range(depth, 0, -1):
        shade = QColor(color)
        shade.setAlphaF(color.alphaF() * (1 - i / depth) ** 2 / depth * 2.2)
        painter.setBrush(shade)
        grown = QRectF(rect).adjusted(-i, -i + 2, i, i + 2)
        painter.drawRoundedRect(grown, radius + i, radius + i)
    painter.restore()
