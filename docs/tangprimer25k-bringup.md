# Tang Primer 25K Dock bring-up

This is the first-hardware procedure for the Sipeed Tang Primer 25K core board
on its 25K Dock. The kernel target is intentionally small: 32 KB on-chip
BSRAM, an immutable UART loader, blank kernel RAM, the Dock debugger UART, and
S1 reset. Older bare-metal CPU/GPU/TPU images remain useful as isolated hardware
diagnostics, but kernels are never baked into the bitstream. The optional
40-pin SDRAM module, USB host, and PMODs are not part of this first target.

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

The build must finish without a failed 25 MHz timing check. The board top uses
the GW5A's dedicated `CLKDIV` to derive that system clock from the Dock's
50 MHz oscillator. It produces
a configuration-keyed `tangprimer25k_top.fs` below `rtl/fpga/build/`. The
per-profile directory prevents CPU, GPU, and TPU netlists from overwriting or
silently reusing one another. The synthesis report beside it should contain 32
`DPB` cells for main memory; a large flip-flop array means BRAM inference has
regressed.

### Compact aXos kernel image

The full aXos userspace/filesystem profile needs more than the Primer target's
32 KiB BSRAM. `kernel-primer-monitor` is the board-sized S-mode profile: Sv32,
delegated timer traps, the physical-page allocator, safe role discovery, and a
resident UART management shell. It deliberately excludes user processes, ELF
loading, syscalls, and AXFS; those remain available in the full 128 KiB and
external-memory profiles.

Run the compact-kernel smoke test, then the required runtime-upload gate and
build the loader-only bitstream with:

```bash
make kernel-primer
make primer-runtime-preflight
```

The first command executes the same image in an ISS with exactly 32,768 bytes
of RAM and in RTL with a 32,768-byte synchronous-BRAM model. The preflight
boots from ROM into blank RAM in simulation, uploads aXos, exercises two
accelerator programs, performs synthesis/place-and-route, and records the
exact source, inputs, tool versions, timing, utilisation, and bitstream hash in
the generated `evidence.json` beside the `.fs` file. It never accesses the
board. (`fpga-runtime-primer` and `fpga-kernel-primer` remain build-only
compatibility targets.) Only after both simulations report `PASS` should the
reversible SRAM image be loaded:

```bash
make -C rtl/fpga program \
  COMPONENT_CONFIG=$PWD/configs/tangprimer25k-runtime-gpu.json \
  RAM_INIT_FILE=$PWD/sw/bootrom/blank.hex \
  ROM_INIT_FILE=$PWD/sw/bootrom/build/uart-ram32768/bootrom.hex
```

At 921600 8-N-1, upload the kernel and run the automated fast-switch check:

```bash
python3 sw/host/axhost.py --fast-switch \
  --upload-kernel sw/kernel/build/primer-runtime/axos_boot.bin \
  --serial /dev/ttyUSB1 --baud 921600
```

