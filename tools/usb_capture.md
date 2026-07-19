# Capturing the DMM protocol from the Windows software

The oscilloscope and signal generator protocols of the 2D72 are known, but the
multimeter (DMM) readout protocol is not. To reverse-engineer it, capture the
USB traffic of the official Windows software while its multimeter view is open.

## Setup (Windows in a VM on this Linux machine)

1. Install the official Joy-IT/Hantek Windows software in a VM
   (VirtualBox/QEMU with USB passthrough of the device, id `0483:2d42`).
2. On the Linux host, load the usbmon kernel module and start Wireshark:

   ```sh
   sudo modprobe usbmon
   sudo wireshark
   ```

3. Find the device's bus/address with `lsusb | grep 0483`, then capture on the
   matching `usbmonN` interface (N = bus number).
4. In the Windows software, switch to the multimeter view and let it read a few
   values in different modes (DC V, AC V, resistance, ...). Also press connect/
   disconnect so the handshake is captured.
5. Stop the capture and save it as a `.pcapng` file.

## What to look for

- The known protocol sends 10-byte packets `[0x00, 0x0A, func_lo, func_hi,
  cmd, val0..val3, 0x00]` to bulk OUT endpoint 0x02 and reads from bulk IN
  endpoint 0x81. The DMM traffic most likely uses the same framing with an
  unknown `func`/`cmd` (the screen-switch command uses func 0x0003).
- Correlate displayed multimeter readings with the bytes returned on the IN
  endpoint to work out the value encoding (likely the CS7721 chip's count
  value plus mode/range flags).

Findings can be turned into code in `src/dmso2d72/protocol.py` — contributions
welcome.

## Helper

`usbmon_filter.py` in this directory extracts bulk transfers to/from the
device from a pcap file exported by Wireshark/tshark.
