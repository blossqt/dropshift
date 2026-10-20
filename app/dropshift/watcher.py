"""Notices a drag anywhere on screen, and a trigger key pressed during it.

Windows has no notification for "a drag has started somewhere", so this polls
the mouse and keyboard: the primary button held down, the cursor moved past the
system's drag threshold, then one of the trigger keys going down. Whether it is
really a drag of files - and not, say, text being selected with Shift held - is
settled by the menu itself: Windows only hands it a drag (DragEnter) if one is
in progress, and the menu stays invisible until then.

A key already held when the button went down is not a trigger: Ctrl held from
the start is Explorer's copy-drag, and that is left alone.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, QTimer, Signal
from PySide6.QtGui import QCursor

from . import win32

IDLE_INTERVAL = 40      # ms between polls while no button is held
DRAG_INTERVAL = 12      # ... and while one is


class DragWatcher(QObject):
    triggered = Signal(str)     # a mode's key went down mid-drag
    moved = Signal(QPoint)      # the cursor, while a drag is under way
    released = Signal()         # the button came up
    escaped = Signal()

    def __init__(self, keys: dict[str, str], parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._down = False
        self._dragging = False
        self._origin = QPoint()
        self._held: dict[int, bool] = {}
        self._escape_held = False
        self._modes: dict[int, str] = {}
        self.set_keys(keys)

    def set_keys(self, keys: dict[str, str]) -> None:
        """keys maps each mode to 'shift', 'ctrl', 'alt' or 'off'."""
        self._modes = {win32.TRIGGER_KEYS[key]: mode for mode, key in keys.items()
                       if key in win32.TRIGGER_KEYS}
        self._held = {vk: win32.key_down(vk) for vk in self._modes}

    def start(self) -> None:
        self._threshold = win32.drag_threshold()
        self._timer.start(IDLE_INTERVAL)

    def stop(self) -> None:
        self._timer.stop()
        self._down = self._dragging = False

    @property
    def dragging(self) -> bool:
        return self._dragging

    def _poll(self) -> None:
        button = win32.key_down(win32.primary_button())
        keys = {vk: win32.key_down(vk) for vk in self._modes}
        escape = win32.key_down(win32.VK_ESCAPE)
        pos = QCursor.pos()

        if button and not self._down:
            self._down, self._dragging, self._origin = True, False, pos
            self._timer.setInterval(DRAG_INTERVAL)
        elif not button and self._down:
            self._down = self._dragging = False
            self._timer.setInterval(IDLE_INTERVAL)
            self.released.emit()
        elif button:
            if not self._dragging:
                travel = (pos - self._origin).manhattanLength()
                self._dragging = travel >= self._threshold
            if self._dragging:
                self.moved.emit(pos)
                if escape and not self._escape_held:
                    self.escaped.emit()
                for vk, down in keys.items():
                    if down and not self._held.get(vk):
                        self.triggered.emit(self._modes[vk])

        self._held = keys
        self._escape_held = escape
