# rtl/fpga/ — FPGA flow entry point

The generic ECP5/Gowin synthesis, place-and-route, packing, and programming
flow.
Board-specific top-level RTL, pin constraints, and physical I/O live beside
their `board` component manifest; this directory does not own a board design.

## Tang Primer 25K Dock target

`configs/tangprimer25k.json` selects a BRAM-only GW5A-25A shell. A dedicated
`CLKDIV` converts the Dock's 50 MHz oscillator to a timing-closed 25 MHz system
clock; the Dock debugger supplies a 115200 8-N-1 UART, and S1 is the active-high
reset. Build it with:

```bash
make fpga CONFIG=configs/tangprimer25k.json
make -C rtl/fpga program COMPONENT_CONFIG=$PWD/configs/tangprimer25k.json
```

Tang targets bake one bare-metal payload into block RAM. Select it explicitly
with `PROGRAM`; the default is `hello`:

```bash
make fpga CONFIG=configs/tangprimer25k-ax2.json PROGRAM=cpu_perf
make fpga CONFIG=configs/tangprimer25k-gpu.json PROGRAM=gpu_perf
make fpga CONFIG=configs/tangprimer25k-tpu.json PROGRAM=tpu
```

Generated artifacts are keyed by both configuration and payload. Switching
from `PROGRAM=hello` to `PROGRAM=cpu_perf` therefore cannot reuse a netlist or
bitstream containing the previous block-RAM image. The payload itself is linked
for the profile's actual RAM size and stored under
`sw/baremetal/build/ram<RAM_BYTES>/`; its stack therefore cannot point beyond
the 16 or 32 KiB memory synthesized for the board.

The profile uses 32 KB of on-chip BSRAM and starts the baked-in bare-metal
image at `0x80000000`. See
[docs/tangprimer25k-bringup.md](../../docs/tangprimer25k-bringup.md) before
programming or flashing hardware.

## ULX3S 85F target

`components/board/ulx3s_85f/ulx3s_85f_top.sv` targets an ULX3S v2/v3 with
LFE5U-85F-6BG381C:

- 25 MHz system clock; `ODDRX1F` launches the SDRAM clock half a cycle later.
- FT231X USB-serial console at 115200 8-N-1 (`axuart_phy.sv`).
- microSD in SPI mode (CMD/MOSI, DAT0/MISO, DAT3/CS_n).
- 32 MiB x16 SDR SDRAM via `axsdram` and explicit ECP5 `BB` DQ pads.
- boot ROM preloaded from `sw/bootrom/build/bootrom.hex`; it loads aXos from
  the SD card, so no kernel image is compiled into FPGA BRAM.

`components/board/ulx3s_85f/ulx3s_85f.lpf` is reduced from the ULX3S
project's published v2/v3 pin map. It constrains only pins used by the shell.
The Makefile targets `--85k`, `CABGA381`, ECP5 speed grade 6, and a 25 MHz
timing goal.

Build after the ECP5 tools in [docs/dependencies.md](../../docs/dependencies.md)
are on `PATH`:

```bash
make fpga CONFIG=configs/ulx3s-85f.json  # boot ROM, Yosys, nextpnr, .bit
make -C rtl/fpga config
make -C rtl/fpga toolchain-report
make -C rtl/fpga program      # reversible SRAM configuration
```

`configs/ulx3s-85f.json` selects every implementation used by the board.
Provide another configuration with a custom core, memory backend, peripheral,
SoC, or board manifest to compose a DIY variant. The stock ULX3S, Tang Nano,
and Tang Primer tops have passed their Yosys synthesis gates; selection alone
does not claim timing or hardware compatibility.

`make flash` writes persistent configuration flash and is intentionally
separate. Do it only after the physical bring-up checks in
[docs/ulx3s-bringup.md](../../docs/ulx3s-bringup.md) pass.

The authored RTL uses standard SystemVerilog package types. Current Yosys
rejects those `import` statements in this design, so `prepare_synth.py`
creates a disposable flattened file in `rtl/fpga/build/`; it never rewrites
source RTL. This is a tool-frontend adapter, not a second hardware design.

Until the ECP5 P&R tools are installed, `make -C rtl/fpga` fails immediately
at its tool preflight rather than spending time on synthesis. The checked-in
target has passed Yosys synthesis; the P&R timing report and physical-board
transcript are deferred to the final hardware evidence gate in
[docs/design-checklist.md](../../docs/design-checklist.md).
