# formal/ — formal verification

Glue and configurations for **riscv-formal** driven by **SymbiYosys**. Formal
checks complement simulation: they explore all instruction/data combinations
within a bounded window and prove the RVFI architectural trace satisfies the
selected ISA properties.

## Prerequisites

Install the required tools as documented in
[`docs/toolchain.md`](../docs/toolchain.md#formal-verification). In short,
the host needs `yosys`, `sby`, and an unmodified `riscv-formal` checkout at
`/opt/riscv-formal`. Boolector and Z3 are optional for exploratory jobs.

```bash
command -v sby yosys
test -d /opt/riscv-formal
```

The default suite uses Yosys's built-in SAT engine after riscv-formal has
generated the ISA assertions. It completes on a commodity developer machine
without an additional SMT solver. Boolector and Z3 remain useful for expanded
or exploratory jobs. To preserve reproducibility, do not vendor or edit the
`/opt/riscv-formal` checkout from this repository.

## Contents

- `components/core/pipeline5/axcore_rvfi_wrapper.sv` connects aXcore's
  one-retire-per-cycle RVFI trace to a formal-only data bus and a stable,
  arbitrary instruction word. This initial bounded suite proves instruction
  semantics across all operand/data values without requiring an impractically
  expensive arbitrary-program history on a developer workstation.
- `checks.cfg` selects the RV32I configuration and bounded check depths. The
  product configuration enables RV32M; its fixed-latency unit is covered by
  directed, randomized, and official RV32M lock-step ISA tests.
- Generated SymbiYosys jobs and SAT artifacts are placed in `build/` and are
  ignored by Git.

## Run

```bash
make -C formal check          # reference core: default proof suite
make -C formal check-minimal  # multi-cycle accelerator-host core
make -C formal check-ax2      # dual-issue core: both retire channels
make -C formal check-all      # all three cores
make -C formal list           # list generated checks (list-minimal, list-ax2)
make -C formal clean          # remove generated proof artifacts
```

Use an individual generated check when iterating on a failure, for example:

```bash
make -C formal generate
python3 formal/run_checks.py insn_add_ch0
python3 formal/run_checks.py --core ax2 insn_add_ch1
```

The generated check and its solver log are below `formal/build/sat/<core>/`.

## Three cores, one environment

A core contributes a formal wrapper and a `checks.cfg`; the ISA properties, the
check generator, and the RTL are shared.  Proving another core is therefore a
matter of describing its trace, not of duplicating a verification environment:

| Core | Configuration | Wrapper | Channels |
|---|---|---|---|
| `axcore` (reference, 5-stage) | `checks.cfg` | `components/core/pipeline5/axcore_rvfi_wrapper.sv` | 1 |
| `minimal` (multi-cycle) | `checks-minimal.cfg` | `components/core/minimal/axcore_rvfi_wrapper.sv` | 1 |
| `ax2` (dual-issue) | `checks-ax2.cfg` | `components/core/ax2/ax2_rvfi_wrapper.sv` | 2 (`nret 2`) |

`minimal` shares the reference core's wrapper *file name* — the worktrees are
per-core directories, so the two never collide — and its `nret 1` trace has the
identical port list.  That is the point: three quite different machines (a
five-stage pipeline, a multi-cycle state machine, and a dual-issue superscalar)
are held to the same ISA properties, and each earns its own evidence rather
than inheriting the reference core's.

ax2 retires a bundle of two, so each instruction is proved on channel 0 and on
channel 1 — the second channel is the one dual issue can get wrong.  Its
`rvfi_order` stays gapless without a reorder network because slot 1 is never
valid without slot 0, and a slot-0 trap squashes slot 1 entirely.

One practical note.  The ax2 suite runs through the same built-in SAT engine as
the reference core and needs no extra solver, but it does need materially more
memory: its block-RAM instruction cache dominates model construction rather
than the bounded unrolling, so the footprint does not shrink with depth (it
peaks at the same size at depth 4, 8, and 13) and a 3 GB machine cannot finish
it.  A hosted runner can, which is why `formal.yml` is where this suite is
expected to run.

Recommended gate policy: run formal jobs for changes touching
`components/core/pipeline5/` and run the relevant simulation legs for every
behavioral change.  The project-wide command matrix is in
[docs/workflow.md](../docs/workflow.md).
