"""Main window: device connection state, menus, theme and the function tabs."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
)

from .. import protocol as p
from .. import settings as st
from ..device import DeviceError, DeviceNotFound, Dmso2d72
from .awg_tab import AwgTab
from .dmm_tab import DmmTab
from .scope_tab import ScopeTab
from .theme import apply_to_app


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DMSO2D72 — Joy-IT / Hantek handheld oscilloscope")
        self.resize(1100, 700)
        self.device: Dmso2d72 | None = None
        self._losing_device = False

        self.scope_tab = ScopeTab()
        self.awg_tab = AwgTab()
        self.dmm_tab = DmmTab()
        self.tabs = (self.scope_tab, self.awg_tab, self.dmm_tab)

        self.tab_widget = QTabWidget()
        self.tab_widget.addTab(self.scope_tab, "Oscilloscope")
        self.tab_widget.addTab(self.awg_tab, "Signal generator")
        self.tab_widget.addTab(self.dmm_tab, "Multimeter")
        self.setCentralWidget(self.tab_widget)

        self.status_label = QLabel()
        rescan = QPushButton("Rescan")
        rescan.clicked.connect(self.rescan)
        self.statusBar().addWidget(self.status_label, stretch=1)
        self.statusBar().addPermanentWidget(rescan)

        for tab in self.tabs:
            tab.device_lost.connect(self._on_device_lost)

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
        screen_menu = device_menu.addMenu("Device &screen")
        for label, value in (
            ("Oscilloscope", p.SCREEN_SCOPE),
            ("Multimeter", p.SCREEN_DMM),
            ("Signal generator", p.SCREEN_AWG),
        ):
            screen_menu.addAction(label).triggered.connect(
                lambda _=False, v=value: self._set_screen(v)
            )
        device_menu.addSeparator()
        self.push_action = device_menu.addAction("&Push settings to device")
        self.push_action.setShortcut(QKeySequence("Ctrl+Shift+P"))
        self.push_action.setToolTip("Re-send every setting, e.g. after changing them on the device")
        self.push_action.triggered.connect(self._push_all)

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
        self.read_action = dmm_menu.addAction("Start &reading")
        self.read_action.setCheckable(True)
        self.read_action.setShortcut(QKeySequence("Ctrl+D"))
        _bind_toggle(self.read_action, self.dmm_tab.start_button)
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
        view_menu.addSeparator()
        for i, label in enumerate(("Oscilloscope", "Signal generator", "Multimeter")):
            action = view_menu.addAction(label)
            action.setShortcut(QKeySequence(f"Ctrl+{i + 1}"))
            action.triggered.connect(lambda _=False, idx=i: self.tab_widget.setCurrentIndex(idx))

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
            settings = st.app_settings()
            settings.setValue("ui/theme", name)

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

    def _set_screen(self, screen: int):
        if self.device is None:
            return
        try:
            self.device.set_screen(screen)
        except DeviceError as e:
            self._on_device_lost(str(e))

    def _push_all(self):
        if self.device is None:
            return
        self.status_label.setText("Configuring device…")
        for tab in self.tabs:
            tab.push_settings()
        if self.device is not None:
            self.status_label.setText(f"Connected: {self.device.product}")

    def _on_device_lost(self, message: str):
        # A failure inside push_settings would otherwise re-enter _set_device
        # from within itself; defer so the current call finishes first.
        if self._losing_device:
            return
        self._losing_device = True
        QTimer.singleShot(0, lambda: self._handle_device_lost(message))

    def _handle_device_lost(self, message: str):
        self._losing_device = False
        if self.device is None:
            return
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

    # ---------------------------------------------------------------- lifecycle

    def _restore_window(self, settings):
        geometry = settings.value("ui/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        state = settings.value("ui/window_state")
        if state is not None:
            self.restoreState(state)
        self.tab_widget.setCurrentIndex(st.get_int(settings, "ui/active_tab", 0))

    def closeEvent(self, event):
        settings = st.app_settings()
        settings.setValue("ui/geometry", self.saveGeometry())
        settings.setValue("ui/window_state", self.saveState())
        settings.setValue("ui/active_tab", self.tab_widget.currentIndex())
        for tab in self.tabs:
            tab.save_settings(settings)
        settings.sync()
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
