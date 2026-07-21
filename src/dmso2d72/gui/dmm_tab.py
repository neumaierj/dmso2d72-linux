"""Multimeter tab: live readings, mode selection, min/max and history.

Both the readout and the mode-set command were reverse-engineered from the
firmware and verified against the device (see re/DMM_PROTOCOL.md). Every mode
is decoded, and the mode can be selected from here.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
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
from ..capture import DmmWorker
from ..device import Dmso2d72
from ..dmm_stats import DmmStats
from .device_tab import DeviceTab, _set_text
from .dmm_history import DmmHistoryView

# Modes that turn the input into a low-impedance shunt. Selecting one by
# accident while the leads sit across a voltage source would short it, so these
# are confirmed first.
CURRENT_MODES = ("AC A", "DC A", "AC mA", "DC mA")

# The device's own soft-key layout, transcribed from the hardware: four pages,
# three slots each (F1/F2/F3), F4 cycles pages. Each slot is
# (button label as shown on the device, protocol.DMM_MODES key) or None for the
# one empty slot. Selecting a mode here uses the same func=0x0001 command the
# device's own keys use, but the device's on-screen highlight cannot be driven
# over USB (firmware limitation, see re/DMM_PROTOCOL.md), so this panel — not
# the device's bar — reflects what the app selected.
PAGES = (
    (("DC V", "DC V"), ("OHM", "Resistance"), ("Buzzer", "Continuity")),
    (("DC A", "DC A"), ("DC mA", "DC mA"), ("DC mV", "DC mV")),
    (("AC V", "AC V"), ("AC A", "AC A"), ("AC mA", "AC mA")),
    (("Diode", "Diode"), ("Cap", "Capacitance"), None),
)


def _page_of(mode: str) -> int:
    """The page index (0-3) that holds a given mode key, or 0 if not found."""
    for i, page in enumerate(PAGES):
        if any(slot and slot[1] == mode for slot in page):
            return i
    return 0


class DmmTab(DeviceTab):
    device_screen = p.SCREEN_DMM
    reading_taken = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: DmmWorker | None = None
        self.stats = DmmStats()
        self._sent_mode: str | None = None
        self._page = 0

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

        # Soft-key panel mirroring the device: a page indicator + F4 to page,
        # and three mode keys for the current page. Clicking a key selects that
        # specific mode immediately (direct select), so any visible mode is one
        # click away; F4 only changes which three are shown.
        self.page_label = QLabel()
        self.page_label.setAlignment(Qt.AlignCenter)
        self.page_button = QPushButton("F4 ▶")
        self.page_button.setToolTip("Next page of modes")
        self.page_button.clicked.connect(self._next_page)

        page_row = QHBoxLayout()
        page_row.addWidget(self.page_label, stretch=1)
        page_row.addWidget(self.page_button)

        self.slot_buttons = []
        slot_row = QHBoxLayout()
        for slot in range(3):
            button = QPushButton()
            button.setCheckable(True)
            # The selected mode gets the theme's highlight colour, so which mode
            # is active reads at a glance in either theme.
            button.setStyleSheet(
                "QPushButton:checked { background-color: palette(highlight);"
                " color: palette(highlighted-text); font-weight: bold; }"
            )
            button.clicked.connect(lambda _=False, s=slot: self._select_slot(s))
            self.slot_buttons.append(button)
            slot_row.addWidget(button)

        mode_box = QGroupBox("Measurement mode")
        mode_layout = QVBoxLayout(mode_box)
        mode_layout.addLayout(page_row)
        mode_layout.addLayout(slot_row)

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
            "These keys mirror the device's own soft-key pages. Selecting a mode "
            "takes effect immediately, but the device's on-screen bar keeps "
            "highlighting its previous entry — trust this panel and the readout, "
            "not the bar on the device."
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

        self._refresh_panel()
        self._set_enabled(False)

    # --------------------------------------------------------------- mode set

    def _refresh_panel(self):
        """Show the current page's three modes and mark the app's selected one."""
        self.page_label.setText(f"Page {self._page + 1}/{len(PAGES)}")
        for slot, button in enumerate(self.slot_buttons):
            entry = PAGES[self._page][slot]
            if entry is None:
                button.setText("—")
                button.setEnabled(False)
                button.setChecked(False)
                continue
            label, mode = entry
            button.setText(label)
            button.setEnabled(self.device is not None)
            button.setChecked(mode == self._sent_mode)

    def _next_page(self):
        self._page = (self._page + 1) % len(PAGES)
        self._refresh_panel()

    def _select_slot(self, slot: int):
        entry = PAGES[self._page][slot]
        if entry is None:
            return
        self._set_mode(entry[1])

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
            self._refresh_panel()  # undo the clicked button's checked state
            return
        if not self._apply(lambda d: d.set_dmm_mode(mode)):
            self._refresh_panel()
            return
        self._sent_mode = mode
        # A new mode means new units; keeping old extremes or history would mix
        # volts and ohms on one axis.
        self._reset_stats()
        self.history.clear()
        self._refresh_panel()

    def focus_mode_selector(self):
        """Bring the app's selected mode into view and focus its key."""
        if self._sent_mode is not None:
            self._page = _page_of(self._sent_mode)
            self._refresh_panel()
        self.slot_buttons[0].setFocus()

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
        # The device's actual mode is unknown until we read it, and we do not
        # push one on connect (a current range would be a low-impedance hazard),
        # so nothing is marked as selected until the user picks a mode.
        self._sent_mode = None
        self._refresh_panel()

    def _set_enabled(self, on: bool):
        super()._set_enabled(on)
        # The empty slot stays disabled regardless, so re-derive per-slot state.
        self._refresh_panel()

    def apply_theme(self, theme):
        self.history.apply_theme(theme)

    def save_settings(self, settings):
        settings.setValue("dmm/page", self._page)
        settings.setValue("dmm/history_window", self.history.window_combo.currentText())

    def restore_settings(self, settings):
        from .. import settings as st

        self._page = st.get_int(settings, "dmm/page", 0) % len(PAGES)
        _set_text(
            self.history.window_combo, st.get_str(settings, "dmm/history_window", "5 min")
        )
        # Deliberately no mode is pushed on connect: silently restoring a current
        # range would put a low-impedance input on the probes without the user
        # asking. Nothing is marked selected until the user picks a mode.
        self._sent_mode = None
        self._refresh_panel()

    def shutdown(self):
        self._stop_worker()
        self.history.stop_logging()
