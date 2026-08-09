# atomiX build, test & deploy — operational reference

**This is the single, canonical command reference for the project.** It covers
building, the full test surface, and real hardware deployment.  It is
maintained: whenever a milestone adds or changes a build, test, or deploy
command, this file is updated in the same change (see
[design-checklist.md](design-checklist.md) → change-ready checklist).

Architecture lives in [DESIGN.md](../DESIGN.md); component contracts in
[components/README.md](../components/README.md); host setup and tool quirks in
[dependencies.md](dependencies.md) and [toolchain.md](toolchain.md).  This doc
is *what to run*, not *why*.

All commands are run from the repository root unless a `-C <dir>` says otherwise.

## Continuous integration

Every command in this file is meant to be reproducible by hand.  CI runs the
ones that need no hardware, so a stale claim surfaces as a red build rather
than as a surprise months later.  The workflows live in
[`.github/workflows/`](../.github/workflows) and are split by cost, not by
subject:

| Workflow | Trigger | Covers | Tier needed |
|---|---|---|---|
| `ci.yml` | push, PR | ISS, profile resolution, cosim, unit testbenches, `component-test`, QEMU-free aXos checks | Core |
| `nightly.yml` | 03:17 UTC daily | randomized fuzzing and paging, official ISA suite on ISS + RTL, three-platform and aXos checks | Core + Kernel |
| `formal.yml` | Sundays 04:23 UTC | bounded riscv-formal instruction proofs, both cores | Formal |

The split follows the tier table above: `ci.yml` needs only the Core tier, so
it runs on every change.  Anything needing QEMU ≥ 7 or the formal stack builds
that tool itself and therefore runs on a schedule instead.

FPGA synthesis, place-and-route, and physical-board results are deliberately
**not** in CI.  Keeping physical claims separate from simulation claims is a
project rule ([design-checklist.md](design-checklist.md)); a green build never
implies a working bitstream.

---

## 0. Prerequisites (tiers)

Install only the tier you need; details in [dependencies.md](dependencies.md).

| Tier | Tools | Unlocks |
|---|---|---|
| Core | `riscv64-unknown-elf-gcc` (rv32 multilib), Verilator, Python 3, GNU make | build + simulation + component tests |
| Kernel | `qemu-system-riscv32` **≥ 7** | aXos S/U-mode boot checks |
| Formal | current Yosys, SymbiYosys, riscv-formal | `make -C formal check`, `check-ax2` |
| FPGA | OSS CAD Suite (Yosys, board flow tools, openFPGALoader) | synthesis + board deploy |

The board component selects the flow: ULX3S uses ECP5 (`nextpnr-ecp5`, `ecppack`),
Tang Nano 20K uses Gowin (`nextpnr-himbaechel`, `gowin_pack`).  Both ship in the
OSS CAD Suite; `make -C rtl/fpga check-tools` verifies the ones the selected
board needs.

Pass a non-default QEMU as `QEMU=/abs/path/to/qemu-system-riscv32`.  Load the
FPGA environment once per shell: `source "$HOME/opt/oss-cad-suite/environment"`.

---

## 1. The pipeline at a glance

```
 profile ─▶ build ─▶ test ───────────────▶ (synth ─▶ deploy)
 configs/   images    ISS · cosim · RTL       ECP5     ULX3S
            & ISS      roles · kernel · host   bitstream board
```

| Stage | Entry command | Proves |
|---|---|---|
| Profile | `make config-check-all` | every profile resolves to compatible components |
| Build | `make -C sim/axsim test` · `make -C sw/baremetal images` | golden ISS + a target image |
| Test | `make component-test` (+ the suites in §3) | selected components compose and run |
| Synth | `make fpga CONFIG=configs/ulx3s-85f.json` | the shell places and routes on ECP5 |
| Deploy | `make -C rtl/fpga program` | the bitstream runs on a real board (reversible) |

---

## 2. Build

### Choose / inspect a profile
```bash
make component-list                              # catalog of selectable components
make component-show COMPONENT=role.gpu-compute   # one manifest
make config-check   CONFIG=configs/sim-bram.json # resolve one profile
make config-check-all                            # resolve every profile in configs/
```

### Build the pieces
```bash
make -C sim/axsim axsim         # the golden ISS binary
make -C sw/baremetal images     # bare-metal .elf/.bin/.hex (hello, timer, role, tpu, gpu, ...)
make -C sw/kernel   images      # aXos image (build/axos_boot.{elf,bin,hex})
```

