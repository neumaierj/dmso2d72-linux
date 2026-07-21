"""Persisted UI settings.

Kept out of the gui package (like protocol.py and device.py) so it stays
importable and testable without constructing widgets.

QSettings returns values untyped, and what you get back depends on the storage
backend. The get_* helpers pin the type at every read so a restored setting can
never come back as a string that happens to be truthy.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings

ORG_NAME = "dmso2d72"
APP_NAME = "dmso2d72"


def app_settings() -> QSettings:
    """Settings for this app. Relies on app.main() having set the org/app name."""
    return QSettings()


def get_bool(settings: QSettings, key: str, default: bool) -> bool:
    return bool(settings.value(key, default, type=bool))


def get_int(settings: QSettings, key: str, default: int) -> int:
    return int(settings.value(key, default, type=int))


def get_float(settings: QSettings, key: str, default: float) -> float:
    return float(settings.value(key, default, type=float))


def get_str(settings: QSettings, key: str, default: str) -> str:
    return str(settings.value(key, default, type=str))
