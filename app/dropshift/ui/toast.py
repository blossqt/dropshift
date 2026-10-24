"""Small cards in the corner of the screen showing each job's progress and result."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontMetrics, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from .. import win32
from . import icons, theme

SHADOW = 0 if theme.glass_on() else 14       # on glass, Windows draws the shadow
WIDTH = 330
HEIGHT = 74
SPACING = 8
LINGER = 6000       # ms a finished toast stays up (not counting while hovered)


class Toast(QWidget):
    closed = Signal(object)

    def __init__(self, title: str, detail: str = ''):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        theme.frost(self)
        self.setMouseTracking(True)
        self.resize(WIDTH + 2 * SHADOW, HEIGHT + 2 * SHADOW)
        self.title, self.detail = title, detail
        self.fraction: float | None = 0.0
        self.error = False
        self.outputs: list[Path] = []
        self._hover = ''
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.close_toast)

    @property
    def _card(self) -> QRectF:
        return QRectF(SHADOW, SHADOW, WIDTH, HEIGHT)

    @property
    def _close_rect(self) -> QRectF:
        return QRectF(self._card.right() - 30, self._card.top() + 8, 22, 22)

    @property
    def _show_rect(self) -> QRectF:
        return QRectF(self._card.right() - 112, self._card.bottom() - 30, 100, 22)

    def set_progress(self, fraction: float) -> None:
        self.fraction = fraction
        self.update()

    def finish(self, title: str, detail: str = '', outputs: list[Path] | None = None,
               error: bool = False) -> None:
        self.title, self.detail, self.error = title, detail, error
        self.outputs = [p for p in (outputs or []) if p.exists()]
        self.fraction = None
        self.update()
        self._timer.start(LINGER * (2 if error else 1))

    def close_toast(self) -> None:
        theme.fade_out(self, self._closed)

    def _closed(self) -> None:
        self.hide()
        self.closed.emit(self)
        self.deleteLater()

    # ------------------------------------------------------------------ mouse

    def enterEvent(self, event):
        self._timer.stop()

    def leaveEvent(self, event):
        self._hover = ''
        self.update()
        if self.fraction is None:
            self._timer.start(LINGER // 2)

    def mouseMoveEvent(self, event):
        pos = event.position()
        hover = 'close' if self._close_rect.contains(pos) else (
            'show' if self.outputs and self._show_rect.contains(pos) else '')
        if hover != self._hover:
            self._hover = hover
            self.setCursor(Qt.PointingHandCursor if hover else Qt.ArrowCursor)
            self.update()

    def mouseReleaseEvent(self, event):
        pos = event.position()
        if self._close_rect.contains(pos):
            self.close_toast()
        elif self.outputs and (self._show_rect.contains(pos) or self._card.contains(pos)):
            win32.show_in_explorer(self.outputs[0])
            self.close_toast()

    # ------------------------------------------------------------------ painting

    def paintEvent(self, event):
        c = theme.colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        card = self._card
        if theme.glass_on():
            theme.paint_glass(p, card)
        else:
            theme.paint_shadow(p, card, 12, SHADOW - 2, c.shadow)
            p.setPen(c.border)
            p.setBrush(c.card)
            p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)

        p.drawPixmap(int(card.left() + 14), int(card.top() + 14), icons.render_app_icon(20))
        left, width = card.left() + 44, card.width() - 44 - 40
        p.setFont(theme.font(9.5, QFont.DemiBold))
        p.setPen(c.error if self.error else c.text)
        metrics = QFontMetrics(p.font())
        p.drawText(QRectF(left, card.top() + 10, width, 22), Qt.AlignVCenter,
                   metrics.elidedText(self.title, Qt.ElideMiddle, int(width)))
        p.setFont(theme.font(8.5))
        p.setPen(c.dim)
        metrics = QFontMetrics(p.font())
        detail_w = width + 30 if not self.outputs else width - 80
        p.drawText(QRectF(left, card.top() + 32, detail_w, 18), Qt.AlignVCenter,
                   metrics.elidedText(self.detail, Qt.ElideRight, int(detail_w)))

        if self.fraction is not None:
            track = QRectF(left, card.bottom() - 18, card.width() - 44 - 16, 4)
            p.setPen(Qt.NoPen)
            p.setBrush(c.track)
            p.drawRoundedRect(track, 2, 2)
            p.setBrush(c.hover)
            done = QRectF(track)
            done.setWidth(max(4.0, track.width() * self.fraction))
            p.drawRoundedRect(done, 2, 2)
        elif self.outputs:
            p.setPen(Qt.NoPen)
            p.setBrush(c.hover if self._hover == 'show' else c.chip)
            p.drawRoundedRect(self._show_rect, 7, 7)
            p.setFont(theme.font(8.5, QFont.DemiBold))
            p.setPen(c.hover_text if self._hover == 'show' else c.text)
            p.drawText(self._show_rect, Qt.AlignCenter, 'Show in folder')

        p.drawPixmap(self._close_rect.adjusted(4, 4, -4, -4).toRect(),
                     icons.glyph('close_x', c.text if self._hover == 'close' else c.dim, 14, 2))
        p.end()


class ToastStack:
    """Keeps toasts stacked upwards from the bottom-right corner."""

    def __init__(self):
        self._toasts: list[Toast] = []

    def show(self, title: str, detail: str = '') -> Toast:
        toast = Toast(title, detail)
        toast.closed.connect(self._closed)
        self._toasts.append(toast)
        self._reflow()
        toast.show()
        return toast

    def _closed(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        self._reflow()

    def _reflow(self) -> None:
        area = QGuiApplication.primaryScreen().availableGeometry()
        bottom = area.bottom() + SHADOW - 8
        for toast in reversed(self._toasts):
            toast.move(area.right() - toast.width() + SHADOW - 8, bottom - toast.height())
            bottom -= toast.height() - 2 * SHADOW + SPACING
