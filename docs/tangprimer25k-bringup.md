# Tang Primer 25K Dock bring-up

This is the first-hardware procedure for the Sipeed Tang Primer 25K core board
on its 25K Dock. The checked-in target is intentionally small: 32 KB on-chip
BSRAM, a bare-metal image baked into the bitstream, the Dock debugger UART, and
S1 reset. The optional 40-pin SDRAM module, USB host, and PMODs are not part of
this first target.

Hardware facts and pin assignments come from Sipeed's
[board documentation](https://wiki.sipeed.com/hardware/en/tang/tang-primer-25k/primer-25k.html)
and [official UART example](https://github.com/sipeed/TangPrimer-25K-example/tree/main/UART/simple_uart).
The open-flow device and packing flags follow apicula's
[GW5A Primer 25K example](https://github.com/YosysHQ/apicula/tree/master/examples/gw5a).

## 1. Inspect before connecting

- Confirm the GW5A-25K core board is fully seated in the Dock in the marked
  orientation.
- Leave PMODs and the optional SDRAM module disconnected for first bring-up.
- Use a data-capable USB-C cable on the Dock debugger port.
- Do not run the persistent `flash` target during initial tests.

## 2. Build tools and bitstream

Use a current matched OSS CAD Suite; GW5A support requires current Yosys,
nextpnr-himbaechel, and apicula:

```bash
source "$HOME/opt/oss-cad-suite/environment"
command -v yosys nextpnr-himbaechel gowin_pack openFPGALoader
make -C rtl/fpga toolchain-report \
  COMPONENT_CONFIG=$PWD/configs/tangprimer25k.json
make fpga CONFIG=configs/tangprimer25k.json
```

The build must finish without a failed 50 MHz timing check. It produces
a configuration-keyed `tangprimer25k_top.fs` below `rtl/fpga/build/`. The
per-profile directory prevents CPU, GPU, and TPU netlists from overwriting or
silently reusing one another. The synthesis report beside it should contain 32
`DPB` cells for main memory; a large flip-flop array means BRAM inference has
regressed.

## 3. Find the UART and program SRAM

Record serial devices before and after connecting the Dock so the correct UART
is unambiguous:

```bash
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
make -C rtl/fpga program \
  COMPONENT_CONFIG=$PWD/configs/tangprimer25k.json
```

`program` invokes `openFPGALoader -b tangprimer25k` without `-f`, so it changes
FPGA SRAM only. A power cycle restores the image already stored in flash.

Open the newly appeared UART at 115200 8-N-1, substituting its actual device:

```bash
picocom -b 115200 /dev/ttyUSB0
```

The default payload prints its hello transcript. Press and release S1 to reset
the SoC and confirm that the transcript restarts. There is no ordinary
FPGA-driven user LED on this Dock; UART output is the bring-up verdict.

## 4. Record the hardware evidence

Keep the following with the first successful run:

- OSS CAD Suite, Yosys, nextpnr-himbaechel, gowin_pack, and openFPGALoader
  versions;
- nextpnr utilisation and 50 MHz timing summary;
- exact core-board marking/revision;
- UART transcript before and after S1 reset;
- whether a power cycle restored the previous flash image.

Only after SRAM programming and this regression pass should persistent flash
be considered:

```bash
make -C rtl/fpga flash \
  COMPONENT_CONFIG=$PWD/configs/tangprimer25k.json
```

That command is intentionally separate because it writes non-volatile
configuration flash.

## Performance profiles

Keep `tangprimer25k.json` as the first-UART baseline. After that succeeds,
three independently maximized alternatives are available:

```bash
make fpga CONFIG=configs/tangprimer25k-ax2.json PROGRAM=cpu_perf
make fpga CONFIG=configs/tangprimer25k-gpu.json PROGRAM=gpu_perf
make fpga CONFIG=configs/tangprimer25k-tpu.json PROGRAM=tpu
```

`PROGRAM` names the bare-metal image baked into on-chip RAM. It is part of the
artifact directory key, so each profile/payload pair produces an independent
netlist and bitstream. Use `PROGRAM=hello` for the initial UART smoke test.

`tangprimer25k-ax2.json` selects the dual-issue AX2 core with a 2 KiB
instruction cache and the largest fitting predictor, a 64-entry BTB. It maps
to 20,893 LUT primitives.
`tangprimer25k-gpu.json` pairs the minimal host with the lean 8-lane SIMT
engine. Explicit GW5A multiplier decomposition maps its lanes to 24
`MULTALU27X18` DSPs; it fits at 22,623/23,040 LUT primitives (98.2%), so treat
place-and-route and 50 MHz timing as hard gates. `tangprimer25k-tpu.json` uses
24 int8 DSP multipliers folded over three K phases and maps its accumulator
buffer to BSRAM; it fits at 14,555 LUT primitives.

Reproduce the board-independent comparisons with:

```bash
python3 tools/bench.py tang
python3 tools/bench.py cpu
python3 tools/bench.py gpu
python3 tools/bench.py tpu
```

Each hardware payload prints a checksum and projected time at both Tang clock
rates. The GPU and TPU payloads split the result into upload,
doorbell-to-done `compute`, `readback+verify`, and full `total` cycles. Capture
the complete UART transcript: matching the simulation checksum proves that the
board ran the same workload and result, while the phase split shows whether
the accelerator or the host/MMIO boundary dominates.

The compute-only simulation measurements remain 359 engine cycles for the
8-lane GPU SAXPY workload and 179 cycles for the folded TPU versus 35,132 CPU
cycles. The CPU comparison now uses the sum of its five measured workload
windows rather than whole-program cycles, because UART output is not useful
work. All time values are pre-P&R projections; the achieved 50 MHz timing
result and physical UART run remain the final gates.

At the target clocks, the exact max-profile simulation currently reports:

| Workload | Tang Nano 20K | Tang Primer 25K | Primer wall-time speedup |
|---|---:|---:|---:|
| CPU, five measured windows | 42,978 cycles / 1,591.8 us | 25,729 cycles / 514.6 us | 3.09× |
| GPU SAXPY, N=256, complete offload | 23,097 cycles / 855.4 us | 22,809 cycles / 456.2 us | 1.88× |
| GPU polynomial, N=256, complete offload | 23,520 cycles / 871.1 us | 23,133 cycles / 462.7 us | 1.88× |
| TPU 12x8x8 GEMM, complete offload | 5,257 cycles / 194.7 us | 5,257 cycles / 105.1 us | 1.85× |

The CPU difference combines the Primer's AX2 core with its higher clock. GPU
compute improves from 6 to 8 lanes, but upload and checked readback dominate
the full boundary. The TPU RTL and host core are identical on both profiles,
so its projected gain is entirely the 50/27 MHz clock ratio.
