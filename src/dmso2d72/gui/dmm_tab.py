"""Multimeter view: live reading, grouped mode selection, min/max and history.

Both the readout and the mode-set command were reverse-engineered from the
firmware and verified against the device (see re/DMM_PROTOCOL.md). Every mode is
decoded and settable, and the panel follows the device's real mode (from the
status frame) whether it was changed here or with the physical keys.

Readings arrive from the window's DeviceMonitor via show_reading(); this view no
longer polls the device itself.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import protocol as p
from ..device import Dmso2d72
from ..dmm_stats import DmmStats
from .device_tab import DeviceTab
from .dmm_history import DmmHistoryView

# Modes that turn the input into a low-impedance shunt. Selecting one by
# accident while the leads sit across a voltage source would short it, so these
# are confirmed first.
CURRENT_MODES = ("AC A", "DC A", "AC mA", "DC mA")

# Every mode grouped by category, all visible at once (no paging). Each entry is
# (button label, protocol.DMM_MODES key); labels use the device's own wording.
GROUPS = (
    ("Voltage", (("DC V", "DC V"), ("AC V", "AC V"), ("DC mV", "DC mV"))),
    ("Current", (("DC A", "DC A"), ("AC A", "AC A"), ("DC mA", "DC mA"), ("AC mA", "AC mA"))),
    ("Resistance · Continuity · Diode",
     (("OHM", "Resistance"), ("Buzzer", "Continuity"), ("Diode", "Diode"))),
    ("Capacitance", (("Capacitance", "Capacitance"),)),
)


def _slot_for_reading(reading) -> str | None:
    """The mode a live reading corresponds to, from its decoded mode and unit.

    The device does not report which mode is selected, but a reading's mode plus
    its unit prefix pin it down uniquely, so the panel can follow the device's
    real mode even when it is changed with the physical keys. Returns a
    DMM_MODES key, or None if unmapped.
    """
    mode, unit = reading.mode, reading.unit
    if mode == "DC Voltage":
        return "DC mV" if unit == "mV" else "DC V"
    if mode == "AC Voltage":
        return "AC V"
    if mode == "DC Current":
        return "DC mA" if unit == "mA" else "DC A"
    if mode == "AC Current":
        return "AC mA" if unit == "mA" else "AC A"
    return {
        "Resistance": "Resistance",
        "Continuity": "Continuity",
        "Capacitance": "Capacitance",
        "Diode": "Diode",
    }.get(mode)


class DmmTab(DeviceTab):
    device_screen = p.SCREEN_DMM

    def __init__(self, parent=None):
        super().__init__(parent)
        self.stats = DmmStats()
        self._active_mode: str | None = None
        # Debounce for following the device's live mode: a mid-switch frame can
        # briefly decode as another mode, so a new mode must persist before the
        # panel follows it.
        self._pending_slot: str | None = None
        self._pending_count = 0
        # After the app commands a mode, ignore readings until the device
        # confirms it, so stale in-flight frames of the old mode do not flicker
        # the highlight. Bounded, so a mode the device never reaches recovers.
        self._expecting: str | None = None
        self._expecting_left = 0

        # ------------------------------------------------------------- readout
        self.value_label = QLabel("--")
        self.value_label.setObjectName("dmmValue")
        self.value_label.setAlignment(Qt.AlignCenter)
        font = QFont()
        font.setPointSize(48)
        font.setBold(True)
        self.value_label.setFont(font)

        self.mode_label = QLabel("")
        self.mode_label.setObjectName("dmmMode")
        self.mode_label.setAlignment(Qt.AlignCenter)
        mode_font = QFont()
        mode_font.setPointSize(16)
        self.mode_label.setFont(mode_font)

        self.min_label = QLabel("--")
        self.max_label = QLabel("--")
        self.count_label = QLabel("0")
        stats_grid = QGridLayout()
        for col, (title, widget) in enumerate(
            (("Minimum", self.min_label), ("Maximum", self.max_label), ("Samples", self.count_label))
        ):
            caption = QLabel(title)
            caption.setAlignment(Qt.AlignCenter)
            widget.setAlignment(Qt.AlignCenter)
            stats_grid.addWidget(caption, 0, col)
            stats_grid.addWidget(widget, 1, col)

        reading_panel = QVBoxLayout()
        reading_panel.addStretch()
        reading_panel.addWidget(self.value_label)
        reading_panel.addWidget(self.mode_label)
        reading_panel.addSpacing(24)
        reading_panel.addLayout(stats_grid)
        reading_panel.addStretch()
        self.screen_panel = QWidget()
        self.screen_panel.setObjectName("dmmScreen")
        self.screen_panel.setLayout(reading_panel)

        self.history = DmmHistoryView()

        readout = QVBoxLayout()
        readout.addWidget(self.screen_panel, stretch=1)
        readout.addWidget(self.history, stretch=1)

        # ------------------------------------------------------------ controls
        # One button per mode, grouped by category, all visible.
        self._mode_buttons: dict[str, QPushButton] = {}
        mode_box = QGroupBox("Measurement mode")
        mode_layout = QVBoxLayout(mode_box)
        for title, entries in GROUPS:
            group = QGroupBox(title)
            grid = QGridLayout(group)
            for i, (label, mode) in enumerate(entries):
                button = QPushButton(label)
                button.setCheckable(True)
                button.setStyleSheet(
                    "QPushButton:checked { background-color: palette(highlight);"
                    " color: palette(highlighted-text); font-weight: bold; }"
                )
                button.clicked.connect(lambda _=False, m=mode: self._set_mode(m))
                self._mode_buttons[mode] = button
                grid.addWidget(button, i // 2, i % 2)
            mode_layout.addWidget(group)

        self.hold_button = QPushButton("Hold")
        self.hold_button.setCheckable(True)
        self.hold_button.setToolTip("Freeze the display; the device keeps measuring")
        self.reset_button = QPushButton("Reset min/max")
        self.reset_button.clicked.connect(self._reset_stats)
        read_box = QGroupBox("Reading")
        read_form = QFormLayout(read_box)
        read_form.addRow(self.hold_button)
        read_form.addRow(self.reset_button)

        self.hint_label = QLabel(
            "Modes follow the live reading, so pressing the F‑keys on the device "
            "highlights the mode here too. The device's own soft‑key bar keeps "
            "its previous entry, so trust this panel and the readout."
        )
        self.hint_label.setWordWrap(True)

        column = QVBoxLayout()
        column.addWidget(mode_box)
        column.addWidget(read_box)
        column.addWidget(self.hint_label)
        column.addStretch()
        self.controls_widget = QWidget()
        self.controls_widget.setLayout(column)
        self.controls_widget.setMaximumWidth(280)

        layout = QHBoxLayout(self)
        layout.addLayout(readout, stretch=1)
        layout.addWidget(self.controls_widget)

        self._refresh_modes()
        self._set_enabled(False)

    # --------------------------------------------------------------- mode set

    def _refresh_modes(self):
        """Enable/disable buttons and mark the active mode."""
        for mode, button in self._mode_buttons.items():
            button.setEnabled(self.device is not None)
            button.setChecked(mode == self._active_mode)

    def _confirm_current_mode(self, mode: str) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Switch to current measurement?")
        box.setText(f"Switch the multimeter to <b>{mode}</b>?")
        box.setInformativeText(
            "A current range makes the input a low-impedance shunt. It must be "
            "wired in series with the load — connecting it across a voltage "
            "source shorts that source and can blow the fuse.\n\n"
            "Check the leads are in the correct jacks before continuing."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.setEscapeButton(QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _set_mode(self, mode: str):
        """The only place the GUI changes the measurement mode."""
        if mode in CURRENT_MODES and not self._confirm_current_mode(mode):
            self._refresh_modes()  # undo the clicked button's checked state
            return
        if not self._apply(lambda d: d.set_dmm_mode(mode)):
            self._refresh_modes()
            return
        self._active_mode = mode
        # Wait for the device to reach this mode before live-follow resumes, so
        # in-flight old-mode frames do not bounce the highlight back.
        self._expecting = mode
        self._expecting_left = 30
        self._pending_slot = None
        self._pending_count = 0
        # A new mode means new units; keeping old extremes or history would mix
        # volts and ohms on one axis.
        self._reset_stats()
        self.history.clear()
        self._refresh_modes()

    def focus_mode_selector(self):
        """Focus the active mode's button, or the first one."""
        button = self._mode_buttons.get(self._active_mode)
        if button is None:
            button = next(iter(self._mode_buttons.values()))
        button.setFocus()

    def export_history(self):
        self.history.export(self)

    # ---------------------------------------------------------------- reading

    def _reset_stats(self):
        self.stats.reset()
        self.min_label.setText("--")
        self.max_label.setText("--")
        self.count_label.setText("0")

    def show_reading(self, reading):
        """Display a reading from the window's DeviceMonitor."""
        if self.hold_button.isChecked():
            return
        if reading is None:
            self.value_label.setText("—")
            self.mode_label.setText("no reading")
            return
        self._follow_device_mode(reading)
        self.value_label.setText(reading.formatted())
        self.mode_label.setText(reading.mode)
        self.stats.update(reading)
        self.min_label.setText(self.stats.format(self.stats.min))
        self.max_label.setText(self.stats.format(self.stats.max))
        self.count_label.setText(str(self.stats.count))
        self.history.add(reading)

    def _follow_device_mode(self, reading):
        """Move the highlight to the device's real mode, from the reading.

        Follows a change whether the app or the physical keys caused it. A new
        mode must persist for two readings before we act, so a torn mid-switch
        frame does not flicker the highlight. A mode change resets stats/history.
        """
        slot = _slot_for_reading(reading)
        if self._expecting is not None:
            self._expecting_left -= 1
            if slot == self._expecting or self._expecting_left <= 0:
                self._expecting = None
            return
        if slot is None or slot == self._active_mode:
            self._pending_slot = None
            self._pending_count = 0
            return
        if slot == self._pending_slot:
            self._pending_count += 1
        else:
            self._pending_slot = slot
            self._pending_count = 1
        if self._pending_count < 2:
            return
        self._active_mode = slot
        self._pending_slot = None
        self._pending_count = 0
        self._reset_stats()
        self.history.clear()
        self._refresh_modes()

    # ----------------------------------------------------------- device state

    def _on_device_changed(self, device: Dmso2d72 | None):
        if device is None:
            self.hold_button.setChecked(False)
            self.value_label.setText("--")
            self.mode_label.setText("")
            self.history.stop_logging()
        # The device's actual mode is unknown until we read it, and we do not
        # push one on connect (a current range would be a low-impedance hazard),
        # so nothing is marked selected until a reading arrives or the user picks.
        self._active_mode = None
        self._refresh_modes()

    def _set_enabled(self, on: bool):
        super()._set_enabled(on)
        self._refresh_modes()

    def apply_theme(self, theme):
        self.history.apply_theme(theme)

    def save_settings(self, settings):
        settings.setValue("dmm/history_window", self.history.window_combo.currentText())

    def restore_settings(self, settings):
        from .. import settings as st
        from .device_tab import _set_text

        _set_text(
            self.history.window_combo, st.get_str(settings, "dmm/history_window", "5 min")
        )
        self._active_mode = None
        self._refresh_modes()

    def shutdown(self):
        self.history.stop_logging()
