"""Multimeter tab: live readings over USB.

The DMM readout protocol was reverse-engineered by hardware probing (see
re/DMM_PROTOCOL.md). The numeric value is decoded for any mode; the unit label
is currently known for DC volts, other modes show the value with a note.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import protocol as p
from ..capture import DmmWorker
from ..device import DeviceError, Dmso2d72


class DmmTab(QWidget):
    device_lost = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.device: Dmso2d72 | None = None
        self.worker: DmmWorker | None = None

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

        self.hint_label = QLabel(
            "Set the measurement mode (DC/AC volts, resistance, ...) on the device "
            "itself; readings appear here. The value is read over USB; the unit is "
            "currently labelled for DC volts."
        )
        self.hint_label.setWordWrap(True)
        self.hint_label.setAlignment(Qt.AlignCenter)

        self.start_button = QPushButton("Start reading")
        self.start_button.setCheckable(True)
        self.start_button.toggled.connect(self._toggle)
        self.switch_button = QPushButton("Switch device to multimeter screen")
        self.switch_button.clicked.connect(self._switch_to_dmm)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.switch_button)
        buttons.addWidget(self.start_button)
        buttons.addStretch()

        layout = QVBoxLayout(self)
        layout.addStretch()
        layout.addWidget(self.value_label)
        layout.addWidget(self.mode_label)
        layout.addSpacing(20)
        layout.addLayout(buttons)
        layout.addSpacing(20)
        layout.addWidget(self.hint_label)
        layout.addStretch()

        self.set_device(None)

    def _switch_to_dmm(self):
        if self.device is None:
            return
        try:
            self.device.set_screen(p.SCREEN_DMM)
        except DeviceError as e:
            self.device_lost.emit(str(e))

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
        if reading is None:
            self.value_label.setText("—")
            self.mode_label.setText("device not in multimeter mode")
            return
        self.value_label.setText(reading.formatted())
        if reading.mode == "unknown":
            self.mode_label.setText("unknown mode (unit not decoded yet)")
        else:
            self.mode_label.setText(reading.mode)

    def set_device(self, device: Dmso2d72 | None):
        self._stop_worker()
        self.device = device
        self.start_button.setEnabled(device is not None)
        self.switch_button.setEnabled(device is not None)
        if device is None:
            self.start_button.setChecked(False)
            self.value_label.setText("--")
            self.mode_label.setText("")

    def shutdown(self):
        self._stop_worker()