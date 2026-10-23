"""Colours for Dropshift's own painted windows, following Windows' light or dark mode.

They are the suite's, from suitetheme.py - the shared file Hanami keeps in
step across the suite's apps - plus the few the drop menu adds below.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QColor

from . import suitetheme
from .suitetheme import *  # noqa: F401,F403 - the suite's look, used as theme.<name>
from .suitetheme import ACCENT, ON_ACCENT, is_dark


BACKDROP = str(Path(__file__).with_name('backdrop.jpg'))     # Iewek Gnos's blossom, on Unsplash, faint behind the windows


@dataclass(frozen=True)
class Colors(suitetheme.Colors):
    chip_text: QColor
    hover: QColor       # the pink: the item under the pointer, and a progress bar's fill
    hover_text: QColor
    tab_on: QColor      # the drop menu's mode now showing
    track: QColor       # a progress bar's empty part


def colors() -> Colors:
    base = suitetheme.colors()
    tab = '#34353f' if is_dark() else '#e6e8f0'
    return Colors(**vars(base), chip_text=QColor(base.text), hover=QColor(ACCENT), hover_text=QColor(ON_ACCENT),
                  tab_on=QColor(tab), track=QColor(tab))
