"""Common device plumbing for the function tabs.

A base class rather than a mixin: device_lost is a Qt Signal, which only works
when declared on a QObject subclass, and Qt does not support inheriting from
QObject twice. All three tabs are QWidgets with the same shape anyway.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QSettings, QSignalBlocker, Signal
from PySide6.QtWidgets import QWidget

from ..device import DeviceError, Dmso2d72


# Restoring settings must not look like the user changing a control, or every
# restored value would be written to a device that is not even connected yet.
def _set_text(combo, text: str) -> None:
    with QSignalBlocker(combo):
        if text in (combo.itemText(i) for i in range(combo.count())):
            combo.setCurrentText(text)


def _set_index(combo, index: int) -> None:
    with QSignalBlocker(combo):
        if 0 <= index < combo.count():
            combo.setCurrentIndex(index)


def _set_value(spin, value) -> None:
    with QSignalBlocker(spin):
        spin.setValue(value)


def _set_checked(widget, checked: bool) -> None:
    with QSignalBlocker(widget):
        widget.setChecked(checked)


class DeviceTab(QWidget):
    """A tab that talks to the device, or is disabled while none is connected."""

    device_lost = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.device: Dmso2d72 | None = None
        # Subclasses assign the widget holding their controls; the default
        # _set_enabled greys it out when no device is connected.
        self.controls_widget: QWidget | None = None

    def _apply(self, fn: Callable[[Dmso2d72], None]) -> bool:
        """Run a device call, reporting a lost device. False if it did not run."""
        if self.device is None:
            return False
        try:
            fn(self.device)
        except DeviceError as e:
            self.device_lost.emit(str(e))
            return False
        return True

    def set_device(self, device: Dmso2d72 | None) -> None:
        """Attach or detach the device. Subclasses override the hooks, not this."""
        self.shutdown()
        was_connected = self.device is not None
        self.device = device
        self._set_enabled(device is not None)
        self._on_device_changed(device)
        # Only on a fresh connection, so re-attaching the same device or
        # disconnecting never floods the bus.
        if device is not None and not was_connected:
            self.push_settings()

    # ------------------------------------------------------------------- hooks

    def _set_enabled(self, on: bool) -> None:
        if self.controls_widget is not None:
            self.controls_widget.setEnabled(on)

    def _on_device_changed(self, device: Dmso2d72 | None) -> None:
        """Reset any UI state that must not survive a connect/disconnect."""

    def push_settings(self) -> None:
        """Send every control's current value, so the device matches the UI."""

    def apply_theme(self, theme) -> None:
        """Re-colour anything the Qt palette does not reach (i.e. plots)."""

    def save_settings(self, settings: QSettings) -> None:
        """Persist control values."""

    def restore_settings(self, settings: QSettings) -> None:
        """Load control values. Runs while disconnected, so it sends nothing."""

    def shutdown(self) -> None:
        """Stop any background worker. Safe to call repeatedly."""
