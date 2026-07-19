#!/usr/bin/env python3
"""Static analysis of the 2D72 STM32 firmware to locate the command dispatcher.

We know the host->device command framing (see src/dmso2d72/protocol.py):
  byte0 = 0x00 (idx)
  byte1 = 0x0A (magic)
  byte2..3 = func (u16 LE)
  byte4 = cmd
  byte5..8 = value
  byte9 = 0x00

Goal: find the code that parses this and dispatches on func/cmd, then look at
the DMM branch. This script does linear Thumb-2 disassembly with capstone and
reports comparisons against the known constants so we can eyeball the dispatch.
"""

import sys
from collections import defaultdict

from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs

BASE = 0x08000000
FW = open(sys.argv[1] if len(sys.argv) > 1 else "27D72_dump.bin", "rb").read()

# Known protocol constants worth finding as immediates in compare instructions.
FUNCS = {
    0x0000: "FUNC_SCOPE_SETTING",
    0x0100: "FUNC_SCOPE_CAPTURE",
    0x0002: "FUNC_AWG_SETTING",
    0x0003: "FUNC_SCREEN_SETTING",
}
SCREEN_VALS = {0x00: "SCREEN_SCOPE", 0x01: "SCREEN_DMM", 0x02: "SCREEN_AWG"}
MAGIC = 0x0A

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True


def disasm_all():
    """Linear sweep; Thumb is 2/4 bytes. Yields (addr, insn)."""
    for insn in md.disasm(FW, BASE):
        yield insn


def find_immediate_compares():
    """Report cmp/cmp-like instructions with small immediates of interest."""
    interesting = set(FUNCS) | set(SCREEN_VALS) | {MAGIC, 0x16}
    hits = defaultdict(list)
    for insn in disasm_all():
        if insn.mnemonic not in ("cmp", "cmn", "sub", "subs", "movs", "mov", "cmp.w"):
            continue
        for op in insn.operands:
            if op.type == 2:  # immediate
                if op.imm in interesting:
                    hits[op.imm].append(insn.address)
    return hits


def find_word_constants(values):
    """Find little-endian 32-bit words in the image (literal pools)."""
    out = defaultdict(list)
    for i in range(0, len(FW) - 3):
        w = int.from_bytes(FW[i : i + 4], "little")
        if w in values:
            out[w].append(BASE + i)
    return out


def main():
    print(f"# firmware {len(FW)} bytes, base 0x{BASE:08x}\n")

    print("## Immediate comparisons against known constants")
    hits = find_immediate_compares()
    for imm in sorted(hits):
        name = FUNCS.get(imm) or SCREEN_VALS.get(imm) or ("MAGIC_0x0A" if imm == MAGIC else "")
        addrs = hits[imm]
        print(f"  0x{imm:04x} {name:20s} {len(addrs):4d} sites: "
              + " ".join(f"0x{a:08x}" for a in addrs[:12])
              + (" ..." if len(addrs) > 12 else ""))

    print("\n## 32-bit literal-pool constants (func/screen values as words)")
    words = find_word_constants(set(FUNCS) | {0x2D42, 0x0483})
    for w in sorted(words):
        name = FUNCS.get(w) or {0x2D42: "USB_PID", 0x0483: "USB_VID"}.get(w, "")
        print(f"  0x{w:08x} {name:20s} {len(words[w])} sites: "
              + " ".join(f"0x{a:08x}" for a in words[w][:8]))


if __name__ == "__main__":
    main()
