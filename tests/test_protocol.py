"""Tests for command framing and capture decoding.

Expected byte sequences are derived from the community reverse-engineered
implementations (lucaoli/Hantek in C, hkoosha/hanteker in Rust), which use
a packed 10-byte struct: idx, 0x0A, func (u16 LE), cmd, val[4], last.
"""

import pytest

from dmso2d72 import protocol as p


def test_command_length_and_header():
    cmd = p.scope_setting(p.SCOPE_START_STOP, p.val_u8(1))
    assert len(cmd) == 10
    assert cmd[0] == 0x00
    assert cmd[1] == 0x0A


def test_scope_start():
    cmd = p.scope_setting(p.SCOPE_START_STOP, p.val_u8(1))
    assert cmd == bytes([0x00, 0x0A, 0x00, 0x00, 0x0C, 1, 0, 0, 0, 0])


def test_enable_ch2():
    cmd = p.scope_setting(p.SCOPE_ENABLE_CH2, p.val_u8(1))
    assert cmd == bytes([0x00, 0x0A, 0x00, 0x00, 0x06, 1, 0, 0, 0, 0])


def test_capture_command_func_is_little_endian():
    # FUNC_SCOPE_CAPTURE = 0x0100 -> bytes 0x00, 0x01
    cmd = p.capture_command(1000, 2)
    # (1000 * 2) / 2 = 1000 = 0x03E8 little-endian, repeated twice
    assert cmd == bytes([0x00, 0x0A, 0x00, 0x01, 0x16, 0xE8, 0x03, 0xE8, 0x03, 0x00])


def test_capture_command_rejects_tiny_captures():
    with pytest.raises(ValueError):
        p.capture_command(32, 1)


def test_time_offset_u32():
    cmd = p.scope_setting(p.SCOPE_OFFSET_TIME, p.val_u32(0x01020304))
    assert cmd[5:9] == bytes([0x04, 0x03, 0x02, 0x01])


def test_awg_frequency():
    cmd = p.awg_frequency_command(1_000_000)
    assert cmd == bytes([0x00, 0x0A, 0x02, 0x00, 0x01, 0x40, 0x42, 0x0F, 0x00, 0x00])


def test_awg_amplitude_positive():
    # 2.5 V -> 2500 mV = 0x09C4 LE, sign word 0
    cmd = p.awg_amplitude_command(2.5)
    assert cmd == bytes([0x00, 0x0A, 0x02, 0x00, 0x02, 0xC4, 0x09, 0x00, 0x00, 0x00])


def test_awg_amplitude_negative_sets_sign_word():
    # -1.234 V -> 1234 mV = 0x04D2 LE, sign word 1
    cmd = p.awg_amplitude_command(-1.234)
    assert cmd == bytes([0x00, 0x0A, 0x02, 0x00, 0x02, 0xD2, 0x04, 0x01, 0x00, 0x00])


def test_awg_trap_duty_order_is_rise_high_low():
    cmd = p.awg_trap_duty_command(10, 30, 40)
    assert cmd[5:9] == bytes([10, 30, 40, 0])


def test_decode_capture_two_channels_interleaved():
    buf = bytes([1, 101, 2, 102, 3, 103])
    decoded = p.decode_capture(buf, [1, 2])
    assert decoded == {1: [1, 2, 3], 2: [101, 102, 103]}


def test_decode_capture_single_channel():
    buf = bytes([5, 6, 7])
    assert p.decode_capture(buf, [2]) == {2: [5, 6, 7]}


def test_raw_to_divisions():
    # baseline 29 = bottom of the 8-division screen
    assert p.raw_to_divisions(29) == pytest.approx(-4.0)
    assert p.raw_to_divisions(29 + 202) == pytest.approx(4.0)
    assert p.raw_to_divisions(29 + 101) == pytest.approx(0.0)


def test_scale_tables_complete():
    assert len(p.VOLT_SCALES) == 10
    assert len(p.TIME_SCALES) == 34
    codes = [code for code, _ in p.TIME_SCALES.values()]
    assert codes == list(range(0x22))


# Real FUNC_DMM_STATUS frames captured from the device (re/dmm_log.jsonl),
# each paired with the value shown on the device's own screen.
DMM_FRAMES = [
    ("550b010a01010300000000050155", 0.000, 3),   # open leads, ~0
    ("550b010a01000301040909050155", 1.499, 3),   # 1.5 V battery
    ("550b010a01000200040908050155", 4.98, 2),    # 5 V supply, auto-ranged
    ("550b010a01000303020908050155", 3.298, 3),   # 3.3 V supply
    ("550b010a01000200030909050155", 3.99, 2),    # ~4 V supply, auto-ranged
]


