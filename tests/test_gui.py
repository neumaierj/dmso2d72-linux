"""GUI behaviour, headless.

Every test patches Dmso2d72 so the suite never grabs the real device — one is
usually attached, and MainWindow connects during construction.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMessageBox

from dmso2d72 import protocol as p
from dmso2d72.device import DeviceNotFound
from dmso2d72.gui import main_window as mw
from dmso2d72.gui.awg_tab import AwgTab
from dmso2d72.gui.dmm_tab import CURRENT_MODES, MODE_ORDER, DmmTab
from dmso2d72.gui.scope_tab import ScopeTab
from fakes import FakeDevice


@pytest.fixture
def window(qapp, monkeypatch, settings_file):
    """A MainWindow that believes no device is present."""

    def no_device(*args, **kwargs):
        raise DeviceNotFound("no device in tests")

    monkeypatch.setattr(mw, "Dmso2d72", no_device)
    w = mw.MainWindow()
    yield w
    w.close()


# ------------------------------------------------------------------- smoke


def test_window_builds_with_three_tabs(window):
    assert window.tab_widget.count() == 3
    assert window.device is None


def test_theme_switch_repaints_plots(window):
    window._set_theme("light", save=False)
    light = window.scope_tab.plot.backgroundBrush().color().name()
    window._set_theme("dark", save=False)
    dark = window.scope_tab.plot.backgroundBrush().color().name()
    assert light != dark


def test_every_menu_action_is_safe_without_a_device(window):
    """Nothing should raise just because no device is connected."""
    # Would close the window or open a modal dialog, so not triggerable here.
    skip = {"&Quit", "&About", "Export waveform CSV…", "Export multimeter history CSV…"}

    def walk(menu):
        for action in menu.actions():
            if action.isSeparator():
                continue
            if action.menu() is not None:
                walk(action.menu())
            elif action.text() not in skip:
                action.trigger()

    for action in window.menuBar().actions():
        if action.menu() is not None:
            walk(action.menu())


# --------------------------------------------------------------- DMM tab


def test_mode_selector_offers_every_protocol_mode(qapp):
    tab = DmmTab()
    assert set(MODE_ORDER) == set(p.DMM_MODES)
    assert tab.mode_combo.count() == len(p.DMM_MODES)


def test_set_mode_sends_the_command(qapp):
    tab = DmmTab()
    fake = FakeDevice()
    tab.set_device(fake)
    tab.mode_combo.setCurrentText("DC V")
    tab.set_mode_button.click()
    assert ("set_dmm_mode", ("DC V",)) in fake.calls


def test_current_mode_asks_first_and_cancel_sends_nothing(qapp, monkeypatch):
    tab = DmmTab()
    fake = FakeDevice()
    tab.set_device(fake)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)
    tab.mode_combo.setCurrentText("DC A")
    tab.set_mode_button.click()
    assert "set_dmm_mode" not in fake.method_names()


def test_current_mode_proceeds_when_confirmed(qapp, monkeypatch):
    tab = DmmTab()
    fake = FakeDevice()
    tab.set_device(fake)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    tab.mode_combo.setCurrentText("DC A")
    tab.set_mode_button.click()
    assert fake.method_names().count("set_dmm_mode") == 1


def test_non_current_modes_do_not_prompt(qapp, monkeypatch):
    """A prompt here would mean the confirmation is firing too broadly."""

    def explode(self):
        raise AssertionError("must not prompt for a non-current mode")

    monkeypatch.setattr(QMessageBox, "exec", explode)
    tab = DmmTab()
    fake = FakeDevice()
    tab.set_device(fake)
    for mode in MODE_ORDER:
        if mode in CURRENT_MODES:
            continue
        tab.mode_combo.setCurrentText(mode)
        tab.set_mode_button.click()
    assert fake.method_names().count("set_dmm_mode") == len(MODE_ORDER) - len(CURRENT_MODES)


def test_dmm_mode_is_not_pushed_on_connect(qapp, settings_file):
    """Restoring a current range silently would be a safety problem."""
    tab = DmmTab()
    fake = FakeDevice()
    tab.set_device(fake)
    assert "set_dmm_mode" not in fake.method_names()


# --------------------------------------------------------- push on connect


def test_scope_pushes_each_setting_once_and_starts_last(qapp):
    tab = ScopeTab()
    fake = FakeDevice()
    tab.set_device(fake)
    names = fake.method_names()
    assert names.count("set_time_scale") == 1
    assert names.count("set_trigger_level") == 1
    # The scope must only run once it is fully configured.
    assert names[-1] == "scope_start"


def test_awg_pushes_settings_but_leaves_output_off(qapp):
    tab = AwgTab()
    fake = FakeDevice()
    tab.set_device(fake)
    assert ("awg_start", (False,)) in fake.calls
    assert ("awg_start", (True,)) not in fake.calls
    assert not tab.start_button.isChecked()


def test_push_aborts_after_first_failure(qapp):
    tab = ScopeTab()
    lost: list[str] = []
    tab.device_lost.connect(lost.append)
    fake = FakeDevice(fail_after=3)
    tab.set_device(fake)
    # One report, and no attempt to plough on through the rest.
    assert len(lost) == 1
    assert len(fake.calls) == 4


def test_disconnect_does_not_push(qapp):
    tab = ScopeTab()
    fake = FakeDevice()
    tab.set_device(fake)
    before = len(fake.calls)
    tab.set_device(None)
    assert len(fake.calls) == before


def test_reconnecting_same_device_pushes_again(qapp):
    tab = ScopeTab()
    fake = FakeDevice()
    tab.set_device(fake)
    first = len(fake.calls)
    tab.set_device(None)
    tab.set_device(fake)
    assert len(fake.calls) == first * 2


# ------------------------------------------------------------- settings


def test_scope_settings_round_trip(qapp, tmp_path):
    from PySide6.QtCore import QSettings

    path = str(tmp_path / "s.ini")
    tab = ScopeTab()
    tab.ch_boxes[2].enabled.setChecked(True)
    tab.time_scale.setCurrentText("10ms")
    tab.trig_level.setValue(150)
    tab.save_settings(QSettings(path, QSettings.IniFormat))

    restored = ScopeTab()
    restored.restore_settings(QSettings(path, QSettings.IniFormat))
    assert restored.ch_boxes[2].enabled.isChecked() is True
    assert restored.time_scale.currentText() == "10ms"
    assert restored.trig_level.value() == 150


def test_restore_sends_nothing_to_a_device(qapp, tmp_path):
    """Restoring runs while disconnected; it must not queue USB writes."""
    from PySide6.QtCore import QSettings

    path = str(tmp_path / "s.ini")
    ScopeTab().save_settings(QSettings(path, QSettings.IniFormat))

    tab = ScopeTab()
    fake = FakeDevice()
    tab.device = fake  # attached without going through set_device
    tab.restore_settings(QSettings(path, QSettings.IniFormat))
    assert fake.calls == []


# ----------------------------------------------------------- DMM history


def _reading(hexframe: str):
    return p.decode_dmm(bytes.fromhex(hexframe))


def test_history_buffers_and_plots(qapp):
    from dmso2d72.gui.dmm_history import DmmHistoryView

    view = DmmHistoryView()
    reading = _reading("550b010800000300090903030255")  # 0.993 kOhm
    for _ in range(5):
        view.add(reading)
    view._redraw()
    xs, ys = view.curve.getData()
    assert len(xs) == 5
    assert list(ys) == [0.993] * 5


def test_history_ignores_overload_samples(qapp):
    """OL readings have no value, so there is nothing to plot."""
    from dmso2d72.gui.dmm_history import DmmHistoryView

    view = DmmHistoryView()
    view.add(_reading("550b0108000002ff004cff040255"))
    assert not view.has_data()


def test_history_trims_to_window(qapp):
    from dmso2d72.gui.dmm_history import DmmHistoryView

    view = DmmHistoryView()
    view.window_combo.setCurrentText("1 min")
    reading = _reading("550b010800000300090903030255")
    view.add(reading)
    # Backdate the first point beyond the window, then trim via a new sample.
    view._points[0] = (view._points[0][0] - 120.0, view._points[0][1])
    view.add(reading)
    assert len(view._points) == 1


def test_history_clear(qapp):
    from dmso2d72.gui.dmm_history import DmmHistoryView

    view = DmmHistoryView()
    view.add(_reading("550b010800000300090903030255"))
    view.clear()
    assert not view.has_data()


def test_mode_change_clears_history_and_stats(qapp):
    tab = DmmTab()
    fake = FakeDevice()
    tab.set_device(fake)
    tab._show_reading(_reading("550b010800000300090903030255"))
    assert tab.history.has_data() and tab.stats.count == 1
    tab.mode_combo.setCurrentText("Resistance")
    tab.set_mode_button.click()
    # Mixing units on one axis or in one min/max would be meaningless.
    assert not tab.history.has_data()
    assert tab.stats.count == 0


def test_hold_freezes_the_display(qapp):
    tab = DmmTab()
    tab.set_device(FakeDevice())
    tab._show_reading(_reading("550b010800000300090903030255"))
    shown = tab.value_label.text()
    tab.hold_button.setChecked(True)
    tab._show_reading(_reading("550b010a01000303000000050155"))  # 3.000 V
    assert tab.value_label.text() == shown
