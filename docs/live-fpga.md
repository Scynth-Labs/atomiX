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
- `DESCRIPTOR_REJECTIONS` counts rising edges of the role ABI's `reject_event`
  line while the role is not isolated.
- `WATCHDOG_EVENTS` counts stall episodes that outlast `WATCHDOG_CYCLES`
  (4,096 by default), derived by the fence from the role window itself.
- configuration generation comes only from `LIVE_CMD_ACTIVATE`.

This distinction prevents a generic bus error from being mislabeled as a
rejected adaptive candidate.

### The two producers, and why they differ in kind

Both counters were shell inputs with nothing driving them until 2026-08-13:
`soc_top.sv` tied `role_reject_event` and `watchdog_event` to `1'b0`, so
neither could advance for any possible input.  Every fitness trial that
required a zero rejection and watchdog delta was therefore reading a constant,
not an observation.  They now have producers, deliberately on opposite sides of
the role boundary.

**Rejection is the role's event.**  Only the role knows which descriptor it
refused, so it reports one, and the role ABI carries a `reject_event` output
beside `irq`: a one-cycle pulse per refused descriptor or job.  `role.morph`
drives it from the same condition that increments its own `REJECTS` register.
The roles with no descriptor to refuse — `none`, `loopback`, `gpu1`,
`gpu-compute`, `tpu-lite` — tie it low at their own boundary, each stating why
in its wrapper.  A zero from those roles is a fact about the role, not a shell
that cannot count.

The fence qualifies what arrives.  The line is edge-triggered, so fabric that
comes up with it stuck high costs one event rather than one per cycle; and it
is masked while isolated, exactly as the completion line is, because a fenced
role's outputs describe fabric that is mid-rewrite.

**The watchdog is the shell's event**, and has to be: a role that has stopped
answering cannot report that it has stopped answering.  The fence already
watches every role-window transaction, so it counts a stall episode that
outlasts `WATCHDOG_CYCLES` as one watchdog event — once per episode, however
long the hang lasts, since the per-cycle view is already `MEMORY_STALLS`.  It
observes and does not act: making the watchdog *isolate* would change what the
fence guarantees and when a role can be torn out from under a driver, which is
a safety decision to take deliberately rather than as a side effect of fixing
telemetry.  `watchdog_event` remains an input for a future shell-level
producer.

What is deliberately *not* counted as a rejection: traffic the fence absorbs
while isolated.  Rediscovering the role by reading the fenced window is the
documented post-swap path, so charging those reads to `DESCRIPTOR_REJECTIONS`
would both mislabel a bus event as a refused candidate and make every trial
spanning a swap ineligible for fitness.

### What the producers cost, and who declines them

They are not free, and on the Tang Primer they are not affordable.  While both
counters were constant zero the synthesiser deleted them *and* their arms of
the 64-bit read mux.  Enabling them takes `role.morph` at one PE from 18,660
LUT4 (81%) to 20,911 (90.8%) on the GW5A-25A, and it then finds no legal
placement at any seed tried — where before it placed, after 20 minutes of
placer effort, on a profile that was already marginal.

`soc.live_role_events` therefore exists, and it is **compile-time, not a
tie-off**: `configure.py` omits the define entirely for `live_role_events: 0`
(its `omit_when_zero` flag), so a declining profile compiles the role ABI port,
the edge detector, and the watchdog counter out.  Every Tang Primer profile
declines them; simulation profiles and any future, larger board take them by
default.  This is narrower than `live_monitor: 0`, which removes the register
window as well and makes `LIVE_ID` itself a bus error — that would break
`roleiso`, the recorded R1 board payload, which reads that register to prove
the monitor stays reachable while the role is fenced.

#### The declined path is the original text, deliberately

Compiling the producers out is not sufficient on its own, and the reason is
worth recording because it contradicts the obvious assumption.  The first two
attempts left the fence passing a locally declared `wire live_reject_event =
1'b0` to the monitor instead of the tied-off `role_reject_event` port the
shell had always passed.  That is the same constant by any reading of the
logic, and the preprocessed sources differ only in that substitution — yet it
synthesised **1,989 more LUT4** (20,649 against 18,660), identical `ALU` and
`DFF` counts, and cost `role.morph` its placement at five seeds.  Gating by
value rather than by `ifdef` behaved the same way (20,280 LUT4).

