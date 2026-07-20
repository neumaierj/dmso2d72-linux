// Set up the STM32F103VET6 memory map and vector table before auto-analysis.
// @category ARM
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.lang.Register;
import java.math.BigInteger;

public class Stm32Setup extends GhidraScript {

    private void block(String name, long addr, long len, boolean write, boolean volat)
            throws Exception {
        Memory mem = currentProgram.getMemory();
        Address a = toAddr(addr);
        if (mem.getBlock(a) != null) {
            println("block exists at " + a + ", skipping " + name);
            return;
        }
        MemoryBlock b = mem.createUninitializedBlock(name, a, len, false);
        b.setRead(true);
        b.setWrite(write);
        b.setExecute(false);
        b.setVolatile(volat);
        println("created " + name + " @ " + a + " len=0x" + Long.toHexString(len));
    }

    @Override
    public void run() throws Exception {
        // STM32F103VET6: 512K flash @ 0x08000000 (loaded from file), 64K SRAM.
        block("SRAM", 0x20000000L, 0x10000, true, false);
        block("PERIPH", 0x40000000L, 0x30000, true, true);
        block("PPB", 0xE0000000L, 0x100000, true, true);

        // Flash is also aliased at 0x00000000 on boot, but the image is linked
        // for 0x08000000, so leave the alias out to avoid duplicate functions.

        Register tmode = currentProgram.getRegister("TMode");
        Address base = toAddr(0x08000000L);

        // Vector table: [0]=initial SP, [1]=reset, then exception handlers.
        int created = 0;
        for (int i = 1; i < 76; i++) {
            Address slot = base.add(i * 4);
            long target;
            try {
                target = getInt(slot) & 0xFFFFFFFFL;
            } catch (Exception e) {
                continue;
            }
            // Thumb handler addresses live in flash with bit0 set.
            if ((target & 1) == 0) continue;
            long fn = target & ~1L;
            if (fn < 0x08000000L || fn >= 0x08080000L) continue;

            Address f = toAddr(fn);
            if (tmode != null) {
                currentProgram.getProgramContext()
                        .setValue(tmode, f, f, BigInteger.ONE);
            }
            disassemble(f);
            if (getFunctionAt(f) == null) {
                createFunction(f, null);
            }
            created++;
        }
        println("vector table: seeded " + created + " handlers");
        println("entry (reset): " + toAddr(getInt(base.add(4)) & ~1L));
    }
}
