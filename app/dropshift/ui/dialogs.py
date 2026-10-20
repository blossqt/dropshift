"""The settings window and the "how it works" card shown on first start."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QRadioButton, QVBoxLayout,
                               QWidget)

from .. import __version__, locate, win32
from ..catalog import MODE_TITLES, MODES
from ..engines import office
from . import icons, theme

KEY_CHOICES = [('Shift', 'shift'), ('Ctrl', 'ctrl'), ('Alt', 'alt'), ('Off', 'off')]
MODE_BLURBS = {'convert': 'make a copy in another format',
               'tools': 'compress, rotate, split, zip...',
               'actions': 'copy, float, open a terminal there...'}


class SettingsDialog(QDialog):
    def __init__(self, settings, controller=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.controller = controller
        self.setWindowTitle('Dropshift settings')
        self.setWindowIcon(icons.app_icon())
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)

        if controller is not None:
            # What the tray menu offers, for when Dropshift is out of the tray.
            row = QHBoxLayout()
            pause = QPushButton('Resume Dropshift' if controller.paused else 'Pause Dropshift')
            pause.clicked.connect(lambda: (controller.set_paused(not controller.paused),
                                           pause.setText('Resume Dropshift' if controller.paused
                                                         else 'Pause Dropshift')))
            row.addWidget(pause)
            welcome = QPushButton('How it works')
            welcome.clicked.connect(controller.show_welcome)
            row.addWidget(welcome)
            row.addStretch(1)
            quit_button = QPushButton('Quit Dropshift')
            quit_button.clicked.connect(lambda: (self.reject(), controller.quit()))
            row.addWidget(quit_button)
            layout.addLayout(row)
            layout.addSpacing(8)

        layout.addWidget(_heading('While dragging a file, press'))
        keys = QFormLayout()
        self._combos: dict[str, QComboBox] = {}
        current = settings.keys
        for mode in MODES:
            combo = QComboBox()
            for label, value in KEY_CHOICES:
                combo.addItem(label, value)
            combo.setCurrentIndex([v for _, v in KEY_CHOICES].index(current[mode]))
            self._combos[mode] = combo
            row = QHBoxLayout()
            row.addWidget(combo)
            hint = QLabel(MODE_BLURBS[mode])
            hint.setEnabled(False)
            row.addWidget(hint, 1)
            keys.addRow(f'{MODE_TITLES[mode]}:', row)
        layout.addLayout(keys)

        layout.addSpacing(8)
        layout.addWidget(_heading('Save what Dropshift makes'))
        self._beside = QRadioButton('Next to the original')
        self._elsewhere = QRadioButton('In this folder:')
        group = QButtonGroup(self)
        group.addButton(self._beside)
        group.addButton(self._elsewhere)
        self._folder = QLineEdit(settings.output_folder or str(win32.downloads_folder()))
        browse = QPushButton('Browse…')
        browse.clicked.connect(self._browse)
        (self._elsewhere if settings.output_folder else self._beside).setChecked(True)
        layout.addWidget(self._beside)
        row = QHBoxLayout()
        row.addWidget(self._elsewhere)
        row.addWidget(self._folder, 1)
        row.addWidget(browse)
        layout.addLayout(row)

        layout.addSpacing(8)
        self._startup = QCheckBox('Start Dropshift when I sign in to Windows')
        self._startup.setChecked(win32.starts_with_windows())
        layout.addWidget(self._startup)
        self._tray = QCheckBox('Show Dropshift in the tray')
        self._tray.setChecked(settings.tray_shown)
        layout.addWidget(self._tray)
        tray_note = QLabel("In Hanami's cherry blossom while Hanami runs, otherwise an icon of its own. "
                           'Turned off, open Dropshift again - from Start or Hanami - to come back here.')
        tray_note.setWordWrap(True)
        tray_note.setEnabled(False)
        layout.addWidget(tray_note)

        layout.addSpacing(8)
        layout.addWidget(_heading('Helpers found on this PC'))
        layout.addWidget(_status_grid())

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        about = QLabel(f'Dropshift {__version__}')
        about.setEnabled(False)
        bottom = QHBoxLayout()
        bottom.addWidget(about)
        bottom.addStretch(1)
        bottom.addWidget(buttons)
        layout.addLayout(bottom)

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, 'Save in', self._folder.text())
        if folder:
            self._folder.setText(folder)
            self._elsewhere.setChecked(True)

    def _save(self):
        keys = {mode: combo.currentData() for mode, combo in self._combos.items()}
        chosen = [k for k in keys.values() if k != 'off']
        if len(chosen) != len(set(chosen)):
            QMessageBox.warning(self, 'Dropshift', 'Each key can open only one menu.')
            return
        self.settings.keys = keys
        self.settings.output_folder = self._folder.text().strip() if self._elsewhere.isChecked() else ''
        if self._startup.isChecked() != win32.starts_with_windows():
            win32.set_start_with_windows(self._startup.isChecked())
        if self.controller is not None and self._tray.isChecked() != self.settings.tray_shown:
            self.controller.set_tray_shown(self._tray.isChecked())
        self.accept()


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setWeight(QFont.DemiBold)
    label.setFont(font)
    return label


def _status_grid() -> QWidget:
    rows = [('FFmpeg', locate.ffmpeg() is not None, 'video, audio and subtitles'),
            ('Microsoft Word', office.available('Word'), 'documents, PDF to Word'),
            ('Microsoft Excel', office.available('Excel'), 'spreadsheets'),
            ('Microsoft PowerPoint', office.available('PowerPoint'), 'slides'),
            ('7-Zip', locate.seven_zip() is not None, '7Z, RAR and ISO archives')]
    widget = QWidget()
    grid = QGridLayout(widget)
    grid.setContentsMargins(0, 0, 0, 0)
    for row, (name, found, what) in enumerate(rows):
        mark = QLabel('✓' if found else '–')
        mark.setStyleSheet(f'color: {theme.ink() if found else "#999"}; font-weight: 600;')
        grid.addWidget(mark, row, 0)
        grid.addWidget(QLabel(name), row, 1)
        note = QLabel(what if found else f'not found - no {what}')
        note.setEnabled(False)
        grid.addWidget(note, row, 2)
    grid.setColumnStretch(2, 1)
    return widget


class WelcomeDialog(QDialog):
    def __init__(self, keys: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle('Dropshift')
        self.setWindowIcon(icons.app_icon())
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(icons.render_app_icon(48))
        top.addWidget(logo, 0, Qt.AlignTop)
        title = QLabel('<b style="font-size:14pt">Dropshift is running</b><br>'
                       'It lives in the tray, by the clock.')
        top.addWidget(title, 1)
        layout.addLayout(top)
        layout.addSpacing(6)
        steps = ['<b>1.</b> Start dragging any file, from Explorer, the desktop or a download.']
        lines = [f'<b>{win32.KEY_LABELS[keys[m]]}</b> to {MODE_BLURBS[m].rstrip(".")}'
                 for m in MODES if keys.get(m, 'off') != 'off']
        steps.append('<b>2.</b> While it is still held, press ' + '; '.join(lines) + '.')
        steps.append('<b>3.</b> Drop it on what you want. The result is saved next to the original.')
        text = QLabel('<br><br>'.join(steps))
        text.setWordWrap(True)
        text.setTextFormat(Qt.RichText)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText('Got it')
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
