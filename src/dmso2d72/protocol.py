"""USB protocol for the Hantek 2D72 / Joy-IT DMSO2D72 handheld oscilloscope.

Pure command framing and data decoding — no USB I/O here.

The protocol was reverse-engineered by the community:
  - https://github.com/lucaoli/Hantek (C, GPL-3.0, tested on a real 2D72)
  - https://github.com/hkoosha/hanteker (Rust port)

Every command is a 10-byte packet sent to bulk OUT endpoint 0x02:

    [idx, 0x0A, func_lo, func_hi, cmd, val0, val1, val2, val3, last]

Waveform data is read from bulk IN endpoint 0x81 in 64-byte chunks.
"""

from __future__ import annotations

import struct

USB_VENDOR_ID = 0x0483
# The 2D72 enumerates with product id 0x2d42 (shared with the 2D42),
# but accept 0x2d72 too in case of firmware variations.
USB_PRODUCT_IDS = (0x2D42, 0x2D72)

WRITE_ENDPOINT = 0x02
READ_ENDPOINT = 0x81
CAPTURE_CHUNK = 64

CMD_IDX = 0x00
CMD_MAGIC = 0x0A  # second byte of every packet

# Function codes (u16, little-endian in the packet)
FUNC_SCOPE_SETTING = 0x0000
FUNC_SCOPE_CAPTURE = 0x0100
FUNC_AWG_SETTING = 0x0002
FUNC_SCREEN_SETTING = 0x0003
# Reverse-engineered by hardware probing (see re/DMM_PROTOCOL.md): querying
# this function returns the live multimeter status/value frame on EP 0x81.
FUNC_DMM_STATUS = 0x0101

# Scope setting commands
SCOPE_ENABLE_CH1 = 0x00
SCOPE_COUPLING_CH1 = 0x01
SCOPE_PROBE_X_CH1 = 0x02
SCOPE_BW_LIMIT_CH1 = 0x03
SCOPE_SCALE_CH1 = 0x04
SCOPE_OFFSET_CH1 = 0x05
SCOPE_ENABLE_CH2 = 0x06
SCOPE_COUPLING_CH2 = 0x07
SCOPE_PROBE_X_CH2 = 0x08
SCOPE_BW_LIMIT_CH2 = 0x09
SCOPE_SCALE_CH2 = 0x0A
SCOPE_OFFSET_CH2 = 0x0B
SCOPE_START_STOP = 0x0C
SCOPE_SCALE_TIME = 0x0E
SCOPE_OFFSET_TIME = 0x0F
SCOPE_TRIGGER_SOURCE = 0x10
SCOPE_TRIGGER_SLOPE = 0x11
SCOPE_TRIGGER_MODE = 0x12
SCOPE_TRIGGER_LEVEL = 0x14
SCOPE_START_RECV = 0x16

# Screen (device function) values
SCREEN_SCOPE = 0x00
SCREEN_DMM = 0x01
SCREEN_AWG = 0x02

# Coupling values
COUPLING_AC = 0x00
COUPLING_DC = 0x01
COUPLING_GND = 0x02
COUPLINGS = {"AC": COUPLING_AC, "DC": COUPLING_DC, "GND": COUPLING_GND}

# Probe attenuation values
PROBES = {"x1": 0x00, "x10": 0x01, "x100": 0x02, "x1000": 0x03}

# Vertical scale: label -> (code, volts per division)
VOLT_SCALES = {
    "10mV": (0x00, 0.010),
    "20mV": (0x01, 0.020),
    "50mV": (0x02, 0.050),
    "100mV": (0x03, 0.100),
    "200mV": (0x04, 0.200),
    "500mV": (0x05, 0.500),
    "1V": (0x06, 1.0),
    "2V": (0x07, 2.0),
    "5V": (0x08, 5.0),
    "10V": (0x09, 10.0),
}

# Horizontal scale: label -> (code, seconds per division)
TIME_SCALES = {
    "5ns": (0x00, 5e-9),
    "10ns": (0x01, 10e-9),
    "20ns": (0x02, 20e-9),
    "50ns": (0x03, 50e-9),
    "100ns": (0x04, 100e-9),
    "200ns": (0x05, 200e-9),
    "500ns": (0x06, 500e-9),
    "1us": (0x07, 1e-6),
    "2us": (0x08, 2e-6),
    "5us": (0x09, 5e-6),
    "10us": (0x0A, 10e-6),
    "20us": (0x0B, 20e-6),
    "50us": (0x0C, 50e-6),
    "100us": (0x0D, 100e-6),
    "200us": (0x0E, 200e-6),
    "500us": (0x0F, 500e-6),
    "1ms": (0x10, 1e-3),
    "2ms": (0x11, 2e-3),
    "5ms": (0x12, 5e-3),
    "10ms": (0x13, 10e-3),
    "20ms": (0x14, 20e-3),
    "50ms": (0x15, 50e-3),
    "100ms": (0x16, 100e-3),
    "200ms": (0x17, 200e-3),
    "500ms": (0x18, 500e-3),
    "1s": (0x19, 1.0),
    "2s": (0x1A, 2.0),
    "5s": (0x1B, 5.0),
    "10s": (0x1C, 10.0),
    "20s": (0x1D, 20.0),
    "50s": (0x1E, 50.0),
    "100s": (0x1F, 100.0),
    "200s": (0x20, 200.0),
    "500s": (0x21, 500.0),
}