aXos build knobs (append to the `sw/kernel` command):

| Knob | Effect |
|---|---|
| `HOSTLINK=1` | host-managed personality: the console pipe carries the host-link protocol instead of the interactive shell |
| `STORAGE=1` | mount the AXFS SD image path |
| `KERNEL_CONFIG=configs/kernel-cooperative.json` | select an alternate kernel-service profile |

### Run one image on a selected SoC profile
```bash
make sim CONFIG=configs/sim-bram.json \
  RAM_INIT_FILE="$PWD/sw/baremetal/build/hello.hex"

make software CONFIG=configs/sim-axos.json   # build + run the profile's software component
```

### Open an interactive session on a profile

A `run` is batch: it consumes a fixed script, then prints the whole transcript.
Every `check-*` target uses that, and it is what a self-checking test wants. To
*type* at the machine instead, build the model once and keep it open — in batch
mode each exchange is a separate process, so the machine reboots between
commands and nothing carries over.

```bash
MODEL=$(make -s -C sim/soc model-path \
  RAM_INIT_FILE="$PWD/sw/kernel/build/axos_boot.hex" RESET_PC=0x80000000 \
  COMPONENT_CONFIG=../../configs/sim-role-loopback.json)
"$MODEL" --uart-interactive        # aXos prompt; Ctrl-D closes the session
```

State persists across commands: running the shell's `role` twice reports
`irq=1` then `irq=2`, because it is one machine rather than two boots. An
interactive run ends on console close or the finisher, so it is not bounded by
`MAX_CYCLES`.