@pytest.mark.parametrize("hexframe,expected,decimals", DMM_FRAMES)
def test_decode_dmm_matches_screen(hexframe, expected, decimals):
    r = p.decode_dmm(bytes.fromhex(hexframe))
    assert r.value == pytest.approx(expected)
    assert r.decimals == decimals
    assert r.mode == "DC Voltage"
    assert r.unit == "V"
    assert r.overload is False


def test_decode_dmm_formatted():
    r = p.decode_dmm(bytes.fromhex("550b010a01000303020908050155"))
    assert r.formatted() == "3.298 V"


# Real resistance/continuity frames captured from the device.
def test_decode_dmm_resistance_kohm():
    r = p.decode_dmm(bytes.fromhex("550b010800000300090905030255"))
    assert r.mode == "Resistance"
    assert r.unit == "kΩ"
    assert r.value == pytest.approx(0.995)
    assert r.formatted() == "0.995 kΩ"


def test_decode_dmm_resistance_ohm():
    r = p.decode_dmm(bytes.fromhex("550b010800000100000002050255"))
    assert r.mode == "Resistance"
    assert r.unit == "Ω"
    assert r.value == pytest.approx(0.2)


def test_decode_dmm_resistance_overload():
    r = p.decode_dmm(bytes.fromhex("550b0108000002ff004cff040255"))
    assert r.mode == "Resistance"
    assert r.overload is True
    assert r.value is None
    assert r.formatted() == "OL MΩ"


def test_decode_dmm_continuity():
    r = p.decode_dmm(bytes.fromhex("550b010900000100000000050255"))
    assert r.mode == "Continuity"
    assert r.unit == "Ω"
    assert r.value == pytest.approx(0.0)


def test_decode_dmm_continuity_overload():
    r = p.decode_dmm(bytes.fromhex("550b0109000001ff004cff050255"))
    assert r.mode == "Continuity"
    assert r.overload is True
    assert r.formatted() == "OL Ω"


def test_decode_dmm_dc_current():
    # captured in DC-Amp mode at rest (screen shows amps)
    r = p.decode_dmm(bytes.fromhex("550b010101010300000000050055"))
    assert r.mode == "DC Current"
    assert r.unit == "A"
    assert r.value == pytest.approx(0.0)


# Real frames for the remaining modes (byte 3 = mode), each paired with the
# device's own screen reading.
@pytest.mark.parametrize("hexframe,mode,unit,value", [
    ("550b010002000300000000050055", "AC Current", "A", 0.0),      # AC 0.000 A
    ("550b010602000300000000050155", "AC Voltage", "V", 0.0),      # AC 0.000 V
    ("550b010301000201000005020055", "DC Current", "mA", 10.05),   # DC 10.05 mA
    ("550b010401000101000002020155", "DC Voltage", "mV", 100.2),   # DC 100.2 mV
    ("550b010202000200000000020055", "AC Current", "mA", 0.0),     # AC 00.00 mA
    ("550b010700000300000000000355", "Capacitance", "nF", 0.0),    # 0.000 nF
])
def test_decode_dmm_more_modes(hexframe, mode, unit, value):
    r = p.decode_dmm(bytes.fromhex(hexframe))
    assert r.mode == mode
    assert r.unit == unit
    assert r.value == pytest.approx(value)


def test_decode_dmm_diode():
    # diode test shares byte3=0x0a with DC volts but has byte4=0x00; 0.599 V drop
    r = p.decode_dmm(bytes.fromhex("550b010a00000300050909050155"))
    assert r.mode == "Diode"
    assert r.unit == "V"
    assert r.value == pytest.approx(0.599)


def test_decode_dmm_diode_open_is_overload():
    # open leads in diode mode -> OL
    r = p.decode_dmm(bytes.fromhex("550b010a000003ff004cff050155"))
    assert r.mode == "Diode"
    assert r.overload is True
    assert r.formatted() == "OL V"


def test_decode_dmm_dcv_vs_diode_differ_only_in_byte4():
    dcv = p.decode_dmm(bytes.fromhex("550b010a01000300000000050155"))
    diode = p.decode_dmm(bytes.fromhex("550b010a00000300000000050155"))
    assert dcv.mode == "DC Voltage"
    assert diode.mode == "Diode"


def test_decode_dmm_sign():
    # byte 5 = 1 marks a negative reading
    frame = bytearray.fromhex("550b010a01000301040909050155")
    frame[5] = 1
    r = p.decode_dmm(bytes(frame))
    assert r.negative is True
    assert r.value == pytest.approx(-1.499)


def test_decode_dmm_rejects_bad_framing():
    with pytest.raises(ValueError):
        p.decode_dmm(bytes.fromhex("00" * 14))
    with pytest.raises(ValueError):
        p.decode_dmm(bytes.fromhex("550b0301"))  # too short
