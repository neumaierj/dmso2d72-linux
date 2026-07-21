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
from dmso2d72.gui.dmm_tab import CURRENT_MODES, DmmTab, _slot_for_reading
from dmso2d72.gui.scope_tab import ScopeTab
from fakes import FakeDevice


def _reading(hexframe: str):
    return p.decode_dmm(bytes.fromhex(hexframe))


def _click_mode(tab, mode: str):
    tab._mode_buttons[mode].click()


@pytest.fixture
def window(qapp, monkeypatch, settings_file):
    """A MainWindow that believes no device is present."""

    def no_device(*args, **kwargs):
        raise DeviceNotFound("no device in tests")

    monkeypatch.setattr(mw, "Dmso2d72", no_device)
    w = mw.MainWindow()
    yield w
    w.close()


@pytest.fixture
def connected_window(qapp, monkeypatch, settings_file):
    """A MainWindow connected to a FakeDevice, monitor stopped for determinism."""
    fake = FakeDevice()
    monkeypatch.setattr(mw, "Dmso2d72", lambda *a, **k: fake)
    w = mw.MainWindow()
    w._stop_monitor()  # no background polling during the test
    fake.calls.clear()
    yield w, fake
    w.close()


# ------------------------------------------------------------------- smoke


def test_window_builds(window):
    assert window.measure_tabs.count() == 2  # Scope + DMM
    assert window.awg_tab.isHidden()  # generator hidden by default
    assert window.device is None


def test_theme_switch_repaints_plots(window):
    window._set_theme("light", save=False)
    light = window.scope_tab.plot.backgroundBrush().color().name()
    window._set_theme("dark", save=False)
    dark = window.scope_tab.plot.backgroundBrush().color().name()
    assert light != dark


def test_every_menu_action_is_safe_without_a_device(window):
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


# --------------------------------------------------------- DMM grouped modes


def test_panel_covers_every_protocol_mode_once(qapp):
    tab = DmmTab()
    assert set(tab._mode_buttons) == set(p.DMM_MODES)


def test_select_sends_the_command_and_marks_it(qapp):
    tab = DmmTab()
    fake = FakeDevice()
    tab.set_device(fake)
    _click_mode(tab, "DC V")
    assert ("set_dmm_mode", ("DC V",)) in fake.calls
    assert tab._active_mode == "DC V"
    assert tab._mode_buttons["DC V"].isChecked()


def test_current_mode_asks_first_and_cancel_sends_nothing(qapp, monkeypatch):
    tab = DmmTab()
    fake = FakeDevice()
    tab.set_device(fake)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)
    _click_mode(tab, "DC A")
    assert "set_dmm_mode" not in fake.method_names()
    assert tab._active_mode is None


def test_current_mode_proceeds_when_confirmed(qapp, monkeypatch):
    tab = DmmTab()
    fake = FakeDevice()
    tab.set_device(fake)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    _click_mode(tab, "DC A")
    assert fake.method_names().count("set_dmm_mode") == 1


def test_non_current_modes_do_not_prompt(qapp, monkeypatch):
    def explode(self):
        raise AssertionError("must not prompt for a non-current mode")

    monkeypatch.setattr(QMessageBox, "exec", explode)
    tab = DmmTab()
    fake = FakeDevice()
    tab.set_device(fake)
    non_current = [m for m in tab._mode_buttons if m not in CURRENT_MODES]
    for mode in non_current:
        _click_mode(tab, mode)
    assert fake.method_names().count("set_dmm_mode") == len(non_current)


def test_dmm_mode_is_not_pushed_on_connect(qapp, settings_file):
    tab = DmmTab()
    fake = FakeDevice()
    tab.set_device(fake)
    assert "set_dmm_mode" not in fake.method_names()


# ------------------------------------------------------ DMM follows the device


