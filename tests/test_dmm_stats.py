"""Min/max tracking over decoded multimeter frames."""

from __future__ import annotations

from dmso2d72 import protocol as p
from dmso2d72.dmm_stats import DmmStats

# Real frames (re/dmm_log.jsonl): 0.993 kOhm, 0.000 A, and an over-range one.
KOHM_993 = "550b010800000300090903030255"
OHM_OVERLOAD = "550b0108000002ff004cff040255"


def reading(hexframe: str):
    return p.decode_dmm(bytes.fromhex(hexframe))


def test_starts_empty():
    stats = DmmStats()
    assert stats.min is None and stats.max is None
    assert stats.count == 0
    assert stats.format(None) == "--"


def test_tracks_min_and_max():
    stats = DmmStats()
    for frame in (
        "550b010a01000301000000050155",  # 1.000 V
        "550b010a01000303000000050155",  # 3.000 V
        "550b010a01000302000000050155",  # 2.000 V
    ):
        stats.update(reading(frame))
    assert stats.min == 1.0
    assert stats.max == 3.0
    assert stats.count == 3


def test_overload_counted_but_does_not_widen_range():
    stats = DmmStats()
    stats.update(reading(KOHM_993))
    stats.update(reading(OHM_OVERLOAD))
    assert stats.overloads == 1
    assert stats.count == 2
    # The OL sample must not become a new extreme.
    assert stats.min == 0.993
    assert stats.max == 0.993


def test_reset_clears_everything():
    stats = DmmStats()
    stats.update(reading(KOHM_993))
    stats.reset()
    assert stats.min is None and stats.max is None
    assert stats.count == 0 and stats.overloads == 0


def test_none_reading_ignored():
    stats = DmmStats()
    stats.update(None)
    assert stats.count == 0


def test_format_matches_reading_precision():
    stats = DmmStats()
    r = reading(KOHM_993)
    stats.update(r)
    # Same decimals and unit as the live readout, so the columns line up.
    assert stats.format(stats.min) == r.formatted()
