"""Signal generator (AWG): a parallel side panel.

The generator runs independently of the measurement, so it is shown alongside
the Scope/DMM view rather than as its own tab. Its commands would draw onto
whatever screen the device shows, so edits are debounced and applied by the
window on the device's AWG screen (set_apply_hook), then it returns to the
measurement screen — see MainWindow._apply_awg.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import protocol as p
from ..device import Dmso2d72
from .device_tab import DeviceTab, _set_checked, _set_text, _set_value

# Which duty-cycle controls actually affect each waveform. Anything else is
# hidden, which is most of the tab for the common Sine case.
DUTY_FIELDS = {
    "Square": ("square",),
    "Ramp": ("ramp",),
    "Trapezoid": ("trap",),
}


class AwgTab(DeviceTab):
    device_screen = p.SCREEN_AWG

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumWidth(340)

        # Edits are coalesced and handed to the window's apply hook, which
        # applies them on the device's AWG screen.
        self._apply_hook: Callable[[list], None] | None = None
        self._pending: list = []
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(300)
        self._apply_timer.timeout.connect(self._flush)

        self.wave_type = QComboBox()
        self.wave_type.addItems(p.AWG_TYPES)
        self.wave_type.setCurrentText("Sine")

        self.frequency = QDoubleSpinBox()
        self.frequency.setRange(1, 25_000_000)
        self.frequency.setDecimals(0)
        self.frequency.setValue(1000)
        self.frequency.setSuffix(" Hz")
        self.frequency.setGroupSeparatorShown(True)

        self.amplitude = QDoubleSpinBox()
        self.amplitude.setRange(-3.5, 3.5)
        self.amplitude.setSingleStep(0.1)
        self.amplitude.setValue(1.0)
        self.amplitude.setSuffix(" V")

        self.offset = QDoubleSpinBox()
        self.offset.setRange(-3.5, 3.5)
        self.offset.setSingleStep(0.1)
        self.offset.setSuffix(" V")

        wave_box = QGroupBox("Waveform")
        wave_form = QFormLayout(wave_box)
        wave_form.addRow("Type", self.wave_type)
        wave_form.addRow("Frequency", self.frequency)
        wave_form.addRow("Amplitude", self.amplitude)
        wave_form.addRow("Offset", self.offset)

        self.square_duty = QDoubleSpinBox()
        self.square_duty.setRange(1, 99)
        self.square_duty.setValue(50)
        self.square_duty.setSuffix(" %")
        self.ramp_duty = QDoubleSpinBox()
        self.ramp_duty.setRange(1, 99)
        self.ramp_duty.setValue(50)
        self.ramp_duty.setSuffix(" %")
        self.trap_rise = QDoubleSpinBox()
        self.trap_rise.setRange(1, 99)
        self.trap_rise.setValue(10)
        self.trap_rise.setSuffix(" %")
        self.trap_high = QDoubleSpinBox()
        self.trap_high.setRange(1, 99)
        self.trap_high.setValue(30)
        self.trap_high.setSuffix(" %")
        self.trap_low = QDoubleSpinBox()
        self.trap_low.setRange(1, 99)
        self.trap_low.setValue(30)
        self.trap_low.setSuffix(" %")

        self.duty_box = QGroupBox("Duty cycle")
        duty_form = QFormLayout(self.duty_box)
        # Kept so rows can be hidden per waveform; hiding a widget alone leaves
        # its label behind in a QFormLayout.
        self.duty_rows = {
            "square": (QLabel("Square duty"), self.square_duty),
            "ramp": (QLabel("Ramp duty"), self.ramp_duty),
            "trap_rise": (QLabel("Trapezoid rise"), self.trap_rise),
            "trap_high": (QLabel("Trapezoid high"), self.trap_high),
            "trap_low": (QLabel("Trapezoid low"), self.trap_low),
        }
        for label, field in self.duty_rows.values():
            duty_form.addRow(label, field)

        self.duty_hint = QLabel("This waveform has no duty-cycle settings.")
        self.duty_hint.setWordWrap(True)
        duty_form.addRow(self.duty_hint)

        self.start_button = QPushButton("Start output")
        self.start_button.setCheckable(True)

        grid = QGridLayout()
        grid.addWidget(wave_box, 0, 0)
        grid.addWidget(self.duty_box, 0, 1)
        grid.addWidget(self.start_button, 1, 0, 1, 2)
        grid.setRowStretch(2, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.controls_widget = QWidget()
        self.controls_widget.setLayout(grid)

        layout = QVBoxLayout(self)
        layout.addWidget(self.controls_widget)

        self._wire_controls()
        self._update_duty_visibility(self.wave_type.currentText())
        self._set_enabled(False)

    def _wire_controls(self):
        self.wave_type.currentTextChanged.connect(
            lambda text: self._queue(lambda d: d.set_awg_type(text))
        )
        self.wave_type.currentTextChanged.connect(self._update_duty_visibility)
        self.frequency.valueChanged.connect(
            lambda v: self._queue(lambda d: d.set_awg_frequency(v))
        )
        self.amplitude.valueChanged.connect(
            lambda v: self._queue(lambda d: d.set_awg_amplitude(v))
        )
        self.offset.valueChanged.connect(lambda v: self._queue(lambda d: d.set_awg_offset(v)))
        self.square_duty.valueChanged.connect(
            lambda v: self._queue(lambda d: d.set_awg_square_duty(v))
        )
        self.ramp_duty.valueChanged.connect(
            lambda v: self._queue(lambda d: d.set_awg_ramp_duty(v))
        )
        for spin in (self.trap_rise, self.trap_high, self.trap_low):
            spin.valueChanged.connect(lambda _: self._queue(self._send_trap_duty))
        self.start_button.toggled.connect(self._toggle_output)

    def set_apply_hook(self, hook: Callable[[list], None]) -> None:
        """The window sets this to apply AWG commands on the AWG screen."""
        self._apply_hook = hook

    def _queue(self, fn) -> None:
        """Coalesce a device call; applied after a short idle (one screen bounce)."""
        self._pending.append(fn)
        self._apply_timer.start()

    def _flush(self) -> None:
        if not self._pending or self.device is None:
            self._pending.clear()
            return
        fns, self._pending = self._pending, []
        if self._apply_hook is not None:
            self._apply_hook(fns)
        else:  # standalone / tests: apply directly
            for fn in fns:
                if not self._apply(fn):
                    break

    def push_fn(self, device: Dmso2d72) -> None:
        """Apply every setting to a device, output off. Called inside the window's
        AWG-screen bounce, so it runs directly (not via _apply)."""
        device.set_awg_type(self.wave_type.currentText())
        device.set_awg_frequency(self.frequency.value())
        device.set_awg_amplitude(self.amplitude.value())
        device.set_awg_offset(self.offset.value())
        device.set_awg_square_duty(self.square_duty.value())
        device.set_awg_ramp_duty(self.ramp_duty.value())
        self._send_trap_duty(device)
        device.awg_start(False)  # never energise on connect
        _set_checked(self.start_button, False)

    def _send_trap_duty(self, device: Dmso2d72):
        device.set_awg_trap_duty(
            self.trap_rise.value(), self.trap_high.value(), self.trap_low.value()
        )

    def _toggle_output(self, on: bool):
        self.start_button.setText("Stop output" if on else "Start output")
        self._queue(lambda d: d.awg_start(on))

    def _update_duty_visibility(self, wave_type: str):
        prefixes = DUTY_FIELDS.get(wave_type, ())
        any_shown = False
        for key, (label, field) in self.duty_rows.items():
            shown = key.startswith(prefixes) if prefixes else False
            label.setVisible(shown)
            field.setVisible(shown)
            any_shown = any_shown or shown
        self.duty_hint.setVisible(not any_shown)

    def _on_device_changed(self, device: Dmso2d72 | None):
        if device is None:
            self.start_button.setChecked(False)

    def push_settings(self):
        """Send the waveform parameters, but never energise the output."""
        steps = [
            lambda d: d.set_awg_type(self.wave_type.currentText()),
            lambda d: d.set_awg_frequency(self.frequency.value()),
            lambda d: d.set_awg_amplitude(self.amplitude.value()),
            lambda d: d.set_awg_offset(self.offset.value()),
            lambda d: d.set_awg_square_duty(self.square_duty.value()),
            lambda d: d.set_awg_ramp_duty(self.ramp_duty.value()),
            self._send_trap_duty,
            # Deliberately off: a saved session must not put a signal on the
            # output the moment the device is plugged in.
            lambda d: d.awg_start(False),
        ]
        for step in steps:
            if not self._apply(step):
                return
        _set_checked(self.start_button, False)

    def save_settings(self, settings):
        settings.setValue("awg/type", self.wave_type.currentText())
        settings.setValue("awg/frequency", self.frequency.value())
        settings.setValue("awg/amplitude", self.amplitude.value())
        settings.setValue("awg/offset", self.offset.value())
        settings.setValue("awg/square_duty", self.square_duty.value())
        settings.setValue("awg/ramp_duty", self.ramp_duty.value())
        settings.setValue("awg/trap_rise", self.trap_rise.value())
        settings.setValue("awg/trap_high", self.trap_high.value())
        settings.setValue("awg/trap_low", self.trap_low.value())

    def restore_settings(self, settings):
        from .. import settings as st

        _set_text(self.wave_type, st.get_str(settings, "awg/type", "Sine"))
        _set_value(self.frequency, st.get_float(settings, "awg/frequency", 1000.0))
        _set_value(self.amplitude, st.get_float(settings, "awg/amplitude", 1.0))
        _set_value(self.offset, st.get_float(settings, "awg/offset", 0.0))
        _set_value(self.square_duty, st.get_float(settings, "awg/square_duty", 50.0))
        _set_value(self.ramp_duty, st.get_float(settings, "awg/ramp_duty", 50.0))
        _set_value(self.trap_rise, st.get_float(settings, "awg/trap_rise", 10.0))
        _set_value(self.trap_high, st.get_float(settings, "awg/trap_high", 30.0))
        _set_value(self.trap_low, st.get_float(settings, "awg/trap_low", 30.0))
        self._update_duty_visibility(self.wave_type.currentText())