Between keystrokes the machine is *stopped*, not spinning: `wfi` parks the hart
and the console is interrupt-driven, so the cycle counter holds still while it
waits for you. `console` in the shell reports which path input actually took —
`irq 21 polled 0 stalls 0` means every byte arrived as an interrupt. A platform
whose PLIC numbers its devices differently (QEMU's `virt`) falls back to polling
and says so there, rather than parking on an interrupt that will never arrive.

### Open the same session in a browser

Optional tier, and load-bearing for nothing — it needs Emscripten
([toolchain.md](toolchain.md)) and no evidence claim rests on it. The same
Verilated model compiled to WebAssembly boots aXos in a tab with no toolchain
and nothing installed:

```bash
./tools/web.sh                 # verify headlessly, then serve and open the page
```

That is the whole thing from a plain shell. It sources the SDK (emsdk
deliberately does not touch your profile), prefers a Verilator the suite is
green on, builds the aXos payload if it has never been built, runs the headless
check, picks a free port, and opens the browser.

```bash
./tools/web.sh --check-only                        # verify, do not serve
./tools/web.sh --port 9000 --no-open               # pin the port, stay put
./tools/web.sh --config configs/sim-bram.json \
               --payload sw/baremetal/build/hello.hex
```

`make web` and `make web-check` call the same script with `WEB_CONFIG` /
`WEB_PAYLOAD`. The steps underneath stay available separately —
`make -C sim/web build|check|bench|serve` — and `make web-bench` times the WASM
machine against the native one on the same host.

Changing the payload does not rebuild the machine: unlike the native model, the
image is loaded into it at run time. Changing the *profile* does rebuild it, and
the bundle is keyed on the selection so a stale one is never served under a new
name.

The page is the same machine, not a re-implementation: clocking, the UART
handshake, and the SPI sampling edge all come from
`components/harness/common/soc_machine.h`, which the batch runner and the
interactive session use too, so a cycle count read off the page is the one a
local run reports. Details and measurements are in
[sim/web/README.md](../sim/web/README.md).

---

## 3. Test

Run the narrowest check that covers a change, then the composition suite before
declaring a component or profile ready.

### 3.1 Core / fast
```bash
make -C sim/axsim  test        # ISS against the rv32 ISA suite
make -C sim/unit   test        # directed RTL unit benches (see `run-*` targets for one bench)
make -C sim/cosim  test        # Verilator lock-step cosimulation vs the ISS
```

### 3.2 Bare-metal, three platforms (ISS · QEMU · RTL)
```bash
make -C sw/baremetal check-hello check-timer check-preempt check-fencei
make -C sw/baremetal check-spi check-sd            # RTL-only (SPI-SD path)
```

### 3.3 Accelerator roles (RTL-only — the ISS does not model the role window)
```bash
make -C sw/baremetal check-role     # role.loopback contract proof
make -C sw/baremetal check-tpu      # TPU-lite folded int8 GEMM vs on-core reference
make -C sw/baremetal check-gpu      # GPU-compute SIMT engine vs on-core reference (8-lane)
make -C sw/baremetal check-gpu-perf # GPU throughput regression vs on-core (8-lane)
make -C sw/baremetal check-gpu1     # gpu1 banked SIMT engine vs on-core ISA oracle
```
Two role components, each tuned by parameter rather than duplicated per size:

- `role.gpu1` — the current engine: banked global memory and a control ISA
  (divergence, branches, divide, shuffle).  Parameters: `lanes`, `banks`,
  `enable_div`, `enable_shfl`.
- `role.gpu-compute` — the earlier single-port engine, kept as the reference the
  gpu1 store-ordering semantics are matched against.  Parameter: `lanes`.

Software reads the geometry from the role's CAPS register, so `check-gpu1` is
the check for any parameterisation.  See
[hardware-capabilities.md](hardware-capabilities.md) for measured cycles.

### 3.4 Components / composition

Validate the vendor-neutral research descriptors and their exact workload
oracles independently of any FPGA toolchain:

```bash
make personality-check
make comparison-check
make live-check
```

```bash
make config-check-all              # all profiles resolve
make component-test                # runs the supplied composition matrix (slower)
make -C sw/baremetal check-suite-minimal   # lean-component family in one suite
make -C sim/unit run-suite-ax2            # every core.ax2 tier vs the official ISA suite
make -C sim/unit run-suite-gpu1           # every role.gpu1 tier vs the ISA oracle
make -C sw/baremetal check-suite-ax2      # ax2 + gpu1 SoC integration
```
Prefer **suites** over a check-plus-profile per hardware combination: a suite
exercises a family of components together.  `check-suite-minimal` runs
`core.minimal` driving the CPU (hello), the GPU role, and the TPU role from the
`sim-minimal*` fixtures.  Add a suite when a family of components (a new core,
an accelerator variant) warrants coverage without one-off profiles.

The ax2 and gpu1 suites show the shape to copy for a **parameterised** family.
Tier coverage lives in `sim/unit`, which builds each tier's RTL directly and so
needs no profile per tier; only the SoC-integration leg needs a profile, and it
needs one (`sim-ax2.json`, `sim-ax2-gpu1.json`), not one per tier.  A tier sweep
does not belong in `configs/` — the tiers differ only in parameters, and adding
a profile each would duplicate coverage the unit suite already has.

### 3.4a Tuning a component
```bash
python3 tools/configure.py describe core.ax2     # what it exposes and the defaults
```
A component is the unit of *architecture*; a size inside it is a build-time
parameter.  A new component is warranted when the architecture changes — a
different pipeline, a different privilege model, a different execution model —
not when a cache or a lane count changes.  So `core.ax2` is one component with
`issue_width`, `icache_kb`, and `btb_entries`, and `role.gpu1` is one component
with `lanes`, `banks`, `enable_div`, and `enable_shfl`.

A profile overrides by name, under the component's kind:

```json
{
  "components": { "core": "core.ax2", "role": "role.gpu1" },
  "parameters": {
    "core": { "issue_width": 1, "icache_kb": 8 },
    "role": { "lanes": 16, "banks": 16 }
  }
}
```

The manifest declares each parameter with the default that *defines the
baseline*, so an unparameterised profile is the reference configuration.
Overrides are validated: naming a parameter the component does not declare is a
configuration error that lists what it does declare, the same discipline that
makes component selection validated rather than hopeful.  Parameters reach the
RTL as `+define+` flags, because they must cross stock module boundaries
(`axcore`, `axrole`) whose port and parameter lists are shared with every other
implementation and must not grow implementation-specific knobs.

### 3.4b Benchmarking
```bash
make -C sw/baremetal images
python3 tools/bench.py cpu     # IPC per core and per ax2 parameter setting
python3 tools/bench.py gpu     # kernel cycles per role parameter setting
python3 tools/bench.py tpu     # int8 GEMM accelerator versus the host CPU
python3 tools/bench.py tang    # exact Nano/Primer max-profile wall-time view
python3 tools/bench.py render  # render workload vs cache policy/size and divider
python3 tools/bench.py         # all five
```
The sweep needs a profile per configuration, but those are measurement fixtures
rather than supported ones, so `bench.py` generates them into a scratch
directory instead of the catalog.  What it sweeps is mostly *parameters* now,
which is the point: the numbers show what each knob is worth instead of
asserting that several near-identical components differ.

The CPU sweep uses the workload-only `cpu_perf measured` cycle count, excluding
setup and UART overhead. The board payloads also print stable checksums and
time projections for 27 MHz Tang Nano and 25 MHz Tang Primer. GPU/TPU payloads
separate upload, doorbell-to-done compute, and readback-plus-verification from
the complete offload total. Those projected microseconds are pre-P&R; use the
achieved hardware clock as the final frequency.

### 3.5 Kernel (aXos) — needs `qemu-system-riscv32` ≥ 7

`check-boot` covers three things on the ISS, QEMU, and the RTL: the interactive
shell, fork/wait with exit-status propagation, and persistent `exec` — which
passes `argv` to `sw/kernel/userprog/hello.c`, then restores the shell instead
of halting the machine. The userspace ABI it targets is
[abi.md](abi.md); the syscall table (`syscall.linux-compat`) and the image
format (`loader.elf32`) are both selectable components, as is the C library
(`libc.axlibc`) that user programs link against.

Write a user program in `sw/kernel/userprog/` as ordinary C: it gets a `main()`,
malloc, printf, string functions, 64-bit arithmetic, and `open`/`read`/`lseek`/
`fstat` on files, and is built and linked entirely separately from the kernel,
reaching it only as an embedded image.  Files come from the selected
`filesystem` component — the SD card when one is present, and a built-in
read-only root when there is not, so a program can read a file on every profile
rather than only the ones with storage.
```bash
make -C sw/kernel check-boot QEMU=/path/to/qemu-system-riscv32   # shell + fork/wait on ISS, QEMU, RTL
make -C sw/kernel check-shell         # generic commands, parsing, and kernel observability on ISS
make -C sw/kernel kernel-component-test QEMU=/path/to/...        # default + cooperative scheduler
make -C sw/kernel check-memory          # 32 MiB cached external-memory RTL
make -C sw/kernel check-storage         # AXFS mount over SPI-SD (RTL)
make -C sw/kernel check-storage-write   # AXFS write/readback (RTL)
make -C sw/kernel check-sdboot          # boot ROM + SD boot through physical-SDRAM RTL
make -C sw/kernel check-uartboot        # immutable ROM + blank RAM + runtime kernel upload
```

### 3.6 Shell control plane + host-link (RTL-only)
```bash
make -C sw/kernel check-role-driver     # aXos drives role.loopback from its own shell
make -C sw/kernel check-role-irq        # completion arrives as an S-mode interrupt, not a poll
make -C sw/kernel check-hostlink        # axhost drives loopback, TPU-lite, and GPU-compute over the link
```

`check-role-driver` also executes `hello.elf` in U-mode against the loopback
role, covering `role_info` plus tokenized `role_submit`/`role_wait`, retry
errors, and the kernel-only MMIO alias.

`check-role-irq` is the narrower claim underneath it: the role's level-sensitive
line reaches S-mode through the PLIC's supervisor context, and the kernel never
reads `STATUS`. It runs two jobs, because a single completion would also pass
with a handler that claims but never completes.

### 3.7 Randomized + formal (run on core / RVFI / translation changes)
```bash
make -C sim/testgen fuzz           # long randomized instruction lock-step
make -C sim/testgen paging         # randomized Sv32 paging
make -C formal check               # riscv-formal bounded proofs, reference core
make -C formal check-minimal       # same properties on core.minimal
make -C formal check-ax2           # same properties on ax2's two retire channels
make -C formal check-all           # all three cores
```

`check-ax2` uses the same solver as the reference suite but needs more memory
(its block-RAM instruction cache dominates model construction, not the bounded
depth), so it does not finish on a small machine; see
[formal/README.md](../formal/README.md).  `formal.yml` runs both cores.

### 3.8 Recommended full regression
```bash
make config-check-all
make -C sim/axsim test
make -C sim/cosim test
make -C sw/baremetal images
make -C sw/baremetal check-hello check-timer check-preempt check-fencei check-role check-tpu check-gpu
make component-test
make -C sw/kernel kernel-component-test QEMU=/path/to/qemu-system-riscv32
make -C sw/kernel check-role-driver check-role-irq check-hostlink check-uartboot
make -C formal check          # after core/RVFI changes
```

---

## 4. Deploy (FPGA synthesis → physical board)

Physical deployment is the **final evidence gate**.  Simulation passing is not
board proof.  The board component selects the flow; three boards are supported:

| Board | Profile | Flow | Main memory |
|---|---|---|---|
| ULX3S-85F (Lattice ECP5) | `configs/ulx3s-85f.json` | ECP5 | external SDRAM + fabric ROM |
| Tang Nano 20K (Gowin GW2A-18C) | `configs/tangnano20k.json` | Gowin | 32 KB on-chip block RAM (BSRAM) |
| Tang Primer 25K Dock (Gowin GW5A-25A) | `configs/tangprimer25k.json` | Gowin | 32 KB on-chip block RAM (BSRAM) |

What each board can actually run, per configuration, backed by real synth/sim
runs: [hardware-capabilities.md](hardware-capabilities.md). Board procedures
and safety notes: [tangprimer25k-bringup.md](tangprimer25k-bringup.md)
and [ulx3s-bringup.md](ulx3s-bringup.md).

### 4.1 Tool check
```bash
source "$HOME/opt/oss-cad-suite/environment"
make -C rtl/fpga check-tools  COMPONENT_CONFIG=$PWD/configs/tangnano20k.json  # flow-specific tools
make -C rtl/fpga toolchain-report COMPONENT_CONFIG=$PWD/configs/tangnano20k.json
```

### 4.2 Synthesis-only gate (no P&R tools needed)
```bash
make -C rtl/fpga synth COMPONENT_CONFIG=$PWD/configs/tangnano20k.json   # yosys netlist only
make -C rtl/fpga synth COMPONENT_CONFIG=$PWD/configs/tangprimer25k.json # GW5A netlist only
```
`synth` is the "does the design map for this board" check: it runs Yosys alone,
so it passes with only `yosys` installed. For both Tang profiles it must map
the 32 KB RAM to block RAM (`DPB` cells), not flip-flops — the memory uses
registered reads (`axram` `SYNC_READ=1`) precisely so it infers BSRAM.
Generated sources, logs, netlists, and bitstreams live in separate
configuration-keyed directories below `rtl/fpga/build/`, so switching between
CPU, GPU, and TPU profiles cannot reuse a sibling profile's artifact.

### 4.3 Synthesis, place-and-route, bitstream
```bash
make fpga CONFIG=configs/ulx3s-85f.json     # top-level wrapper (ECP5), or:
make fpga CONFIG=configs/tangnano20k.json   # Gowin/Tang Nano
make fpga CONFIG=configs/tangprimer25k.json # Gowin/Tang Primer 25K
make primer-runtime-preflight               # exact Primer runtime image + evidence; no board access
make -C rtl/fpga config COMPONENT_CONFIG=$PWD/configs/tangnano20k.json  # print resolved selection
```
The P&R tool (`nextpnr-ecp5` / `nextpnr-himbaechel`) prints utilisation and
timing at the end; the board clock target (25 MHz ULX3S, 27 MHz Tang Nano,
25 MHz Tang Primer) must pass. Do not program a bitstream from a failed or
unconstrained P&R run.

The reproducible stage-2 partial-reconfiguration measurement uses explicit
matching placement seeds and writes a JSON frame/tile report:

```bash
make -C rtl/fpga pr-delta
```

It is a research measurement, not a programming target.  On the ULX3S 85F it
also records the expected current Trellis diagnostic that delta address
encoding is implemented only for 45F; see [partial-reconfig.md](partial-reconfig.md).

### 4.4 Program the board
```bash
make -C rtl/fpga program COMPONENT_CONFIG=$PWD/configs/tangnano20k.json  # reversible SRAM config
```
`program` targets the board named in the manifest (`ulx3s`, `tangnano20k`, or
`tangprimer25k`).
Then open the console (`picocom -b 115200 /dev/ttyUSB0`) and confirm the UART
transcript; for the Tang Nano the BL616 exposes the USB serial and LED5 shows a
~0.5 s heartbeat.

For Tang Primer use the same command with `configs/tangprimer25k.json`; its
programmer name is `tangprimer25k`, the onboard debugger UART is 115200 8-N-1,
and S1 resets the SoC. The Dock has no ordinary FPGA user LED, so UART is the
verdict.

### 4.5 Persistent flash — only after a passing board proof
```bash
make -C rtl/fpga flash COMPONENT_CONFIG=$PWD/configs/tangnano20k.json  # writes config flash
```
`program` is the normal dev path; flash is persistent.

---

## 5. Maintaining this document

After every milestone, update this file in the same change if the milestone:

- adds or renames a `check-*`, build, or deploy target;
- introduces a new build knob (like `HOSTLINK=1`) or profile that users run;
- changes a required tool or version.

Keep the command groups and the §3.8 full-regression sequence current.  A
milestone is not done until its reproducible command lives here.
