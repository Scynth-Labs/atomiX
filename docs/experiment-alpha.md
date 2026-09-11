# Running your first experiment

This is the M0 walkthrough: bring a workload, run it on more than one thing,
and get a result someone else can reproduce.  Two paths are given because they
have different prerequisites and prove different claims.

**Path A** needs nothing but a C compiler and Python.  No RISC-V toolchain, no
Verilator, no FPGA tools, no board.  It exists because a platform that claims
to span software and hardware has to be usable from the software side alone.

**Path B** adds the RTL legs: the same workload as a SIMT kernel on real RTL
under Verilator, and one unchanged RISC-V image on three different cores.

Neither path needs hardware.  The [engineering
checklist](design-checklist.md#platform-product-gates) says which claims each
kind of evidence can and cannot support.

> This document is preparation for AX-04, not the gate itself.  AX-04 closes
> only when two people other than its implementer have run one of these paths
> and recorded what happened, including what went wrong.  Reading a walkthrough
> written by the person who wrote the code proves nothing about a newcomer.

Start from a fresh checkout and record the exact revision before installing or
running anything:

```bash
git clone https://github.com/scynth-labs/atomiX.git
cd atomiX
git rev-parse HEAD
```

If someone supplied the checkout, record that as help and run `git status
--short`; a participant result must say whether it began from an unmodified
tree. Use the [pilot report template](experiment-alpha-pilot-template.md) so
the two independent results capture the same acceptance evidence. The reports
must collectively cover both paths; their repository location and naming are
documented in
[`research/experiments/pilots/`](../research/experiments/pilots/README.md).

## Path A — native only

### What you need

| Tool | Version | Check |
|---|---|---|
| Python | 3.10 or newer | `python3 --version` |
| A C compiler | any C11 host compiler | `cc --version` |
| GNU Make | any recent | `make --version` |

Set `CC` if you want a specific compiler; the adapter records whichever one it
used, so the choice becomes part of the result rather than an assumption.

### Run it

```bash
make experiment-run ONLY=saxpy-native
```

That builds `sw/native/saxpy_i32.c` with your compiler and runs it over four
cases of `org.atomix.workload.saxpy-i32` — a signed case, an int32 wrap case at
`INT32_MIN`/`INT32_MAX`, a single element, and a seven-element tail.  Each case
is judged against an oracle recomputed independently in Python, so the program
cannot mark its own homework.

You should see one record written and a line like:

```
  saxpy-native    pass      host 154 ns
```

The number is *your machine's* elapsed time for the whole case set, median of
five repetitions.  It is not comparable to anything in the RTL tables below,
and the report will say so in its own output.

### Read it

```bash
make experiment-report EXPERIMENT_READ=build/experiments/records
```

The four RTL candidates appear as `no record` because you have not run them.
That is the intended behaviour: a candidate that was not tried is named, never
omitted.

### Change a declared choice

Everything the run depends on is declared, so changing one is a plan edit
rather than a code edit.  Try the repetition count:

```bash
make experiment-run ONLY=saxpy-native REPETITIONS=25
```

The record's `execution.repetitions` and its host-elapsed method string both
change, and the second run is *not* reused from the first, because repetitions
are part of the reuse key. Now repeat that same declared choice:

```bash
make experiment-run ONLY=saxpy-native REPETITIONS=25
```

That one is reused — every input is identical to the immediately preceding
result on disk, so the runner says `reused` and executes nothing. Returning to
the default of five repetitions is another declared change and therefore runs
again. Editing `sw/native/saxpy_i32.c`, or changing `-O2` to `-O1` in
[the plan](../research/experiments/saxpy-native-vs-rtl.json), makes it stale
again.

Finally, replay a result recorded by someone else:

```bash
make experiment-replay RECORD=research/experiments/records/saxpy-native.json
```

The deterministic identities and oracle output must match. Host elapsed time
is printed but not asserted because this is a different execution on a
different machine or at a different moment.

### What this evidence does not support

The host elapsed time says what *this* machine did with *this* compiler on this
particular afternoon.  It says nothing about the atomiX cores, nothing about
any FPGA, and nothing about what a different host would do.  It cannot be
divided by a model-cycle count to produce a frequency, and no command in this
repository will do that for you.

## Path B — native and RTL

### What you need, in addition

| Tool | Version | Needed for | Check |
|---|---|---|---|
| Verilator | 5.x | every RTL leg | `verilator --version` |
| RISC-V GCC | any `riscv*-elf-gcc` | the same-binary plan only | `riscv64-unknown-elf-gcc --version` |

`make doctor` reports what is installed and what the supported versions are.

### The same workload, implemented twice

```bash
make experiment-run
```

This runs the whole `saxpy-native-vs-rtl` plan: the host executable from Path A
plus a nine-instruction SIMT kernel on `role.gpu-compute`, swept over 1, 2, 4,
and 8 lanes.  The first run builds four Verilator models, which is most of the
elapsed time; later runs reuse them.

```bash
make experiment-report
```

Read the two tables.  The kernel's model cycles fall 505 → 332 → 240 → 212 as
the lanes double, which is diminishing returns from a single-ported global
memory rather than a disappointment.  The native candidate appears under that
table as *does not apply to this target*, because a host process has no model
cycle count — not because nobody measured one.

Then ask a question with a bound:

```bash
make experiment-report CONSTRAINT='org.atomix.metric.execute-cycles<=300'
make experiment-report CONSTRAINT='org.atomix.metric.lut-used<=5000'
```

The first returns the two lane counts that qualify.  The second returns nobody:
these are simulation records, and simulation cannot produce a LUT count.  An
unmeasured metric never satisfies a bound.

### One binary, three cores

```bash
make experiment-run EXPERIMENT_PLAN=research/experiments/same-binary-cores.json
make experiment-report EXPERIMENT_PLAN=research/experiments/same-binary-cores.json
```

The same `cpu_perf` image runs on `core.minimal`, `core.pipeline5`, and
`core.ax2`: 70,650 / 42,978 / 25,729 cycles.  Check the identity table — one
payload hash, three machine hashes.  That is what makes the spread attributable
to the core rather than merely observed alongside it.

### Reproduce a result, and hand one to someone else

```bash
make experiment-replay RECORD=research/experiments/records/saxpy-simt-rtl-lanes-8.json
make experiment-export CANDIDATE=saxpy-simt-rtl-lanes-4
make experiment-reproduce
```

The replay re-runs one recorded candidate and compares the artifact, build,
model, and profile hashes *before* it compares any number.  The export writes a
bundle carrying the plan, the workload, the record, and every declared input
with its hash; reproducing it checks those hashes, rebuilds, and requires the
oracle outputs and every deterministic value to match.  Elapsed times are shown
side by side and never asserted.

## What it took here

Measured on one Linux laptop, so treat these as a shape rather than a promise:

| Step | Elapsed | What dominates it |
|---|---|---|
| Path A, first run | 1.3 s | compiling one C file |
| Path A, report | 0.8 s | reading and validating records |
| Path B, whole saxpy plan | 7 s | Verilating four role models |
| Path B, same-binary plan | 3 s | three SoC models, already built |

Two caveats, because those last two numbers are only honest with them. This
machine had a warm compiler cache and previously built Verilator models: the
*first* Verilation of the role model here took about 25 seconds on its own, and
the SoC models are cached by `sim/soc` outside the experiment's build tree. On a
machine that has never built any of it, budget a few minutes for the first RTL
run and expect seconds thereafter. Installing Verilator and a RISC-V toolchain,
if you do not have them, will take considerably longer than any of this — which
is exactly why Path A exists.

## If you are reproducing this for AX-04

Record these separately, because they answer different questions:

1. **Prerequisites and setup** — everything before the first `make`, including
   installing tools and reading this page.
2. **Build** — the first run's elapsed time, which is mostly compilation.
3. **Interaction** — active time after that first run spent reading the report,
   changing a choice, and replaying a record. Do not fold compilation into this
   number.

Also record **time to first understood comparison** as build time plus the
interaction time up to that point. The target is fifteen minutes after
prerequisites. This total preserves the user-facing target while the separate
numbers show whether compilation or interaction caused friction.

Also record, in your own words:

- every failure, including ones you worked around, and any help you needed;
- one tradeoff the comparison shows;
- one thing the evidence does *not* support, which the report should have made
  obvious.

If the target is missed, the fix is to remove the friction and repeat, or to
state a revised target and why, before repeating.  A walkthrough that only its
author can follow has not been demonstrated.

## Where to go next

- [`research/experiments/README.md`](../research/experiments/README.md) — the
  plan format, metric applicability, and what makes a result reusable.
- [`docs/workflow.md`](workflow.md#34c-experiments-one-workload-several-implementations-and-targets)
  — the full command inventory.
- [`docs/execution-targets.md`](execution-targets.md) — which target can earn
  which claim, and which ones are not implemented yet.
