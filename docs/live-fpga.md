# Live FPGA: adaptive reconfiguration research

“Live FPGA” is the atomiX name for a closed-loop system that observes its own
execution, evaluates bounded alternatives, activates a verified candidate, and
rolls back when correctness or safety fails.  The initial implementation is
L0 observation.  It does not yet claim autonomous improvement.

## Kernel architecture: immutable manager, selectable evolution service

The kernel does not rewrite itself. A small management kernel remains the
trusted owner of reset, isolation, telemetry, watchdog/recovery, and eventual
configuration actuation. Its `evolution` service is a replaceable component
behind the API in `sw/kernel/include/evolution.h`. That separation prevents a
larger search policy from becoming a permanent kernel dependency and lets an
external manifest replace the supplied policy without adopting one vendor's
runtime or bitstream format.

Four profiles are supplied:

| Profile | Component | Records | State cap | Resident cap | Primer gate |
|---|---|---:|---:|---:|---|
| predetermined | `evolution.none` | 0 | 0 B | 12 KiB | 32 KiB link + boot |
| `kernel-evolve-small` | `evolution.small` | 4 | 96 B | 16 KiB | 32 KiB link + boot |
| `kernel-evolve-mid` | `evolution.mid` | 16 | 336 B | 20 KiB | 32 KiB link + boot |
| `kernel-evolve-large` | `evolution.large` | 64 | 1,296 B | 24 KiB | 32 KiB link + boot |

The class names describe bounded policy capacity, not permission to weaken the
safety boundary. Each evolving tier accepts versioned candidate records,
refuses to propose a record whose correctness flag is clear, and ranks correct
records by caller-supplied fitness with candidate ID as the deterministic
tie-breaker. A table accepts only one objective ID and rejects a mixed-objective
record rather than comparing unrelated score scales.
It returns a proposal only. It cannot activate a role, write configuration
memory, mutate native FPGA frames, or promote a candidate.

The state limit is compiled into each component and guarded by a static
assertion. The linker separately enforces the tier's total resident cap, so a
`small` policy cannot consume the space reserved for `mid` or `large`. Each
public profile also selects the compact monitor kernel and an exact 32 KiB RAM
map. The linker reserves the final 4 KiB for the bootstrap stack and fails if
code, data, page tables, or evolution state cross it.
`make evolution-check` tests the policy semantics, links all four profiles,
boots each on the ISS with exactly 32 KiB, and reports the remaining headroom.
The current resident sizes are 8,220 (`none`), 12,412 (`small`), 12,652 (`mid`),
and 13,612 bytes (`large`). `none` retains four free 4 KiB allocator pages; the
three callable evolution services retain three.

The host-link test personality is a separate, host-managed build rather than a
public evolution tier. It has an explicit 16 KiB resident ceiling so its frame
transport and GPU dispatcher fit, while the predetermined interactive profile
keeps its 12 KiB ceiling and `kernel-evolve-small` keeps its 16 KiB ceiling.
This exception is selected by the host-link build target; it does not enlarge
any evolution component or grant configuration authority.

This is still not a claim that the kernel can evolve an FPGA. Configuration
actuation and rollback remain later, separately gated capabilities.

## Deterministic fitness record, version 1.0

The selected `fitness` component is separate from the `evolution` component.
`fitness.cycles-per-work` consumes a trial; `evolution.small`, `.mid`, or
`.large` stores and ranks the resulting compact record. A future energy,
latency, or multi-objective policy can therefore replace the fitness component
without changing the evolution service or management boundary. Predetermined
profiles select `fitness.none` and link no fitness code.

A trial pins the numeric and namespaced candidate identity, workload revision
and case, expected work count, exact oracle result, optional integer energy in
picojoules, and two L0 snapshots. It is eligible only when all of these hold:

- the snapshots are adjacent modulo 2^32;
- the oracle passed at least one case and recorded an output SHA-256;
- completed work equals the workload's declared work count;
- descriptor-rejection and watchdog deltas are zero;
- configuration generation is unchanged during the workload;
- elapsed cycles are non-zero and memory stalls do not exceed elapsed cycles.

All 64-bit counter deltas use unsigned modular subtraction, including a wrap
between snapshots. The initial objective is cycles per work item encoded as
Q22.10, lower is better:

```text
fitness = ceil(delta_cycles * 1024 / delta_work)
```