So the `` `else `` arm of the monitor connection is the pre-2026-08-13 text
verbatim, and the port stays declared unconditionally.  With that, a declining
profile's netlist is identical to the one before the producers existed — cell
for cell, every LUT and MUX class — which is the only definition of "unchanged"
worth having on a part this full.  A bisect established this: the morph role's
guarded port alone reproduces the old netlist exactly, so the perturbation was
entirely in how the fence expressed a constant to `axlivemon`.

The consequence is stated plainly rather than buried: **on the Primer these two
counters still read zero, now by declaration instead of by accident.**  A board
result for the wired counters needs hardware with room for them.

## L3 bounded morph-genome search

The first L3 experiment is complete.  It does not expose all 416 configuration
bits to mutation.  Each workload keeps its reviewed mode, dimensions, address
strides, immediates, accumulator seed, and range-checked buffer layout fixed;
only words 10 and 11 change.  They contain one homogeneous 14-bit PE
descriptor: four 3-bit source muxes plus the load/hold accumulator rule.  The
result is a finite **8,192-candidate** operation-and-local-route space.  The
shell, role window, sequencer bounds, and all addresses are outside it.

`tools/morph_search.py` compares three deterministic strategies against the
exact scalar recurrence, 50-element SIMT SAXPY, and 12x8x8 systolic GEMM used
by the RTL reference bench, plus a second canary input for each:

| strategy | scalar evaluations | SIMT evaluations | systolic evaluations | result |
|---|---:|---:|---:|---|
| lexicographic exhaustive | 6,339 | 4,628 | 5,132 | exact on all three |
| seeded full permutation | 318 | 83 | 799 | exact on all three |
| greedy coordinate descent | 103 | 69 | 69 | trapped on all three |

The fixed permutation found an exact proposal much sooner in this experiment,
but one seed is not evidence that random order is generally superior.  The
important negative result is that output-word and bit mismatch do not form a
smooth enough fitness landscape for coordinate descent.  Exhaustive traversal
therefore remains the completeness oracle.  Some winners are algebraic route
aliases of the hand-written descriptor; that is a valid result for the fixed
workload contract, not permission to generalise them to other dimensions or
accumulator initialisation.

The generated record stores the complete genomes, exact output hashes,
content IDs, source hashes, search counts, and a known-good RTL rollback per
workload.  It also says `org.atomix.not-authorized`: a model winner is only a
proposal.  Canary, RTL shadow evaluation, and manager-owned rollback remain
mandatory before any volatile activation.  `make l3-check` recomputes the
record, mutation-tests the oracle, and runs the reviewed rollback genomes on
the RTL morph bench.  It does not claim that a newly found alias has already
passed its own RTL shadow run.

## L0 event evidence

```bash
make live-check
```

The first test proves exact event and snapshot semantics, including simultaneous
events, snapshot stability, and reset priority.  The second proves the monitor
is reachable through the immutable shell page alongside a role that can be
isolated, and covers the two derivations above: a refused descriptor counted
once, a stuck reject line counted once rather than per cycle, a rejection from
an isolated role not counted at all, a stall episode past the threshold raising
exactly one watchdog event, a second episode raising a second, and a stall
shorter than the threshold raising none.  `run-axroleiso-no-role-events` runs
the same stimulus against a build that declined the producers, which is what
every Primer profile does: the two counters must stay at zero while the fence,
the register window, and the other four counters behave identically.

The third — `make -C sw/baremetal check-livecount`, an RTL run of
`sw/baremetal/examples/livecount.c` on `configs/sim-morph.json` — is the one the
tie-off would have survived: it proves the *wiring* in an assembled SoC.  A
descriptor the morph fabric refuses reaches `DESCRIPTOR_REJECTIONS`, twice in
succession and in agreement with the role's own `REJECTS` register; an accepted
job does not; and eight rediscovery reads against a fenced window do not.  The
watchdog cannot be provoked from software on a healthy role, so that test
requires it to stay at zero and the fault injection stays in the unit bench.
`make live-check` runs all three.

```text
livecount: rejections=2 role_rejects=2 work=2 stalls=100 watchdogs=0
livecount: PASS (role rejections reach the shell counter, fenced reads do not)
```

This is simulation evidence.  The counters have run on the Tang Primer — see
[achievements/tangprimer25k.md](achievements/tangprimer25k.md) — but that board
run predates these producers and read the tied-off zeros.
