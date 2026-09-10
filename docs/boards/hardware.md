# Physical FPGA lab board

[All boards](README.md) · [Primer procedure](../tangprimer25k-bringup.md) ·
[Physical results](../achievements/tangprimer25k.md)

The Tang Primer 25K Dock is the only available board. Ready means a task can
start at a scheduled lab session; it does not mean the board is attached now.
All owners are unassigned. No hardware action is part of creating this board.

This is the lab queue for the available FPGA equipment. Native CPU, external
accelerator, and ASIC feasibility work lives on the [targets board](targets.md)
and has its own execution/evidence requirements. This board does not define
the platform's full hardware scope.

| Card / outcome | Priority | State | Depends on / blocker | First reviewable slice |
|---|---|---|---|---|
| [HW-01: reproducible Primer runtime evidence bundle](../design-checklist.md#tang-primer-25k--current-hardware-priority) | P1 | Ready | Dock access for the repeat; existing physical runtime records for preparation | Inventory exact loader/profile/payload identities and map earlier observations to them before repeating the SRAM procedure |
| [HW-02: resident GPU+TPU validated on the Dock](../research-checklist.md#r2--fast-compute-personality-transformation) | P2 | Next | HW-01; exact composite loader fit and timing preflight | Prepare a matched two-engine workload and rejection/recovery transcript, then run one volatile session |
| [HW-03: bounded L3 physical trial](../research-checklist.md#r3--live-fpga-adaptive-logic) | P2 | Blocked | R3 readiness: fitting Primer morph profile, exact supported modes, oracle/canary cases, telemetry and authority decisions RX-04/RX-05 | Map simulated cases onto the available fabric and record unsupported cases; do not schedule activation until the gate passes |
| [HW-04: measured energy per completed workload](../research-checklist.md#r2--fast-compute-personality-transformation) | P2 | Blocked | RX-07; available calibrated fixture | Once available, establish idle baseline and trigger repeatability before matched workload measurements |
| [HW-05: physical ECP5 partial-load trial](../research-checklist.md#r1--partial-reconfiguration-of-an-fpga) | P2 | Blocked | RX-06 accepted confined delta; matching ECP5 board access; reviewed live-load/recovery procedure | After blockers clear, repeat offline rejection gates and prepare a volatile trial on the exact supported device |

## Evidence that closes a hardware card

The owning checklist states each workload's acceptance cases. Every physical
record also needs the actual device/core/Dock revision, tool versions, source
and resolved profile, loader-bitstream hash, independently identified runtime
payloads, timing/resource reports, serial identity, complete observations, and
recovery/failure outcomes. Keep compact records and hashes in the repository;
keep generated images, logs, and build trees outside tracked source.

Earlier physical observations stay bound to their original hardware identity.
A current P&R pass does not renew an old board claim. A new payload must fit its
runtime memory budget and load through the stable loader; it does not trigger
re-synthesis. Hardware changes need fresh fit/timing and physical evidence.

Use the [lab skill](../../skills/tang-primer-lab/SKILL.md) and the authoritative
[Primer procedure](../tangprimer25k-bringup.md) when executing these cards.
Preserve the management shell, UART loader, isolation, watchdog, oracle,
provenance, and rollback boundaries. Respect the Primer's 32 KiB main RAM and
each independently selected kernel-evolve tier's existing fit gate.

SRAM programming is the development path. Flash programming requires explicit
approval in the turn performing it. Buying a board or fixture is a separate
decision; a backlog card does not supply missing equipment or authorize a
purchase. The experiment alpha has no dependency on HW-02 through HW-05.

## Priority decisions

- 2026-09-10: prioritize a repeatable bundle for the hardware already owned.
  Keep composite-engine, energy, and adaptive trials tied to concrete evidence
  questions. Additional boards and optional Dock peripherals remain conditional.
