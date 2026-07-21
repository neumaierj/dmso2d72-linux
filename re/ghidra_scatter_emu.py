#!/usr/bin/env python3
"""Emulate image B's scatter-load so the firmware initialises .data itself,
then read the USB endpoint callback tables out of the resulting RAM image.

The scatter-load loop at 0x08030abc runs before any peripheral setup, so it
touches nothing but flash and SRAM -- it can be emulated with memory alone.
"""
import struct
import sys

from unicorn import *
from unicorn.arm_const import *

FLASH_BASE = 0x08000000
RAM_BASE = 0x20000000
RAM_SIZE = 0x10000
STACK_TOP = 0x2000F560  # image B initial SP, from its vector table

SCATTER = 0x08030ABC  # the scatter-load loop (push {r4,lr} ... pop {r4,pc})
RETURN_MAGIC = 0xDEADBEF0  # sentinel LR: emulation stops when we return here

fw = open(sys.argv[1], "rb").read()

mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
mu.mem_map(FLASH_BASE, (len(fw) + 0xFFF) & ~0xFFF)
mu.mem_write(FLASH_BASE, fw)
mu.mem_map(RAM_BASE, RAM_SIZE)
mu.mem_write(RAM_BASE, b"\x00" * RAM_SIZE)
# Landing pad for the sentinel return address.
mu.mem_map(RETURN_MAGIC & ~0xFFF, 0x1000)

mu.reg_write(UC_ARM_REG_SP, STACK_TOP)
mu.reg_write(UC_ARM_REG_LR, RETURN_MAGIC | 1)

written = []


def on_write(uc, access, address, size, value, user_data):
    if RAM_BASE <= address < RAM_BASE + RAM_SIZE:
        written.append((address, size, value))


mu.hook_add(UC_HOOK_MEM_WRITE, on_write)

try:
    mu.emu_start(SCATTER | 1, RETURN_MAGIC, timeout=30 * UC_SECOND_SCALE)
    print("scatter-load returned cleanly")
except UcError as e:
    pc = mu.reg_read(UC_ARM_REG_PC)
    print(f"emulation stopped: {e} at PC=0x{pc:08x}")

ram = mu.mem_read(RAM_BASE, RAM_SIZE)
nonzero = sum(1 for b in ram if b)
print(f"RAM bytes initialised (non-zero): {nonzero} / {RAM_SIZE}")
lo = min((a for a, _, _ in written), default=None)
hi = max((a for a, _, _ in written), default=None)
if lo is not None:
    print(f"RAM write span: 0x{lo:08x} .. 0x{hi:08x}  ({len(written)} writes)")


def word(addr):
    off = addr - RAM_BASE
    return struct.unpack_from("<I", ram, off)[0]


print()
for name, tbl in (("pEpInt_OUT (RX)", 0x20004974), ("pEpInt_IN  (TX)", 0x20004958)):
    print(f"=== {name} @ 0x{tbl:08x} ===")
    for i in range(7):
        v = word(tbl + i * 4)
        fn = v & ~1
        ok = (v & 1) and 0x08000000 <= fn < 0x08080000
        print(f"   EP{i+1}: 0x{v:08x}" + ("   <-- HANDLER" if ok else ""))