# Trigger values
TRIGGER_SLOPES = {"Rising": 0x00, "Falling": 0x01, "Both": 0x02}
TRIGGER_MODES = {"Auto": 0x00, "Normal": 0x01, "Single": 0x02}

# AWG setting commands
AWG_TYPE = 0x00
AWG_FREQ = 0x01
AWG_AMPLITUDE = 0x02
AWG_OFFSET = 0x03
AWG_SQUARE_DUTY = 0x04
AWG_RAMP_DUTY = 0x05
AWG_TRAP_DUTY = 0x06
AWG_START_STOP = 0x08

AWG_TYPES = {
    "Square": 0x00,
    "Ramp": 0x01,
    "Sine": 0x02,
    "Trapezoid": 0x03,
    "Arb1": 0x04,
    "Arb2": 0x05,
    "Arb3": 0x06,
    "Arb4": 0x07,
}

# Waveform samples are unsigned bytes drawn on a screen of 8 vertical
# divisions: value 29 is the bottom of the screen, 202 counts span it.
SAMPLE_BASELINE = 29
SAMPLE_SPAN = 202
SCREEN_DIVS_V = 8


def val_u8(v0: int, v1: int = 0, v2: int = 0, v3: int = 0) -> bytes:
    return bytes((v0, v1, v2, v3))


def val_u16(a: int, b: int = 0) -> bytes:
    return struct.pack("<HH", a, b)


def val_u32(v: int) -> bytes:
    return struct.pack("<I", v)


def build_command(func: int, cmd: int, val: bytes = b"\x00\x00\x00\x00") -> bytes:
    if len(val) != 4:
        raise ValueError(f"val must be 4 bytes, got {len(val)}")
    return bytes((CMD_IDX, CMD_MAGIC)) + struct.pack("<H", func) + bytes((cmd,)) + val + b"\x00"


def scope_setting(cmd: int, val: bytes = b"\x00\x00\x00\x00") -> bytes:
    return build_command(FUNC_SCOPE_SETTING, cmd, val)


def capture_command(num_samples: int, num_channels: int) -> bytes:
    """Command requesting a waveform transfer of num_samples per channel."""
    if num_samples < 64:
        raise ValueError("minimum number of samples is 64")
    if num_channels not in (1, 2):
        raise ValueError("num_channels must be 1 or 2")
    half = (num_samples * num_channels) // 2
    return build_command(FUNC_SCOPE_CAPTURE, SCOPE_START_RECV, val_u16(half, half))


def awg_amplitude_command(volts: float) -> bytes:
    """AWG amplitude: millivolts magnitude + sign flag, both u16 LE."""
    raw = int(abs(volts) * 1000)
    sign = 1 if volts < 0 else 0
    return build_command(FUNC_AWG_SETTING, AWG_AMPLITUDE, val_u16(raw, sign))


def awg_offset_command(volts: float) -> bytes:
    raw = int(abs(volts) * 1000)
    sign = 1 if volts < 0 else 0
    return build_command(FUNC_AWG_SETTING, AWG_OFFSET, val_u16(raw, sign))


def awg_frequency_command(hz: float) -> bytes:
    return build_command(FUNC_AWG_SETTING, AWG_FREQ, val_u32(int(hz)))


def awg_duty_command(cmd: int, duty_percent: float) -> bytes:
    """Duty cycle for square (AWG_SQUARE_DUTY) or ramp (AWG_RAMP_DUTY), in percent."""
    return build_command(FUNC_AWG_SETTING, cmd, val_u16(int(duty_percent)))


def awg_trap_duty_command(rise_percent: float, high_percent: float, low_percent: float) -> bytes:
    return build_command(
        FUNC_AWG_SETTING,
        AWG_TRAP_DUTY,
        val_u8(int(rise_percent), int(high_percent), int(low_percent)),
    )


def decode_capture(buffer: bytes, channels: list[int]) -> dict[int, list[int]]:
    """Split an interleaved capture buffer into raw per-channel samples.

    The device sends one byte per channel per sample point, channels
    interleaved in ascending order.
    """
    n = len(channels)
    if n == 0:
        raise ValueError("no channels given")
    ordered = sorted(channels)
    return {ch: list(buffer[i::n]) for i, ch in enumerate(ordered)}


