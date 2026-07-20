# Reverse-engineering the DMSO2D72 multimeter (DMM) protocol

Working notes. Goal: find the USB command that makes the device return a
multimeter reading, and decode the returned bytes — **without** the Windows app,
by analysing the firmware and by correlating the returned data stream with known
physical inputs.

## What is already known (scope/AWG)

Host → device commands are 10-byte packets on bulk OUT endpoint `0x02`:

| offset | field | notes |
|-------:|-------|-------|
| 0 | idx   | 0x00 |
| 1 | magic | 0x0A |
| 2–3 | func (u16 LE) | 0x0000 scope-setting, 0x0002 AWG, 0x0003 screen, 0x0100 scope-capture |
| 4 | cmd   | per-function command |
| 5–8 | value | u8/u16/u32 little-endian |
| 9 | last  | 0x00 |

Waveform data is read back on bulk IN endpoint `0x81`. Switching the device to
its multimeter screen is `func=0x0003, cmd=0x00, value=0x01` (already used by the
app).

## Firmware findings (from `27D72_dump.bin`, STM32F103VET6, 512 KB)

Static analysis with capstone (`analyze.py`, `dispatch.py`, `funcscan.py`,
`context.py` in this directory). Confirmed by reading the disassembly:

1. ~~**The framing above is correct.** The command parser (around `0x08000a00`
   and `0x08001300`) validates `magic == 0x0A` (byte offset 1) and then reads
   func at offset 2, cmd at offset 4, value at offset 6.~~
   **RETRACTED 2026-07-20 — this was a misreading.** See "Ghidra pass" below.
   The function at `0x08000882` (containing the `cmp #0x0a` at `0x080009de`) is
   the **USB chapter-9 standard setup handler**, not the vendor parser. The
   byte at offset 1 is `bRequest`, and `0x0A` is `GET_INTERFACE` — not a magic
   number. Offsets 2/4/6 are `wValue`/`wIndex`/`wLength` of the 8-byte SETUP
   packet, which is why they lined up with func/cmd/value by coincidence.

   The 10-byte framing itself is still correct — it is proven empirically by
   the scope, AWG and DMM code working against real hardware — but the
   *firmware evidence* previously cited for it does not support it.

2. There is a **long if/else dispatch chain** keyed on (func, cmd, value); each
   arm calls a handler. This is where a DMM command would live.
   ⚠️ This claim came from the same scripted pass and should be treated as
   unverified; the chain described above is the chapter-9 request dispatch.

3. **Scripted disassembly could not reliably pin the exact DMM read command.**
   The firmware is stripped, the parser copies the packet pointer through several
   registers, and large stretches above ~`0x08003000` are data/lookup tables that
   a linear disassembler mis-decodes. Reliably extracting the specific func/cmd
   that triggers a CS7721 read + EP `0x81` response needs either:
   - an **interactive Ghidra** session (load as ARM Cortex-M3 @ `0x08000000`,
     let it recover functions and follow xrefs from the parser to the USB-IN
     write and the CS7721 GPIO/serial read), or
   - **hardware probing** with the harness below (faster to a first result).

   The decode step needs hardware regardless, so probing is the practical path.

## Open feasibility question

It is not yet confirmed that the device streams live DMM *values* over USB at
all — the Windows software may only *control* the DMM (mode/range) while the
reading stays on the device's own LCD. The probe harness settles this directly:
if some command returns changing bytes on EP `0x81` while the input changes, a
readout path exists.

## Candidate commands to try (probe harness)

Evidence-based guesses, tried conservatively (zero payload, DMM screen active):

- `func=0x0001` — the one small func value **unused** by scope/AWG/screen; the
  DMM is the "third instrument".
- `func=0x0101`, `func=0x0103` — "read"-class variants (scope-capture sets the
  high byte: `0x0100`); a DMM read may mirror that.
- `func=0x0003` with `cmd` != 0 — a query on the screen/function channel.
- Small `cmd` sweep (0x00–0x1F) under each candidate func.

## Experiment protocol (decode the data stream)

Run `tools/dmm_probe.py` (see `--help`). For each step, pass `--label` describing
what is physically on the DMM inputs; every frame is logged to `re/dmm_log.jsonl`.

1. **Find the read command.** `--find` tries the candidate commands above and
   reports which return a non-empty, *changing* response. Keep the winning
   `func`/`cmd`.
