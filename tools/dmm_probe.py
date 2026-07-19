#!/usr/bin/env python3
"""Probe the DMSO2D72 for its (undocumented) multimeter readout command.

This is a reverse-engineering aid: it sends commands to the device and logs
whatever comes back on the bulk IN endpoint, so we can (a) find the command
that returns a DMM reading and (b) correlate the returned bytes with known
physical inputs to decode them.

It is deliberately conservative: it only ever sends the 10-byte command
framing the device already expects, with zero payloads by default, and never
touches the scope/AWG setting functions during a sweep. If the device ever
misbehaves, power-cycle it (a plain power-off recovers it).

Every response is appended to re/dmm_log.jsonl with a --label describing what
was physically connected, so the decode work can be done offline afterwards.

Examples
--------
    # See which candidate commands return data (device in DMM mode):
    python tools/dmm_probe.py --find --label "9V battery on V/COM"

    # Poll one command and watch which bytes move as you change the input:
    python tools/dmm_probe.py --func 0x0001 --cmd 0x00 --poll \
        --label "AWG 2.0V DC loopback"

    # Single shot:
    python tools/dmm_probe.py --func 0x0001 --cmd 0x00 --label "open leads"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow running straight from a checkout without installing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dmso2d72 import protocol as p  # noqa: E402
from dmso2d72.device import DeviceError, DeviceNotFound, Dmso2d72  # noqa: E402

LOG_PATH = Path(__file__).resolve().parent.parent / "re" / "dmm_log.jsonl"

# Evidence-based candidate read commands (see re/DMM_PROTOCOL.md). Each is
# (func, cmd). The sweep also tries cmd 0x00..0x1f under each candidate func.
CANDIDATE_FUNCS = [0x0001, 0x0101, 0x0103, 0x0003]


def log(entry: dict) -> None:
    LOG_PATH.parent.mkdir(exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def record(func: int, cmd: int, val: bytes, resp: bytes, label: str) -> dict:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "func": func,
        "cmd": cmd,
        "val": val.hex(),
        "recv_hex": resp.hex(),
        "recv_len": len(resp),
        "label": label,
    }
    log(entry)
    return entry


def hexbytes(b: bytes) -> str:
    return " ".join(f"{x:02x}" for x in b) if b else "(no response)"


def do_find(dev: Dmso2d72, label: str) -> None:
    print("Trying candidate DMM read commands (device should be in DMM mode)...\n")
    hits = []
    for func in CANDIDATE_FUNCS:
        for cmd in range(0x20):
            resp = dev.raw_query(func, cmd)
            record(func, cmd, b"\x00\x00\x00\x00", resp, label)
            if resp:
                print(f"  func=0x{func:04x} cmd=0x{cmd:02x} -> {len(resp):2d} bytes: {hexbytes(resp)}")
                hits.append((func, cmd, resp))
            time.sleep(0.02)
    print()
    if hits:
        print(f"{len(hits)} command(s) returned data. Re-run with --poll on a promising")
        print("one and change the input to see which bytes track the reading.")
    else:
        print("No command returned data. The device may not stream DMM values over")
        print("USB at all (see the feasibility note in re/DMM_PROTOCOL.md), or the")
        print("read command is outside the candidate set — widen CANDIDATE_FUNCS.")


def do_poll(dev: Dmso2d72, func: int, cmd: int, val: bytes, label: str) -> None:
    print(f"Polling func=0x{func:04x} cmd=0x{cmd:02x}  (Ctrl-C to stop)\n")
    prev = None
    try:
        while True:
            resp = dev.raw_query(func, cmd, val)
            record(func, cmd, val, resp, label)
            if resp != prev:
                marker = ""
                if prev is not None and len(prev) == len(resp):
                    moved = [i for i in range(len(resp)) if resp[i] != prev[i]]
                    marker = f"   changed bytes: {moved}"
                print(f"{hexbytes(resp)}{marker}")
                prev = resp
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nstopped.")


def do_single(dev: Dmso2d72, func: int, cmd: int, val: bytes, label: str) -> None:
    resp = dev.raw_query(func, cmd, val)
    record(func, cmd, val, resp, label)
    print(f"func=0x{func:04x} cmd=0x{cmd:02x} val={val.hex()} -> {hexbytes(resp)}")


def parse_int(s: str) -> int:
    return int(s, 0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default="", help="what is physically connected to the DMM inputs")
    ap.add_argument("--func", type=parse_int, default=0x0001, help="function code (e.g. 0x0001)")
    ap.add_argument("--cmd", type=parse_int, default=0x00, help="command byte")
    ap.add_argument("--val", default="00000000", help="4-byte value as hex (default zeros)")
    ap.add_argument("--find", action="store_true", help="try candidate read commands and report hits")
    ap.add_argument("--poll", action="store_true", help="repeat and show a live byte diff")
    ap.add_argument("--dmm-screen", action="store_true", help="switch device to DMM screen first")
    args = ap.parse_args()

    val = bytes.fromhex(args.val)
    if len(val) != 4:
        ap.error("--val must be exactly 4 bytes (8 hex chars)")

    try:
        dev = Dmso2d72()
    except DeviceNotFound as e:
        print(e, file=sys.stderr)
        return 1
    except DeviceError as e:
        print(e, file=sys.stderr)
        return 2

    print(f"Connected: {dev.product}")
    if not args.label:
        print("warning: no --label given; log entries won't say what was connected.\n")

    try:
        if args.dmm_screen:
            dev.set_screen(p.SCREEN_DMM)
            time.sleep(0.5)
        if args.find:
            do_find(dev, args.label)
        elif args.poll:
            do_poll(dev, args.func, args.cmd, val, args.label)
        else:
            do_single(dev, args.func, args.cmd, val, args.label)
    finally:
        dev.close()
    print(f"\nlog: {LOG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
