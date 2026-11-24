"""The drag watcher, with Windows' key state stood in for: idle, it reads only the mouse button; a key
held before the button went down is no trigger, and one pressed mid-drag is.

    %LOCALAPPDATA%\\DropshiftBuild\\venv\\Scripts\\python.exe tests\\test_watcher.py
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from dropshift import watcher, win32  # noqa: E402


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.down: set[int] = set()
        self.reads: list[int] = []
        self.pos = QPoint(100, 100)

        def key_down(vk):
            self.reads.append(vk)
            return vk in self.down
        patches = [mock.patch.object(win32, 'key_down', key_down),
                   mock.patch.object(win32, 'primary_button', lambda: 0x01),
                   mock.patch.object(watcher.QCursor, 'pos', lambda: self.pos)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.watch = watcher.DragWatcher({'convert': 'shift', 'tools': 'ctrl', 'actions': 'alt'})
        self.watch._threshold = 4
        self.triggered = []
        self.watch.triggered.connect(self.triggered.append)

    def test_idle_reads_only_the_button(self):
        self.reads.clear()                      # (the keys are read once as it starts)
        for _ in range(10):
            self.watch._poll()
        self.assertEqual(self.reads, [0x01] * 10)

    def test_a_key_held_from_the_start_is_no_trigger_but_one_pressed_mid_drag_is(self):
        shift, ctrl = win32.TRIGGER_KEYS['shift'], win32.TRIGGER_KEYS['ctrl']
        self.down |= {shift, 0x01}               # Shift held as the button goes down (Explorer's move-drag)
        self.watch._poll()
        self.pos = QPoint(140, 100)              # past the drag threshold
        self.watch._poll()
        self.assertEqual(self.triggered, [])
        self.down.add(ctrl)                      # Ctrl pressed mid-drag
        self.watch._poll()
        self.assertEqual(self.triggered, ['tools'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