2. **DC volts.** Loop the AWG output (set a DC offset) into the DMM inputs, and/or
   use batteries. Record frames at several known voltages: 0 V, ~1.5 V (AA),
   ~9 V (block), plus a few AWG steps. Set the device to DC-V mode.
   → fit `raw_count → volts` (expect linear, 4000-count full scale); find the
   **sign** byte and the **range/decimal-point** byte by switching ranges.
3. **AC volts.** AWG sine at known amplitudes → AC-mode byte and scaling.
4. **Resistance / continuity.** Known resistors and short/open leads → resistance
   mode byte, overload ("OL") flag, continuity threshold.
5. **Mode/range fields.** Hold the input constant and switch modes on the device
   (and vice-versa) to isolate which bytes encode measurement type vs. range.

Fill in the decode table below as results arrive.

## Hardware probe results (2026-07-19) — read channel FOUND

`tools/dmm_probe.py --find` on the real device: **`func=0x0001` and `func=0x0003`
return nothing; `func=0x0101` and `func=0x0103` return data** (independent of the
low `cmd` byte for 0x0101; `cmd` selects a sub-frame for 0x0103).

- **`func=0x0101`** → constant 14-byte status frame:
  `55 0b 01 0a 00 00 03 00 00 00 00 05 01 55`
  Framing `55 … 55`; likely holds mode/range (`03`, `05`, `01` stand out).
- **`func=0x0103`** → data frames, selected by `cmd`:
  - `cmd=0x01..0x05` → 16 bytes: `55 0b 03 01 | 57 06 75 40 30 37 57 41 05 d4 ff 39`
  - `cmd=0x06` → same payload, 64-byte zero-padded, header `55 0b 03 02 00 06 …`
  - `cmd=0x00`,`0x07`,`0x08` → 5-byte headers `55 0b 03 0X 01`
  - Common framing: `55` start, `0b` const, byte2 = func low byte echo,
    byte3 = sub-frame id, then payload.

**Open problem:** the `func=0x0103/cmd=0x01` data frame is **byte-identical over
30 samples and unchanged from a capture 30 min earlier** — no last-digit jitter.
So it is either (a) the live value and simply very stable, or (b) a config/ID
frame, not the reading. Cannot be resolved without changing a known input and
re-capturing. → run `tools/dmm_decode_session.py` (guided) to capture frames for
open/short leads, 1.5 V, 9 V, and a resistor, each labelled with the device's own
screen reading. Comparing frames across inputs will reveal which bytes are the
value/mode/range.

Note: the random mode-switching seen during `--find` was self-inflicted — the
sweep included `func=0x0003` (screen select), which switched the device to
scope/AWG. The decode session and normal reads never use `func=0x0003` except one
explicit switch-to-DMM.

## SOLVED — value frame fully decoded (all modes, incl. negative and OL)

The live value is the **`func=0x0101`** reply (not `0x0103`, which is a static
config frame). It is 14 bytes, verified against the device screen across every
front-panel mode, both signs and over-range:

```
offset:  0    1    2    3    4    5    6     7   8   9  10   11    12   13
bytes:  0x55 0x0b 0x01  ?  ac/dc sign dec   d1  d2  d3  d4  range cat 0x55
```

| byte(s) | meaning | encoding |
|---------|---------|----------|
| 0, 13   | framing | constant 0x55 |
| 1       | length? | constant 0x0b |
| 2       | func echo | 0x01 |
| 3       | (unreliable) | changes with polarity — see note; not used for mode |
| **4**   | AC/DC/other | 0x01 DC, 0x02 AC, 0x00 (Ω/continuity/cap/diode) |
| 5       | sign | 0 = positive, 1 = negative |
| 6       | decimal places | 3 → `X.XXX`, 2 → `XX.XX`, … (auto-range) |
| 7..10   | 4 display digits | plain binary 0..9 MSB-first, **or `ff 00 4c ff` = OL** |
| 11      | range | 0x05 base (V/A/Ω), 0x02 milli (mV/mA), 0x03 kΩ, 0x04 MΩ, 0x00 nF |
| **12**  | category | 0x00 current, 0x01 voltage, 0x02 resistance/cont, 0x03 capacitance |

**value = (-1)^sign × (d1·1000 + d2·100 + d3·10 + d4) / 10^dec**, and
**overload ("OL")** when any digit byte > 9.

