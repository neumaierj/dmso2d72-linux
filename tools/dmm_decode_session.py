#!/usr/bin/env python3
"""Guided capture session to decode the DMM data frame.

Walks you through applying a series of KNOWN inputs to the multimeter. For each
one you type what the device's own screen shows, then it captures the response
frames (both the status frame func=0x0101 and the data frame func=0x0103) and
appends them to re/dmm_log.jsonl with your label. Afterwards we compare the
frames across inputs to work out which bytes encode the value, mode and range.

Run it with the device connected and set to the multimeter function you want to
characterise (e.g. DC volts). Follow the prompts.

    python tools/dmm_decode_session.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dmso2d72 import protocol as p  # noqa: E402
from dmso2d72.device import DeviceError, DeviceNotFound, Dmso2d72  # noqa: E402

LOG_PATH = Path(__file__).resolve().parent.parent / "re" / "dmm_log.jsonl"

# The commands worth capturing for each input (func, cmd) -> short name.
CAPTURES = [
    (0x0101, 0x00, "status"),
    (0x0103, 0x00, "data-hdr"),
    (0x0103, 0x01, "data"),
    (0x0103, 0x06, "data-long"),
]

# Suggested inputs; you can also just enter your own at each step.
SUGGESTED = [
    "open leads (nothing connected)",
    "shorted leads (0 V)",
    "1.5 V AA battery",
    "9 V block battery",
    "known resistor (enter value + mode)",
]


def log(entry: dict) -> None:
    LOG_PATH.parent.mkdir(exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def capture_frames(dev: Dmso2d72, label: str, screen_reading: str) -> None:
    for func, cmd, name in CAPTURES:
        frames = []
        for _ in range(5):
            frames.append(dev.raw_query(func, cmd).hex())
            time.sleep(0.05)
        stable = len(set(frames)) == 1
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": "decode",
            "func": func,
            "cmd": cmd,
            "frame_name": name,
            "recv_hex": frames[0],
            "recv_len": len(frames[0]) // 2,
            "distinct_over_5": len(set(frames)),
            "label": label,
            "screen_reading": screen_reading,
        }
        log(entry)
        flag = "" if stable else f"  (varied: {len(set(frames))} distinct!)"
        print(f"    {name:10s} func=0x{func:04x} cmd=0x{cmd:02x} -> {frames[0]}{flag}")


def main() -> int:
    try:
        dev = Dmso2d72()
    except (DeviceNotFound, DeviceError) as e:
        print(e, file=sys.stderr)
        return 1
    print(f"Connected: {dev.product}\n")
    print("For each input: set it up on the DMM, read the device's OWN screen,")
    print("and type both here. Enter an empty label to finish.\n")
    print("Suggested inputs to cover:")
    for s in SUGGESTED:
        print(f"  - {s}")
    print()

    try:
        while True:
            label = input("Input description (empty to finish): ").strip()
            if not label:
                break
            reading = input("  What does the device screen show (e.g. '9.00 V DC', 'OL', '0.998 kOhm')? ").strip()
            print("  capturing...")
            capture_frames(dev, label, reading)
            print()
    finally:
        dev.close()
    print(f"\nDone. Log: {LOG_PATH}")
    print("Send me the log (or its new lines) and I'll decode the frame layout.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
