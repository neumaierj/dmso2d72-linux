"""Multimeter tab: live readings, mode selection, min/max and history.

Both the readout and the mode-set command were reverse-engineered from the
firmware and verified against the device (see re/DMM_PROTOCOL.md). Every mode
is decoded, and the mode can be selected from here.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
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
from ..capture import DmmWorker
from ..device import Dmso2d72
from ..dmm_stats import DmmStats
from .device_tab import DeviceTab, _set_text
from .dmm_history import DmmHistoryView

# Modes that turn the input into a low-impedance shunt. Selecting one by
# accident while the leads sit across a voltage source would short it, so these
# are confirmed first.
CURRENT_MODES = ("AC A", "DC A", "AC mA", "DC mA")

# Grouped for the selector rather than alphabetically, so related ranges sit
# together the way they do on the device.
MODE_ORDER = (
    "DC V",
    "AC V",
    "DC mV",
    "DC A",
    "AC A",
    "DC mA",
    "AC mA",
    "Resistance",
    "Continuity",
    "Diode",
    "Capacitance",
)


class _NoScrollComboBox(QComboBox):
    """A combo that ignores the wheel, so scrolling the page cannot arm a mode."""

    def wheelEvent(self, event):
        event.ignore()


class DmmTab(DeviceTab):
    reading_taken = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: DmmWorker | None = None
        self.stats = DmmStats()
        self._sent_mode: str | None = None

        self.value_label = QLabel("--")
        self.value_label.setAlignment(Qt.AlignCenter)
        font = QFont()
        font.setPointSize(48)
        font.setBold(True)
        self.value_label.setFont(font)

        self.mode_label = QLabel("")
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

        self.history = DmmHistoryView()

        # The reading is what this tab is for, so it gets as much room as the
        # history and sits centred rather than pinned to the top.
        reading_panel = QVBoxLayout()
        reading_panel.addStretch()
        reading_panel.addWidget(self.value_label)
        reading_panel.addWidget(self.mode_label)
        reading_panel.addSpacing(24)
        reading_panel.addLayout(stats_grid)
        reading_panel.addStretch()
        reading_widget = QWidget()
        reading_widget.setLayout(reading_panel)

        readout = QVBoxLayout()
        readout.addWidget(reading_widget, stretch=1)
        readout.addWidget(self.history, stretch=1)

        # ------------------------------------------------------------ controls

        self.mode_combo = _NoScrollComboBox()
        self.mode_combo.addItems(MODE_ORDER)
        self.mode_combo.setCurrentText("DC V")
        self.mode_combo.currentTextChanged.connect(self._update_set_button)
        self.set_mode_button = QPushButton("Set mode")
        self.set_mode_button.clicked.connect(self._set_mode)

        mode_box = QGroupBox("Measurement mode")
        mode_form = QFormLayout(mode_box)
        mode_form.addRow(self.mode_combo)
        mode_form.addRow(self.set_mode_button)

        self.start_button = QPushButton("Start reading")
        self.start_button.setCheckable(True)
        self.start_button.toggled.connect(self._toggle)
        self.hold_button = QPushButton("Hold")
        self.hold_button.setCheckable(True)
        self.hold_button.setToolTip("Freeze the display; polling continues")
        self.reset_button = QPushButton("Reset min/max")
        self.reset_button.clicked.connect(self._reset_stats)
        self.switch_button = QPushButton("Show multimeter on device")
        self.switch_button.clicked.connect(self._switch_to_dmm)

        read_box = QGroupBox("Reading")
        read_form = QFormLayout(read_box)
        read_form.addRow(self.start_button)
        read_form.addRow(self.hold_button)
        read_form.addRow(self.reset_button)
        read_form.addRow(self.switch_button)

        self.hint_label = QLabel(
            "Changing the mode from here takes effect immediately, but the "
            "device's own soft-key bar keeps highlighting the previous entry — "
            "trust this readout, not the bottom line on the device."
        )
        self.hint_label.setWordWrap(True)

        column = QVBoxLayout()
        column.addWidget(mode_box)
        column.addWidget(read_box)
        column.addWidget(self.hint_label)
        column.addStretch()
        self.controls_widget = QWidget()
        self.controls_widget.setLayout(column)
        self.controls_widget.setMaximumWidth(260)

        layout = QHBoxLayout(self)
        layout.addLayout(readout, stretch=1)
        layout.addWidget(self.controls_widget)

        self._update_set_button()
        self._set_enabled(False)

    # --------------------------------------------------------------- mode set

    def _update_set_button(self):
        pending = self.mode_combo.currentText()
        self.set_mode_button.setEnabled(pending != self._sent_mode)
        self.set_mode_button.setText(
            "Mode is set" if pending == self._sent_mode else f"Set to {pending}"
        )

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

    def _set_mode(self):
        """The only place the GUI changes the measurement mode."""
        mode = self.mode_combo.currentText()
        if mode in CURRENT_MODES and not self._confirm_current_mode(mode):
            return
        if not self._apply(lambda d: d.set_dmm_mode(mode)):
            return
        self._sent_mode = mode
        # A new mode means new units; keeping old extremes or history would mix
        # volts and ohms on one axis.
        self._reset_stats()
        self.history.clear()
        self._update_set_button()

    def focus_mode_selector(self):
        self.mode_combo.setFocus()
        self.mode_combo.showPopup()

    def export_history(self):
        self.history.export(self)

    # ---------------------------------------------------------------- reading

    def _switch_to_dmm(self):
        self._apply(lambda d: d.set_screen(p.SCREEN_DMM))

    def _reset_stats(self):
        self.stats.reset()
        self.min_label.setText("--")
        self.max_label.setText("--")
        self.count_label.setText("0")

    def _toggle(self, on: bool):
        self.start_button.setText("Stop reading" if on else "Start reading")
        if on:
            if self.device is None:
                self.start_button.setChecked(False)
                return
            self.worker = DmmWorker(self.device)
            self.worker.reading.connect(self._show_reading)
            self.worker.failed.connect(self._worker_failed)
            self.worker.start()
        else:
            self._stop_worker()
            self.value_label.setText("--")
            self.mode_label.setText("")

    def _stop_worker(self):
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(2000)
            self.worker = None

    def _worker_failed(self, message: str):
        self.start_button.setChecked(False)
        self.device_lost.emit(message)

    def _show_reading(self, reading):
        if self.hold_button.isChecked():
            return
        if reading is None:
            self.value_label.setText("—")
            self.mode_label.setText("device is not on the multimeter screen")
            return
        self.value_label.setText(reading.formatted())
        self.mode_label.setText(reading.mode)
        self.stats.update(reading)
        self.min_label.setText(self.stats.format(self.stats.min))
        self.max_label.setText(self.stats.format(self.stats.max))
        self.count_label.setText(str(self.stats.count))
        self.history.add(reading)
        self.reading_taken.emit(reading)

    # ----------------------------------------------------------- device state

    def _on_device_changed(self, device: Dmso2d72 | None):
        if device is None:
            self.start_button.setChecked(False)
            self.hold_button.setChecked(False)
            self.value_label.setText("--")
            self.mode_label.setText("")
            self.history.stop_logging()
            # The device may be set to anything when it comes back.
            self._sent_mode = None
            self._update_set_button()

    def apply_theme(self, theme):
        self.history.apply_theme(theme)

    def save_settings(self, settings):
        settings.setValue("dmm/mode", self.mode_combo.currentText())
        settings.setValue("dmm/history_window", self.history.window_combo.currentText())

    def restore_settings(self, settings):
        from .. import settings as st

        _set_text(self.mode_combo, st.get_str(settings, "dmm/mode", "DC V"))
        _set_text(
            self.history.window_combo, st.get_str(settings, "dmm/history_window", "5 min")
        )
        # Deliberately not pushed to the device on connect: silently restoring a
        # current range would put a low-impedance input on the probes without
        # the user asking. The combo shows it; the button still has to be used.
        self._sent_mode = None
        self._update_set_button()

    def shutdown(self):
        self._stop_worker()
        self.history.stop_logging()
