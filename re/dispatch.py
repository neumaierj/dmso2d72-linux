#!/usr/bin/env python3
"""Enumerate the (func, cmd) command table the firmware checks.

The parser loads func via `ldrh rX, [rY, #2]` and cmd via `ldrb rX, [rY, #4]`
then compares against immediates. We walk the disassembly and, whenever we see
a halfword load from offset 2 (func) or a byte load from offset 4 (cmd)
followed shortly by a cmp #imm, record the constant. This reconstructs the set
of commands the device recognises, including any not in our known list.
"""

from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG

BASE = 0x08000000
FW = open("27D72_dump.bin", "rb").read()
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True

KNOWN_FUNCS = {0x0000: "SCOPE_SETTING", 0x0100: "SCOPE_CAPTURE", 0x0002: "AWG_SETTING", 0x0003: "SCREEN"}

# Disassemble the command-handling region generously.
REGION_END = 0x08002000
insns = list(md.disasm(FW[: REGION_END - BASE], BASE))

# Track, per register, whether it currently holds func (offset2) or cmd (offset4).
func_pairs = []  # (addr, func_value)
cmd_pairs = []   # (addr, cmd_value)

# reg -> tag ("func"/"cmd") most recently loaded into it
reg_tag = {}

for insn in insns:
    m = insn.mnemonic
    ops = insn.operands
    if m in ("ldrh", "ldrh.w") and len(ops) == 2 and ops[1].type == ARM_OP_MEM:
        dst = insn.reg_name(ops[0].reg)
        if ops[1].mem.disp == 2:
            reg_tag[dst] = "func"
        else:
            reg_tag.pop(dst, None)
    elif m in ("ldrb", "ldrb.w") and len(ops) == 2 and ops[1].type == ARM_OP_MEM:
        dst = insn.reg_name(ops[0].reg)
        if ops[1].mem.disp == 4:
            reg_tag[dst] = "cmd"
        else:
            reg_tag.pop(dst, None)
    elif m in ("cmp", "cmp.w") and len(ops) == 2 and ops[0].type == ARM_OP_REG and ops[1].type == ARM_OP_IMM:
        reg = insn.reg_name(ops[0].reg)
        tag = reg_tag.get(reg)
        if tag == "func":
            func_pairs.append((insn.address, ops[1].imm))
        elif tag == "cmd":
            cmd_pairs.append((insn.address, ops[1].imm))
    elif m in ("uxtb", "uxth", "mov", "movs") and ops and ops[0].type == ARM_OP_REG:
        # value moved; propagate tag from src if simple reg move, else clear dst
        dst = insn.reg_name(ops[0].reg)
        if len(ops) == 2 and ops[1].type == ARM_OP_REG:
            src = insn.reg_name(ops[1].reg)
            if reg_tag.get(src):
                reg_tag[dst] = reg_tag[src]
            else:
                reg_tag.pop(dst, None)

print("## func values compared (from ldrh [x,#2])")
seen = {}
for addr, val in func_pairs:
    seen.setdefault(val, []).append(addr)
for val in sorted(seen):
    print(f"  func=0x{val:04x} {KNOWN_FUNCS.get(val,'?'):16s} at " + " ".join(f"0x{a:08x}" for a in seen[val][:8]))

print("\n## cmd values compared (from ldrb [x,#4])")
seen = {}
for addr, val in cmd_pairs:
    seen.setdefault(val, []).append(addr)
for val in sorted(seen):
    print(f"  cmd=0x{val:02x} ({val:3d})  {len(seen[val]):3d}x  first at 0x{seen[val][0]:08x}")
