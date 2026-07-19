#!/usr/bin/env python3
"""Disassemble a window around a given address (Thumb-2), aligned."""
import sys

from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs

BASE = 0x08000000
FW = open("27D72_dump.bin", "rb").read()
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True

addr = int(sys.argv[1], 16)
before = int(sys.argv[2]) if len(sys.argv) > 2 else 40
after = int(sys.argv[3]) if len(sys.argv) > 3 else 80

start = addr - before
off = start - BASE
for insn in md.disasm(FW[off : off + before + after], start):
    marker = "  <<<" if insn.address == addr else ""
    print(f"0x{insn.address:08x}: {insn.mnemonic:8s} {insn.op_str}{marker}")
