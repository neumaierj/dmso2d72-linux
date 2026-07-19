"""Oscilloscope tab: live waveform display and acquisition controls."""

from __future__ import annotations

import csv

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
import pyqtgraph as pg

from .. import protocol as p
from ..capture import CaptureWorker
from ..device import DeviceError, Dmso2d72

CHANNEL_COLORS = {1: "#f4d03f", 2: "#5dade2"}


class ChannelBox(QGroupBox):
    changed = Signal()

    def __init__(self, channel: int):
        super().__init__(f"Channel {channel}")
        self.channel = channel

        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(channel == 1)
        self.coupling = QComboBox()
        self.coupling.addItems(p.COUPLINGS)
        self.coupling.setCurrentText("DC")
        self.probe = QComboBox()
        self.probe.addItems(p.PROBES)
        self.scale = QComboBox()
        self.scale.addItems(p.VOLT_SCALES)
        self.scale.setCurrentText("1V")
        self.offset = QSpinBox()
        self.offset.setRange(0, 200)
        self.offset.setValue(100)
        self.offset.setToolTip("Vertical offset, raw device units (100 = center)")
        self.bw_limit = QCheckBox("20 MHz bandwidth limit")

        form = QFormLayout(self)
        form.addRow(self.enabled)
        form.addRow("Coupling", self.coupling)
        form.addRow("Probe", self.probe)
        form.addRow("Volts/div", self.scale)
        form.addRow("Offset", self.offset)
        form.addRow(self.bw_limit)


