"""USB access to the Hantek 2D72 / Joy-IT DMSO2D72.

All device operations go through a lock so that GUI setting changes and the
background capture thread never interleave USB transfers.
"""

from __future__ import annotations

import threading

import usb.core
import usb.util

from . import protocol as p


class DeviceError(Exception):
    """A USB operation failed (device unplugged, permissions, timeout...)."""


class DeviceNotFound(DeviceError):
    """No DMSO2D72/2D72 found on the bus."""


def _match(dev) -> bool:
    return dev.idVendor == p.USB_VENDOR_ID and dev.idProduct in p.USB_PRODUCT_IDS


class Dmso2d72:
    """A connected oscilloscope."""

    def __init__(self, timeout_ms: int = 2000):
        self.timeout_ms = timeout_ms
        self.lock = threading.Lock()
        dev = usb.core.find(custom_match=_match)
        if dev is None:
            raise DeviceNotFound(
                "No DMSO2D72 found (looked for USB id 0483:2d42 / 0483:2d72). "
                "Is the device plugged in and turned on?"
            )
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except (usb.core.USBError, NotImplementedError):
            pass
        try:
            dev.set_configuration()
            usb.util.claim_interface(dev, 0)
        except usb.core.USBError as e:
            raise DeviceError(
                f"Found the device but could not claim it: {e}. "
                "On Linux this is usually a permissions problem — install the "
                "udev rule from udev/99-dmso2d72.rules or run once with sudo."
            ) from e
        self._dev = dev
        self.product = usb.util.get_string(dev, dev.iProduct) or "Hantek 2D72"

    def close(self) -> None:
        with self.lock:
            try:
                usb.util.release_interface(self._dev, 0)
            except usb.core.USBError:
                pass
            usb.util.dispose_resources(self._dev)

    # ------------------------------------------------------------------ raw I/O

    def _write(self, data: bytes) -> None:
        try:
            self._dev.write(p.WRITE_ENDPOINT, data, self.timeout_ms)
        except usb.core.USBError as e:
            raise DeviceError(f"USB write failed: {e}") from e

    def _send(self, data: bytes) -> None:
        with self.lock:
            self._write(data)

    def _send_setting(self, cmd: int, val: bytes) -> None:
        self._send(p.scope_setting(cmd, val))

    def raw_query(self, func: int, cmd: int, val: bytes = b"\x00\x00\x00\x00",
                  read_len: int = 64, read_timeout_ms: int = 300) -> bytes:
        """Send an arbitrary command and try to read a response on EP 0x81.

        Used by the DMM reverse-engineering probe (tools/dmm_probe.py). Returns
        the raw response bytes (possibly empty if the device sends nothing).
        A read timeout is treated as "no response", not an error.
        """
        packet = p.build_command(func, cmd, val)
        with self.lock:
            self._write(packet)
            try:
                data = self._dev.read(p.READ_ENDPOINT, read_len, read_timeout_ms)
            except usb.core.USBError as e:
                if e.errno in (110, None):  # ETIMEDOUT / libusb timeout
                    return b""
                raise DeviceError(f"USB read failed: {e}") from e
        return bytes(data)

    # -------------------------------------------------------------------- scope

    def scope_start(self, running: bool) -> None:
        self._send_setting(p.SCOPE_START_STOP, p.val_u8(1 if running else 0))

    def set_channel_enabled(self, channel: int, enabled: bool) -> None:
        cmd = p.SCOPE_ENABLE_CH1 if channel == 1 else p.SCOPE_ENABLE_CH2
        self._send_setting(cmd, p.val_u8(1 if enabled else 0))

    def set_coupling(self, channel: int, coupling: str) -> None:
        cmd = p.SCOPE_COUPLING_CH1 if channel == 1 else p.SCOPE_COUPLING_CH2
        self._send_setting(cmd, p.val_u8(p.COUPLINGS[coupling]))

    def set_probe(self, channel: int, probe: str) -> None:
        cmd = p.SCOPE_PROBE_X_CH1 if channel == 1 else p.SCOPE_PROBE_X_CH2
        self._send_setting(cmd, p.val_u8(p.PROBES[probe]))

    def set_bandwidth_limit(self, channel: int, enabled: bool) -> None:
        cmd = p.SCOPE_BW_LIMIT_CH1 if channel == 1 else p.SCOPE_BW_LIMIT_CH2
        self._send_setting(cmd, p.val_u8(1 if enabled else 0))

    def set_volt_scale(self, channel: int, scale_label: str) -> None:
        cmd = p.SCOPE_SCALE_CH1 if channel == 1 else p.SCOPE_SCALE_CH2
        code, _ = p.VOLT_SCALES[scale_label]
        self._send_setting(cmd, p.val_u8(code))

    def set_channel_offset(self, channel: int, offset: int) -> None:
        """Vertical offset as raw device value 0..200 (100 = center)."""
        cmd = p.SCOPE_OFFSET_CH1 if channel == 1 else p.SCOPE_OFFSET_CH2
        self._send_setting(cmd, p.val_u8(max(0, min(200, offset))))

    def set_time_scale(self, scale_label: str) -> None:
        code, _ = p.TIME_SCALES[scale_label]
        self._send_setting(p.SCOPE_SCALE_TIME, p.val_u8(code))

    def set_time_offset(self, offset: int) -> None:
        self._send_setting(p.SCOPE_OFFSET_TIME, p.val_u32(offset))

    def set_trigger_source(self, channel: int) -> None:
        self._send_setting(p.SCOPE_TRIGGER_SOURCE, p.val_u8(channel - 1))

    def set_trigger_slope(self, slope: str) -> None:
        self._send_setting(p.SCOPE_TRIGGER_SLOPE, p.val_u8(p.TRIGGER_SLOPES[slope]))

    def set_trigger_mode(self, mode: str) -> None:
        self._send_setting(p.SCOPE_TRIGGER_MODE, p.val_u8(p.TRIGGER_MODES[mode]))

    def set_trigger_level(self, level: int) -> None:
        """Trigger level as raw device value 0..200 (100 = center)."""
        self._send_setting(p.SCOPE_TRIGGER_LEVEL, p.val_u8(max(0, min(200, level))))

    def capture(self, channels: list[int], num_samples: int) -> dict[int, list[int]]:
        """Fetch one waveform for the given channels, num_samples per channel."""
        channels = sorted(channels)
        cmd = p.capture_command(num_samples, len(channels))
        total = num_samples * len(channels)
        buffer = bytearray()
        with self.lock:
            while len(buffer) < total:
                self._write(cmd)
                length = min(p.CAPTURE_CHUNK, total - len(buffer))
                try:
                    chunk = self._dev.read(p.READ_ENDPOINT, length, self.timeout_ms)
                except usb.core.USBError as e:
                    raise DeviceError(f"USB read failed: {e}") from e
                buffer.extend(chunk)
        return p.decode_capture(bytes(buffer[:total]), channels)

    # ---------------------------------------------------------------------- awg

    def set_awg_type(self, awg_type: str) -> None:
        self._send(p.build_command(p.FUNC_AWG_SETTING, p.AWG_TYPE, p.val_u8(p.AWG_TYPES[awg_type])))

    def set_awg_frequency(self, hz: float) -> None:
        self._send(p.awg_frequency_command(hz))

    def set_awg_amplitude(self, volts: float) -> None:
        self._send(p.awg_amplitude_command(volts))

    def set_awg_offset(self, volts: float) -> None:
        self._send(p.awg_offset_command(volts))

    def set_awg_square_duty(self, percent: float) -> None:
        self._send(p.awg_duty_command(p.AWG_SQUARE_DUTY, percent))

    def set_awg_ramp_duty(self, percent: float) -> None:
        self._send(p.awg_duty_command(p.AWG_RAMP_DUTY, percent))

    def set_awg_trap_duty(self, rise: float, high: float, low: float) -> None:
        self._send(p.awg_trap_duty_command(rise, high, low))

    def awg_start(self, running: bool) -> None:
        self._send(
            p.build_command(p.FUNC_AWG_SETTING, p.AWG_START_STOP, p.val_u8(1 if running else 0))
        )

    # -------------------------------------------------------------------- dmm

    def set_dmm_mode(self, mode: str) -> None:
        """Select the multimeter mode, one of the protocol.DMM_MODES keys.

        The reading takes a moment to auto-range afterwards. Note the device
        applies the mode but leaves its own soft-key menu highlighting the
        previously selected entry (see re/DMM_PROTOCOL.md).
        """
        self._send(p.dmm_mode_command(mode))

    def read_dmm(self) -> "p.DmmReading | None":
        """Read one multimeter value. Returns None if the device sent no frame
        (e.g. it is not currently on the multimeter screen)."""
        frame = self.raw_query(p.FUNC_DMM_STATUS, 0x00)
        if len(frame) != p.DMM_FRAME_LEN:
            return None
        try:
            return p.decode_dmm(frame)
        except ValueError:
            return None

    # ------------------------------------------------------------------- screen

    def set_screen(self, screen: int) -> None:
        """Switch the device between scope/DMM/AWG screens (protocol.SCREEN_*)."""
        self._send(p.build_command(p.FUNC_SCREEN_SETTING, 0x00, p.val_u8(screen)))
