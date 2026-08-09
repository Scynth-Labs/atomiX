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

## 2. Attach the Dock to WSL2

Windows owns newly connected USB devices, so a WSL2 development environment
must attach the Dock explicitly with
[`usbipd-win`](https://learn.microsoft.com/en-us/windows/wsl/connect-usb).
Install it once from an Administrator PowerShell if it is not already present:

```powershell
winget install --interactive --exact dorssel.usbipd-win
```

Keep a WSL terminal open. In Administrator PowerShell, find the Dock and share
it once (sharing persists across unplugging and reboots):

```powershell
usbipd list
usbipd bind --busid <BUSID>
```

Identify the Dock by VID:PID `0403:6010` and the two interfaces named
`USB Serial Converter A, USB Serial Converter B`; do not assume that its BUSID
will remain the same after moving it to another Windows USB port. Then attach
the shared device from an ordinary PowerShell:

```powershell
usbipd attach --wsl --busid <BUSID>
usbipd list
```

Verify both the USB identity and serial interfaces inside WSL:

```bash
lsusb -d 0403:6010
dmesg | tail -n 50
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```

The Dock's FT2232 debugger exposes channel A for JTAG and channel B for UART;
on the verified setup they appeared as `/dev/ttyUSB0` and `/dev/ttyUSB1`,
respectively. If the serial nodes are owned by group `dialout` and the current
user is not in that group, grant access once and then fully exit and reopen the
WSL distribution:

```bash
sudo usermod -aG dialout "$USER"
```

The attach is not persistent: repeat `usbipd attach` after unplugging the Dock,
restarting Windows, or running `wsl --shutdown`. To return the device to
Windows without unplugging it, run:

```powershell
usbipd detach --busid <BUSID>
```

The verified power-cycle recovery sequence is: unplug/reconnect the Dock,
repeat `usbipd attach`, confirm both `/dev/ttyUSB*` nodes and
`openFPGALoader --detect`, reload the chosen image with `program` (SRAM only),
then rerun its UART regression. Do not expect a volatile runtime to survive a
power cycle; the board first restores its previously stored flash image.

Completed WSL/JTAG/UART observations belong in the
[Tang Primer achievement record](achievements/tangprimer25k.md), not in this
procedure.

## 3. Build tools and bitstream

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
exact source, inputs, tool versions, placement seed, timing, utilisation, and
bitstream hash in the generated seed-specific evidence manifest beside the
`.fs` file. It never accesses the
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

Expect ROM acknowledgement `AXOK`, kernel-ready marker `AXRD`, and finally
`FAST SWITCH PASS`. The verified physical result and release hashes are recorded
in the [Tang Primer achievement record](achievements/tangprimer25k.md).

## 4. Find the UART and program SRAM

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

## 5. Record the hardware evidence

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

Keep successful resource, timing, workload, and UART results in the
[Tang Primer achievement record](achievements/tangprimer25k.md). The broader
cross-board comparison remains in
[hardware-capabilities.md](hardware-capabilities.md).

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

This path is simulation-verified by `make runtime-primer` and physically
verified on the Dock. Its exact results, release hashes, and remaining recovery
tests are maintained in the
[Tang Primer achievement record](achievements/tangprimer25k.md).

For repeatability and bounded loader recovery, the physical host supports:

```bash
python3 sw/host/axhost.py --test-loader-recovery \
  --upload-kernel sw/kernel/build/primer-runtime/axos_boot.bin \
  --fast-switch --repeat 10 --serial /dev/ttyUSB1 --baud 921600
```

The destructive diagnostic below deliberately leaves the ROM waiting for the
rest of a frame. Run it only when S1 is accessible, then press and release S1
before attempting the normal complete upload again:

```bash
python3 sw/host/axhost.py --interrupt-upload-at 2048 \
  --upload-kernel sw/kernel/build/primer-runtime/axos_boot.bin \
  --serial /dev/ttyUSB1 --baud 921600 --timeout 0.5
```
