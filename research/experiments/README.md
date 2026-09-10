# Experiment plans and run records

An **experiment plan** is the versioned input a user brings to atomiX.  It
names one workload and its oracle, the cases to run, the implementations that
claim to satisfy it, the execution targets that can host them, what each target
class is able to measure, and the budget a run may spend.  A **record** is what
one candidate's run produced, including the runs that failed, were blocked, or
timed out.

Validate everything with:

```bash
make experiment-check
```

## The two claims, kept apart

`same-binary-cores.json` runs one unchanged `cpu_perf` image on `sim-minimal`,
`sim-bram`, and `sim-ax2`.  Only the core selection differs, so the spread is
attributable to the machine.  The image requires only what all three profiles
actually provide: the capability list is the intersection of their component
manifests, which is why it asks for `rv32im` and `zicsr` but not `m-mode`,
`sv32`, `rvfi`, or precise traps — those belong to individual cores, not to the
binary's contract.

`saxpy-native-vs-rtl.json` runs two unrelated implementations of the same
integer semantics: a host C11 executable and a nine-instruction SIMT kernel on
the `role.gpu-compute` engine.  Their artifacts have nothing in common; their
logical inputs and oracle results must match exactly, including the wrap,
single-element, and SIMT-tail cases pinned by workload revision 2.

A report shows these separately.  One says what a core choice was worth; the
other says what an implementation choice was worth.  Neither is a score.

## Why this is not the R2 comparison plan

[`../comparisons/`](../comparisons/) keeps the R2 contract and its required
FPGA metric matrix, and `tools/comparison_contract.py` still owns it unchanged.
That matrix is wrong for a host process: an ordinary executable has no LUT
count, no configuration transfer, and no role transition.  Rather than let a
host candidate invent those fields to satisfy a schema, a plan here declares
metric **applicability** per target class:

- `org.atomix.required` — the record must carry the metric, measured or
  explicitly unavailable, but never silently omitted.
- `org.atomix.optional` — the record may carry it.
- `org.atomix.inapplicable` — the target has no such quantity.  A record that
  reports one anyway is rejected.

Measured zero stays a result.  `org.atomix.unavailable` means nobody measured
it.  `org.atomix.inapplicable` means there is nothing there to measure.

## Measurement domains

Every metric belongs to one domain, and a target may only be *required* to
produce metrics from its own domain (context metrics excepted):

| Domain | What it holds |
|---|---|
| `org.atomix.domain.context` | logical work, repetitions, artifact size |
| `org.atomix.domain.model-cycles` | deterministic cycles of an RTL model |
| `org.atomix.domain.host-elapsed` | wall time of the workload's own host execution |
| `org.atomix.domain.simulator-host-elapsed` | wall time spent *simulating*, which is a cost of the tool, not of the design |
| `org.atomix.domain.device-resources` | LUTs and friends, at an evidence level that can contain them |

`policy.cross_domain_ranking` must be `org.atomix.forbidden` at schema major 1.
Native elapsed time, simulator wall time, and model cycles are not one scale,
and no arithmetic in this repository converts between them.

## Adding a plan

Copy a shipped plan, give it a new namespaced ID, and pin the workload revision
you wrote it against — a plan naming a revision that does not exist is rejected
rather than quietly matched to a newer one.  Namespaced target classes,
adapters, capabilities, metrics, and domains from outside this tree are
accepted; the self-test proves an external backend validates without touching
the base validator.

Copy `record-template.json` only when a run is ready.  A template carries no
measured value and can never enter a comparison.