**mode = (byte 4, byte 12)**, unit = f(byte 12 category, byte 11 range).
byte 4 splits AC/DC and marks diode (0x00, category voltage); resistance vs
continuity share (byte4=0x00, byte12=0x02) and are split by byte 3 (0x09 =
continuity, else resistance — both are always positive so byte 3 is stable there).

⚠️ **byte 3 is NOT the mode.** It changes with polarity (positive DC volts =
0x0a, **negative = 0x05**), which is why an earlier byte-3 mode table
mislabelled negative readings. Keying on (byte 4, byte 12) fixes this.

| byte4 | byte12 | mode | unit (from byte 11) |
|------:|-------:|------|------|
| 0x01 | 0x01 | DC Voltage | 0x05 V, 0x02 mV |
| 0x02 | 0x01 | AC Voltage | 0x05 V, 0x02 mV |
| 0x00 | 0x01 | Diode | V (e.g. 0.599; open = OL) |
| 0x01 | 0x00 | DC Current | 0x05 A, 0x02 mA |
| 0x02 | 0x00 | AC Current | 0x05 A, 0x02 mA |
| 0x00 | 0x02 | Resistance / Continuity¹ | 0x05 Ω, 0x03 kΩ, 0x04 MΩ |
| 0x00 | 0x03 | Capacitance | 0x00 nF |

¹ split by byte 3: 0x09 continuity, 0x08 resistance.

Implemented as `protocol.decode_dmm()` (`DMM_TYPES`, `DMM_UNITS`) with real
frames as unit-test fixtures, `device.read_dmm()`, `capture.DmmWorker`, and a
live `gui/dmm_tab.py`.

### Remaining (minor)
- All front-panel DMM modes are decoded (DC/AC volts inc. mV, DC/AC current inc.
  mA, resistance Ω/kΩ/MΩ, continuity, capacitance, diode), both signs and OL.
- Capacitance fully verified: nF (byte 11 = 0x00) and µF (byte 11 = 0x01),
  in-range values confirmed against the screen (47 µF → 48.52, 10 µF → 11.31).
  A 206 µF cap reads **OL** because the device's max capacitance range is 100 µF
  (Hantek 2D72 manual: 40 nF / 400 nF / 4 µF / 40 µF / 100 µF) — correct
  over-range behaviour, not a fault.
- Sign confirmed with a genuine negative reading (DC -1.997 V).

**All DMM modes and ranges are now decoded and verified against the device
screen.** Nothing outstanding.

## Remote mode selection (func=0x0001) — attempted, NO usable result

Goal: find a USB command that changes the DMM mode/range remotely (today only
the front-panel dial/buttons can). Tool: `tools/dmm_setmode_probe.py`, which
reads the status frame, sends a candidate `func=0x0001` command (the one
small func value unused by scope/AWG/screen), re-reads, and reports whether
`(byte4, byte11, byte12)` changed. Logged to `re/dmm_log.jsonl`
(`session: "setmode"`, `"setmode-artifact"`).

**Outcome: no command was shown to set the DMM mode.** The sweep appeared to
find several, but follow-up testing showed those "hits" were measurement
artifacts. Recorded here so the next attempt doesn't repeat the mistake.

### Why the sweep's hits were not real

The sweep reported 7 mode changes (`cmd=0x00 val0=6`, `cmd=0x01 val0=7`,
`cmd=0x03 val0=7`, `cmd=0x0a val0=2/3/7/10`). Three checks invalidate them:

1. **The probe cannot attribute a change to a command.** It compares a read
   taken 0.2 s before a write with one 0.2 s after. Replaying the log,
   **76 of 187 rows have `before` ≠ the previous row's `after`** — the state
   moved *between* probes, with no command in between. The before/after
   window is far too narrow to isolate cause, so a `changed=True` is just as
   likely to be catching ambient movement as a command's effect.
2. **The state is not stable while writes are in flight, and reverts.**
   Sending `cmd=0x0a val0=3` at the sweep's 0.2 s cadence, 20 consecutive
   reads gave Capacitance 19× / Diode 1×; stopping the writes and waiting
   3 s returned it to Diode 9×/10. A single write followed by a 6 s poll
   showed Capacitance for exactly **one** read at +1.1 s, then Diode for the
   rest. So the apparent mode "change" does not persist.
