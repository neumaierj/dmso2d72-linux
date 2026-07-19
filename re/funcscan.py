#!/usr/bin/env python3
"""Find func comparisons by proximity: a halfword load from offset 2 followed
within a small window by any cmp #imm. Also lists all cmp immediates in the
command region that fall in the plausible func range (high byte set)."""

from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG

BASE = 0x08000000
FW = open("27D72_dump.bin", "rb").read()
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True
REGION_END = 0x08002000
insns = list(md.disasm(FW[: REGION_END - BASE], BASE))

func_cmps = {}
WINDOW = 6
for i, insn in enumerate(insns):
    if insn.mnemonic.startswith("ldrh") and len(insn.operands) == 2:
        mem = insn.operands[1]
        if mem.type == ARM_OP_MEM and mem.mem.disp == 2:
            # look ahead for cmp #imm
            for j in range(i + 1, min(i + 1 + WINDOW, len(insns))):
                nx = insns[j]
                if nx.mnemonic.startswith("cmp") and len(nx.operands) == 2 and nx.operands[1].type == ARM_OP_IMM:
                    func_cmps.setdefault(nx.operands[1].imm, []).append(nx.address)
                    break
                if nx.mnemonic in ("bl", "blx", "bx", "pop"):
                    break

print("## func compared shortly after ldrh[.,#2]")
for v in sorted(func_cmps):
    print(f"  func=0x{v:04x}  {len(func_cmps[v])}x  at " + " ".join(f"0x{a:08x}" for a in func_cmps[v][:10]))

# All cmp immediates with high byte set (candidate 16-bit funcs) in region
print("\n## all cmp #imm with 0x100 <= imm <= 0x0fff in command region")
big = {}
for insn in insns:
    if insn.mnemonic.startswith("cmp") and len(insn.operands) == 2 and insn.operands[1].type == ARM_OP_IMM:
        v = insn.operands[1].imm
        if 0x100 <= v <= 0x0FFF:
            big.setdefault(v, []).append(insn.address)
for v in sorted(big):
    print(f"  0x{v:04x}  {len(big[v])}x  at " + " ".join(f"0x{a:08x}" for a in big[v][:6]))
