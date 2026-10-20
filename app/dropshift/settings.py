"""What Dropshift remembers, kept in the registry under HKCU\\Software\\Dropshift."""

from __future__ import annotations

from PySide6.QtCore import QSettings

from .catalog import MODES

DEFAULT_KEYS = {'convert': 'shift', 'tools': 'ctrl', 'actions': 'alt'}


class Settings:
    def __init__(self):
        self._store = QSettings('Dropshift', 'Dropshift')

    @property
    def keys(self) -> dict[str, str]:
        keys = {mode: str(self._store.value(f'keys/{mode}', DEFAULT_KEYS[mode])) for mode in MODES}
        return {mode: key if key in ('shift', 'ctrl', 'alt', 'off') else 'off' for mode, key in keys.items()}

    @keys.setter
    def keys(self, keys: dict[str, str]) -> None:
        for mode in MODES:
            self._store.setValue(f'keys/{mode}', keys.get(mode, 'off'))

    @property
    def output_folder(self) -> str:
        """'' for beside the original."""
        return str(self._store.value('output/folder', '') or '')

    @output_folder.setter
    def output_folder(self, folder: str) -> None:
        self._store.setValue('output/folder', folder)

    @property
    def last_folder(self) -> str:
        return str(self._store.value('actions/last_folder', '') or '')

    @last_folder.setter
    def last_folder(self, folder: str) -> None:
        self._store.setValue('actions/last_folder', folder)

    @property
    def tray_shown(self) -> bool:
        """In the tray (Hanami's icon, or Dropshift's own), or out of it altogether."""
        return str(self._store.value('tray/shown', True)).lower() in ('true', '1')

    @tray_shown.setter
    def tray_shown(self, value: bool) -> None:
        self._store.setValue('tray/shown', bool(value))

    @property
    def welcomed(self) -> bool:
        return self._store.value('welcomed', False, type=bool)

    @welcomed.setter
    def welcomed(self, value: bool) -> None:
        self._store.setValue('welcomed', value)
