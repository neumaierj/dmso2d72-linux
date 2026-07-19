"""Multimeter tab (experimental).

The DMM readout protocol of the 2D72 has not been publicly reverse-engineered
yet, so this tab can only switch the device to its multimeter screen. See
tools/usb_capture.md for how to help capture the missing protocol.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from .. import protocol as p
from ..device import DeviceError, Dmso2d72


class DmmTab(QWidget):
    device_lost = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.device: Dmso2d72 | None = None

        info = QLabel(
            "<h3>Multimeter — experimental</h3>"
            "<p>Reading multimeter values over USB is not supported yet: unlike the "
            "oscilloscope and signal generator, the DMM readout protocol of this "
            "device has not been publicly reverse-engineered.</p>"
            "<p>The button below switches the instrument to its multimeter screen; "
            "readings are then taken on the device itself.</p>"
            "<p>If you want to help fill this gap, capture the USB traffic of the "
            "official Windows software while its multimeter view is open — see "
            "<code>tools/usb_capture.md</code> in this repository for a guide.</p>"
        )
        info.setWordWrap(True)

        self.switch_button = QPushButton("Switch device to multimeter screen")
        self.switch_button.clicked.connect(self._switch_to_dmm)

        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addWidget(self.switch_button)
        layout.addStretch()

        self.set_device(None)

    def _switch_to_dmm(self):
        if self.device is None:
            return
        try:
            self.device.set_screen(p.SCREEN_DMM)
        except DeviceError as e:
            self.device_lost.emit(str(e))

    def set_device(self, device: Dmso2d72 | None):
        self.device = device
        self.switch_button.setEnabled(device is not None)
