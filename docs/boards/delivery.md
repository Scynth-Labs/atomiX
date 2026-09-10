# Delivery board

[All boards](README.md) · [Roadmap](../roadmap.md) ·
[Engineering acceptance gates](../design-checklist.md#platform-product-gates)

Order within a priority is top to bottom. All owners are unassigned. The four
P0 cards here plus AX-10 on the [targets board](targets.md) deliver M0.

| Card / outcome | Priority | State | Depends on | First reviewable slice |
|---|---|---|---|---|
| [AX-01: describe a workload-driven experiment](../design-checklist.md#ax-01) | P0 | Done | Existing workload and comparison contracts | Specify workload semantics, distinct implementation/target identities, and metric applicability for native/RTL and same-binary fixtures |
| [AX-02: execute and resume a bounded design-space sweep](../design-checklist.md#ax-02) | P0 | Done | AX-01, AX-10 | Execute a fixed plan through the native CPU and RTL adapters and retain every attempted outcome |
| [AX-03: compare and replay a design decision](../design-checklist.md#ax-03) | P0 | Done | AX-02 | Render a local comparison from the run records, including a rejected candidate and a replay reference |
| [AX-04: prove a new user can use the experiment alpha](../design-checklist.md#ax-04) | P0 | Ready | AX-03; independent participants for the pilot gate | Write separate native-only and native/RTL walkthroughs; test locally before arranging reproduction |
| [AX-05: ship an external-component SDK example](../design-checklist.md#ax-05) | P1 | Done | AX-01; completed independently while AX-04 awaits participants | Package one small replacement outside the source tree with its manifest and runnable conformance checks |
| [AX-06: prepare a reproducible preview release](../design-checklist.md#ax-06) | P1 | Next | AX-04, AX-05, AX-07; HW-01 only for advertised hardware images | Define the supported subset and inventory the artifacts, compatibility promises, and reproduction commands |
| [AX-07: detect experiment regressions in CI](../design-checklist.md#ax-07) | P1 | Next | AX-02, AX-03 | Add one deterministic CPU comparison to the existing verification manifest with a deliberately failing control |
| [AX-08: open and share an experiment in the browser](../design-checklist.md#ax-08) | P1 | Next | AX-03; WASM build environment and browser for validation | Import one native experiment record into the existing web machine and confirm identity and result parity |
| [AX-09: asynchronous host jobs](../design-checklist.md#platform-expansion) | P2 | Next | RX-02 identifies a useful workload or pilot demonstrates blocking cost | Specify submit/poll/fetch ownership and recovery using the existing open host-link gate before adding operations |

## What is done, and what is next

AX-01 and AX-10 are closed. `research/experiments/` holds two plans -- one
image across three cores, and one workload across a host CPU and the RTL role
-- and the records of their runs. `tools/execution/` is the adapter boundary
they run through, and `make adapter-check` proves its refusals.

AX-02 is closed too. A plan may sweep a target's build-time parameters over an
explicitly enumerated set; the expansion is bounded before it runs, an
out-of-range point is refused before its model is built, and every run writes
a state file saying what was attempted, what was reused, and what was never
tried. `research/experiments/records/` now holds a five-candidate run of the
saxpy plan.

AX-03 is closed. `make experiment-report` renders one table per measurement
domain and says which pairings are not comparable; `make experiment-export`
writes a self-contained bundle and `make experiment-reproduce` rebuilds it from
that description alone, refusing a changed input or a mismatched evidence
level.

AX-04 is what remains of M0, and it is the one card its implementer cannot
close. Its walkthroughs are written and tested:
[experiment-alpha.md](../experiment-alpha.md) has both paths with measured
local timings. The walkthroughs can be written and tested locally -- a native-only path
needing no RISC-V or RTL toolchain, and the paired native/RTL path with its
prerequisites -- but the gate needs two people other than the implementer to
run them, change a declared choice, and say what the evidence does not support.
Until those two reproductions are recorded, this stays open no matter how well
the commands work here.

AX-05 is closed. The delayed-finisher SDK package carries its source, manifest,
documented bounded knob, simulation-only compatibility claim, profiles,
license, migration rules, and conformance runner together. `make
external-component-check` proves the package after copying it outside the
checkout, including default/non-default latency and two precise refusal cases.
The wider replacement inventory remains candid about gaps rather than granting
the SDK example's evidence to other components.

## The first slice, as it was taken

AX-01 starts from [the existing benchmark driver](../../tools/bench.py),
[comparison rules](../comparison-contract.md), and
[runtime payload reuse](../workflow.md#open-an-interactive-session-on-a-profile).
The benchmark driver already knows how to sweep several implementations; the
new work makes a user's experiment a versioned input instead of another
hard-coded benchmark family.

Keep machine and payload identities separate. For the first fixture, use the
same `cpu_perf` bytes on `sim-minimal`, `sim-bram`, and `sim-ax2`; declare the
intersection of their capabilities rather than inheriting the reference core's
privilege or formal claims. Reject a deliberately incompatible requirement.
Inspect the current comparison schema before extending it; preserve existing
R2 records and validators.

Add a second fixture binding a native implementation and an RTL implementation
to the existing `saxpy-i32` semantics. Their artifacts can differ; the logical
inputs and oracle must agree. AX-10 supplies those execution adapters. The
native leg must not import a board profile or require an RTL toolchain. Its host
timing cannot be ranked against the simulator's wall time or modeled cycles.

AX-02 begins with finite enumeration. It needs bounds, cancellation, restart,
and trustworthy records before it needs a clever search strategy. New limits
belong to the experiment's declared inputs or owning component/profile; use
the existing resolver for hardware and kernel settings.

## Reuse and validation

| Area | Existing starting points | Existing gates to retain |
|---|---|---|
| Experiment input and compatibility | `tools/configure.py`, `research/comparisons/`, `research/personalities/` | `make config-check-all`, `make comparison-check`, `make personality-check` |
| Runner and record handling | `tools/bench.py`, `tools/verify.py`, `tools/bug_report.py` | `make verification-check`, `make -C sim/soc check-runtime-payload`, `make bug-report-check` |
| Comparison and browser | `tools/evidence_views.py`, `sim/web/` | `make evidence-views`, `make web-compare-check`, `make web-page-check` |
| External components and preview | `components/`, `configs/`, `tools/requirements.json` | Component-specific checks, `make requirements-check`, `make verify-smoke` |

These gates cover the existing foundations. They do not prove the new feature
until its acceptance cases are implemented and exercised. Consult
[workflow.md](../workflow.md) for command context; record new targets there
when implemented. A missing browser that causes a skip cannot close AX-08.

## Priority decisions

- 2026-09-10: chose the reproducible experiment loop as the first deliverable.
  Existing core comparisons make it attainable without new RTL or lab equipment.
- 2026-09-10: moved asynchronous host jobs behind measured demand. Browser
  sharing remains useful, but native replay is the first portable handoff.
- 2026-09-10: added native software execution to M0 through AX-10. The product
  boundary now spans software and hardware implementations; FPGA is one target.
- 2026-09-10: closed AX-01 and AX-10 together. The contract needed a real
  adapter to be worth trusting, and the adapters needed the contract to have
  somewhere honest to put a host process's missing LUT count.
- 2026-09-10: closed AX-02 with finite enumeration only. A search strategy was
  deliberately not added: bounds, resume, and trustworthy records are what a
  sweep needs before it needs to be clever about where it looks next.
- 2026-09-10: closed AX-03 without a summary score, deliberately. The report
  ranks within a measurement domain and states in its own output that no ratio
  across domains means anything; a single number would have been easier to read
  and would have been the most misleading thing here.
- 2026-09-10: wrote AX-04's walkthroughs and left the card open. Testing them
  here is preparation; the gate is two other people, and no amount of local
  polish substitutes for that.
