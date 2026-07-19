"""Main window: device connection state and the three function tabs."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QMainWindow, QPushButton, QTabWidget

from ..device import DeviceError, DeviceNotFound, Dmso2d72
from .awg_tab import AwgTab
from .dmm_tab import DmmTab
from .scope_tab import ScopeTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DMSO2D72 — Joy-IT / Hantek handheld oscilloscope")
        self.resize(1100, 700)
        self.device: Dmso2d72 | None = None

        self.scope_tab = ScopeTab()
        self.awg_tab = AwgTab()
        self.dmm_tab = DmmTab()

        tabs = QTabWidget()
        tabs.addTab(self.scope_tab, "Oscilloscope")
        tabs.addTab(self.awg_tab, "Signal generator")
        tabs.addTab(self.dmm_tab, "Multimeter")
        self.setCentralWidget(tabs)

        self.status_label = QLabel()
        rescan = QPushButton("Rescan")
        rescan.clicked.connect(self.rescan)
        self.statusBar().addWidget(self.status_label, stretch=1)
        self.statusBar().addPermanentWidget(rescan)

        for tab in (self.scope_tab, self.awg_tab, self.dmm_tab):
            tab.device_lost.connect(self._on_device_lost)

        self.rescan()

    def rescan(self):
        if self.device is not None:
            return
        try:
            self.device = Dmso2d72()
        except DeviceNotFound:
            self._set_device(None, "No device found — plug in the DMSO2D72 and click Rescan.")
            return
        except DeviceError as e:
            self._set_device(None, str(e))
            return
        self._set_device(self.device, f"Connected: {self.device.product}")

    def _on_device_lost(self, message: str):
        if self.device is not None:
            self.device.close()
            self.device = None
        self._set_device(None, f"Device connection lost: {message}")

    def _set_device(self, device: Dmso2d72 | None, status: str):
        self.device = device
        self.status_label.setText(status)
        for tab in (self.scope_tab, self.awg_tab, self.dmm_tab):
            tab.set_device(device)

    def closeEvent(self, event):
        self.scope_tab.shutdown()
        if self.device is not None:
            self.device.close()
        super().closeEvent(event)
