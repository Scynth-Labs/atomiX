# Integrated verification and nightly suites

atomiX uses one versioned manifest,
[`tests/verification-suites.json`](../tests/verification-suites.json), to compose
local verification, per-change CI, and scheduled suites. Subsystem Makefiles
remain the source of build logic; the manifest gives those checks stable stage
identities, timeouts, requirements, ordering, and suite membership.

## Verification ladder

| Layer | Primary stages | Finds |
|---|---|---|
| Contracts | profile resolution, research contracts | incompatible or stale composition data |
| Behaviour | Live FPGA native loop | policy, fitness, oracle, authority, and rollback errors |
| Target software | aXsim and RV32 Live FPGA loop | compiler, ABI, trap, arithmetic, and kernel-component errors |
| RTL equivalence | directed and official-ISA cosim | CPU/ISS divergence per retired instruction |
| RTL integration | unit, SoC, role, accelerator, and aXos stages | timing-independent hardware composition and protocol errors |
| Platform agreement | ISS, QEMU, and Verilator | platform assumptions leaking into software |
| Search | deterministic fuzz and paging campaigns | long-tail instruction and VM interactions |
| Formal | separate weekly workflow | bounded architectural counterexamples |
| Physical | explicit Primer procedure only | tool, timing, configuration, clock, power, and electrical failures |

No layer is allowed to claim the guarantees of the layer below it. In
particular, a green nightly run is not FPGA bitstream or physical-board
evidence.

## Commands

```bash
python3 tools/verify.py validate
python3 tools/verify.py list
make verify-smoke
make nightly-integrated
```

`smoke` is the practical local ladder: profiles, research contracts, golden
ISS, Live FPGA RTL isolation, candidate-registry integrity, and the native/RV32
closed loop. The scheduled
workflow additionally runs:

- `nightly-integrated`: ordinary CI plus the Live FPGA loop, every RTL unit,
  component composition, the full experiment parameter sweep, accelerator
  workloads, architecture variants, aXos runtime switching, storage writes,
  and SD boot;
- `randomized`: fixed-seed M-mode fuzzing and Sv32 generation;
- `isa`: official RV32UI/RV32MI/RV32UM on aXsim and lock-step RTL; and
- `three-platform`: matching bare-metal and aXos behaviour on ISS, QEMU, and
  Verilator.

The GitHub jobs are separate so heavyweight campaigns run in parallel. Stages
inside one job are sequential, preventing shared build-directory races and
making a later integration stage consume artifacts produced by earlier ones.
Ordinary CI runs `native-adapter-conformance` without an RTL-tool prerequisite
and `experiment-regression` with the three deterministic preview CPU profiles;
the latter records its elapsed stage cost in the verification summary.

## Results and failure handling

Every stage streams its output and also writes
`build/verification/<suite>/<stage>.log`. The runner continuously updates
`summary.json` (`org.atomix.verification-result.v2`), including timestamps,
duration, result, exit code, and log path.
Scheduled jobs use `--keep-going` to report independent failures together and
upload the entire suite directory even when all stages pass. CI stops at the
first failed stage for fast feedback and uploads logs on failure.

Each process runs in its own process group. A stage that exceeds its declared
timeout is terminated with its children and recorded as `timeout`; a missing
required tool is recorded as `blocked`; a stage the suite asked for but never
reached is recorded as `not-run`. All three fail the suite, and every outcome
is counted by name in `counts` and printed as an `outcomes:` line, so a pass is
never read off a list whose length changed.

### The record names the machine that ran

A result that does not say what produced it is a claim about nothing in
particular. Each summary carries an `environment` block — Verilator, Yosys,
RISC-V GCC, clang, QEMU, make, node and Python versions, the host, the git
revision, and whether the worktree was clean — and each stage may declare the
profiles it exercises:

```json
"kernel-storage-mutation": {
  "label": "...",
  "command": ["make", "-C", "sw/kernel", "check-sdboot"],
  "profiles": ["configs/sim-sdram.json", "configs/kernel-slow-memory.json"]
}
```

The runner resolves each one itself and records what it resolved to — name,
core, memory, cache, role, harness, simulation top, board, scheduler, every
setting, and the define list — so the record describes the machine rather than
repeating the profile's own name for it. A stage naming a profile that does not
resolve **fails before its command runs**: a result labelled with a machine
nothing could have built is worse than no result.

`python3 tools/verify.py self-test`, which `make verification-check` runs, is
the check on all of that. It runs a synthetic suite designed to go wrong and
requires that a missing tool blocks, an unresolvable configuration fails
without executing its command, a passing stage records the machine it ran on,
and stages never attempted survive into the summary as `not-run`.

## Adding coverage

Add or reuse a stage in `tests/verification-suites.json`, then place its ID in
the narrowest relevant suite. Keep commands as argument arrays—never shell
fragments—and keep deterministic seeds explicit in the underlying Makefile.
Use a new component or profile's existing check target rather than duplicating
its build recipe in the manifest.

Run these before submitting the change:

```bash
make verification-check   # validate the manifest, then self-test the runner
make verify-smoke
```

If a test needs a new dependency tier, give it a separate scheduled shard. Do
not weaken or skip an existing stage merely because the new tool is absent.