class ScopeTab(QWidget):
    device_lost = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.device: Dmso2d72 | None = None
        self.worker: CaptureWorker | None = None
        self.last_data: dict[int, list[int]] = {}

        self.plot = pg.PlotWidget(background="#101418")
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setYRange(-4, 4, padding=0)
        self.plot.setLabel("left", "divisions")
        self.plot.setLabel("bottom", "sample")
        self.curves = {
            ch: self.plot.plot(pen=pg.mkPen(color, width=2)) for ch, color in CHANNEL_COLORS.items()
        }

        self.ch_boxes = {1: ChannelBox(1), 2: ChannelBox(2)}

        self.time_scale = QComboBox()
        self.time_scale.addItems(p.TIME_SCALES)
        self.time_scale.setCurrentText("1ms")
        time_box = QGroupBox("Horizontal")
        time_form = QFormLayout(time_box)
        time_form.addRow("Time/div", self.time_scale)

        self.trig_source = QComboBox()
        self.trig_source.addItems(["CH1", "CH2"])
        self.trig_slope = QComboBox()
        self.trig_slope.addItems(p.TRIGGER_SLOPES)
        self.trig_mode = QComboBox()
        self.trig_mode.addItems(p.TRIGGER_MODES)
        self.trig_level = QSpinBox()
        self.trig_level.setRange(0, 200)
        self.trig_level.setValue(100)
        self.trig_level.setToolTip("Trigger level, raw device units (100 = center)")
        trig_box = QGroupBox("Trigger")
        trig_form = QFormLayout(trig_box)
        trig_form.addRow("Source", self.trig_source)
        trig_form.addRow("Slope", self.trig_slope)
        trig_form.addRow("Mode", self.trig_mode)
        trig_form.addRow("Level", self.trig_level)

        self.samples = QSpinBox()
        self.samples.setRange(64, 6000)
        self.samples.setValue(1000)
        self.run_button = QPushButton("Stop scope")
        self.run_button.setCheckable(True)
        self.run_button.setChecked(True)
        self.live_button = QPushButton("Start live view")
        self.live_button.setCheckable(True)
        self.single_button = QPushButton("Single capture")
        self.export_button = QPushButton("Export CSV…")
        acq_box = QGroupBox("Acquisition")
        acq_form = QFormLayout(acq_box)
        acq_form.addRow("Samples", self.samples)
        acq_form.addRow(self.run_button)
        acq_form.addRow(self.live_button)
        acq_form.addRow(self.single_button)
        acq_form.addRow(self.export_button)

        controls = QVBoxLayout()
        controls.addWidget(self.ch_boxes[1])
        controls.addWidget(self.ch_boxes[2])
        controls.addWidget(time_box)
        controls.addWidget(trig_box)
        controls.addWidget(acq_box)
        controls.addStretch()
        self.controls_widget = QWidget()
        self.controls_widget.setLayout(controls)
        self.controls_widget.setMaximumWidth(260)

        layout = QHBoxLayout(self)
        layout.addWidget(self.plot, stretch=1)
        layout.addWidget(self.controls_widget)

        self._wire_controls()
        self.set_device(None)

    # ------------------------------------------------------------------ wiring

    def _wire_controls(self):
        for ch, box in self.ch_boxes.items():
            box.enabled.toggled.connect(
                lambda on, ch=ch: self._apply(lambda d: d.set_channel_enabled(ch, on))
            )
            box.enabled.toggled.connect(self._channels_changed)
            box.coupling.currentTextChanged.connect(
                lambda text, ch=ch: self._apply(lambda d: d.set_coupling(ch, text))
            )
            box.probe.currentTextChanged.connect(
                lambda text, ch=ch: self._apply(lambda d: d.set_probe(ch, text))
            )
            box.scale.currentTextChanged.connect(
                lambda text, ch=ch: self._apply(lambda d: d.set_volt_scale(ch, text))
            )
            box.offset.valueChanged.connect(
                lambda value, ch=ch: self._apply(lambda d: d.set_channel_offset(ch, value))
            )
            box.bw_limit.toggled.connect(
                lambda on, ch=ch: self._apply(lambda d: d.set_bandwidth_limit(ch, on))
            )
        self.time_scale.currentTextChanged.connect(
            lambda text: self._apply(lambda d: d.set_time_scale(text))
        )
        self.trig_source.currentIndexChanged.connect(
            lambda i: self._apply(lambda d: d.set_trigger_source(i + 1))
        )
        self.trig_slope.currentTextChanged.connect(
            lambda text: self._apply(lambda d: d.set_trigger_slope(text))
        )
        self.trig_mode.currentTextChanged.connect(
            lambda text: self._apply(lambda d: d.set_trigger_mode(text))
        )
        self.trig_level.valueChanged.connect(
            lambda value: self._apply(lambda d: d.set_trigger_level(value))
        )
        self.samples.valueChanged.connect(self._channels_changed)
        self.run_button.toggled.connect(self._toggle_run)
        self.live_button.toggled.connect(self._toggle_live)
        self.single_button.clicked.connect(self._single_capture)
        self.export_button.clicked.connect(self._export_csv)

    def _apply(self, fn):
        if self.device is None:
            return
        try:
            fn(self.device)
        except DeviceError as e:
            self.device_lost.emit(str(e))

    def enabled_channels(self) -> list[int]:
        return [ch for ch, box in self.ch_boxes.items() if box.enabled.isChecked()]

    # ------------------------------------------------------------- device state

    def set_device(self, device: Dmso2d72 | None):
        self._stop_worker()
        self.device = device
        self.controls_widget.setEnabled(device is not None)
        if device is None:
            self.live_button.setChecked(False)

    def shutdown(self):
        self._stop_worker()

    # -------------------------------------------------------------- acquisition

    def _toggle_run(self, running: bool):
        self.run_button.setText("Stop scope" if running else "Start scope")
        self._apply(lambda d: d.scope_start(running))

    def _channels_changed(self, *_):
        if self.worker is not None:
            self.worker.configure(self.enabled_channels(), self.samples.value())

    def _toggle_live(self, on: bool):
        self.live_button.setText("Stop live view" if on else "Start live view")
        if on:
            if self.device is None:
                self.live_button.setChecked(False)
                return
            self.worker = CaptureWorker(self.device, self.enabled_channels(), self.samples.value())
            self.worker.data_ready.connect(self._show_data)
            self.worker.failed.connect(self._worker_failed)
            self.worker.start()
        else:
            self._stop_worker()

    def _stop_worker(self):
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(3000)
            self.worker = None

    def _worker_failed(self, message: str):
        self.live_button.setChecked(False)
        self.device_lost.emit(message)

    def _single_capture(self):
        if self.device is None or self.worker is not None:
            return
        channels = self.enabled_channels()
        if not channels:
            return
        try:
            data = self.device.capture(channels, self.samples.value())
        except DeviceError as e:
            self.device_lost.emit(str(e))
            return
        self._show_data(data)

    def _show_data(self, data: dict[int, list[int]]):
        self.last_data = data
        for ch, curve in self.curves.items():
            samples = data.get(ch)
            if samples and self.ch_boxes[ch].enabled.isChecked():
                curve.setData([p.raw_to_divisions(s) for s in samples])
            else:
                curve.clear()

    def _export_csv(self):
        if not self.last_data:
            QMessageBox.information(self, "Export CSV", "No captured data to export yet.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "waveform.csv", "CSV (*.csv)")
        if not path:
            return
        channels = sorted(self.last_data)
        n = max(len(v) for v in self.last_data.values())
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            header = ["sample"]
            for ch in channels:
                header += [f"ch{ch}_raw", f"ch{ch}_divisions"]
            writer.writerow(header)
            for i in range(n):
                row: list = [i]
                for ch in channels:
                    samples = self.last_data[ch]
                    if i < len(samples):
                        row += [samples[i], round(p.raw_to_divisions(samples[i]), 4)]
                    else:
                        row += ["", ""]
                writer.writerow(row)
