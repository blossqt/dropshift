"""The menu that appears mid-drag, for dropping files onto what they should become.

It opens with its header under the cursor, and is at first all but invisible:
only once Windows hands it the drag (DragEnter) does it know there are files,
which ones, and so what to offer - and only then does it fade in. A drag that
turns out not to carry files (text being selected, a window being moved)
never shows it at all.

Every drop is accepted as a copy. Explorer treats Shift as "move" mid-drag,
and a drop reported as a move would make it delete the original.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QFont, QFontMetrics, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from . import catalog, win32
from .catalog import KIND_TITLES, MODE_TITLES, MODES, Op
from .ui import icons, theme

SHADOW = 18
CARD_W = 360
PAD = 12
HEADER_H = 56
TABS_H = 34
FOOTER_H = 32
GROUP_H = 24
CHIP_H = 38
CHIP_COLS = 4
TILE_H = 66
TILE_COLS = 3
GAP = 6
RADIUS = 14

PROBE_OPACITY = 0.02    # see-through but still a drop target
PROBE_TIMEOUT = 450     # ms to wait for Windows to hand over a drag
FAR_AWAY = 110          # px beyond the card at which the menu gives up
RELEASE_GRACE = 300     # ms after the button comes up before closing anyway

DEV = os.environ.get('DROPSHIFT_DEV') == '1'


def _dev(message: str) -> None:
    if DEV:
        print(f'[menu {time.monotonic():.3f}] {message}', flush=True)

_PLURALS = {'image': 'images', 'svg': 'SVG drawings', 'video': 'videos', 'audio': 'audio files',
            'pdf': 'PDFs', 'document': 'documents', 'text': 'text files', 'sheet': 'spreadsheets',
            'slides': 'slide decks', 'data': 'JSON files', 'subtitles': 'subtitle files',
            'archive': 'archives', 'folder': 'folders', 'other': 'other files'}


@dataclass
class _Item:
    rect: QRectF
    kind: str           # 'tab' or 'op'
    op: Op | None = None
    mode: str = ''


def local_paths(mime) -> list[Path]:
    if not mime or not mime.hasUrls():
        return []
    return [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile() and url.toLocalFile()]


def human_size(size: int) -> str:
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:.0f} {unit}' if unit == 'bytes' else f'{size:.1f} {unit}'
        size /= 1024
    return ''


def describe(paths: list[Path]) -> tuple[str, str, str]:
    """(title, subtitle, glyph) for the header."""
    if len(paths) == 1:
        path = paths[0]
        kind = catalog.kind_of(path)
        if kind == 'folder':
            return path.name or str(path), 'Folder', 'folder'
        try:
            size = human_size(path.stat().st_size)
        except OSError:
            size = ''
        what = 'file' if kind == 'other' else KIND_TITLES[kind].lower()
        ext = catalog.ext_of(path).upper()
        subtitle = ' · '.join(part for part in (f'{ext} {what}'.strip(), size) if part)
        return path.name, subtitle, kind
    counts: dict[str, int] = {}
    for path in paths:
        kind = catalog.kind_of(path)
        counts[kind] = counts.get(kind, 0) + 1
    parts = [f'{n} {_PLURALS[k] if n > 1 else KIND_TITLES[k].lower()}' for k, n in counts.items()]
    noun = 'items' if 'folder' in counts else 'files'
    glyph = next(iter(counts)) if len(counts) == 1 else 'files'
    return f'{len(paths)} {noun}', ', '.join(parts), glyph


class DropMenu(QWidget):
    chosen = Signal(object, list)   # Op, [Path]

    def __init__(self, keys: dict[str, str]):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.keys = keys
        self._mode = 'convert'
        self._paths: list[Path] = []
        self._known = False
        self._items: list[_Item] = []
        self._labels: list[tuple[QRectF, str]] = []
        self._empty = ''
        self._hover: _Item | None = None
        self._anchor = QPoint()
        self._card = QRectF()
        self._opening = 0       # counts openings, so a stale timer can't close a newer menu

        self._fade = QVariantAnimation(self)
        self._fade.setDuration(120)
        self._fade.valueChanged.connect(lambda v: self.setWindowOpacity(float(v)))
        self._probe = QTimer(self)
        self._probe.setSingleShot(True)
        self._probe.timeout.connect(self._probe_expired)

    # ------------------------------------------------------------------ opening and closing

    def open(self, mode: str, pos: QPoint) -> None:
        if self.isVisible():
            self.set_mode(mode)
            return
        self._mode, self._paths, self._known = mode, [], False
        self._hover, self._anchor = None, QPoint(pos)
        self._opening += 1
        self._fade.stop()
        self.setWindowOpacity(PROBE_OPACITY)
        self._relayout()
        self._place(pos)
        self.show()
        self.raise_()
        _dev(f'opened ({mode}) at {pos.x()},{pos.y()}')
        # Give Windows' drag loop a reason to look under the cursor again.
        QTimer.singleShot(40, self._nudge)
        self._probe.start(PROBE_TIMEOUT)

    def dismiss(self) -> None:
        self._probe.stop()
        self._fade.stop()
        self.hide()
        self._paths, self._known, self._hover = [], False, None

    def set_mode(self, mode: str) -> None:
        if mode != self._mode:
            self._mode, self._hover = mode, None
            self._relayout()

    def track(self, pos: QPoint) -> None:
        """The cursor moved mid-drag; heading well away from the menu closes it."""
        if not self.isVisible() or not self._known:
            return
        card = self._card.translated(QPointF(self.pos()))
        if not card.adjusted(-FAR_AWAY, -FAR_AWAY, FAR_AWAY, FAR_AWAY).contains(QPointF(pos)):
            self.dismiss()

    def released(self) -> None:
        if self.isVisible():
            opening = self._opening
            QTimer.singleShot(RELEASE_GRACE, lambda: self._after_release(opening))

    def _after_release(self, opening: int) -> None:
        if self.isVisible() and opening == self._opening:
            self.dismiss()

    def _nudge(self) -> None:
        if self.isVisible() and not self._known:
            _dev('nudging the cursor')
            win32.nudge_cursor()

    def _probe_expired(self) -> None:
        if not self._known:
            _dev('no drag arrived; closing')
            self.dismiss()

    # ------------------------------------------------------------------ layout

    def _relayout(self) -> None:
        self._items, self._labels, self._empty = [], [], ''
        x0, y = SHADOW, SHADOW
        inner = CARD_W - 2 * PAD
        y += HEADER_H
        tab_w = inner / len(MODES)
        for i, mode in enumerate(MODES):
            rect = QRectF(x0 + PAD + i * tab_w, y, tab_w, TABS_H - 6)
            self._items.append(_Item(rect, 'tab', mode=mode))
        y += TABS_H + PAD

        groups = catalog.ops_for(self._mode, self._paths) if self._known else []
        if self._known and not groups:
            self._empty = self._empty_text()
            y += 40
        for title, ops in groups:
            if title:
                self._labels.append((QRectF(x0 + PAD, y, inner, GROUP_H), title))
                y += GROUP_H
            cols = CHIP_COLS if self._mode == 'convert' else TILE_COLS
            height = CHIP_H if self._mode == 'convert' else TILE_H
            width = (inner - GAP * (cols - 1)) / cols
            for index, op in enumerate(ops):
                row, col = divmod(index, cols)
                rect = QRectF(x0 + PAD + col * (width + GAP), y + row * (height + GAP), width, height)
                self._items.append(_Item(rect, 'op', op=op))
            rows = (len(ops) + cols - 1) // cols
            y += rows * height + (rows - 1) * GAP + PAD
        y += FOOTER_H - PAD / 2
        self._card = QRectF(SHADOW, SHADOW, CARD_W, y - SHADOW)

        top_left = self.pos()
        self.resize(CARD_W + 2 * SHADOW, round(y + SHADOW))
        if self.isVisible():
            self._keep_on_screen(top_left)
        self.update()

    def _empty_text(self) -> str:
        others = [f'{win32.KEY_LABELS[self.keys.get(m, "off")]} for {MODE_TITLES[m].lower()}'
                  for m in MODES if m != self._mode and self.keys.get(m, 'off') != 'off']
        what = {'convert': 'Nothing to convert these to', 'tools': 'No tools for these',
                'actions': 'No actions for these'}[self._mode]
        return what + (' - ' + ', '.join(others) if others else '')

    def _place(self, pos: QPoint) -> None:
        # The header's icon goes under the cursor, so the drop targets are a
        # short move down from where the drag already is.
        top_left = pos - QPoint(SHADOW + PAD + 18, SHADOW + HEADER_H // 2)
        self._keep_on_screen(top_left)

    def _keep_on_screen(self, top_left: QPoint) -> None:
        screen = QGuiApplication.screenAt(self._anchor) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = min(max(top_left.x(), area.left() - SHADOW), area.right() - self.width() + SHADOW)
        y = min(max(top_left.y(), area.top() - SHADOW), area.bottom() - self.height() + SHADOW)
        self.move(x, y)

    def screen_point_of(self, op_id: str) -> tuple[int, int] | None:
        """For the drag test: the middle of a choice, in physical screen pixels."""
        item = next((i for i in self._items if i.op and i.op.id == op_id), None)
        if not item or not self.isVisible() or not self._known:
            return None
        import ctypes
        from ctypes import wintypes
        ratio = self.devicePixelRatioF()
        point = wintypes.POINT(round(item.rect.center().x() * ratio), round(item.rect.center().y() * ratio))
        ctypes.windll.user32.ClientToScreen(int(self.winId()), ctypes.byref(point))
        return point.x, point.y

    # ------------------------------------------------------------------ drag and drop

    def _item_at(self, pos: QPointF) -> _Item | None:
        return next((i for i in self._items if i.rect.contains(pos)), None)

    def _hover_at(self, pos: QPointF) -> _Item | None:
        item = self._item_at(pos)
        if item and item.kind == 'tab' and item.mode != self._mode:
            self.set_mode(item.mode)
            item = self._item_at(pos)
        if item is not self._hover:
            self._hover = item
            self.update()
        return item

    def dragEnterEvent(self, event):
        paths = local_paths(event.mimeData())
        _dev(f'drag entered: {[p.name for p in paths]}, actions {int(event.possibleActions().value)}')
        if not paths or not (event.possibleActions() & Qt.CopyAction):
            event.ignore()
            if not self._known:
                QTimer.singleShot(0, self.dismiss)
            return
        if not self._known or paths != self._paths:
            self._paths, self._known = paths, True
            self._probe.stop()
            self._relayout()
            self._fade.stop()
            self._fade.setStartValue(self.windowOpacity())
            self._fade.setEndValue(1.0)
            self._fade.start()
        event.setDropAction(Qt.CopyAction)
        event.accept()
        self._hover_at(event.position())

    def dragMoveEvent(self, event):
        item = self._hover_at(event.position())
        if item and item.kind == 'op':
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        if self._hover:
            self._hover = None
            self.update()

    def dropEvent(self, event):
        item = self._item_at(event.position())
        if not item or item.kind != 'op':
            event.ignore()
            return
        event.setDropAction(Qt.CopyAction)
        event.accept()
        op, paths = item.op, list(self._paths)
        _dev(f'dropped on {op.id}')
        # After the drop returns: the app it came from waits on this call.
        QTimer.singleShot(0, self.dismiss)
        QTimer.singleShot(0, lambda: self.chosen.emit(op, paths))

    # ------------------------------------------------------------------ painting

    def paintEvent(self, event):
        c = theme.colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        theme.paint_shadow(p, self._card, RADIUS, SHADOW - 4, c.shadow)
        p.setPen(c.border)
        p.setBrush(c.card)
        p.drawRoundedRect(self._card.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)

        self._paint_header(p, c)
        for item in self._items:
            if item.kind == 'tab':
                self._paint_tab(p, c, item)
        p.setFont(theme.font(8, QFont.DemiBold))
        p.setPen(c.dim)
        for rect, title in self._labels:
            p.drawText(rect.adjusted(2, 0, 0, -4), Qt.AlignLeft | Qt.AlignBottom, title.upper())
        for item in self._items:
            if item.kind == 'op':
                if self._mode == 'convert':
                    self._paint_chip(p, c, item)
                else:
                    self._paint_tile(p, c, item)
        if self._empty:
            p.setFont(theme.font(9))
            p.setPen(c.dim)
            rect = QRectF(self._card.left() + PAD, SHADOW + HEADER_H + TABS_H + PAD,
                          CARD_W - 2 * PAD, 40)
            p.drawText(rect, Qt.AlignCenter | Qt.TextWordWrap, self._empty)
        self._paint_footer(p, c)
        p.end()

    def _paint_header(self, p: QPainter, c) -> None:
        title, subtitle, glyph = describe(self._paths) if self._paths else ('', '', 'files')
        badge = QRectF(self._card.left() + PAD, SHADOW + (HEADER_H - 36) / 2 + 2, 36, 36)
        p.setPen(Qt.NoPen)
        p.setBrush(c.chip)
        p.drawRoundedRect(badge, 9, 9)
        p.drawPixmap(badge.adjusted(7, 7, -7, -7).toRect(), icons.glyph(glyph, c.text, 22))
        left = badge.right() + 10
        width = self._card.right() - PAD - left
        p.setFont(theme.font(10, QFont.DemiBold))
        p.setPen(c.text)
        metrics = QFontMetrics(p.font())
        p.drawText(QRectF(left, badge.top() - 1, width, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   metrics.elidedText(title, Qt.ElideMiddle, int(width)))
        p.setFont(theme.font(8.5))
        p.setPen(c.dim)
        metrics = QFontMetrics(p.font())
        p.drawText(QRectF(left, badge.top() + 18, width, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   metrics.elidedText(subtitle, Qt.ElideRight, int(width)))

    def _paint_tab(self, p: QPainter, c, item: _Item) -> None:
        on = item.mode == self._mode
        if on:
            p.setPen(Qt.NoPen)
            p.setBrush(c.tab_on)
            p.drawRoundedRect(item.rect, 8, 8)
        name = MODE_TITLES[item.mode]
        key = win32.KEY_LABELS.get(self.keys.get(item.mode, 'off'), '')
        name_font = theme.font(9, QFont.DemiBold if on else QFont.Normal)
        key_font = theme.font(7.5)
        name_w = QFontMetrics(name_font).horizontalAdvance(name)
        key_w = QFontMetrics(key_font).horizontalAdvance(key) + 10 if key else 0
        x = item.rect.center().x() - (name_w + (key_w + 5 if key else 0)) / 2
        p.setFont(name_font)
        p.setPen(c.text if on else c.dim)
        p.drawText(QRectF(x, item.rect.top(), name_w + 2, item.rect.height()), Qt.AlignVCenter, name)
        if key:
            pill = QRectF(x + name_w + 5, item.rect.center().y() - 8, key_w, 16)
            p.setPen(c.border)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(pill.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
            p.setFont(key_font)
            p.setPen(c.dim)
            p.drawText(pill, Qt.AlignCenter, key)

    def _paint_chip(self, p: QPainter, c, item: _Item) -> None:
        hot = item is self._hover
        p.setPen(Qt.NoPen)
        p.setBrush(c.hover if hot else c.chip)
        p.drawRoundedRect(item.rect, 9, 9)
        p.setFont(theme.font(9.5, QFont.DemiBold))
        p.setPen(c.hover_text if hot else c.chip_text)
        p.drawText(item.rect, Qt.AlignCenter, item.op.label)

    def _paint_tile(self, p: QPainter, c, item: _Item) -> None:
        hot = item is self._hover
        p.setPen(Qt.NoPen)
        p.setBrush(c.hover if hot else c.chip)
        p.drawRoundedRect(item.rect, 10, 10)
        ink = c.hover_text if hot else c.chip_text
        r = item.rect
        icon = QRectF(r.center().x() - 11, r.top() + 11, 22, 22)
        p.drawPixmap(icon.toRect(), icons.glyph(item.op.icon, ink, 22))
        p.setFont(theme.font(8.5))
        p.setPen(ink)
        metrics = QFontMetrics(p.font())
        p.drawText(QRectF(r.left() + 4, r.top() + 38, r.width() - 8, 20), Qt.AlignCenter,
                   metrics.elidedText(item.op.label_for(self._paths), Qt.ElideRight, int(r.width() - 8)))

    def _paint_footer(self, p: QPainter, c) -> None:
        rect = QRectF(self._card.left() + PAD, self._card.bottom() - FOOTER_H, CARD_W - 2 * PAD,
                      FOOTER_H - 4)
        if self._hover and self._hover.op:
            text = self._hover.op.hint
            taken, skipped = catalog.split(self._hover.op, self._paths)
            if skipped and taken:
                text += f'  ({len(skipped)} skipped)'
        else:
            text = 'Drop on a choice · Esc to cancel'
        p.setFont(theme.font(8.5))
        p.setPen(c.dim)
        metrics = QFontMetrics(p.font())
        p.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter,
                   metrics.elidedText(text, Qt.ElideRight, int(rect.width())))
