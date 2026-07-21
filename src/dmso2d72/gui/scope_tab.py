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
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
import pyqtgraph as pg

from .. import protocol as p
from ..capture import CaptureWorker
from ..device import Dmso2d72
from .device_tab import DeviceTab, _set_checked, _set_index, _set_text, _set_value


class ChannelBox(QGroupBox):
    changed = Signal()

    def __init__(self, channel: int):
        super().__init__(f"Channel {channel}")
        self.channel = channel

        # Filled in by apply_theme so the box matches its curve.
        self.swatch = QLabel()
        self.swatch.setFixedSize(28, 12)
        self.swatch.setToolTip("Colour of this channel's trace")

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

        header = QHBoxLayout()
        header.addWidget(self.enabled)
        header.addStretch()
        header.addWidget(self.swatch)

        form = QFormLayout(self)
        form.addRow(header)
        form.addRow("Coupling", self.coupling)
        form.addRow("Probe", self.probe)
        form.addRow("Volts/div", self.scale)
        form.addRow("Offset", self.offset)
        form.addRow(self.bw_limit)


class ScopeTab(DeviceTab):
    device_screen = p.SCREEN_SCOPE

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: CaptureWorker | None = None
        self.last_data: dict[int, list[int]] = {}

        self.plot = pg.PlotWidget()
        self.plot.setYRange(-4, 4, padding=0)
        self.plot.setLabel("left", "divisions")
        self.plot.setLabel("bottom", "sample")
        self.curves = {ch: self.plot.plot() for ch in (1, 2)}

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
        self._set_enabled(False)

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

    def enabled_channels(self) -> list[int]:
        return [ch for ch, box in self.ch_boxes.items() if box.enabled.isChecked()]

    # ------------------------------------------------------------- device state

    def _on_device_changed(self, device: Dmso2d72 | None):
        if device is None:
            self.live_button.setChecked(False)

    def push_settings(self):
        """Send every scope setting once, in dependency order.

        Explicit calls rather than re-emitting widget signals: a signal replay
        would run the same lambdas Qt already connected and write each setting
        twice. Aborts on the first failure so a device that went away produces
        one device_lost, not one per setting.
        """
        steps = []
        for ch, box in self.ch_boxes.items():
            steps += [
                lambda d, c=ch, b=box: d.set_channel_enabled(c, b.enabled.isChecked()),
                lambda d, c=ch, b=box: d.set_coupling(c, b.coupling.currentText()),
                lambda d, c=ch, b=box: d.set_probe(c, b.probe.currentText()),
                lambda d, c=ch, b=box: d.set_volt_scale(c, b.scale.currentText()),
                lambda d, c=ch, b=box: d.set_channel_offset(c, b.offset.value()),
                lambda d, c=ch, b=box: d.set_bandwidth_limit(c, b.bw_limit.isChecked()),
            ]
        steps += [
            lambda d: d.set_time_scale(self.time_scale.currentText()),
            lambda d: d.set_trigger_source(self.trig_source.currentIndex() + 1),
            lambda d: d.set_trigger_slope(self.trig_slope.currentText()),
            lambda d: d.set_trigger_mode(self.trig_mode.currentText()),
            lambda d: d.set_trigger_level(self.trig_level.value()),
            # Last, so the scope only starts once it is fully configured. This
            # is also what finally tells the device about run_button's initial
            # state, which no signal ever carried.
            lambda d: d.scope_start(self.run_button.isChecked()),
        ]
        for step in steps:
            if not self._apply(step):
                return

    def apply_theme(self, theme):
        self.plot.setBackground(theme.plot_background)
        for edge in ("left", "bottom"):
            axis = self.plot.getAxis(edge)
            axis.setPen(theme.axis)
            axis.setTextPen(theme.axis)
        self.plot.showGrid(x=True, y=True, alpha=theme.grid_alpha)
        for ch, curve in self.curves.items():
            curve.setPen(pg.mkPen(theme.channel_colors[ch], width=2))
            self.ch_boxes[ch].swatch.setStyleSheet(
                f"background-color: {theme.channel_colors[ch]}; border: 1px solid {theme.axis};"
            )

    def save_settings(self, settings):
        for ch, box in self.ch_boxes.items():
            settings.setValue(f"scope/ch{ch}/enabled", box.enabled.isChecked())
            settings.setValue(f"scope/ch{ch}/coupling", box.coupling.currentText())
            settings.setValue(f"scope/ch{ch}/probe", box.probe.currentText())
            settings.setValue(f"scope/ch{ch}/scale", box.scale.currentText())
            settings.setValue(f"scope/ch{ch}/offset", box.offset.value())
            settings.setValue(f"scope/ch{ch}/bw_limit", box.bw_limit.isChecked())
        settings.setValue("scope/time_scale", self.time_scale.currentText())
        settings.setValue("scope/trig_source", self.trig_source.currentIndex())
        settings.setValue("scope/trig_slope", self.trig_slope.currentText())
        settings.setValue("scope/trig_mode", self.trig_mode.currentText())
        settings.setValue("scope/trig_level", self.trig_level.value())
        settings.setValue("scope/samples", self.samples.value())
        settings.setValue("scope/running", self.run_button.isChecked())

    def restore_settings(self, settings):
        from .. import settings as st

        for ch, box in self.ch_boxes.items():
            _set_checked(box.enabled, st.get_bool(settings, f"scope/ch{ch}/enabled", ch == 1))
            _set_text(box.coupling, st.get_str(settings, f"scope/ch{ch}/coupling", "DC"))
            _set_text(box.probe, st.get_str(settings, f"scope/ch{ch}/probe", "x1"))
            _set_text(box.scale, st.get_str(settings, f"scope/ch{ch}/scale", "1V"))
            _set_value(box.offset, st.get_int(settings, f"scope/ch{ch}/offset", 100))
            _set_checked(box.bw_limit, st.get_bool(settings, f"scope/ch{ch}/bw_limit", False))
        _set_text(self.time_scale, st.get_str(settings, "scope/time_scale", "1ms"))
        _set_index(self.trig_source, st.get_int(settings, "scope/trig_source", 0))
        _set_text(self.trig_slope, st.get_str(settings, "scope/trig_slope", "Rising"))
        _set_text(self.trig_mode, st.get_str(settings, "scope/trig_mode", "Auto"))
        _set_value(self.trig_level, st.get_int(settings, "scope/trig_level", 100))
        _set_value(self.samples, st.get_int(settings, "scope/samples", 1000))
        _set_checked(self.run_button, st.get_bool(settings, "scope/running", True))
        self.run_button.setText("Stop scope" if self.run_button.isChecked() else "Start scope")

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
