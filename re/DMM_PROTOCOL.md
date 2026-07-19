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

1. **The framing above is correct.** The command parser (around `0x08000a00`
   and `0x08001300`) validates `magic == 0x0A` (byte offset 1 — this constant is
   compared in exactly 3 places in the image) and then reads **func at offset 2
   (halfword)**, **cmd at offset 4 (byte)**, and **value at offset 6**, exactly
   matching `src/dmso2d72/protocol.py`. Example, at `0x080009e2`:

   ```
   ... == 0x0A          ; magic
   ldrh r0,[r0,#2]; cmp #0   ; func  == 0x0000
   ldrb r0,[r0,#4]; cmp #0   ; cmd   == 0x00
   ldrh r0,[r0,#6]; cmp #1   ; value == 1
   blx  <handler>
   ```

2. There is a **long if/else dispatch chain** keyed on (func, cmd, value); each
   arm calls a handler. This is where a DMM command would live.

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
- **Higher sub-ranges** only seen at their base: capacitance beyond nF (µF range
  not yet captured — needs a large cap). The numeric value is always correct;
  only the µF unit prefix would be missing until captured.
- Sign confirmed with a genuine negative reading (DC -1.997 V).
