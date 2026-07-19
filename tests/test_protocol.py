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
