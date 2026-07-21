"""Main window: measurement views (Scope/DMM), a parallel AWG panel, the
device monitor that mirrors the hardware, menus and theme."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
)

from .. import protocol as p
from .. import settings as st
from ..capture import DeviceMonitor
from ..device import DeviceError, DeviceNotFound, Dmso2d72
from .awg_tab import AwgTab
from .dmm_tab import DmmTab
from .scope_tab import ScopeTab
from .theme import apply_to_app


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DMSO2D72 — Joy-IT / Hantek handheld oscilloscope")
        self.resize(1200, 700)
        self.device: Dmso2d72 | None = None
        self.monitor: DeviceMonitor | None = None
        self._losing_device = False
        # Guards for the two directions of screen sync (see _sync_measurement).
        self._syncing_from_device = False
        self._suppress_measurement_sync = False

        self.scope_tab = ScopeTab()
        self.dmm_tab = DmmTab()
        self.awg_tab = AwgTab()
        # Every view that talks to the device, for the connect/theme/settings loops.
        self.tabs = (self.scope_tab, self.dmm_tab, self.awg_tab)

        # The device shows one measurement at a time: Scope or DMM.
        self.measure_tabs = QTabWidget()
        self.measure_tabs.addTab(self.scope_tab, "Oscilloscope")
        self.measure_tabs.addTab(self.dmm_tab, "Multimeter")

        # The signal generator runs in parallel, so it is a side panel, not a
        # tab. Hidden until the user shows it.
        self.awg_tab.set_apply_hook(self._apply_awg)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.measure_tabs)
        self.splitter.addWidget(self.awg_tab)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.awg_tab.setVisible(False)
        self.setCentralWidget(self.splitter)

        self.status_label = QLabel()
        rescan = QPushButton("Rescan")
        rescan.clicked.connect(self.rescan)
        self.statusBar().addWidget(self.status_label, stretch=1)
        self.statusBar().addPermanentWidget(rescan)

        for tab in self.tabs:
            tab.device_lost.connect(self._on_device_lost)
        self.measure_tabs.currentChanged.connect(lambda _: self._sync_measurement())

        self._build_menus()

        # Restore before connecting, so restoring writes nothing to the device.
        settings = st.app_settings()
        self._restore_window(settings)
        for tab in self.tabs:
            tab.restore_settings(settings)
        self._set_theme(st.get_str(settings, "ui/theme", "system"), save=False)

        self.rescan()

    # -------------------------------------------------------------------- menus

    def _build_menus(self):
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        self.export_waveform_action = file_menu.addAction("Export waveform CSV…")
        self.export_waveform_action.setShortcut(QKeySequence("Ctrl+E"))
        self.export_waveform_action.triggered.connect(self.scope_tab._export_csv)
        self.export_history_action = file_menu.addAction("Export multimeter history CSV…")
        self.export_history_action.triggered.connect(self.dmm_tab.export_history)
        file_menu.addSeparator()
        quit_action = file_menu.addAction("&Quit")
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)

        device_menu = bar.addMenu("&Device")
        rescan_action = device_menu.addAction("&Rescan")
        rescan_action.setShortcut(QKeySequence("F5"))
        rescan_action.triggered.connect(self.rescan)
        device_menu.addSeparator()
        self.push_action = device_menu.addAction("&Push settings to device")
        self.push_action.setShortcut(QKeySequence("Ctrl+Shift+P"))
        self.push_action.setToolTip("Re-send the visible view's settings")
        self.push_action.triggered.connect(self._push_current)

        scope_menu = bar.addMenu("&Scope")
        self.run_action = scope_menu.addAction("&Run")
        self.run_action.setCheckable(True)
        self.run_action.setShortcut(QKeySequence("Ctrl+R"))
        _bind_toggle(self.run_action, self.scope_tab.run_button)
        self.live_action = scope_menu.addAction("&Live view")
        self.live_action.setCheckable(True)
        _bind_toggle(self.live_action, self.scope_tab.live_button)
        single_action = scope_menu.addAction("&Single capture")
        single_action.setShortcut(QKeySequence("Ctrl+Return"))
        single_action.triggered.connect(self.scope_tab.single_button.click)

        dmm_menu = bar.addMenu("&Multimeter")
        self.hold_action = dmm_menu.addAction("&Hold")
        self.hold_action.setCheckable(True)
        self.hold_action.setShortcut(QKeySequence("Ctrl+H"))
        _bind_toggle(self.hold_action, self.dmm_tab.hold_button)
        reset_action = dmm_menu.addAction("Reset &min/max")
        reset_action.setShortcut(QKeySequence("Ctrl+Shift+R"))
        reset_action.triggered.connect(self.dmm_tab.reset_button.click)
        dmm_menu.addSeparator()
        mode_action = dmm_menu.addAction("Set &mode…")
        mode_action.setShortcut(QKeySequence("Ctrl+M"))
        mode_action.triggered.connect(self.dmm_tab.focus_mode_selector)

        view_menu = bar.addMenu("&View")
        osc = view_menu.addAction("&Oscilloscope")
        osc.setShortcut(QKeySequence("Ctrl+1"))
        osc.triggered.connect(lambda: self.measure_tabs.setCurrentWidget(self.scope_tab))
        mm = view_menu.addAction("&Multimeter")
        mm.setShortcut(QKeySequence("Ctrl+2"))
        mm.triggered.connect(lambda: self.measure_tabs.setCurrentWidget(self.dmm_tab))
        self.awg_action = view_menu.addAction("&Signal generator panel")
        self.awg_action.setCheckable(True)
        self.awg_action.setShortcut(QKeySequence("Ctrl+3"))
        self.awg_action.toggled.connect(self._toggle_awg)
        view_menu.addSeparator()
        theme_menu = view_menu.addMenu("&Theme")
        self.theme_group = QActionGroup(self)
        self.theme_group.setExclusive(True)
        self.theme_actions = {}
        for name, label in (("dark", "Dark"), ("light", "Light"), ("system", "Follow system")):
            action = theme_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda _=False, n=name: self._set_theme(n))
            self.theme_group.addAction(action)
            self.theme_actions[name] = action

        help_menu = bar.addMenu("&Help")
        help_menu.addAction("&About").triggered.connect(self._about)

    def _about(self):
        QMessageBox.about(
            self,
            "About DMSO2D72",
            "<b>DMSO2D72</b><br>"
            "Linux interface for the Joy-IT DMSO2D72 / Hantek 2D72.<br><br>"
            "The USB protocol, including the multimeter readout and mode "
            "selection, was reverse-engineered; see re/DMM_PROTOCOL.md.<br><br>"
            "GPL-3.0-or-later.",
        )

    # -------------------------------------------------------------------- theme

    def _set_theme(self, name: str, save: bool = True):
        app = QApplication.instance()
        theme = apply_to_app(app, name)
        for tab in self.tabs:
            tab.apply_theme(theme)
        action = self.theme_actions.get(name)
        if action is not None and not action.isChecked():
            action.setChecked(True)
        if save:
            st.app_settings().setValue("ui/theme", name)

    # --------------------------------------------------------------------- awg

    def _toggle_awg(self, show: bool):
        self.awg_tab.setVisible(show)
        if show and self.device is not None:
            # Push the generator settings once, on its own screen, so the device
            # matches the panel without fragmenting the measurement.
            if not self.awg_tab._settings_pushed:
                self._apply_awg([self.awg_tab.push_fn])

    def _current_measurement_screen(self) -> int:
        return (
            p.SCREEN_SCOPE
            if self.measure_tabs.currentWidget() is self.scope_tab
            else p.SCREEN_DMM
        )

    def _apply_awg(self, fns):
        """Apply AWG commands on the AWG screen, so they never fragment the
        measurement screen, then return. Suppresses the device→app follow for
        the moment the device sits on the AWG screen (which reads as 'dmm')."""
        if self.device is None:
            return
        measurement = self._current_measurement_screen()
        self._suppress_measurement_sync = True
        try:
            self.device.set_screen(p.SCREEN_AWG)
            for fn in fns:
                fn(self.device)
            self.device.set_screen(measurement)
        except DeviceError as e:
            self._on_device_lost(str(e))
            return
        self.awg_tab._settings_pushed = True
        # Absorb the monitor poll that may have caught the AWG screen mid-bounce.
        QTimer.singleShot(400, lambda: setattr(self, "_suppress_measurement_sync", False))

    # ------------------------------------------------------------------- device

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

    def _push_current(self):
        """Re-send the visible measurement view's settings (menu action)."""
        if self.device is None:
            return
        tab = self.measure_tabs.currentWidget()
        tab._settings_pushed = False
        self.status_label.setText("Configuring device…")
        self._sync_measurement()
        if self.device is not None:
            self.status_label.setText(f"Connected: {self.device.product}")

    def _sync_measurement(self):
        """Show the active measurement on the device and configure it once.

        Selecting the screen first (a clean redraw) and pushing only the active
        view's settings keeps the device consistent without fragmenting it.
        Skips the screen write when the change came from the device itself.
        """
        if self.device is None:
            return
        tab = self.measure_tabs.currentWidget()
        if not self._syncing_from_device and tab.device_screen is not None:
            try:
                self.device.set_screen(tab.device_screen)
            except DeviceError as e:
                self._on_device_lost(str(e))
                return
        tab.activate()

    def _on_device_measurement(self, measurement: str):
        """The device switched measurement (physical button) — follow it."""
        if self._suppress_measurement_sync or self.device is None:
            return
        target = self.scope_tab if measurement == "scope" else self.dmm_tab
        if self.measure_tabs.currentWidget() is target:
            return
        self._syncing_from_device = True
        self.measure_tabs.setCurrentWidget(target)
        self._syncing_from_device = False

    def _on_device_lost(self, message: str):
        if self._losing_device:
            return
        self._losing_device = True
        QTimer.singleShot(0, lambda: self._handle_device_lost(message))

    def _handle_device_lost(self, message: str):
        self._losing_device = False
        if self.device is None:
            return
        self._stop_monitor()
        self.device.close()
        self.device = None
        self._set_device(None, f"Device connection lost: {message}")

    def _set_device(self, device: Dmso2d72 | None, status: str):
        self.device = device
        self.status_label.setText(status)
        for tab in self.tabs:
            tab.set_device(device)
        for action in (self.push_action, self.export_history_action):
            action.setEnabled(device is not None)
        if device is not None:
            self._sync_measurement()
            self._start_monitor(device)

    def _start_monitor(self, device: Dmso2d72):
        self._stop_monitor()
        self.monitor = DeviceMonitor(device)
        self.monitor.measurement_changed.connect(self._on_device_measurement)
        self.monitor.reading.connect(self.dmm_tab.show_reading)
        self.monitor.failed.connect(self._on_device_lost)
        self.monitor.start()

    def _stop_monitor(self):
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor.wait(2000)
            self.monitor = None

    # ---------------------------------------------------------------- lifecycle

    def _restore_window(self, settings):
        geometry = settings.value("ui/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        state = settings.value("ui/window_state")
        if state is not None:
            self.restoreState(state)
        self.measure_tabs.setCurrentIndex(st.get_int(settings, "ui/measurement", 0))
        if st.get_bool(settings, "ui/awg_shown", False):
            self.awg_action.setChecked(True)  # toggles the panel visible

    def closeEvent(self, event):
        settings = st.app_settings()
        settings.setValue("ui/geometry", self.saveGeometry())
        settings.setValue("ui/window_state", self.saveState())
        settings.setValue("ui/measurement", self.measure_tabs.currentIndex())
        settings.setValue("ui/awg_shown", self.awg_tab.isVisible())
        for tab in self.tabs:
            tab.save_settings(settings)
        settings.sync()
        self._stop_monitor()
        for tab in self.tabs:
            tab.shutdown()
        if self.device is not None:
            self.device.close()
        super().closeEvent(event)


def _bind_toggle(action, button) -> None:
    """Keep a checkable menu action and its button as one piece of state."""
    action.setChecked(button.isChecked())
    action.toggled.connect(lambda on: button.setChecked(on))
    button.toggled.connect(lambda on: action.setChecked(on))
