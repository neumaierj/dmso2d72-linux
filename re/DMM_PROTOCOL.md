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

Explanations, which **cannot be told apart from the USB side alone**:

- the write changes real internal state but the firmware re-asserts the mode
  from the soft-key menu selection, so it snaps back;
- the write perturbs the status *reporting* path, and the odd frames are
  stale, torn or substituted frames rather than a real mode change.

⚠️ **Update 2026-07-21:** the device owner watched the screen during a run and
**the device did change modes**, while the bottom-line mode indicator did not
follow. So "nothing really changed" is wrong — something real happens, and the
device is left inconsistent. What is undetermined is whether the *measurement*
changes or only the *reported* mode. The conclusion below ("not a usable
remote mode-select") stands only in the narrow sense that nothing persists
predictably enough to build on yet — not as evidence that no path exists.

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

## ~~SOLVED: remote mode-select is IMPOSSIBLE~~ — RETRACTED 2026-07-21

**This conclusion was wrong and is withdrawn.** Two errors:

1. **"Selected by the front-panel rotary switch" was fabricated.** The device
   has **no rotary switch**. The mode is chosen from an on-screen soft-key
   menu on the bottom line of the display — pages of modes selected with the
   **F1–F4** buttons. This was never verified against the hardware; it was
   assumed from how handheld DMMs usually work and then stated as fact.
2. **"No USB command can change the mode" is contradicted by observation.**
   During the `dmm_setmode_probe.py` run the device owner watched the screen
   and **the device did switch modes** — while the bottom-line mode indicator
   did *not* update, leaving the device in an inconsistent state.

The logic error: USART2 being receive-only shows the STM32 does not command
the DMM chip *over USART2*. I leapt from there to "nothing can change the
mode", which only follows if the chip is the sole owner of the mode — and
that is exactly what the soft-key menu disproves. Since F1–F4 change the
mode, the STM32 necessarily has a control path.

### What survives, and what it means

Still solid (verified several ways, unchanged):

- The 14-byte frame arrives on **USART2 at 2400 baud** (`USART_Mode = 0x0004`,
  Rx only; the sole `USART_SendData` caller targets USART1). ISR
  `FUN_08024d5c` fills a buffer at `0x2000d46c`, raising a frame-ready flag at
  `0x2000d555` once the index passes `0xd`.

New evidence that explains the inconsistent state — **there are two separate
pieces of mode state**:

- **The STM32 keeps its own 14-byte frame** at `0x200048fc` and copies it
  *over* the UART receive buffer:
  `FUN_0800cd7c(dst=0x2000d46c, src=0x200048fc, 0xe)` in `FUN_0802341a`,
  guarded by a key-edge condition. So the frame the USB reply exposes is **not
  necessarily what the measurement chip sent** — the firmware can substitute
  its own.
- **The menu state is separate.** `FUN_08022a28` (next to the mode-label
  strings at `0x08022f08`) renders the soft-key bar from a page number
  (`FUN_08023000` → 1/2/3) and a selection index at `DAT_0802329c + 0x19`,
  compared against 0/1, 3/4, 6/7, 9/10 — three items per page across three
  pages, matching the F1–F4 paged menu.

Two independent states, one feeding the USB frame and one driving the bottom
line, is a straightforward explanation for a write that changes the reported
mode while the menu label stays put.

### ANSWERED 2026-07-21: the mode change is REAL — remote mode-select works

Controlled test on hardware, with a **0.993 kΩ resistor connected** and the
device in resistance mode, owner watching the screen:

| | before | after |
|-|--------|-------|
| frame | `550b010800000300090903030255` | `550b010101000300000000050055` |
| reading | 0.993 kΩ | 0.000 A |
| digits (bytes 7–10) | `0,9,9,3` | `0,0,0,0` |
| byte 4 / byte 12 | `0x00` / `0x02` resistance | `0x01` / `0x00` DC current |

**One** write of `func=0x0001, cmd=0x01, val0=7` did it, and unlike every
earlier attempt **it persisted** — 12/12 identical reads over 6 s with no
further writes.

Three independent reasons this is a genuine measurement-mode change, not a
relabel or a synthesized frame:

1. **The digits changed.** A cosmetic relabel would have kept `0.993` and
   swapped the unit, giving "0.993 A". The value went to `0.000`, which is
   physically correct for a current measurement with a resistor and no source.
2. **The device's own display changed.** The owner confirms the screen now
   reads "DC 0.000A". The display and the USB reply both render from the
   buffer at `0x2000d46c`, so this is not a USB-reporting artifact.
3. **The substitution source still says "resistance" — the decisive
   argument.** The copy `FUN_0800cd7c(0x2000d46c ← 0x200048fc, 0xe)` takes its
   14 bytes from the struct at `0x200048fc`, and **that is the same struct
   holding the soft-key menu state** (page at `+0x17`, selection at `+0x19`).
   The menu still read "Ohm" throughout. So had the firmware been substituting
   a frame, the display would have shown *resistance*. It showed DC current.
   Therefore no substitution was in play and the frame came from the chip.
   **The measurement chip itself switched mode.**

   ⚠️ Correction: an earlier version of this list argued from timing, claiming
   the copy was "one-shot behind a key-edge guard". That was a misreading —
   `FUN_080244d6`/`FUN_080244d0` index `DAT_08024694 = 0x2000d46c`, i.e. they
   read **frame bytes 12 and 10**, not key state, so the guard is a test of
   frame annunciator bits and the copy is not key-driven. The conclusion is
   unchanged; the reason above replaces it and does not depend on timing.

So a control path from the STM32 to the measurement chip **does** exist. It is
**not** USART2 (still verified receive-only), so it is most likely GPIO —
locating it is the remaining work.

**The soft-key menu does not track it.** The owner reports the bottom line
still showed "Ohm" selected while the device measured and displayed DC current.
The menu is separate state (`DAT_0802329c + 0x19`, page + selection index), so
a remote switch leaves the UI inconsistent with the actual mode.

⚠️ **Safety.** After a remote switch the device really is in the new mode
while the menu still claims the old one. Current mode presents a low-impedance
input; do not connect the leads across a voltage source while the displayed
menu mode cannot be trusted. Re-select a mode with F1–F4 (or power-cycle) to
restore a consistent state before using the meter normally.

### Known RAM layout (image B)

| address | meaning |
|---------|---------|
| `0x2000d46c` | 14-byte DMM frame buffer — filled by the USART2 RX ISR, read by **both** the LCD renderer and the USB reply |
| `0x2000d554` / `0x2000d555` | RX index / frame-ready flag |
| `0x200048fc` | UI/mode struct: 14-byte frame template at offset 0, **page at `+0x17`**, **selection index at `+0x19`** |

That two-buffer split is the whole story: the remote write changes the chip
(hence `0x2000d46c`, hence display and USB), while the menu reads
`0x200048fc`, which nothing updated.

### Fixing the menu desync — the options

The `func=0x0001` path is almost certainly **not** the intended interface; it
reaches the measurement path as a side effect and skips the UI bookkeeping.

1. **Find image B's real vendor dispatcher (highest value).** The Windows
   software presumably switches modes cleanly, so a proper command likely
   exists and would update both by construction. This also subsumes the other
   open item — the dispatcher is what `func=0x0001` writes are landing in.
2. **Emulate an F1–F4 key press.** The firmware's own handler updates mode and
   menu together. Needs the key input path: GPIO scan → key-state variable →
   handler, then a USB-reachable write to that variable. Note the earlier
   candidate for this was the misread above, so the key path is **not yet
   located** — start from the GPIO port the buttons sit on.
3. **Write `0x200048fc + 0x17/0x19` directly.** Only viable if some command
   can reach arbitrary RAM; fragile and would desync in the other direction if
   the chip did not actually switch. Last resort.

⚠️ **Caution before more blind sweeping.** The firmware contains the strings
`This is Calibration mode`, `Cancel`, `Next` near the mode labels, so some of
this command space is plausibly a calibration/service channel. A sweep that
writes many (cmd, val0) combinations could in principle disturb calibration
constants. Prefer targeted, one-write-at-a-time tests with the screen watched,
over the broad sweep `dmm_setmode_probe.py` performs.

## Dispatcher hunt in image B (2026-07-21) — traced to the dispatch point

The USB receive path is now traced end to end, down to the exact call that
would invoke the vendor command handler:

```
vector 0x08005090 (USB_LP_CAN1_RX0)
  -> 0x08030aae            thunk into FUN_08030a00   (ISR body)
  -> FUN_08030620          ST CTR_LP: reads EPnR, extracts EP index (& 0xf)
       |- EP == 0          control/chapter-9 path
       `- EP != 0          FUN_0803079a  (OUT/RX, clears CTR_RX via & 0xf8f)
                           FUN_08030770  (IN/TX,  clears CTR_TX via & 0x8f0f)
                             both do  (*table[EPindex - 1])()
```

Endpoint callback tables, indexed `[EPindex-1]`:

| table | address |
|-------|---------|
| `pEpInt_OUT` (RX) | **`0x20004974`** |
| `pEpInt_IN` (TX)  | **`0x20004958`** |

The vendor dispatcher is therefore `pEpInt_OUT[1]` — the EP2-OUT callback.

### Blocker: those tables are in RAM and `.data` is compressed

The tables are **RAM**, not flash, so their contents are only established at
startup. Image B is an ARMCC/Keil build whose scatter-load **decompresses**
initialised data: `FUN_080304be` is a textbook LZ decompressor (literal runs
plus length/distance back-references), and the pointer trio at `0x08030218`
(`0x0802f952`, `0x0802f86c`, `0x0802f8c0`) are the scatter-load helpers.

So the initial contents of `pEpInt_OUT` are **not plain bytes anywhere in the
dump**, which is why scanning flash for pointer tables in image B finds
nothing, and why the EP2-OUT callback address cannot simply be read off.

Also re-confirmed for image B: **`0x0101` and `0x0103` never appear as
comparison immediates** (0 sites), the same as image A — so there is no
shortcut via constant search. The func code must be tested some other way
(most likely as separate high/low bytes).

### SOLVED by emulating the scatter-load

Porting the decompressor was unnecessary. **The scatter-load runs before any
peripheral init, so it touches nothing but flash and SRAM and can be emulated
with memory alone.** `re/ghidra_scatter_emu.py` (Unicorn) maps the dump at
`0x08000000` plus 64 K of RAM, sets SP to image B's initial `0x2000f560`, and
runs the loop at `0x08030abc` until it returns — the firmware decompresses its
own `.data`, and the tables can then be read straight out of emulated RAM.
44521 RAM writes spanning `0x20001400`–`0x2000f55c`, matching the scatter
table's destination exactly.

Result — `0x08030230` is the default handler (`bx lr`), filling 12 of 14 slots:

| slot | value | resolves to |
|------|-------|-------------|
| `pEpInt_OUT[1]` (EP2 OUT) | `0x08030ba0` | `b.w` → **`0x0802d834`** |
| `pEpInt_IN[0]` (EP1 IN) | `0x08030b9c` | `b.w` → `0x0802d82c` |

## THE VENDOR COMMAND PATH (complete)

```
EP2 OUT interrupt
  -> FUN_0802d834      GetEPRxCount(EP2); PMAToUserBufferCopy(0x2000d294, 0xd8, n);
                       *0x2000d557 = 0xff        (command-pending flag)
  -> FUN_0802df34      main() polls ...
     -> FUN_0802ca2c   func dispatch, on the command buffer at 0x2000d294
        -> per-func handler
```

| RAM | meaning |
|-----|---------|
| `0x2000d294` | **command buffer** — the received 10-byte packet |
| `0x2000d522` | received byte count |
| `0x2000d557` | command-pending flag (`0xff` = packet waiting) |

`FUN_0802ca2c` tests **`buf[2]` and `buf[3]` separately** — func low byte then
high byte. That is why `0x0101`/`0x0103` never appear as 16-bit immediates.

| func | handler | role |
|------|---------|------|
| `0x0000` | `FUN_0802ab94` | scope settings (switches on `buf[4]` = cmd) |
| `0x0100` | `FUN_0802b0bc` | scope capture |
| **`0x0001`** | **`FUN_0802a824`** | **DMM mode set** |
| `0x0101` | `FUN_0802aa10` | DMM status reply (builds the `0x55` frame) |
| `0x0002` | `FUN_0802b50c` | AWG settings |
| `0x0102` | `FUN_0802b900` | AWG reply |
| `0x0003` | `FUN_0802ba94` | screen select |
| `0x0103` | `FUN_0802c758` | config reply |

## DMM mode set: `func=0x0001`, `cmd` = mode index, value ignored

`FUN_0802a824` switches on `buf[4]` (**cmd**) only. `val0..3` are never read,
and `default:` returns without acting — so `cmd > 10` is a no-op.

| cmd | firmware label | mode | internal id |
|----:|----------------|------|------------:|
| 0 | `AC A` | AC Current | 2 |
| 1 | `DC A` | DC Current | 1 |
| 2 | `AC mA` | AC Current (mA) | 4 |
| 3 | `DC mA` | DC Current (mA) | 3 |
| 4 | `DC mV` | DC Voltage (mV) | 9 |
| 5 | `DC V` | DC Voltage | 10 |
| 6 | `AC V` | AC Voltage | 11 |
| 7 | `DIAN RONG` (电容) | Capacitance | 5 |
| 8 | `DIAN ZU` (电阻) | Resistance | 6 |
| 9 | `TONG DUAN` (通断) | Continuity | 7 |
| 10 | `ER JI GUAN` (二极管) | Diode | 8 |

Each case sets the on-screen label (`FUN_08014a48`), redraws the soft-key bar
(`FUN_08023238`), sets the mode (`FUN_08021e2c`) and commands the measurement
hardware (`FUN_0802507e` / `FUN_080250ba` / `FUN_08025132` / …), then stores
the internal id to `*DAT_0802b4fc`.

**Verified on hardware** with a 0.993 kΩ resistor connected:
`func=0x0001 cmd=8` → briefly Capacitance/OL while auto-ranging, then settled
to **0.993 kΩ Resistance**, stable. And the earlier accidental hit
`cmd=0x01` → **DC Current**, exactly matching `cmd 1 = DC A`.

### This explains every earlier observation
- The sweep swept `cmd` 0x00–0x0f × `val0` 0x00–0x0a. Only `cmd` 0–10 do
  anything and `val0` is ignored, so **each iteration set a different mode** —
  the "drift" (76/187 rows changing between probes) was the sweep itself
  walking the mode table, not noise, and `cmd` 0x0b–0x0f were inert exactly as
  observed.
- The sweep ended on `cmd=0x0a` = Diode, which is where the device was found.
- Modes did not "revert"; each successive write simply set another mode.

### The soft-key selection highlight — SOLVED (2026-07-21): not USB-reachable

`func=0x0001` sets the measurement mode but does **not** move the on-screen
soft-key highlight. The full menu model and key path are now decoded, and the
verdict is that no USB command can drive the highlight with this firmware.

**Menu model.** The DMM soft-key menu is state in the struct at `0x200048fc`:
- `+0x17` = **page** (0–3), getter `FUN_08023000`
- `+0x19` = **selection index** (0–10), the master mode selector
- `FUN_08023992` reads `+0x19` (via `FUN_08023ac4`) and applies the mode;
  `FUN_08022a28` renders the highlight from `+0x17`/`+0x19`.

Matches the hardware exactly — four pages of three, transcribed from the
device's own soft-key bar:

| page | F1 | F2 | F3 |
|------|----|----|----|
| 1/4 | DC V | OHM (Resistance) | Buzzer (Continuity) |
| 2/4 | DC A | DC mA | DC mV |
| 3/4 | AC V | AC A | AC mA |
| 4/4 | Diode | Capacitance | — |

**Key path.** `FUN_08023acc(keycode)` is the F1–F4 handler: it advances the
page (`+0x17` mod 4), sets the selection (`+0x19`), then the caller runs
`FUN_08023992` (apply) + `FUN_08022a28` (render). It is called **only from
`main()` (`FUN_0802df34`)**, fed a keycode read from `0x2000d525`, which is
written **only by the scan/debounce routine `FUN_0802ef56`** (a timer ISR, no
direct callers). The "virtual key" reader `FUN_0802d86c` and the physical
reader `FUN_0801dd7c` both consume that same byte.

**Why USB cannot reach it.** The USB dispatcher `FUN_0802ca2c` and all its
handlers (`FUN_0802a824` 0x0001, `FUN_0802ab94` 0x0000, `FUN_0802b50c` 0x0002,
`FUN_0802ba94` 0x0003) were checked: **none write `+0x17`, `+0x19`, or
`0x2000d525`.** `func=0x0001` sets the mode via a separate path
(`FUN_08021e2c` + range routines) and never touches the menu struct.
`func=0x0003` only *re-renders/applies the existing* selection
(`FUN_08023d84` → `FUN_08022a28` + `FUN_08023992`); it cannot change it. Every
one of the ~20 functions referencing `0x200048fc` lives in the DMM-display
subsystem (`0x08022xxx`–`0x08023xxx`), structurally disjoint from the USB
handlers (`0x0802axxx`–`0x0802cxxx`).

**Confirmed on hardware (owner watching the screen).** From a consistent OHM
state (highlight + reading both on OHM), sending `set_dmm_mode("DC V")`:
the reading changed to DC V, the **highlight stayed on OHM**. Exactly as the
static analysis predicts. (DC V is F1 and OHM is F2 on the same page, so a
following highlight would have been unmistakable.)

**Verdict:** the device's own soft-key bar cannot be synced over USB without
modifying the firmware. The app must be its own source of truth for the
selected mode. `gui/dmm_tab.py` therefore shows the device's page/slot layout
and highlights the app-selected mode itself; the note there and in the README
tells the user not to trust the device's on-screen bar after a remote switch.

### Supporting detail for the USART2 findings (image B)

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

These establish only that the STM32 does not command the measurement chip
**over USART2**. They say nothing about GPIO or other paths, and they do not
constrain the STM32's own substituted frame at `0x200048fc`.

Also note the ~58 ms frame time at 2400 baud: the USB read copies a buffer the
ISR concurrently refills, so a badly-timed read can return a frame torn across
two updates. That remains a plausible contributor to the earlier transients,
but it is **not** an explanation for a real mode change on the device.

### Consequences
- Remote mode-select: **open**. The app currently only *reports* mode; do not
  document it as impossible, and do not build UI asserting the user must
  change it by hand until the question above is settled.
- Still open (and likely related): locating image B's vendor command
  dispatcher, which is what `func=0x0001` writes are reaching.
