# dmso2d72-linux

A Linux interface for the **Joy-IT JT-DMSO2D72** (aka **Hantek 2D72/2D42**)
3-in-1 handheld oscilloscope, signal generator and multimeter. The
manufacturer only ships a Windows application; this project provides a native
Linux GUI.

## Features

- **Oscilloscope**: live 2-channel waveform view, channel enable/coupling/
  probe/volts-div/offset/bandwidth-limit, time base, trigger (source, slope,
  mode, level), run/stop, single or continuous capture, CSV export.
- **Signal generator**: waveform type (sine, square, ramp, trapezoid, arb
  slots), frequency, amplitude, offset, duty cycles, output start/stop.
- **Multimeter**: live readings over USB, **remote mode selection**, min/max,
  hold, a rolling history plot and CSV logging. The DMM protocol was reverse-
  engineered for this project — first by probing the device, then from the
  firmware itself (see [re/DMM_PROTOCOL.md](re/DMM_PROTOCOL.md)).
  **All modes are decoded and settable**: DC/AC volts (V, mV), DC/AC current
  (A, mA), resistance (Ω/kΩ/MΩ), continuity, capacitance and diode test —
  with sign, auto-ranging and over-range "OL".
- **Selectable dark/light theme** (View ▸ Theme, or follow the system), and
  window/instrument settings persist between sessions.
- Menu bar with keyboard shortcuts; settings are re-sent to the device on
  connect so the app and the instrument cannot silently disagree.

### Multimeter mode selection — two things to know

Setting the mode from the app changes the measurement immediately, but the
device's own soft-key bar keeps highlighting the previously selected entry.
That is a firmware quirk, not a bug here: **trust the app's readout, not the
bottom line on the device.**

⚠️ The four current ranges (`AC A`, `DC A`, `AC mA`, `DC mA`) make the input a
**low-impedance shunt**, which must be wired in series with the load — putting
it across a voltage source shorts that source. Selecting one of these from the
app therefore asks for confirmation first.

## Installation

```sh
git clone https://github.com/neumaierj/dmso2d72-linux
cd dmso2d72-linux
python3 -m venv .venv
.venv/bin/pip install .

# allow non-root USB access to the device
sudo cp udev/99-dmso2d72.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Requires Python ≥ 3.11 and libusb (preinstalled on most distributions).

## Usage

Plug in the device, turn it on, then:

```sh
.venv/bin/dmso2d72
```

The status bar shows the connection state; use **Rescan** after plugging in
the device. On the oscilloscope tab, **Start live view** polls waveforms
continuously; **Single capture** fetches one waveform.

The device enumerates as USB `0483:2d42` (the 2D72 shares the 2D42's product
id). The Hantek 2C42/2C72/2D42 models use the same protocol and should work
too (scope/AWG features permitting), but only the DMSO2D72 has been targeted.

## Hardware test checklist

- [ ] Device detected after plug-in + Rescan (status bar shows product name)
- [ ] Live view shows a moving trace with the probe on the calibration output
- [ ] Volts/div, time/div, coupling and offset changes are reflected on the
      device's own screen
- [ ] Trigger level/slope changes affect the trace
- [ ] AWG: 1 kHz sine on the generator output, visible on CH1
- [ ] CSV export contains plausible sample values
- [ ] Multimeter: DC-volts reading matches the device screen (Start reading)
- [ ] Multimeter: each of the 11 modes can be selected from the app and the
      reading changes accordingly (the device's soft-key highlight will lag —
      see above)
- [ ] Multimeter: choosing a current range asks for confirmation, and
      cancelling really leaves the previous mode in place
- [ ] Multimeter: min/max track a changing input; Hold freezes the display
- [ ] Theme switch (View ▸ Theme) recolours the live trace legibly both ways
- [ ] Unplugging mid-capture reports the loss once, without crashing, and
      settings are re-applied on reconnect

## Vertical units

Waveforms are plotted in **screen divisions** (−4 … +4), exactly as on the
device display; multiply by the selected volts/div (and probe factor) for
volts. Raw sample bytes and division values are both included in CSV exports.

## Development

```sh
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                       # protocol + GUI tests, no hardware needed
QT_QPA_PLATFORM=offscreen .venv/bin/python -m dmso2d72.app --smoke-test
```

The GUI tests run headless and stub out the device, so they neither need
hardware nor disturb an attached one.

Protocol framing lives in `src/dmso2d72/protocol.py` and USB I/O in
`src/dmso2d72/device.py`; neither imports Qt. The GUI is in
`src/dmso2d72/gui/`, where the tabs share `device_tab.py` for connect/disconnect
plumbing and `theme.py` owns every colour that the Qt palette does not reach.

## Credits

The USB protocol implementation is based on the community reverse-engineering
work in:

- [lucaoli/Hantek](https://github.com/lucaoli/Hantek) — C/GTK tool for the
  2D72 (GPL-3.0)
- [hkoosha/hanteker](https://github.com/hkoosha/hanteker) — Rust
  library/CLI port

This project is not affiliated with Joy-IT or Hantek. Use at your own risk.

## License

[GPL-3.0-or-later](LICENSE)