def test_slot_for_reading_maps_every_mode(qapp):
    cases = {
        "550b010a01000300000000050155": "DC V",
        "550b010a01000300000000020155": "DC mV",
        "550b010a02000300000000050155": "AC V",
        "550b010a01000300000000050055": "DC A",
        "550b010a01000300000000020055": "DC mA",
        "550b010a02000300000000050055": "AC A",
        "550b010a02000300000000020055": "AC mA",
        "550b010800000300090903030255": "Resistance",
        "550b010900000100000000050255": "Continuity",
        "550b010a00000300000000000355": "Capacitance",
        "550b010a00000300050909050155": "Diode",
    }
    for frame, expected in cases.items():
        assert _slot_for_reading(_reading(frame)) == expected


def test_panel_follows_the_device_after_two_readings(qapp):
    tab = DmmTab()
    tab.set_device(FakeDevice())
    r = _reading("550b010800000300090903030255")  # Resistance
    tab.show_reading(r)
    assert tab._active_mode is None  # one reading is not enough (debounce)
    tab.show_reading(r)
    assert tab._active_mode == "Resistance"
    assert tab._mode_buttons["Resistance"].isChecked()


def test_single_torn_frame_does_not_move_the_highlight(qapp):
    tab = DmmTab()
    tab.set_device(FakeDevice())
    dcv = _reading("550b010a01000300000000050155")
    for _ in range(2):
        tab.show_reading(dcv)
    assert tab._active_mode == "DC V"
    tab.show_reading(_reading("550b010a00000300000000000355"))  # one Capacitance blip
    tab.show_reading(dcv)
    assert tab._active_mode == "DC V"


def test_app_set_is_not_bounced_by_stale_old_mode_frames(qapp):
    tab = DmmTab()
    tab.set_device(FakeDevice())
    res = _reading("550b010800000300090903030255")
    for _ in range(2):
        tab.show_reading(res)
    assert tab._active_mode == "Resistance"
    _click_mode(tab, "DC V")
    assert tab._active_mode == "DC V"
    for _ in range(3):
        tab.show_reading(res)  # stale old-mode frames
    assert tab._active_mode == "DC V"
    dcv = _reading("550b010a01000300000000050155")
    for _ in range(2):
        tab.show_reading(dcv)
    assert tab._active_mode == "DC V"


def test_mode_change_clears_history_and_stats(qapp):
    tab = DmmTab()
    tab.set_device(FakeDevice())
    tab.show_reading(_reading("550b010800000300090903030255"))
    assert tab.history.has_data() and tab.stats.count == 1
    _click_mode(tab, "Resistance")
    assert not tab.history.has_data()
    assert tab.stats.count == 0


def test_hold_freezes_the_display(qapp):
    tab = DmmTab()
    tab.set_device(FakeDevice())
    tab.show_reading(_reading("550b010800000300090903030255"))
    shown = tab.value_label.text()
    tab.hold_button.setChecked(True)
    tab.show_reading(_reading("550b010a01000303000000050155"))  # 3.000 V
    assert tab.value_label.text() == shown


# --------------------------------------------------------- push on activate


def test_set_device_alone_does_not_push(qapp):
    tab = ScopeTab()
    fake = FakeDevice()
    tab.set_device(fake)
    assert fake.calls == []


def test_scope_pushes_each_setting_once_and_starts_last(qapp):
    tab = ScopeTab()
    fake = FakeDevice()
    tab.set_device(fake)
    tab.activate()
    names = fake.method_names()
    assert names.count("set_time_scale") == 1
    assert names[-1] == "scope_start"


def test_activate_pushes_only_once_per_connection(qapp):
    tab = ScopeTab()
    fake = FakeDevice()
    tab.set_device(fake)
    tab.activate()
    n = len(fake.calls)
    tab.activate()
    assert len(fake.calls) == n


