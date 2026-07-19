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
