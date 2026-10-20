"""The Show metadata window: everything a file says about itself, in sections."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QFileInfo, QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QComboBox, QFileIconProvider, QHBoxLayout, QLabel,
                               QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import catalog, win32
from ..engines import metadata
from . import icons


class _Reader(QObject):
    done = Signal(object, object)    # path, sections


class InfoWindow(QWidget):
    closed = Signal(object)

    def __init__(self, paths: list[Path], strip: Callable[[list[Path]], None]):
        super().__init__(None, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowIcon(icons.app_icon())
        self.resize(560, 640)
        self.paths = paths
        self._strip = strip
        self._sections: dict[Path, list] = {}
        self._reader = _Reader(self)
        self._reader.done.connect(self._loaded)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self._icon = QLabel()
        top.addWidget(self._icon)
        self._title = QLabel()
        font = self._title.font()
        font.setPointSizeF(font.pointSizeF() * 1.25)
        font.setWeight(QFont.DemiBold)
        self._title.setFont(font)
        self._title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(self._title, 1)
        layout.addLayout(top)

        self._picker = QComboBox()
        for path in paths:
            self._picker.addItem(QFileIconProvider().icon(QFileInfo(str(path))), path.name)
        self._picker.currentIndexChanged.connect(lambda i: self._show(self.paths[i]))
        self._picker.setVisible(len(paths) > 1)
        layout.addWidget(self._picker)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self._tree.setUniformRowHeights(True)
        self._tree.setWordWrap(True)
        layout.addWidget(self._tree, 1)

        buttons = QHBoxLayout()
        self._copy = QPushButton('Copy all')
        self._copy.clicked.connect(self._copy_all)
        self._map = QPushButton('Show on map')
        self._map.clicked.connect(self._open_map)
        self._remove = QPushButton('Remove metadata')
        self._remove.setToolTip('Save a copy without camera, location and author details')
        self._remove.clicked.connect(lambda: self._strip([self.current]))
        folder = QPushButton('Show in folder')
        folder.clicked.connect(lambda: win32.show_in_explorer(self.current))
        close = QPushButton('Close')
        close.clicked.connect(self.close)
        for button in (self._copy, self._map, self._remove, folder):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        QShortcut(QKeySequence.Copy, self._tree, self._copy_selected)
        QShortcut(QKeySequence(Qt.Key_Escape), self, self.close)
        self._show(paths[0])

    @property
    def current(self) -> Path:
        return self.paths[max(self._picker.currentIndex(), 0)]

    # ------------------------------------------------------------------ loading

    def _show(self, path: Path) -> None:
        self.setWindowTitle(f'{path.name} - metadata')
        self._title.setText(path.name or str(path))
        self._icon.setPixmap(QFileIconProvider().icon(QFileInfo(str(path))).pixmap(32, 32))
        strip = catalog.BY_ID['tools.strip']
        self._remove.setVisible(strip.takes(path) and strip.available())
        if path in self._sections:
            self._fill(self._sections[path])
            return
        self._tree.clear()
        QTreeWidgetItem(self._tree, ['Reading…', ''])
        self._map.setVisible(False)
        self._copy.setEnabled(False)
        # ffprobe and big folders can take a moment; keep the window responsive.
        threading.Thread(target=self._read, args=(path,), daemon=True).start()

    def _read(self, path: Path) -> None:
        try:
            sections = metadata.describe(path)
        except Exception as error:  # noqa: BLE001
            sections = [('File', [('Could not read', str(error))])]
        try:
            self._reader.done.emit(path, sections)
        except RuntimeError:
            pass     # the window was closed first

    def _loaded(self, path: Path, sections) -> None:
        self._sections[path] = sections
        if path == self.current:
            self._fill(sections)

    def _fill(self, sections) -> None:
        self._tree.clear()
        bold = QFont(self._tree.font())
        bold.setWeight(QFont.DemiBold)
        for title, rows in sections:
            head = QTreeWidgetItem(self._tree, [title, ''])
            head.setFont(0, bold)
            head.setFirstColumnSpanned(True)
            for field, value in rows:
                item = QTreeWidgetItem(head, [field, value])
                item.setToolTip(1, value)
            # Long lists (every EXIF tag) start folded away.
            head.setExpanded(title != 'All EXIF')
        self._tree.resizeColumnToContents(0)
        self._tree.setColumnWidth(0, min(self._tree.columnWidth(0) + 16, 220))
        self._map.setVisible(metadata.location_of(sections) is not None)
        self._copy.setEnabled(True)

    # ------------------------------------------------------------------ buttons

    def _copy_all(self) -> None:
        sections = self._sections.get(self.current)
        if sections:
            QApplication.clipboard().setText(metadata.as_text(self.current, sections))

    def _copy_selected(self) -> None:
        lines = []
        for item in self._tree.selectedItems():
            lines.append(item.text(0) if item.parent() is None else f'{item.text(0)}: {item.text(1)}')
        if lines:
            QApplication.clipboard().setText('\n'.join(lines))

    def _open_map(self) -> None:
        place = metadata.location_of(self._sections.get(self.current, []))
        if place:
            lat, lon = place
            QDesktopServices.openUrl(QUrl(f'https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=16/{lat:.6f}/{lon:.6f}'))

    def closeEvent(self, event):
        self.closed.emit(self)
        super().closeEvent(event)
