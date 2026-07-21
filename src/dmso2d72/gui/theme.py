"""Light/dark theming.

Qt's colour scheme hint drives the widget chrome, so switching repaints every
existing widget without a restart and without a stylesheet that would have to
re-implement disabled and highlight states by hand.

pyqtgraph plots are not palette-driven, so they carry their own colours here.
Curve colours are per-theme rather than fixed: the dark theme's yellow is
illegible on a white background.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

THEME_NAMES = ("dark", "light", "system")


@dataclass(frozen=True)
class Theme:
    name: str
    plot_background: str
    axis: str
    grid_alpha: float
    dmm_history: str
    channel_colors: dict[int, str] = field(default_factory=dict)


THEMES = {
    "dark": Theme(
        name="dark",
        plot_background="#101418",
        axis="#b0b8c0",
        grid_alpha=0.3,
        dmm_history="#5dade2",
        channel_colors={1: "#f4d03f", 2: "#5dade2"},
    ),
    "light": Theme(
        name="light",
        plot_background="#ffffff",
        axis="#404040",
        grid_alpha=0.25,
        dmm_history="#1f6fb2",
        # Darkened: the dark theme's #f4d03f/#5dade2 wash out on white.
        channel_colors={1: "#b8860b", 2: "#1f6fb2"},
    ),
}

_SCHEMES = {
    "dark": Qt.ColorScheme.Dark,
    "light": Qt.ColorScheme.Light,
    "system": Qt.ColorScheme.Unknown,
}


def resolve(name: str, app: QApplication | None = None) -> Theme:
    """The Theme to paint plots with. "system" follows the desktop's scheme."""
    if name in THEMES:
        return THEMES[name]
    app = app or QApplication.instance()
    effective = app.styleHints().colorScheme() if app is not None else Qt.ColorScheme.Unknown
    return THEMES["light"] if effective == Qt.ColorScheme.Light else THEMES["dark"]


def _dark_palette() -> QPalette:
    """An explicit dark palette.

    The colour-scheme hint alone does not restyle widgets on every platform
    (it does not under the offscreen platform, and not on all desktops), so the
    palette is set as well rather than trusted to follow.
    """
    window = QColor("#1e2227")
    base = QColor("#15181c")
    text = QColor("#e2e6ea")
    button = QColor("#2a2f36")
    disabled = QColor("#6b7278")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, window)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, base)
    palette.setColor(QPalette.ColorRole.AlternateBase, window)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, button)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.ToolTipBase, base)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ff6b6b"))
    palette.setColor(QPalette.ColorRole.Link, QColor("#5dade2"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2a82da"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.WindowText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return palette


def apply_to_app(app: QApplication, name: str) -> Theme:
    """Switch the app between light/dark and return the matching plot theme.

    Single choke point for everything colour-related outside the plots.
    """
    if name not in THEME_NAMES:
        name = "system"
    app.styleHints().setColorScheme(_SCHEMES[name])
    theme = resolve(name, app)
    app.setPalette(_dark_palette() if theme.name == "dark" else app.style().standardPalette())
    # So plots built later (the DMM history) start with the right colours.
    pg.setConfigOption("background", theme.plot_background)
    pg.setConfigOption("foreground", theme.axis)
    return theme