3. **The same command with a real settle time does nothing.** `cmd=0x0a
   val0=3` sent 4× with 2 s between reads: no change, all four Diode.

Meanwhile the frame is genuinely rock-stable at rest — 30 reads over 7.5 s
with zero writes were byte-identical — so this is not general read noise or
someone touching the dial. It is specifically writes to `func=0x0001` that
perturb what the status read returns, transiently.

### What that means

Two explanations remain, and **they cannot be told apart from the USB side
alone**:

- the write briefly changes real internal state, but firmware re-asserts the
  mode from the physical rotary switch (which never moved), so it snaps back;
- the write perturbs the status *reporting* path, and the odd frames are
  stale/alternate sub-frames rather than a real mode change.

Under either one, `func=0x0001` is **not a usable remote mode-select**: the
state does not persist, so there is nothing to build on. This also fits the
long-standing open question above — the Windows app may only control
mode/range in ways tied to the physical switch.

### If picking this up again

- **Watch the physical screen.** Every conclusion here is from the status
  frame alone, which is exactly what made the false positives possible. The
  device's own LCD is the ground truth, as in `dmm_decode_session.py`.
- **Never trust a single before/after read.** `try_command` now re-reads
  after 1.0 s and only reports a change that is still present (see below);
  a change that vanishes is an artifact, not a result.
- The remaining honest lead is firmware analysis (interactive Ghidra, per the
  section above), not further blind sweeps of this function.

## Ghidra pass (2026-07-20) — infrastructure mapped, no mode-set found

Ghidra headless **works well here and supersedes the capstone scripts**
(`analyze.py`, `dispatch.py`, `funcscan.py`), which mis-decoded data tables and
recovered almost nothing usable. Reproduce with:

```
analyzeHeadless <proj> fw -import re/27D72_dump.bin \
  -processor ARM:LE:32:Cortex -loader BinaryLoader -loader-baseAddr 0x08000000 \
  -scriptPath re -preScript ghidra_Stm32Setup.java
```

`re/ghidra_Stm32Setup.java` adds the STM32F103VET6 memory map (SRAM
`0x20000000`+64K, peripherals `0x40000000`, PPB `0xE0000000`) and seeds the
vector table as Thumb code before auto-analysis. Needs JDK 21 (JDK 25 is
rejected by Ghidra 12.1.2). Result: **1839 functions, 47987 instructions**,
reset vector `0x08003020`.

### Confirmed: the old "framing confirmed in firmware" claim was wrong

`FUN_08000882` decompiles unambiguously to the USB standard request handler:
it switches on `bRequest` = 6 `GET_DESCRIPTOR` / 0 `GET_STATUS` /
8 `GET_CONFIGURATION` / **0x0A `GET_INTERFACE`**, and for `GET_DESCRIPTOR`
picks between descriptor types 1/2/3 (DEVICE/CONFIG/STRING) by calling
function pointers at struct offsets `0x1c`/`0x20`/`0x24`.

Cross-check that settles it: those offsets are indices 7/8/9 of ST's
`DEVICE_PROP` struct, and the table found at `0x08003300` has
`[7]=0x0800140c`, `[8]=0x0800141a` — exactly `GetDeviceDescriptor` /
`GetConfigDescriptor`. So `0x0A` is a USB request code, not the protocol magic.

### What was mapped

- USB device stack: 48 functions, `0x08000254`–`0x08000dxx`.
- `DEVICE_PROP` table `0x08003300`; endpoint callback tables `0x08003358` /
  `0x08003398`, almost all entries pointing at `NOP_Process` (`0x08000d30`).
- Device-state variable `0x200007d8`, written with ST's state enum
  (1 `ATTACHED`, 4 `ADDRESSED`, 5 `CONFIGURED`) by `0x08001014` / `0x08001028`.
- DMM mode label strings exist — `DC V`, `AC V`, `DC A`, `AC A`, `DC mA`,
  `AC mA`, `DC mV`, `OHM`, `Hold` — around `0x08022e00`–`0x08023070`.

### What was NOT found (open)

**No vendor command dispatcher and no DMM mode-set command.** Specifically:

- `0x0101` and `0x0103` never appear as 16-bit immediates anywhere in the
  image, so the func code is not compared as a halfword constant. It is
  probably split into high/low byte tests, which are too generic to grep for.
