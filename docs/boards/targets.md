# Execution targets and toolchains board

[All boards](README.md) · [Execution-target design](../execution-targets.md) ·
[Roadmap](../roadmap.md)

This board owns the work that makes atomiX useful across software execution,
architecture models, programmable hardware, and eventual silicon. All owners
are unassigned. AX-10 is part of M0 and comes after delivery card AX-01; the
ASIC dependency audit can start independently without a board or PDK.

| Card / outcome | Priority | State | Depends on | First reviewable slice |
|---|---|---|---|---|
| [AX-10: native CPU and RTL execution adapters](../design-checklist.md#ax-10) | P0 | Done | AX-01 | Define the adapter contract and implement one native integer workload against the shared oracle, without invoking FPGA or RISC-V tools |
| [AX-12: ISS and emulator adapters](../design-checklist.md#ax-12) | P1 | Ready | AX-10 | Wrap the existing aXsim run path, preserving its functional scope; add QEMU through its own capability/prerequisite declaration |
| [AX-11: compiler/runtime and hardware co-design experiments](../design-checklist.md#ax-11) | P1 | Next | AX-10, AX-02, AX-03 | Compare two declared compiler choices on one native workload with separate artifact identities and the same oracle |
| [RX-08: ASIC portability dependency audit](../research-checklist.md#rx-08) | P1 | Ready | Existing RTL and manifests | Inventory memory, arithmetic, initialization, reset/clock, and I/O assumptions for one small block; identify required technology boundaries |
| [AX-13: external accelerator backend](../design-checklist.md#ax-13) | P2 | Next | AX-10, AX-03; a supported device/runtime for execution | Inventory available compute devices and choose one workload/adapter only when an actual target is accessible |
| [RX-09: technology-mapped implementation feasibility](../research-checklist.md#rx-09) | P2 | Next | RX-08; accessible libraries, tools, and compute budget | Specify a bounded synthesis/physical-design experiment for the audited block, with named constraints and required reports |

## What keeps this work focused

AX-10 proves portability early using the native host already needed for
development. AX-12 makes existing functional platforms available through the
same experiment path. AX-11 lets the platform explore both software and
hardware choices, while keeping which variables changed visible in the result.

AX-13 can select a GPU or another accessible accelerator through its own
backend; it does not mandate CUDA, a specific vendor, an NPU purchase, or a
remote service. If access is absent when its execution slice is pulled, mark
that slice Blocked. CPU fallbacks can be separately identified candidates.

RX-08 and RX-09 earn an ASIC feasibility decision. Foundry selection, tapeout,
and purchasing are later work, with their own prerequisites. Existing Primer
limits stay local to those profiles; they do not size host experiments or
future technology targets.

Current commands remain in [workflow.md](../workflow.md). New adapters must
add focused conformance checks and appropriate suite coverage before closure;
the existing simulator or FPGA suite cannot certify an unimplemented backend.

## Priority decisions

- 2026-09-10: broadened the product to hardware/software co-design. Added a
  native CPU adapter to the first milestone so FPGA independence is exercised.
- 2026-09-10: opened ASIC feasibility as staged research and retained physical
  implementation, sign-off, and manufactured-device evidence as distinct gates.
- 2026-09-10: closed AX-10 with three adapters -- native host, RTL role, and
  RTL SoC -- behind one boundary in `tools/execution/`. AX-12 becomes a matter
  of adding aXsim and QEMU to that map rather than of designing an interface.
