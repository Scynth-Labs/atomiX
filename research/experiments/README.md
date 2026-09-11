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

## The claims, kept apart

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

`riscv-software-models.json` runs one unchanged RV32IM/ILP32 `cpu_perf` ELF on
aXsim and QEMU virt. Both must reproduce checksum `0xe9266745`, but their
counters deliberately do not share a metric: aXsim defines `mcycle`/`minstret`
as retired instructions, while QEMU's guest `mcycle` follows emulator virtual
time. Both are kept apart from simulator host duration and RTL model cycles.

`saxpy-software-hardware-codesign.json` makes two controlled comparisons. The
first changes only GCC's optimization configuration (`-O0`/`-O2`) for one
native source. The second is a complete 2x2 experiment over an actual SIMT
algorithm input (multiply-immediate or an add chain for `a=3`) and the role's
manifest-owned lane parameter (1 or 4). Algorithm, layout, runtime policy,
compiler configuration, and effective machine profile are derived from inputs
that reach the adapters. The recorded RTL result is 505/240 cycles for
multiply and 556/258 for the add chain at 1/4 lanes: width helps both, while
the attempted strength reduction loses at both widths.

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
| `org.atomix.domain.iss-retired-instructions` | aXsim's deterministic retired-instruction counts |
| `org.atomix.domain.emulator-guest-counters` | counters observed by a guest on a named system emulator; semantics belong to that emulator |

`policy.cross_domain_ranking` must be `org.atomix.forbidden` at schema major 1.
Native elapsed time, simulator wall time, and model cycles are not one scale,
and no arithmetic in this repository converts between them.

## Sweeps, bounds, and what a run remembers

A candidate may declare a sweep over a target's build-time parameters as an
explicitly enumerated set:

```json
"sweep": {"target_parameters": {"lanes": [1, 2, 4, 8]}}
```

Ranges and step counts are deliberately absent. The expansion has to be
countable before the run starts, and `budget.max_candidates` is checked against
the expanded space rather than the written list. The component manifest owns
what values are legal -- `role.gpu-compute` declares `lanes` 1..256 and
`data_words` as a power of two in 256..4096 -- so an out-of-range point is
refused before its model is built, by the component's own rule.

Each run writes `run-state-<plan>.json` beside its records:

| Disposition | Meaning |
|---|---|
| `org.atomix.attempted` | this run produced the record |
| `org.atomix.reused` | the previous record's inputs were identical, so it still stands |
| `org.atomix.not-attempted` | bounded out, or never reached; its status stays `not-run` |

The distinction in the last row is the point. A candidate that was never tried
and a candidate that lost are different results, and a report that cannot tell
them apart is guessing.

Reuse compares artifact and build hashes, the target's model and profile
hashes, tool versions, the workload revision and its selected cases, and the
repetition count. Changing `-O2` to `-O1` is enough to make a result stale.
`--no-reuse` re-executes regardless; `--resume` keeps outcomes already recorded,
including blocked and failed ones, and `--retry` names the ones to attempt
again.

## Reading the result

```bash
make experiment-report
make experiment-report CONSTRAINT='org.atomix.metric.execute-cycles<=300'
```

The report ranks within a measurement domain and refuses to rank across them.
It prints, in its own output, that no ratio between a model-cycle row and a
host-time row means anything, and there is no combined score anywhere in this
repository. A candidate is listed under a domain it cannot enter with the
reason -- inapplicable to that class of target, or unmeasured -- rather than
quietly dropped, and a candidate that failed its oracle keeps its record and
loses its place in every table.

A constraint answers with three groups: candidates that qualify, candidates
outside the bound, and candidates about which there is no evidence. The third
group never counts as a pass. Asking for a LUT budget in a simulation-only
experiment therefore qualifies nobody, which is the correct answer.

```bash
make experiment-export CANDIDATE=saxpy-simt-rtl-lanes-4
make experiment-reproduce
```

A bundle carries the plan, the workload, the record, every declared input with
its hash, and the commit to retrieve. Reproducing it checks those hashes before
rebuilding anything, runs through the ordinary runner rather than a path only
bundles use, and compares identity, oracle outputs, and the deterministic
values. A changed input or a mismatched evidence level is refused.

## Publishing the records

```bash
make experiment-pages
python3 -m http.server -d build/pages 8000
```

The site is generated from these files and nothing else, so it can only ever be
as current as they are, and every page says so. Each result links to the record
JSON behind it, shows the artifact and machine hashes that produced it, names
the method behind each measurement, prints the commit each record was taken at,
and carries the commands to check it locally.

`make experiment-pages-check` regenerates the site and requires it to be
byte-identical to what is on disk — an edited page is a failure rather than a
nicer-looking truth — and separately requires that the rendering kept the rules:
excluded candidates still named, artifact hashes still shown, and a page with
two measurement domains still saying they do not compare.

## Adding a plan

Copy a shipped plan, give it a new namespaced ID, and pin the workload revision
you wrote it against — a plan naming a revision that does not exist is rejected
rather than quietly matched to a newer one.  Namespaced target classes,
adapters, capabilities, metrics, and domains from outside this tree are
accepted; the self-test proves an external backend validates without touching
the base validator.

Copy `record-template.json` only when a run is ready.  A template carries no
measured value and can never enter a comparison.