- Vendor commands arrive on **bulk** EP 0x02, so they are not handled by the
  chapter-9 path above; the handler is a main-loop consumer of the endpoint
  buffer that has not been located yet.
- The mode strings have **zero** absolute pointers to them anywhere in flash,
  so the label lookup is not a simple pointer table. Only ~275 words in the
  whole 512K image look like flash pointers, so most data references are
  PC-relative and won't be found by scanning for absolute addresses.

Next: find the bulk EP2-OUT buffer consumer (trace from the USB ISR's endpoint
handling into the main loop) — that is the entry point to the vendor dispatch,
and the only place a DMM mode-set could live.

## The image contains TWO firmware images (2026-07-20)

This invalidates the addresses in the section above, and explains why the
vendor dispatcher could not be found there.

| | vector table | initial SP | reset | role |
|-|-------------|-----------|-------|------|
| **A** | `0x08000000` | `0x20000bf8` (~3 KB) | `0x08003020` | bootloader |
| **B** | `0x08005000` | `0x2000f560` (~62 KB) | `0x08030b80` | **application** |

Everything analysed in the Ghidra section above — the chapter-9 handler
`FUN_08000882`, `DEVICE_PROP` at `0x08003300`, the all-`NOP_Process` endpoint
tables — belongs to **image A, the bootloader**. That is why its endpoint
callbacks are unused and no vendor commands appear there.

The application (image B) has its **own** USB stack; its `USB_LP_CAN1_RX0`
ISR is at `0x08030aae` and it carries a second copy of the ST USB helpers
around `0x0802eb70` (`SetEPRxStatus`) / `0x0802ecb0` (`GetEPTxAddr`).
Read image B's vector table at `0x08005000` (index = 16 + IRQ number) to find
application ISRs. **The vendor command dispatcher is still not located, but it
is in image B, not image A.**

## SOLVED: remote mode-select is IMPOSSIBLE — the DMM link is one-way

Found while looking for the EP2-OUT consumer, and it settles the question the
`func=0x0001` probing could not.

**The DMM is a separate measurement chip that streams to the STM32 over
USART2, and the STM32 never talks back.**

Evidence, all from image B:

1. **The 14-byte frame arrives on USART2.** The ISR `FUN_08024d5c`
   (vector `0x080050d8`) reads `USART_ReceiveData` and appends to a buffer at
   `0x2000d46c`, indexed by `0x2000d554`; **when the index passes `0xd` it sets
   a frame-ready flag at `0x2000d555` and resets** — i.e. exactly 14 bytes,
   our frame. `0x525` in that ISR is ST's `USART_IT_RXNE`.
2. **USART2 is initialised receive-only.** `FUN_08024f96` builds a
   `USART_InitTypeDef` with baud `0x960` = **2400** and
   **`USART_Mode = 0x0004` = `USART_Mode_Rx`** — `USART_Mode_Tx` (`0x0008`)
   is never set, so the transmit enable bit is never turned on.
3. **The only transmit routine targets a different UART.** `FUN_0802741c` is
   `USART_SendData` (`strh r1,[r0,#4]`); its *only* caller is `FUN_08025324`
   (a putchar: send, then poll TC `0x40`), and that passes base
   `0x40013800` = **USART1**, not USART2.
4. **USART2's base appears as a literal in exactly one place** (`0x08025284`),
   reachable from only three functions: the RX ISR, the init, and an accessor
   returning `(base, USART_IT_RXNE)`. There is no fourth path.

**Therefore the DMM mode/range is owned entirely by the measurement chip,
selected by the front-panel rotary switch wired to it. The STM32 only listens
and relays. No USB command can change it, and none will ever be found —
`tools/dmm_setmode_probe.py` was searching for something that does not exist.**

This also offers a plausible (not proven) mechanism for the transient
artifacts: at 2400 baud a 14-byte frame takes ~58 ms, and the USB read copies
a buffer the ISR is concurrently refilling, so a badly-timed read returns a
frame torn across two updates — which is exactly what a "valid-looking but
wrong mode" sample is.

### Consequences
- Remote mode-select: **closed, not achievable.** Any UI must ask the user to
  turn the dial; the app can only *report* the mode it reads.
- Still open (but unrelated to the DMM): locating image B's vendor command
  dispatcher, for scope/AWG control coverage.
