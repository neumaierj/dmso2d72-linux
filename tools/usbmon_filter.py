#!/usr/bin/env python3
"""Extract 2D72 bulk transfers from a usbmon capture.

Usage:
    tshark -r capture.pcapng -T fields -e usb.endpoint_address -e usb.capdata \
        > transfers.txt
    python3 usbmon_filter.py transfers.txt

Prints one line per transfer with direction, endpoint and hex payload, and
decodes the known 10-byte command framing where it applies.
"""

import sys


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1

    with open(sys.argv[1]) as f:
        for line in f:
            parts = line.split()
            if len(parts) != 2:
                continue
            endpoint, hexdata = parts
            data = bytes.fromhex(hexdata.replace(":", ""))
            ep = int(endpoint, 16)
            direction = "IN " if ep & 0x80 else "OUT"
            note = ""
            if direction == "OUT" and len(data) == 10 and data[1] == 0x0A:
                func = data[2] | (data[3] << 8)
                note = f"  cmd-frame func=0x{func:04x} cmd=0x{data[4]:02x} val={data[5:9].hex()}"
            print(f"{direction} ep=0x{ep:02x} {data.hex()}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
