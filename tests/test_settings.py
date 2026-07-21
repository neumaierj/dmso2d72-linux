"""Settings helpers must return real types, not whatever the backend stored."""

from __future__ import annotations

from PySide6.QtCore import QSettings

from dmso2d72 import settings as s


def _settings(tmp_path) -> QSettings:
    return QSettings(str(tmp_path / "t.ini"), QSettings.IniFormat)


def test_bool_round_trip_false_stays_false(tmp_path):
    st = _settings(tmp_path)
    st.setValue("a/flag", False)
    st.sync()
    assert s.get_bool(_settings(tmp_path), "a/flag", True) is False


def test_bool_round_trip_true(tmp_path):
    st = _settings(tmp_path)
    st.setValue("a/flag", True)
    st.sync()
    assert s.get_bool(_settings(tmp_path), "a/flag", False) is True


def test_numeric_round_trip(tmp_path):
    st = _settings(tmp_path)
    st.setValue("a/i", 7)
    st.setValue("a/f", 1.5)
    st.sync()
    fresh = _settings(tmp_path)
    assert s.get_int(fresh, "a/i", 0) == 7
    assert s.get_float(fresh, "a/f", 0.0) == 1.5


def test_defaults_when_missing(tmp_path):
    st = _settings(tmp_path)
    assert s.get_bool(st, "nope/x", True) is True
    assert s.get_int(st, "nope/x", 42) == 42
    assert s.get_float(st, "nope/x", 2.5) == 2.5
    assert s.get_str(st, "nope/x", "hi") == "hi"
