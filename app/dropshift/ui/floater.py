"""Float: a small always-on-top shelf holding files to drag somewhere later.

Drag a row out to drop that file somewhere, or "Drag all" for every file;
drop more files onto the shelf to add them, and drag its header to move it.
Files that get moved away are taken off the shelf.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileInfo, QMimeData, QPoint, QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QDrag, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QApplication, QFileIconProvider, QWidget

from . import icons, theme

SHADOW = 0 if theme.glass_on() else 14       # on glass, Windows draws the shadow
WIDTH = 250
HEADER_H = 34
ROW_H = 34
MAX_ROWS = 6
HEADER, ALL, NOWHERE = -1, -3, -2


class FloatShelf(QWidget):
    closed = Signal(object)

    def __init__(self, paths: list[Path]):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
                         | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        theme.frost(self)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.paths: list[Path] = []
        self._press: QPoint | None = None
        self._press_row = NOWHERE
        self._hover = NOWHERE
        self._icons = QFileIconProvider()
        self.add(paths)

    # ------------------------------------------------------------------ contents

    def add(self, paths: list[Path]) -> None:
        known = {str(p).lower() for p in self.paths}
        self.paths += [p for p in paths if str(p).lower() not in known]
        rows = min(len(self.paths), MAX_ROWS) + (1 if len(self.paths) > MAX_ROWS else 0)
        self.resize(WIDTH + 2 * SHADOW, HEADER_H + rows * ROW_H + 8 + 2 * SHADOW)
        self.update()

    def _prune(self) -> None:
        self.paths = [p for p in self.paths if p.exists()]
        if not self.paths:
            self.close_shelf()
        else:
            self.add([])

    def close_shelf(self) -> None:
        self.hide()
        self.closed.emit(self)
        self.deleteLater()

    @property
    def _card(self) -> QRectF:
        return QRectF(SHADOW, SHADOW, self.width() - 2 * SHADOW, self.height() - 2 * SHADOW)

    def _close_rect(self) -> QRectF:
        return QRectF(self._card.right() - 30, self._card.top() + 6, 22, 22)

    def _all_rect(self) -> QRectF:
        return QRectF(self._card.right() - 100, self._card.top() + 7, 64, 20)

    def _row_at(self, pos) -> int:
        """A row index, ALL for the Drag all pill, HEADER, or NOWHERE."""
        card = self._card
        if not card.contains(pos):
            return NOWHERE
        if len(self.paths) > 1 and self._all_rect().contains(pos):
            return ALL
        if pos.y() < card.top() + HEADER_H:
            return HEADER
        row = int((pos.y() - card.top() - HEADER_H) // ROW_H)
        return row if row < min(len(self.paths), MAX_ROWS) else NOWHERE

    # ------------------------------------------------------------------ mouse

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press = event.position().toPoint()
            self._press_row = self._row_at(event.position())

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self._press is None:
            row = self._row_at(pos)
            if row != self._hover:
                self._hover = row
                self.update()
            return
        if (pos.toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        if self._press_row in (HEADER, NOWHERE):
            self.move(event.globalPosition().toPoint() - self._press)
            return
        paths = self.paths if self._press_row == ALL else [self.paths[self._press_row]]
        self._press = None
        self._drag(paths)

    def mouseReleaseEvent(self, event):
        if self._press is not None and self._close_rect().contains(event.position())                 and self._close_rect().contains(QPointF(self._press)):
            self.close_shelf()
        self._press = None

    def leaveEvent(self, event):
        self._hover = NOWHERE
        self.update()

    def _drag(self, paths: list[Path]) -> None:
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self._icons.icon(QFileInfo(str(paths[0]))).pixmap(32, 32))
        drag.exec(Qt.CopyAction | Qt.MoveAction | Qt.LinkAction, Qt.CopyAction)
        self._prune()

    # ------------------------------------------------------------------ drops onto the shelf

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and event.source() is not self:
            event.setDropAction(Qt.CopyAction)
            event.accept()

    def dragMoveEvent(self, event):
        event.setDropAction(Qt.CopyAction)
        event.accept()

    def dropEvent(self, event):
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        event.setDropAction(Qt.CopyAction)
        event.accept()
        self.add(paths)

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

        p.drawPixmap(int(card.left() + 10), int(card.top() + 9), icons.glyph('pin', c.dim, 16))
        p.setFont(theme.font(8.5, QFont.DemiBold))
        p.setPen(c.dim)
        count = len(self.paths)
        p.drawText(QRectF(card.left() + 32, card.top(), card.width() - 70, HEADER_H),
                   Qt.AlignVCenter, f'Float · {count} item{"s" if count != 1 else ""}')
        if count > 1:
            pill = self._all_rect()
            p.setPen(Qt.NoPen)
            p.setBrush(c.hover if self._hover == ALL else c.chip)
            p.drawRoundedRect(pill, 7, 7)
            p.setFont(theme.font(8, QFont.DemiBold))
            p.setPen(c.hover_text if self._hover == ALL else c.text)
            p.drawText(pill, Qt.AlignCenter, 'Drag all')
        p.drawPixmap(self._close_rect().adjusted(4, 4, -4, -4).toRect(),
                     icons.glyph('close_x', c.text if self._hover == HEADER else c.dim, 14, 2))

        p.setFont(theme.font(9))
        metrics = QFontMetrics(p.font())
        for index, path in enumerate(self.paths[:MAX_ROWS]):
            row = QRectF(card.left() + 4, card.top() + HEADER_H + index * ROW_H, card.width() - 8, ROW_H)
            if index == self._hover:
                p.setPen(Qt.NoPen)
                p.setBrush(c.chip)
                p.drawRoundedRect(row, 8, 8)
            icon = self._icons.icon(QFileInfo(str(path))).pixmap(20, 20)
            p.drawPixmap(int(row.left() + 8), int(row.center().y() - 10), icon)
            p.setPen(c.text)
            text_w = int(row.width() - 44)
            p.drawText(QRectF(row.left() + 36, row.top(), text_w, ROW_H), Qt.AlignVCenter,
                       metrics.elidedText(path.name or str(path), Qt.ElideMiddle, text_w))
        if count > MAX_ROWS:
            p.setPen(c.dim)
            p.drawText(QRectF(card.left() + 40, card.top() + HEADER_H + MAX_ROWS * ROW_H, 200, ROW_H),
                       Qt.AlignVCenter, f'and {count - MAX_ROWS} more')
        p.end()
