"""The drop menu's tabs, mid-drag: another mode's tab turns the menu to it only once the pointer rests
there, so passing over one on the way to a tile changes nothing.

    %LOCALAPPDATA%\\DropshiftBuild\\venv\\Scripts\\python.exe tests\\test_menu.py
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from dropshift.menu import TAB_DWELL, DropMenu  # noqa: E402


class TabTests(unittest.TestCase):
    def setUp(self):
        self.menu = DropMenu({'convert': 'shift', 'tools': 'ctrl', 'actions': 'alt'})
        self.menu.open('actions', QPoint(300, 300))

    def tearDown(self):
        self.menu.dismiss()
        self.menu.deleteLater()

    def tab(self, mode: str):
        return next(item for item in self.menu._items if item.kind == 'tab' and item.mode == mode)

    def test_passing_over_a_tab_keeps_the_mode(self):
        self.menu._hover_at(self.tab('convert').rect.center())
        self.menu._hover_at(self.tab('convert').rect.center() + QPoint(0, 60))     # on, down past it
        QTest.qWait(TAB_DWELL + 150)
        self.assertEqual(self.menu._mode, 'actions', 'still Actions: the pointer only went over Convert')

    def test_resting_on_a_tab_turns_to_it(self):
        self.menu._hover_at(self.tab('convert').rect.center())
        self.assertEqual(self.menu._mode, 'actions', 'not at once')
        QTest.qWait(TAB_DWELL + 150)
        self.assertEqual(self.menu._mode, 'convert')

    def test_the_wait_ends_with_the_menu(self):
        self.menu._hover_at(self.tab('tools').rect.center())
        self.menu.dismiss()
        QTest.qWait(TAB_DWELL + 150)
        self.assertEqual(self.menu._mode, 'actions')


if __name__ == '__main__':
    unittest.main(verbosity=2)
