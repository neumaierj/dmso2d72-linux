"""Background waveform capture thread."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from .device import DeviceError, Dmso2d72


class CaptureWorker(QThread):
    """Continuously polls the scope for waveform data while running.

    Emits data_ready with {channel: [raw samples]} for each capture, or
    failed with a message if the device stops responding (then exits).
    """

    # Signal(object), not Signal(dict): the payload has int channel keys,
    # which cannot convert to QVariantMap.
    data_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, device: Dmso2d72, channels: list[int], num_samples: int, parent=None):
        super().__init__(parent)
        self._device = device
        self._channels = channels
        self._num_samples = num_samples
        self._stop = False

    def configure(self, channels: list[int], num_samples: int) -> None:
        self._channels = channels
        self._num_samples = num_samples

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        while not self._stop:
            channels = list(self._channels)
            num_samples = self._num_samples
            if not channels:
                self.msleep(200)
                continue
            try:
                data = self._device.capture(channels, num_samples)
            except DeviceError as e:
                if not self._stop:
                    self.failed.emit(str(e))
                return
            self.data_ready.emit(data)
            self.msleep(50)


class DeviceMonitor(QThread):
    """Low-rate poll of the device's active measurement and multimeter reading.

    Runs the whole time a device is connected so the app can mirror the device
    (which measurement screen it shows) and keep the multimeter readout live. It
    shares the device lock with the scope CaptureWorker; to avoid contending
    with a running capture it reads the multimeter only when the device is not
    on the scope screen (the reading is only needed on the DMM view anyway).

    Emits measurement_changed("scope"|"dmm") when the active measurement
    changes, reading(DmmReading|None) for the multimeter, or failed on error.
    """

    measurement_changed = Signal(str)
    reading = Signal(object)
    failed = Signal(str)

    def __init__(self, device: Dmso2d72, parent=None):
        super().__init__(parent)
        self._device = device
        self._stop = False
        self._last_measurement: str | None = None

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        while not self._stop:
            try:
                measurement = self._device.active_measurement()
                if measurement is not None and measurement != self._last_measurement:
                    self._last_measurement = measurement
                    self.measurement_changed.emit(measurement)
                if measurement != "scope":
                    self.reading.emit(self._device.read_dmm())
            except DeviceError as e:
                if not self._stop:
                    self.failed.emit(str(e))
                return
            self.msleep(250)
