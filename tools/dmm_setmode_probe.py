#!/usr/bin/env python3
"""Closed-loop probe for a USB command that switches the DMM measurement mode.

NOTE: a previous "superseded -- no such command exists" note here was wrong and
has been removed. The device owner watched the screen during a run of this tool
and the device *did* switch modes (though the bottom-line mode indicator did not
follow, leaving it inconsistent). Whether a write changes the real measurement
mode or only the mode the frame reports is still undetermined -- see
re/DMM_PROTOCOL.md. Judge results by the readings, not the reported mode.

The device's mode (DC/AC volts, current, resistance, ...) is normally chosen with
the front-panel buttons. This tool looks for a USB command that changes it: for
each candidate it reads the current mode (func=0x0101 status frame), sends the
candidate write command, then re-reads and reports whether the mode bytes
(byte 4 AC/DC, byte 11 range, byte 12 category) changed.

A change is only reported if it is still present after a 1 s settle. Writes to
func=0x0001 make the status frame flicker to another mode for a sample or two
without the mode really changing; an earlier version of this tool reported those
transients as hits. Confirm anything found against the device's own screen --
the status frame alone is not sufficient evidence. See re/DMM_PROTOCOL.md.

Conservative: it only sends func=0x0001 (the unused settings-family code; scope
0x0000 / AWG 0x0002 / screen 0x0003 are never touched), with small payloads. It
re-asserts the DMM screen if a command knocks the device off it. If the device
ever misbehaves, power-cycle it. Every attempt is logged to re/dmm_log.jsonl.

    python tools/dmm_setmode_probe.py            # sweep func=0x0001
    python tools/dmm_setmode_probe.py --func 0x0001 --cmd 0x00 --val0 3  # one shot
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dmso2d72 import protocol as p  # noqa: E402
from dmso2d72.device import DeviceError, DeviceNotFound, Dmso2d72  # noqa: E402

LOG_PATH = Path(__file__).resolve().parent.parent / "re" / "dmm_log.jsonl"


def log(entry: dict) -> None:
    LOG_PATH.parent.mkdir(exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def mode_signature(dev: Dmso2d72):
    """Return (frame_hex, mode_str, (byte4, byte11, byte12)) or None."""
    frame = dev.raw_query(p.FUNC_DMM_STATUS, 0x00)
    if len(frame) != p.DMM_FRAME_LEN:
        return None
    try:
        r = p.decode_dmm(frame)
    except ValueError:
        return None
    return frame.hex(), f"{r.mode} [{r.unit}]", (frame[4], frame[11], frame[12])


def ensure_dmm_screen(dev: Dmso2d72):
    sig = mode_signature(dev)
    if sig is None:
        dev.set_screen(p.SCREEN_DMM)
        time.sleep(0.5)
    return mode_signature(dev)


def try_command(dev: Dmso2d72, func: int, cmd: int, val0: int) -> bool:
    before = ensure_dmm_screen(dev)
    dev._send(p.build_command(func, cmd, p.val_u8(val0)))
    time.sleep(0.2)
    after = mode_signature(dev)
    # Writes to this function make the status read flicker for a sample or two
    # without the mode actually changing (see re/DMM_PROTOCOL.md). Only count a
    # change that is still there after the device has settled.
    time.sleep(1.0)
    settled = mode_signature(dev)
    moved = bool(before and after and before[2] != after[2])
    changed = bool(moved and settled and before[2] != settled[2])
    off_screen = after is None
    log({
        "ts": datetime.now(timezone.utc).isoformat(),
        "session": "setmode",
        "func": func, "cmd": cmd, "val0": val0,
        "before": before[0] if before else None,
        "after": after[0] if after else None,
        "settled": settled[0] if settled else None,
        "before_mode": before[1] if before else None,
        "after_mode": after[1] if after else None,
        "settled_mode": settled[1] if settled else None,
        "transient": moved and not changed,
        "changed": changed, "off_screen": off_screen,
    })
    if moved and not changed:
        print(f"  func=0x{func:04x} cmd=0x{cmd:02x} val0={val0} -> transient only "
              f"({before[1]} -> {after[1]} -> {settled[1]}), ignored")
    if off_screen:
        print(f"  func=0x{func:04x} cmd=0x{cmd:02x} val0={val0} -> LEFT DMM SCREEN "
              f"(re-asserting)")
        dev.set_screen(p.SCREEN_DMM)
        time.sleep(0.5)
    elif changed:
        print(f"  func=0x{func:04x} cmd=0x{cmd:02x} val0={val0} -> MODE CHANGED: "
              f"{before[1]} -> {after[1]}")
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--func", type=lambda s: int(s, 0), default=0x0001)
    ap.add_argument("--cmd", type=lambda s: int(s, 0), default=None, help="single cmd (else sweep 0x00-0x0f)")
    ap.add_argument("--val0", type=lambda s: int(s, 0), default=None, help="single val0 (else sweep 0x00-0x0a)")
    args = ap.parse_args()

    try:
        dev = Dmso2d72()
    except (DeviceNotFound, DeviceError) as e:
        print(e, file=sys.stderr)
        return 1
    print(f"Connected: {dev.product}")

    start = ensure_dmm_screen(dev)
    if start is None:
        print("Could not get a DMM status frame; is the device on the multimeter screen?")
        dev.close()
        return 2
    print(f"Starting mode: {start[1]}  frame={start[0]}\n")

    cmds = [args.cmd] if args.cmd is not None else range(0x10)
    vals = [args.val0] if args.val0 is not None else range(0x0B)
    hits = []
    try:
        for cmd in cmds:
            for val0 in vals:
                if try_command(dev, args.func, cmd, val0):
                    hits.append((cmd, val0))
                time.sleep(0.05)
    finally:
        dev.close()

    print()
    if hits:
        print(f"{len(hits)} command(s) changed the mode: "
              + ", ".join(f"cmd=0x{c:02x} val0={v}" for c, v in hits))
        print("Re-run single ones with --cmd/--val0 to map each mode.")
    else:
        print("No func=0x%04x command changed the DMM mode. Either the switch uses a"
              % args.func)
        print("different mechanism (front-panel key events) or is not exposed over USB.")
    print(f"log: {LOG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