def test_push_aborts_after_first_failure(qapp):
    tab = ScopeTab()
    lost: list[str] = []
    tab.device_lost.connect(lost.append)
    fake = FakeDevice(fail_after=3)
    tab.set_device(fake)
    tab.activate()
    assert len(lost) == 1
    assert len(fake.calls) == 4


# --------------------------------------------- measurement mirrors the device


def test_connect_configures_the_active_measurement(qapp, monkeypatch, settings_file):
    fake = FakeDevice()
    monkeypatch.setattr(mw, "Dmso2d72", lambda *a, **k: fake)
    w = mw.MainWindow()
    w._stop_monitor()
    try:
        names = fake.method_names()
        assert "set_screen" in names
        # Default active measurement is the scope; it is configured, DMM is not.
        assert w.measure_tabs.currentWidget() is w.scope_tab
        assert "set_time_scale" in names
        assert not w.dmm_tab._settings_pushed
    finally:
        w.close()


def test_device_switch_follows_without_re_setting_screen(qapp, connected_window):
    """A physical instrument switch moves the app view but must not write back."""
    w, fake = connected_window
    w._on_device_measurement("dmm")
    assert w.measure_tabs.currentWidget() is w.dmm_tab
    assert "set_screen" not in fake.method_names()  # loop guard


def test_selecting_measurement_sets_the_screen(qapp, connected_window):
    w, fake = connected_window
    w.measure_tabs.setCurrentWidget(w.dmm_tab)
    assert ("set_screen", (p.SCREEN_DMM,)) in fake.calls


# ------------------------------------------------------------- AWG side panel


def test_awg_toggle_shows_and_pushes_on_awg_screen(qapp, connected_window):
    w, fake = connected_window
    w.measure_tabs.setCurrentWidget(w.scope_tab)
    fake.calls.clear()
    w.awg_action.setChecked(True)  # show the panel -> initial push via bounce
    assert not w.awg_tab.isHidden()
    names = fake.method_names()
    # Applied on the AWG screen, then back to the measurement screen.
    assert ("set_screen", (p.SCREEN_AWG,)) in fake.calls
    assert ("set_screen", (p.SCREEN_SCOPE,)) in fake.calls
    assert "set_awg_type" in names


def test_awg_edit_applies_on_awg_screen(qapp, connected_window):
    w, fake = connected_window
    w.awg_action.setChecked(True)
    w.measure_tabs.setCurrentWidget(w.dmm_tab)
    fake.calls.clear()
    w.awg_tab.frequency.setValue(2500)  # queues a change
    w.awg_tab._flush()  # apply now instead of waiting for the debounce timer
    assert ("set_screen", (p.SCREEN_AWG,)) in fake.calls
    assert ("set_awg_frequency", (2500.0,)) in fake.calls
    assert ("set_screen", (p.SCREEN_DMM,)) in fake.calls  # returned to the measurement


def test_awg_edit_without_hook_applies_directly(qapp):
    """Standalone (no window hook): edits apply straight to the device."""
    tab = AwgTab()
    fake = FakeDevice()
    tab.set_device(fake)
    tab.amplitude.setValue(2.0)
    tab._flush()
    assert ("set_awg_amplitude", (2.0,)) in fake.calls


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
    from PySide6.QtCore import QSettings

    path = str(tmp_path / "s.ini")
    ScopeTab().save_settings(QSettings(path, QSettings.IniFormat))

    tab = ScopeTab()
    fake = FakeDevice()
    tab.device = fake
    tab.restore_settings(QSettings(path, QSettings.IniFormat))
    assert fake.calls == []


# ----------------------------------------------------------- DMM history


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
    view._points[0] = (view._points[0][0] - 120.0, view._points[0][1])
    view.add(reading)
    assert len(view._points) == 1


def test_history_clear(qapp):
    from dmso2d72.gui.dmm_history import DmmHistoryView

    view = DmmHistoryView()
    view.add(_reading("550b010800000300090903030255"))
    view.clear()
    assert not view.has_data()