The computation uses integer arithmetic only. A result outside uint32 is
ineligible rather than silently truncated. Incorrect and unsafe trials carry
fitness `0xffffffff` with the correctness flag clear, so better performance can
never erase a failed oracle or safety event. Different objective IDs are never
compared as though their numeric scores had the same meaning.

The complete JSON evidence record is under `research/live-fpga/`; it preserves
raw snapshots, exact rational metrics, rejection reasons, and the compact
kernel evolution record. `tools/live_fitness.py` recomputes every derived field
instead of trusting entered results. The freestanding C implementation uses the
same rules and a bounded 64-by-32 divider, avoiding floating point and an
implicit runtime-library dependency on RV32.

```bash
make fitness-check
```

This validates the JSON contract, hard-gate negative cases, counter wrap,
host C implementation, callable code in every evolving RISC-V profile, and the
exact 32 KiB Primer link/boot gate.

## Content-addressed candidate registry

The R3 registry under `research/live-fpga/registry/` separates three identities
that must not be conflated:

- the logical program ID used by people and workload contracts;
- an immutable `sha256:` candidate ID computed from canonical JSON containing
  the numeric ID, role/workload, artifact hash, exact source/profile hashes,
  tool versions, parent, and mutation; and
- separately hashed evidence and deployment documents referenced by that
  candidate.

This means another test run or deployment attempt adds a record without
renaming the construction it tested. Changing the program, profile, source,
toolchain declaration, parent, or mutation necessarily produces a different
candidate ID. Repository file references are rehashed during validation, and
lineage must resolve inside the registry without self-parenting or cycles.

The first registry contains the reviewed SAXPY and polynomial GPU programs. A
hashed RTL evidence document records the exact program/output hashes and cycle
counts from `check-primer-runtime`. Separate physical evidence and deployment
documents record 30 exact-output executions per candidate on Tang Primer 25K,
the volatile image identities, benchmark sample, and reset/recovery coverage.
Simulation and physical claims remain distinct content-addressed records.

```bash
make registry-check
```

Negative tests change candidate content without changing its ID, introduce an
unknown parent, and alter a hashed evidence result. Each must be rejected.

## L1 reviewed-program selection

The first selecting policy runs on the host and accepts only programs listed in
the versioned L1 document under `research/live-fpga/policy/`. The initial list
contains the exact SAXPY and polynomial word streams already exercised by
`axhost --fast-switch`; the policy recomputes each little-endian program hash
and checks that those words still match the runtime client. Each allow-list
entry must also resolve to the registry identity with matching numeric ID,
role, workload, format, and artifact hash.

Selection is deliberately ordered:

1. require an approved allow-list entry for the requested role;
2. require exact workload ID and revision compatibility;
3. discard ineligible fitness records;
4. require one objective ID for the whole decision;
5. rank compatible candidates by fitness, then stable program ID;
6. apply the declared minimum-improvement threshold before switching between
   two compatible programs.

The output is `hold`, `propose`, or `no-candidate` plus namespaced reason codes,
the current and proposed content-addressed candidate IDs, the current and best
fitness when comparable, and
`actuation: org.atomix.not-authorized`. The tool never imports or calls the
serial transport, `axhost` activation operations, FPGA programming tools, or
kernel upload path. A proposal therefore records intent without gaining the
authority to deploy it.

The checked example requests SAXPY while polynomial is resident. Polynomial's
synthetic example score is numerically lower, but it is workload-incompatible,
so it cannot win; the policy proposes the reviewed SAXPY program and records
both `current-incompatible` and `reviewed-workload-match`. Negative tests cover
an ineligible oracle result, mixed objectives, a tampered program word, and a
faster wrong-workload program.

```bash
make policy-check
```

## Closed-loop virtual FPGA

`sim/livefpga` links the same freestanding `fitness.c` and `evolution.c` used by
the `kernel-evolve-small` image into a deterministic virtual shell. The model
produces adjacent L0 snapshots for a baseline, a correct improvement, a faster
incorrect candidate, and a watchdog-failing candidate. It then fault-injects
the selected candidate's activation canary.

The test requires the real kernel components to reject the unsafe candidates,
select the correct improvement, leave the active configuration unchanged when
merely proposing it, invalidate it after the failed canary, and propose the
known correct baseline. A separate simulated immutable manager performs both
activation and rollback, preserving the same authority boundary intended for
hardware.

