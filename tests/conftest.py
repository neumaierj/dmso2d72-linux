"""Shared test fixtures.

The GUI tests run headless. QT_QPA_PLATFORM has to be set before anything
imports Qt, so it is done at module import time rather than in a fixture.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole session; Qt does not allow more than one."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    """Point QSettings at a throwaway ini file instead of the user's config."""
    from PySide6.QtCore import QSettings

    path = tmp_path / "test.ini"

    def fake_settings(*args, **kwargs):
        return QSettings(str(path), QSettings.IniFormat)

    monkeypatch.setattr("dmso2d72.settings.app_settings", fake_settings)
    return path
