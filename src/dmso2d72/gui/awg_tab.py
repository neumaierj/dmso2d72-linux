"""Signal generator (AWG) tab."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import protocol as p
from ..device import DeviceError, Dmso2d72


class AwgTab(QWidget):
    device_lost = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.device: Dmso2d72 | None = None

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

        duty_box = QGroupBox("Duty cycle")
        duty_form = QFormLayout(duty_box)
        duty_form.addRow("Square duty", self.square_duty)
        duty_form.addRow("Ramp duty", self.ramp_duty)
        duty_form.addRow("Trapezoid rise", self.trap_rise)
        duty_form.addRow("Trapezoid high", self.trap_high)
        duty_form.addRow("Trapezoid low", self.trap_low)

        self.start_button = QPushButton("Start output")
        self.start_button.setCheckable(True)

        column = QVBoxLayout()
        column.addWidget(wave_box)
        column.addWidget(duty_box)
        column.addWidget(self.start_button)
        column.addStretch()
        self.controls_widget = QWidget()
        self.controls_widget.setLayout(column)
        self.controls_widget.setMaximumWidth(340)

        layout = QHBoxLayout(self)
        layout.addWidget(self.controls_widget)
        layout.addStretch()

        self._wire_controls()
        self.set_device(None)

    def _wire_controls(self):
        self.wave_type.currentTextChanged.connect(
            lambda text: self._apply(lambda d: d.set_awg_type(text))
        )
        self.frequency.valueChanged.connect(
            lambda v: self._apply(lambda d: d.set_awg_frequency(v))
        )
        self.amplitude.valueChanged.connect(
            lambda v: self._apply(lambda d: d.set_awg_amplitude(v))
        )
        self.offset.valueChanged.connect(lambda v: self._apply(lambda d: d.set_awg_offset(v)))
        self.square_duty.valueChanged.connect(
            lambda v: self._apply(lambda d: d.set_awg_square_duty(v))
        )
        self.ramp_duty.valueChanged.connect(
            lambda v: self._apply(lambda d: d.set_awg_ramp_duty(v))
        )
        for spin in (self.trap_rise, self.trap_high, self.trap_low):
            spin.valueChanged.connect(lambda _: self._apply(self._send_trap_duty))
        self.start_button.toggled.connect(self._toggle_output)

    def _send_trap_duty(self, device: Dmso2d72):
        device.set_awg_trap_duty(
            self.trap_rise.value(), self.trap_high.value(), self.trap_low.value()
        )

    def _toggle_output(self, on: bool):
        self.start_button.setText("Stop output" if on else "Start output")
        self._apply(lambda d: d.awg_start(on))

    def _apply(self, fn):
        if self.device is None:
            return
        try:
            fn(self.device)
        except DeviceError as e:
            self.device_lost.emit(str(e))

    def set_device(self, device: Dmso2d72 | None):
        self.device = device
        self.controls_widget.setEnabled(device is not None)
        if device is None:
            self.start_button.setChecked(False)