The scenario runs first as a verbose native executable and then as a
freestanding RV32 image in aXsim with the Primer's exact 32 KiB RAM size. The
second leg executes target-compiled component code and catches RV32 ABI,
arithmetic, and instruction-set differences.

```bash
make live-sim-check
```

This behavioural layer runs quickly and deterministically, so it is suitable
for every change to fitness or evolution logic. It complements rather than
replaces aXsim boot tests and Verilator RTL tests: it does not model Gowin frame
timing, clocks, metastability, routing, power, or electrical failure. Those
remain RTL, timing-analysis, and physical-Primer gates.

## Immutable boundary

Telemetry, role isolation, reset, UART recovery, the watchdog, and the
configuration-generation counter belong to the management shell.  They must
not be synthesized into the role that will be replaced.  Otherwise a candidate
could erase its history, hide a failure, or damage the recovery mechanism.

`axlivemon` is therefore a shell component with generic event inputs.  It has
no knowledge of Gowin, ECP5, AMD DFX, an accelerator ISA, or the eventual morph
fabric.  Hard roles, overlay programs, and native partial loaders can feed the
same events.

## L0 telemetry schema, version 1.0

The reference shell maps the monitor into the existing shell-control page at
`0x1002_0100`.  It remains reachable while the role window at `0x4000_0000` is
isolated.

| Offset | Name | Access | Meaning |
|---:|---|---|---|
| `0x00` | `LIVE_ID` | RO | `0x61584c56` (`aXLV`) |
| `0x04` | `LIVE_VERSION` | RO | `0x00010000` (major 1, minor 0) |
| `0x08` | `LIVE_COMMAND` | WO | `1` snapshot, `2` record verified activation |
| `0x0c` | `LIVE_SEQUENCE` | RO | snapshot sequence, modulo 2^32 |
| `0x10` | `CYCLES` | RO, 64-bit | shell clocks since reset |
| `0x18` | `WORK_COMPLETED` | RO, 64-bit | rising completion events |
| `0x20` | `MEMORY_STALLS` | RO, 64-bit | cycles with a waiting role transaction |
| `0x28` | `DESCRIPTOR_REJECTIONS` | RO, 64-bit | explicit rejected-candidate/job events |
| `0x30` | `WATCHDOG_EVENTS` | RO, 64-bit | watchdog firings |
| `0x38` | `CONFIGURATION_GENERATION` | RO, 64-bit | verified activation commands |

Each 64-bit value occupies low then high 32-bit words.  Reads return the last
snapshot, not live counters, so a 32-bit CPU cannot observe a torn value.  A
snapshot includes its clock edge and every event asserted on that edge.  Live
counters continue running afterward.  Counters wrap modulo 2^64; consumers use
modular deltas between snapshots.

Version 1 is a minimum observation set, not a closed event registry.  A future
sensor block publishes its own namespaced schema/version at another aligned
offset; it does not repurpose these counters or require every role to adopt one
vendor's telemetry model.

`ACTIVATE` is intentionally explicit.  Isolation changes, reset release, UART
bytes, or a bitstream transfer do not imply a successful new generation.  The
management path records activation only after integrity checks, role discovery,
and the canary workload have passed.  The current project has no automatic
writer yet, so a generation increment is not evidence of partial
reconfiguration by itself.

## Current event wiring

- `CYCLES` is counted unconditionally outside reset.
- `WORK_COMPLETED` is derived from a rising role completion interrupt while
  the role is not isolated.
- `MEMORY_STALLS` counts any clock with a valid role-window transaction waiting
  for `ready`; simultaneous instruction/data stalls count as one stalled cycle.
- descriptor rejection and watchdog are explicit shell inputs.  The reference
  SoC ties them low until those producers land; the unit tests drive both.
- configuration generation comes only from `LIVE_CMD_ACTIVATE`.

This distinction prevents a generic bus error from being mislabeled as a
rejected adaptive candidate.

## What this enables next

The next Live FPGA item is L2 shadow evaluation. It will resolve a candidate
through this registry, run its oracle and safety gates in simulation, and emit
a signed-off volatile test request. Producing that request will remain separate
from deployment authority.

## Evidence

```bash
make live-check
```

The first test proves exact event and snapshot semantics, including simultaneous
events, snapshot stability, and reset priority.  The second proves the monitor
is reachable through the immutable shell page alongside a role that can be
isolated.