def raw_to_divisions(raw: int) -> float:
    """Convert a raw sample byte to screen position in divisions (-4 .. +4)."""
    return (raw - SAMPLE_BASELINE) / SAMPLE_SPAN * SCREEN_DIVS_V - SCREEN_DIVS_V / 2


# --------------------------------------------------------------------- DMM ---
#
# The multimeter value frame (reply to FUNC_DMM_STATUS) is 14 bytes, verified
# against the device screen for DC volts, resistance and continuity
# (re/dmm_log.jsonl):
#
#   offset:  0    1    2    3     4    5     6      7   8   9  10    11    12   13
#   bytes:  0x55 0x0b 0x01 mode  ?   sign  dec    d1  d2  d3  d4   range  ?  0x55
#
#   - byte 3  = measurement mode: 0x0a DC volts, 0x08 resistance, 0x09 continuity
#   - byte 5  = sign (0 positive, 1 negative)
#   - byte 6  = number of decimal places (auto-range: 3 -> X.XXX, 2 -> XX.XX ...)
#   - bytes 7..10 = the four displayed digits, most significant first, each a
#                   plain binary 0..9 -- OR an overload sentinel (0xff...) when
#                   the reading is over range ("OL" on screen)
#   - byte 11 = range/unit prefix (resistance: 0x05 ohm, 0x03 kohm, 0x04 Mohm)
#   - value = (-1)**sign * (d1*1000 + d2*100 + d3*10 + d4) / 10**dec
#
# Still to capture: AC volts, DC/AC current, capacitance, diode (add their byte-3
# code and unit below). The numeric decode already works for any mode.

DMM_FRAME_LEN = 14
DMM_FRAME_START = 0x55

OHM = "Ω"

DMM_MODE_OHM = 0x08  # resistance: unit comes from the range byte (byte 11)

# byte 3 -> (mode name, fixed unit), all verified against the device screen.
# byte 3 selects both the measurement type and its coarse range, so e.g. the
# volts (0x0a) and millivolts (0x04) positions are distinct codes; auto-ranging
# within a code adjusts the decimal-places byte. A None unit means "derive from
# the range byte" (resistance). Diode-test reports as DC volts (0x0a).
DMM_MODES = {
    0x00: ("AC Current", "A"),
    0x01: ("DC Current", "A"),
    0x02: ("AC Current", "mA"),
    0x03: ("DC Current", "mA"),
    0x04: ("DC Voltage", "mV"),
    0x06: ("AC Voltage", "V"),
    0x07: ("Capacitance", "nF"),
    DMM_MODE_OHM: ("Resistance", None),
    0x09: ("Continuity", OHM),
    0x0A: ("DC Voltage", "V"),
}

# resistance range: byte 11 -> unit prefix
DMM_OHM_UNITS = {0x05: OHM, 0x03: "k" + OHM, 0x04: "M" + OHM}


class DmmReading:
    """A decoded multimeter reading. value is None when the input is over range
    (the device shows "OL"); overload is True in that case."""

    def __init__(self, value: float | None, decimals: int, negative: bool,
                 mode: str, unit: str, overload: bool, raw: bytes):
        self.value = value
        self.decimals = decimals
        self.negative = negative
        self.mode = mode
        self.unit = unit
        self.overload = overload
        self.raw = raw

    def formatted(self) -> str:
        """Value formatted like the device screen, e.g. '3.298 V' or 'OL MΩ'."""
        num = "OL" if self.overload else f"{self.value:.{self.decimals}f}"
        return f"{num} {self.unit}".strip()

    def __repr__(self) -> str:
        return f"DmmReading({self.formatted()!r}, mode={self.mode!r})"


def decode_dmm(frame: bytes) -> DmmReading:
    """Decode a FUNC_DMM_STATUS reply into a reading. Raises ValueError if the
    frame is not a well-formed 14-byte DMM status frame."""
    if len(frame) != DMM_FRAME_LEN:
        raise ValueError(f"expected {DMM_FRAME_LEN}-byte DMM frame, got {len(frame)}")
    if frame[0] != DMM_FRAME_START or frame[-1] != DMM_FRAME_START:
        raise ValueError("bad DMM frame framing (missing 0x55 start/end)")

    negative = frame[5] == 1
    decimals = frame[6]
    digit_bytes = frame[7:11]
    overload = any(b > 9 for b in digit_bytes)
    if overload:
        value = None
    else:
        digits = digit_bytes[0] * 1000 + digit_bytes[1] * 100 + digit_bytes[2] * 10 + digit_bytes[3]
        value = digits / (10**decimals)
        if negative:
            value = -value

    mode, fixed_unit = DMM_MODES.get(frame[3], ("unknown", ""))
    if frame[3] == DMM_MODE_OHM:
        unit = DMM_OHM_UNITS.get(frame[11], OHM)
    else:
        unit = fixed_unit if fixed_unit is not None else ""
    return DmmReading(value, decimals, negative, mode, unit, overload, bytes(frame))
