# atomiX

[![CI](https://github.com/ShubhendraGautam/atomiX/actions/workflows/ci.yml/badge.svg)](https://github.com/ShubhendraGautam/atomiX/actions/workflows/ci.yml)
[![Nightly](https://github.com/ShubhendraGautam/atomiX/actions/workflows/nightly.yml/badge.svg)](https://github.com/ShubhendraGautam/atomiX/actions/workflows/nightly.yml)
[![Formal](https://github.com/ShubhendraGautam/atomiX/actions/workflows/formal.yml/badge.svg)](https://github.com/ShubhendraGautam/atomiX/actions/workflows/formal.yml)

> **A DIY RISC-V computer, operating system, and FPGA platform.**
> Build the reference machine — or replace the parts that matter to you.

| Reference machine | Evidence | Platform direction |
|---|---|---|
| RV32IM, five stages, M/S/U + Sv32 | ISS · lock-step RTL · ISA tests · formal | ULX3S/Tang shells + swappable accelerator roles |

**Status:** simulation-verified reference system · component-first builds ·
Tang Primer 25K CPU, GPU, and TPU verified on physical FPGA hardware.

[Architecture](DESIGN.md) · [Build/test/deploy](docs/workflow.md) ·
[Dependencies](docs/dependencies.md) ·
[Live checklist](docs/design-checklist.md) ·
[Research checklist](docs/research-checklist.md) ·
[Components](components/README.md)

---

## What is atomiX?

atomiX is a from-scratch RISC-V computer that grows into a reconfigurable FPGA
platform.  The reference build includes a five-stage CPU, SoC, bare-metal
runtime, and the aXos kernel.  The longer-term platform keeps that computer as
the management shell while accelerator roles attach at a defined boundary —
the role window is live today (`role.loopback` proves it), with TPU-lite,
GPU-compute, and the banked-memory gpu1 family implemented and verified as
selectable roles.  Their completion is available either polled or as a machine
external interrupt through the shell's PLIC.
The normal reconfiguration path is now program loading, not synthesis:
`make runtime-primer` uploads one 32 KiB aXos payload into a stable RTL image,
then loads, runs, replaces, and re-runs GPU microcode in milliseconds.
Kernel builds follow the same rule: the fixed image boots an immutable UART
loader and `axhost --upload-kernel` installs aXos into blank RAM with CRC-32
verification, so kernel changes never invoke FPGA tools.

It is designed to be modified.  A user can substitute the CPU, memory,
interconnect, peripherals, board, simulation harness, or aXos service policy
without forking the rest of the project.

## Hardware achievement

On 2026-07-29, a Sipeed Tang Primer 25K Dock completed the first physical
atomiX bring-up. The RV32IM CPU booted from on-chip BSRAM and printed over the
Dock UART; separate volatile-SRAM images then passed the self-checking 4-lane
GPU-compute and folded 24-MAC TPU-lite workloads. See the
[captured evidence and reproduction commands](docs/tangprimer25k-bringup.md#verified-hardware-result).

```text
  RISC-V core ── aXbus SoC ── aXos
       │             │          │
       └──── selectable components ────┐
                                        ▼
                    FPGA shell + swappable accelerator roles
```

## Start in three commands

Install the core tools first — the safe, tiered instructions are in
[Dependencies](docs/dependencies.md).

```bash
make -C sim/axsim test
make -C sw/baremetal images
make sim CONFIG=configs/sim-bram.json \
  RAM_INIT_FILE="$PWD/sw/baremetal/build/hello.hex"
```

That path builds the golden ISS, creates a target image, and runs it on the
selected Verilated SoC.  Continue with the
[build/test/deploy reference](docs/workflow.md) for three-platform checks,
randomized cosimulation, aXos, host-link, formal verification, and FPGA
synthesis — it is the single, maintained list of every command.

## Or boot it with nothing installed

The same Verilated SoC compiles to WebAssembly, so aXos boots in a browser tab
with no toolchain and no FPGA — 27,509 cycles to a shell prompt in about 30 ms,
which is native wall-clock parity on the same host.

```bash
./tools/web.sh                # or: make web
```

One command from a plain shell: it sources the Emscripten SDK, picks a
Verilator the suite is green on, builds the payload if it is missing, verifies
the machine headlessly, finds a free port, and opens the page. Emscripten is
the one thing it cannot install for you — see [docs/toolchain.md](docs/toolchain.md).

It is the machine, not a re-implementation: the page clocks the model through
the same runner code `make sim` uses, so a cycle count read off it is the one a
local run reports. Details in [sim/web/](sim/web/).

## Build it your way

Profiles select compatible components; manifests make every selection visible
and reproducible.

```bash
make component-list
make config-check CONFIG=configs/sim-bram.json
make component-show COMPONENT=memory.sdram
```

The stock integration contracts are intentionally small.  An external manifest
can point to an out-of-tree implementation, but it earns its own compatibility
and verification claim.  Read the [component catalog](components/README.md),
[profile guide](configs/README.md), and
[component map](docs/component-map.md) before making a replacement.

## Where to go next

| I want to… | Start here |
|---|---|
| Understand the machine | [DESIGN.md](DESIGN.md) |
| Build, test, or synthesize | [docs/workflow.md](docs/workflow.md) |
| Set up a host or FPGA toolchain | [docs/dependencies.md](docs/dependencies.md) |
| Change an implementation | [components/README.md](components/README.md) |
| Inspect current evidence and open work | [docs/design-checklist.md](docs/design-checklist.md) |
| Track partial reconfiguration, morph compute, and adaptive-logic research | [docs/research-checklist.md](docs/research-checklist.md) |
| Extend the open compute-personality contract | [docs/personality-contract.md](docs/personality-contract.md) |
| Compare compute implementations without a vendor-specific score | [docs/comparison-contract.md](docs/comparison-contract.md) |
| Follow the adaptive “Live FPGA” track | [docs/live-fpga.md](docs/live-fpga.md) |
| Prepare the Tang Primer 25K | [docs/tangprimer25k-bringup.md](docs/tangprimer25k-bringup.md) |
| Prepare the ULX3S | [docs/ulx3s-bringup.md](docs/ulx3s-bringup.md) |

## Repository map

| Area | Purpose |
|---|---|
| [components/](components/) | Selectable manifests and their owned RTL/service sources |
| [configs/](configs/) | Reproducible system and kernel-service profiles |
| [research/](research/) | Versioned research contracts, workloads, and experiment inputs |
| [sim/](sim/) | ISS, lock-step harnesses, SoC runner, and generators |
| [formal/](formal/) | riscv-formal and SymbiYosys integration |
| [rtl/](rtl/) | Generic FPGA flow and architecture entry points |
| [sw/](sw/) | Bare-metal runtime, boot ROM, aXos, and future host/user software |
| [docs/](docs/) | Build, dependency, architecture, and board documentation |

---

**Build what teaches. Verify what matters. Keep the seams open.**

## Licence, attribution, and marks

atomiX is copyright © 2026 Shubhendra Gautam and atomiX contributors, released
under the [MIT License](LICENSE).

| File | Purpose |
|---|---|
| [LICENSE](LICENSE) | MIT terms — the copyright grant |
| [NOTICE](NOTICE) | attribution notice and external tool dependencies |
| [AUTHORS.md](AUTHORS.md) | who wrote it |
| [CITATION.cff](CITATION.cff) | how to cite it in published work |
| [TRADEMARKS.md](TRADEMARKS.md) | use of the atomiX name, and third-party marks |
| [CONTRIBUTING.md](CONTRIBUTING.md) | DCO sign-off and evidence standards |

The MIT License grants broad rights to use, modify and redistribute the code,
including commercially. It does **not** grant rights in the project's name:
that is covered separately by [TRADEMARKS.md](TRADEMARKS.md).