Expect `AXOK` for the kernel upload followed by `FAST SWITCH PASS`. This kernel
profile remains simulation-verified until that physical transcript is captured.

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
picocom -b 115200 /dev/ttyUSB1
```

The default payload prints its hello transcript. Press and release S1 to reset
the SoC and confirm that the transcript restarts. There is no ordinary
FPGA-driven user LED on this Dock; UART output is the bring-up verdict.

## 4. Record the hardware evidence

Keep the following with the first successful run:

- OSS CAD Suite, Yosys, nextpnr-himbaechel, gowin_pack, and openFPGALoader
  versions;
- nextpnr utilisation and 25 MHz timing summary;
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
three independent performance alternatives are available:

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
`tangprimer25k-gpu.json` pairs the minimal host with the verified 4-lane SIMT
engine. Explicit GW5A multiplier decomposition maps its lanes to 12
`MULTALU27X18` DSPs. It routes at 18,280/23,040 LUT primitives (79.3%) and
38.47 MHz. Eight lanes overflowed at 109% LUT use; six lanes packed to 96% but
could not be legally placed. `tangprimer25k-tpu.json` uses 24 int8 multipliers
folded over three K phases and maps its buffers to 48 BSRAMs. It routes at
17,345 LUT primitives and 32.65 MHz.

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

The physical 4-lane GPU run measured 2,457 compute cycles for SAXPY and 3,029
for the polynomial kernel at N=256. The folded TPU measured 189 compute cycles
versus 42,995 CPU cycles for the same GEMM. The hardware payloads include
upload, checked readback, and stable checksums, so these are functional
measurements rather than unverified timing projections.

At the target clocks, the current RTL/physical evidence compares as follows:

| Workload | Tang Nano 20K | Tang Primer 25K | Primer wall-time speedup |
|---|---:|---:|---:|
| CPU, five measured windows | 42,978 cycles / 1,591.8 us | 25,729 cycles / 1,029.2 us | 1.55× |
| GPU SAXPY, N=256, complete offload | 23,097 cycles / 855.4 us | 29,887 cycles / 1,195.5 us | 0.72× |
| GPU polynomial, N=256, complete offload | 23,520 cycles / 871.1 us | 30,513 cycles / 1,220.5 us | 0.71× |
| TPU 12x8x8 GEMM, complete offload | 5,257 cycles / 194.7 us | 6,893 cycles / 275.7 us | 0.71× |

The CPU row remains the AX2 RTL comparison. The Primer GPU and TPU rows are
physical UART measurements; their Nano counterparts are RTL measurements.
Upload and checked readback dominate both complete offload boundaries.

## Verified hardware result

Physical bring-up completed on 2026-07-29 using volatile SRAM programming only:

| Image | Routed resources | Routed fmax | UART verdict |
|---|---|---:|---|
| CPU `hello` | 12,179 LUT4, 2,699 FF, 36 BSRAM | 32.23 MHz | hello output observed; S1 reset confirmed |
| GPU `gpu_perf` | 18,280 LUT4, 2,446 FF, 40 BSRAM, 12 DSP | 38.47 MHz | `gpu-perf: PASS`; N=256 checksums `0xf515cdf9` / `0xbe878696` |
| TPU `tpu` | 17,345 LUT4, 3,696 FF, 48 BSRAM, 24 DSP | 32.65 MHz | `role tpu-lite: PASS`; checksum `0x8acb4a08` |

The GPU transcript covers SAXPY and polynomial kernels at 32, 64, 128, and 256
threads, checking every result against the on-core reference. The TPU
transcript checks the folded int8 GEMM against its CPU reference. CPU, GPU, and
TPU are separate profile bitstreams rather than simultaneous accelerators.

## Resident-kernel runtime switching

The next Primer image keeps the compact 32 KiB aXos management kernel and a
small programmable GPU overlay resident together. Synthesis/P&R occurs once:

```bash
make runtime-primer
make fpga-runtime-primer
```

After attaching the board, configure SRAM with the already-built image:

```bash
make -C rtl/fpga program \
  COMPONENT_CONFIG="$PWD/configs/tangprimer25k-runtime-gpu.json" \
  RAM_INIT_FILE="$PWD/sw/bootrom/blank.hex" \
  ROM_INIT_FILE="$PWD/sw/bootrom/build/uart-ram32768/bootrom.hex"
```

The runtime profile owns the UART at 921600 baud. Load, execute, replace, and
re-execute accelerator programs without another synthesis, bitstream load, or
aXos reboot:

```bash
python3 sw/host/axhost.py --fast-switch \
  --upload-kernel sw/kernel/build/primer-runtime/axos_boot.bin \
  --serial /dev/ttyUSB1 --baud 921600
```

This path is simulation-verified by `make runtime-primer`; do not add it to the
physical evidence table until the command above passes on the board.
