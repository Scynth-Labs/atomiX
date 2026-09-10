# atomiX documentation

`DESIGN.md` is the architectural contract.  This directory holds the focused
guides and interface specifications that make the contract buildable,
verifiable, and replaceable.

## Start with these guides

- [roadmap.md](roadmap.md) — platform direction, milestone exit gates, adoption
  targets, and work deliberately deferred.
- [boards/](boards/README.md) — delivery, execution targets, research, and lab priorities with
  dependencies, first slices, and a task template.
- [experiment-alpha.md](experiment-alpha.md) — the M0 walkthrough: run one
  workload natively, then on RTL, and reproduce someone else's result. Start
  here if you want to use the platform rather than build it.
- [workflow.md](workflow.md) — the single, maintained build, test, and deploy
  command reference (profile selection, all checks, formal, and the FPGA flow).
  Its [experiments section](workflow.md#34c-experiments-one-workload-several-implementations-and-targets)
  covers running a plan, comparing its records, and publishing them as pages.
- [verification.md](verification.md) — shared CI/nightly suite manifest,
  coverage ladder, stage logs, timeouts, and extension rules.
- [dependencies.md](dependencies.md) — dependency tiers and compatibility
  baseline.
- [design-checklist.md](design-checklist.md) — live, evidence-based status and
  the final hardware gate.
- [research-checklist.md](research-checklist.md) — staged partial-
  reconfiguration, morph-fabric, and adaptive-logic experiments.
- [toolchain.md](toolchain.md) — exact Ubuntu/Debian setup and known tool
  workarounds.
- [tangprimer25k-bringup.md](tangprimer25k-bringup.md) — safe Tang Primer 25K
  Dock build, SRAM programming, and UART procedure.
- [ulx3s-bringup.md](ulx3s-bringup.md) — safe ULX3S board procedure.
- [achievements/](achievements/) — per-hardware record of completed physical
  and board-independent evidence, release hashes, and open failures.
- [benchmarks/tangprimer25k.md](benchmarks/tangprimer25k.md) — physical runtime,
  kernel-tier, and fresh synthesis measurements for the available board.

## Architecture and composition

- [execution-targets.md](execution-targets.md) — planned native, simulated,
  accelerator, FPGA, and ASIC boundaries for hardware/software co-design.
- [axbus.md](axbus.md) — the normative aXbus transaction contract.
- [memory.md](memory.md) — reference memory, cache, SDRAM, and SD architecture.
- [components.md](components.md) — component model and extension boundary.
- [component-map.md](component-map.md) — which repository areas are selectable
  and where their sources live.
- [host-protocol.md](host-protocol.md) — host-link framing between `axhost` and
  the shell control plane.
- [abi.md](abi.md) — the aXos userspace ABI: syscall convention and numbers,
  ELF entry contract, process state, files, and kernel-mediated role jobs.
- [personality-contract.md](personality-contract.md) — vendor-neutral compute
  personality, workload, capability-negotiation, and reconfiguration contract.
- [comparison-contract.md](comparison-contract.md) — correctness-gated R2
  comparison matrix and machine-readable evidence rules.
- [live-fpga.md](live-fpga.md) — adaptive-reconfiguration safety model and the
  immutable L0 telemetry schema.

Keep a specification and its implementation change together whenever a
documented interface changes.
