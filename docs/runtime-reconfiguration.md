# Runtime reconfiguration

The operational loop must never invoke synthesis or place-and-route. Those are
offline image-build steps, comparable to compiling firmware. atomiX now has
three deliberately separate levels:

| Change | Mechanism | Expected scale | Kernel survives? |
|---|---|---:|---|
| Algorithm/data | load role program/descriptors | milliseconds | yes |
| aXos kernel | CRC-checked UART upload into RAM | tenths of a second | replaces kernel |
| Physical datapath | load a cached, prebuilt SRAM bitstream | seconds | no |
| Physical datapath, live | partial role-region reconfiguration | research | intended |

The first level is the default product path. The fixed image contains aXcore,
the immutable loader, and a programmable accelerator overlay. aXos and its
host-link driver are uploaded after reset. The host sends `GPU_LOAD` only when
the accelerator program changes, then any number of `GPU_EXEC` jobs. A
nine-instruction program is 38 payload bytes, or 42 bytes including the frame
header. An 8-N-1 UART sends ten wire bits per byte, so the runtime Primer
profile's 921600-baud link needs about 0.46 ms for that switch (the conservative
console profiles remain at 115200). The in-FPGA load itself takes hundreds of
clock cycles.

The Primer runtime profile sizes the overlay's global memory to 256 words,
matching the host protocol's current 200-word job cap instead of spending 16
KiB of scarce BRAM on the accelerator benchmark profile's 4096-word buffer.
That leaves BRAM for the resident 32 KiB kernel and for future shared
GPU/TPU-overlay work.

Run the simulation-first proof:

```bash
make primer-runtime-preflight
```

It boots one exact 32 KiB aXos image, loads and executes SAXPY, loads a
different polynomial program, executes it, verifies both results, builds the
loader-only FPGA image, and writes a hashed `evidence.json` beside the
bitstream. The same host command is ready for the board transport:

```bash
python3 sw/host/axhost.py --fast-switch --serial /dev/ttyUSB1 --baud 921600 \
  --upload-kernel sw/kernel/build/primer-runtime/axos_boot.bin
```

Do not run the hardware command until the
`tangprimer25k-runtime-gpu` image has passed synthesis/P&R and has been
programmed. Physical evidence remains separate from the simulation result.

The current loader-only Primer image routes at 32.97 MHz against its 25 MHz
constraint and uses 18,399/23,040 LUT4s, 3,308/23,040 DFFs, 44/56 BSRAMs, and
3/28 `MULTALU27X18` DSPs. Its `.fs` SHA-256 is
`1b60669ab39f78a1fab68ff8f5e0b947960d64c29fdf4661f520b13008774878`.
This qualifies synthesis/P&R independently of whichever kernel is uploaded;
it is build evidence only. A new physical claim still requires the serial
regression on the board.

This overlay cannot change arbitrary FPGA topology. A TPU systolic array and a
SIMT engine are still different physical datapaths. On the Primer, switching
between those currently means loading a cached bitstream and rebooting aXos.
True live role-region replacement depends on a verified GW5A partial-
reconfiguration flow; it is not claimed today.

## Kernel loading invariant

Every kernel profile going forward is a runtime payload. A small immutable UART
ROM scans for an `AXK1` frame, checks its RAM bound and CRC-32, writes the binary
to `0x8000_0000`, executes `fence.i`, and starts aXos. The ROM rejects corrupt
and oversized payloads and remains ready for a retry.

At 921600 baud the compact Primer kernel's 4,853-byte binary takes roughly
0.053 seconds on the wire; the current 49,064-byte full kernel takes roughly
0.53 seconds. Neither changes the FPGA image.

The only reason to run FPGA tools is now an actual RTL or physical-datapath
change. Frequently used hard roles should be prebuilt and cached; a live
GPU+TPU composite/shared-memory role is preferred on devices where it fits,
while true partial reconfiguration remains an optional research optimization.
