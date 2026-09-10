# Research priority board

[All boards](README.md) · [Roadmap](../roadmap.md) ·
[Research acceptance gates](../research-checklist.md)

Research should answer a decision that changes what gets built. A recorded
refutation is useful; it does not satisfy the positive capability gate that the
experiment was attempting. All owners are unassigned.

ASIC portability and implementation feasibility (RX-08/RX-09) are ordered on
the [targets board](targets.md), with acceptance in the research checklist.
Their first dependency audit needs no FPGA board or technology library.

| Card / question | Priority | State | Depends on | First reviewable slice / decision |
|---|---|---|---|---|
| [RX-01: does search beat a small exhaustive baseline?](../research-checklist.md#rx-01) | P1 | Next | AX-02, AX-03 | Enumerate a declared space and freeze the evaluation budget and held-out cases before comparing a search policy |
| [RX-02: when does an accelerator pay for the full job?](../research-checklist.md#rx-02) | P1 | Next | AX-01 | Define matched small/medium/large inputs and separate transfer, execution, verification, and switching costs |
| [RX-03: does adaptation outperform a fixed choice?](../research-checklist.md#rx-03) | P1 | Next | RX-02; RX-04/RX-05 before any changed actuation | Freeze a workload sequence, static baselines, and transition/recovery budget before evaluating a reviewed policy |
| [RX-04: is zero an observation or an absent producer?](../research-checklist.md#r3--live-fpga-adaptive-logic) | P1 | Ready | Existing telemetry and declined Primer profiles | Add a presence/observation truth table and one unavailable-counter rejection case to the existing R3 gate |
| [RX-05: who owns watchdog recovery?](../research-checklist.md#r3--live-fpga-adaptive-logic) | P1 | Next | RX-04 | Compare observe-only and manager-requested isolation in fault-injected simulation; document the authority decision |
| [RX-06: can a role change preserve the ECP5 shell exactly?](../research-checklist.md#r1--partial-reconfiguration-of-an-fpga) | P2 | Next | FPGA toolchain and bounded compute budget | Run the existing 45F stable-shell gate before attempting an accepted delta; stop and record a refutation when the method fails |
| [RX-07: what energy measurement can the lab support?](../research-checklist.md#r2--fast-compute-personality-transformation) | P2 | Ready | Existing workload timing records | Specify boundary, required temporal resolution, calibration, and uncertainty; decide whether a fixture could answer RX-02 |

## Rules for an experiment worth running

- Write the hypothesis, baseline, workload split, evaluation budget, stopping
  rule, and next decision before execution. Search budgets are experiment
  inputs; they are not universal architecture limits.
- Keep correctness as an eligibility gate. Report excluded, failed, and timed-out
  candidates alongside the valid frontier. Unavailable measurements stay
  unavailable, including power and declined telemetry producers.
- Compare only compatible evidence: same workload, measurement boundary,
  identities, and declared environment. Keep simulated cycles, routed timing,
  and physical observations separate.
- Candidate generation is proposal-only. Reuse the existing oracle, registry,
  canary, isolation, watchdog, and rollback boundaries. A better score never
  grants authority to load a bitstream or promote a candidate autonomously.

For software and external-accelerator experiments, use the selected target's
actual execution and recovery contract. Workload semantics can be shared across
ISAs; device metrics and configuration authority cannot be inferred from that.

## Gates and escalation

RX-01–RX-03 add experiment plans to the existing comparison machinery; start
with `make comparison-check` and the relevant workload checks. RX-04/RX-05
use the Live FPGA simulation and L3 gates. RX-06 keeps `make pr-gate-check`
and the current rejected candidate as a negative control. Changing referenced
evidence requires `make registry-check`, and substantive changes finish with
`make verify-smoke`. Exact procedures remain in [workflow.md](../workflow.md).

Physical L3 trials move to HW-03 only after the R3 readiness gate is met.
Physical energy work moves to HW-04 only with a calibrated fixture. A live ECP5
trial moves to HW-05 only after offline confinement and board availability.
L4 remains blocked by the full R1 exit gate.

## Priority decisions

- 2026-09-10: put optimization quality and end-to-end accelerator value ahead
  of expanding the architecture catalog. Both can justify later engineering.
- 2026-09-10: keep missing telemetry as the independently pullable correctness
  task. Defer 85F address-map work behind the existing 45F confinement gates;
  defer broader partial-reconfiguration work behind demonstrated need.
