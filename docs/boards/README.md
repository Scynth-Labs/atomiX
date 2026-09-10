# Development priority boards

Start with the [platform roadmap](../roadmap.md). These boards turn its outcomes
into a pull queue; the checklists retain the detailed acceptance criteria and
historical evidence. Initial triage: 2026-09-10. No implementation is assigned
or claimed complete by this planning change.

| Board | Purpose | First pull |
|---|---|---|
| [Delivery](delivery.md) | Usable experiments, component SDK, reproducible release | AX-01: define the first workload-driven experiment |
| [Execution targets](targets.md) | Native execution, compiler/runtime work, emulators, accelerators, and ASIC feasibility | AX-10 after AX-01; RX-08 audit can start independently |
| [Research](research.md) | Test optimization and adaptation value; retire technical uncertainty | RX-04: distinguish unavailable telemetry from observed zero |
| [Hardware](hardware.md) | Earn repeatable claims on the available Tang Primer 25K Dock | HW-01: consolidate and repeat the exact runtime-image evidence |

AX-01 is the default next task for a single developer. Independent research and
lab rows are options when the appropriate capacity or equipment is available;
separate boards do not imply concurrent commitments. M0 follows AX-01 → AX-10
→ AX-02 → AX-03 → AX-04 across delivery and execution targets.

## Priority and state

| Priority | Meaning |
|---|---|
| P0 | Required for the current milestone, or a defect invalidating a claim it depends on |
| P1 | Next adoption/reliability improvement, or prerequisite for a selected research result |
| P2 | Conditional expansion or bounded exploratory work; promote only with a stated reason |

| State | Meaning |
|---|---|
| Ready | First slice is specified and has no unmet prerequisite |
| Next | Ordered behind named work or intentionally outside the current cycle |
| Active | A named owner is executing one bounded slice |
| Review | Implementation and required evidence are available for assessment |
| Blocked | Work cannot proceed without the named external input or unresolved gate |
| Done | Owning checklist gate is satisfied and evidence is linked |

Priority answers why work matters; state answers whether it can be pulled.
A P0 dependency can be Next. A lower-priority lab task can be Ready. Every row
starts unassigned. An owner is recorded only when someone actually takes it.

## Keep development fast

- Pull the highest-priority Ready task whose dependencies and equipment are
  available. Default work-in-progress limit: one Active implementation slice
  per developer, plus one item awaiting review. Finish or unblock before pulling
  another; adapt this planning limit explicitly if team capacity changes.
- Before starting, record the owner, first-slice boundary, and expected evidence.
  Use the task template below. Keep a larger parent open until all its criteria
  pass; board rows are outcomes, not estimates of one commit each.
- Review priorities at the end of each development cycle or weekly, whichever
  comes first. Inspect blocked work and failed gates before adding features.
  Record why a card moved; retain negative research results.
- Change priority/state/owner here, acceptance criteria in the linked checklist,
  and commands in [workflow.md](../workflow.md). Do not maintain a second full
  acceptance checklist on the board. An eventual hosted board should link these
  IDs and documents.
- When closing work, link the evidence from its checklist gate and retain the
  board row as Done with a short result/date. Never convert historical simulation
  or P&R evidence into a physical result during backlog cleanup.

## Task template

Copy this into a task note or issue body when pulling a card. Creating a local
task does not require creating a hosted issue.

```text
ID / title:
Parent board card and checklist gate:
User outcome or falsifiable hypothesis:
Priority / state / owner:
First slice (aim for 1–2 working days):
Dependencies / explicit blocker:
Affected workload/implementation/target/component/profile/contract:
Acceptance criteria: link the owning gate; identify this slice's subset
Validation: exact existing workflow commands; add new commands there when built
Evidence level and output location:
Result: include failures, unavailable metrics, and next decision
```

The [change-ready checklist](../design-checklist.md#change-ready-checklist)
applies to implementation. Narrow verification precedes `make verify-smoke`;
the scheduled integrated suite retains its role. Live FPGA evidence keeps its
registry integrity gate. Physical FPGA tasks use the lab skill and procedure,
preserve the immutable management shell, and program SRAM only. The three
kernel-evolve tiers remain independently selectable with their existing 32 KiB
Primer fit gates. Native and ASIC work uses its own target requirements and
cannot inherit physical FPGA evidence.
