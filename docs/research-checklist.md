# Research checklist

This board tracks atomiX research questions separately from the engineering
completion gates in [design-checklist.md](design-checklist.md).  A plausible
idea is not a capability: every checked research item needs a recorded
experiment, artifact, result, and decision.  A useful negative result counts.

Status legend:

- `[x]` Evidence recorded and the stated question answered.
- `[~]` Partial evidence exists; the question remains open.
- `[ ]` Queued experiment.
- `[!]` Blocked on hardware, tooling, or a prerequisite named in the item.

The only purchased and physically verified board is the Tang Primer 25K Dock
(GW5A-25A).  Work labelled **no hardware** can proceed now.  ULX3S work may
produce build and analysis evidence, but cannot close a live-hardware gate
until that board is acquired or borrowed.

## Research rules

Each experiment must record:

1. the hypothesis and one falsifiable success criterion;
2. exact sources, profile, tool versions, seed, and command;
3. correctness results before performance results;
4. latency, throughput, and FPGA resource/timing results where applicable;
5. raw artifacts plus a short conclusion and next decision.

Never use `verified`, `adaptive`, `partial`, or `live` for a result that only
demonstrates synthesis.  Simulation, place-and-route, volatile board execution,
and live reconfiguration are separate evidence levels.

A recorded number must reproduce from the command recorded beside it.  On
2026-08-10 the RTL fast-switch evidence record was found to claim load/execute
cycles of 340/889 and 119/701 that its own command had never produced, at any
commit; the real figures are 119/887 and 119/699.  Records are now resealed
with `python3 tools/candidate_registry.py seal`, which recomputes content IDs
and registry references and refuses to refresh a tracked artifact digest
unless the caller states that the test was re-run.

## R1 — Partial reconfiguration of an FPGA

**Question:** can atomiX replace only the accelerator role while the management
CPU, memory, UART, and loader remain alive and unchanged?

Production dynamic-function-exchange flows preserve one static placement and
routing result across configurations, while the changing module occupies an
explicit partition.  The atomiX experiment follows the same invariants even
though its current open-source ECP5 path is lower-level.  See the
[AMD DFX introduction](https://docs.amd.com/r/2024.2-English/ug909-vivado-partial-reconfiguration/Introduction)
and the detailed atomiX [partial-reconfiguration plan](partial-reconfig.md).

- [x] Define a stable, isolatable role ABI and keep the management shell
  outside it.  Evidence: `axrole` plus `axroleiso` and their tests — and, since
  2026-08-10, **the fence running on real silicon**.  On the Tang Primer the
  full swap protocol minus the bitstream write was executed: with the fence
  asserted every read of the role window completed and returned zero, the role
  stayed invisible while held in reset, the management CPU and the Live FPGA
  monitor kept running throughout, and releasing the fence restored the role
  and allowed a verified personality change.  `make -C sw/baremetal
  check-roleiso` is the simulation gate; the board result and hashes are in
  [tangprimer25k.md](achievements/tangprimer25k.md).  This is the R1 exit
  gate's isolation clause, discharged on hardware even though the Primer
  cannot supply the configuration write.
- [x] Build same-seed ECP5-85F `role.none` and `role.loopback` baselines and
  measure their delta.  The unconstrained result changes 8,603/13,294 CRAM
  frames across all 126 frame groups, proving that ordinary P&R is not a
  usable partial image.  Evidence: `make -C rtl/fpga pr-delta`.
- [x] Decode the changed-frame set and emit a real partial bitstream.  The
  85F's missing frame-address encoder was treated as the thing to defeat; it
  was cheaper to remove the need for it.  `ecppack --delta` fully supports the
  ECP5-45F, the ULX3S ships a 45F variant on the same CABGA381 package and
  pinout, and the atomiX shell needs 13,782 LUT4 against the 45F's 44,000 — so
  nothing in R1's question required the 85F.  Retargeting is a board-manifest
  change (`board.ulx3s-45f`, `--45k`, idcode `0x41112043`) reusing the 85F
  sources and constraint file unchanged.  No ULX3S is in hand for either
  variant, so the hardware gate is exactly where it was.

  **A partial bitstream for the atomiX role swap now exists.**  Evidence:
  `make -C rtl/fpga pr-delta PR_REFERENCE_CONFIG=../../configs/ulx3s-45f.json
  PR_CANDIDATE_CONFIG=../../configs/ulx3s-45f-loopback.json`.  It writes 7,377
  frames, each with an explicit `LSC_WRITE_ADDRESS`, verified independently by
  `tools/ecp5_frames.py`.

- [~] **No hardware:** validate or implement the ECP5-85F frame-address map
  against full bitstreams and deliberately small placement changes.
  **Deprioritised, not abandoned:** the 45F retarget above removes this from
  R1's critical path.  The investigation and its apparatus are kept because the
  map is still the only route to partial reconfiguration on an 85F board, and
  because the probe method generalises.  What it established:
  Trellis's refusal is a hardcoded `FIXME`, not missing data — `devices.json`
  already carries the full 85F geometry; the map cannot be read from a full
  bitstream, which names no addresses; single-tile `.config` perturbations
  yield exact index/address pairs (26 recorded from 55 sites in
  `research/partial-reconfig/ecp5-45f-frame-address-probe.json`); and the
  offset is piecewise over at least nine segments and non-monotonic.  A second
  obstacle was also found: `ecppack` compresses 85F frame data even without
  `--compress`, so that path needs a decompressor too.

- [ ] **No hardware — now the binding constraint:** lock shell placement and
  routing, constrain the role to whole-frame-compatible columns, and show that
  two role implementations have identical shell frames and boundary routing.
  With `--delta` working on the 45F, this is measurable rather than
  theoretical: unconstrained place-and-route rewrites 7,377 of 9,470 frames
  (77.9%), spread over 89 columns and 69 rows, touching all 90 frame groups.
  The resulting "partial" image is 892,674 bytes against 381,080 for the full
  compressed bitstream — **2.3x larger than simply reloading the whole
  device**, which is the sharpest possible statement of why confinement, not
  the encoder, was always the real problem.
- [ ] **No hardware:** generate a candidate delta, unpack it, and prove it
  addresses only the allowed region; reject truncated, out-of-region, and
  wrong-shell inputs before any physical load attempt.
- [!] Load `role.none` and `role.loopback` partial images on an active ULX3S,
  prove the UART/CPU survive, and test isolation during the swap.  **Blocked:**
  no ULX3S is currently available.
- [x] **No hardware, parallel feasibility spike:** document GW5A-25A
  configuration frames and test whether the open Gowin/Apicula tool surface
  can safely express a Primer partial image.  **Outcome: not feasible with the
  current tools**, on two independent grounds, and this closes the item.

  *Tool surface.*  `gowin_pack` and `gowin_unpack` expose no frame-address,
  region, offset, or partial-image option of any kind — only whole-bitstream
  pack and unpack.  There is no intermediate comparable to Trellis `.config`,
  so a Gowin delta cannot even be expressed in tile coordinates the way the
  ECP5 measurement is.

  *Measurement.*  `tools/gowin_delta.py` compared two GW5A-25A builds differing
  only in their role component (`role.none` vs `role.loopback`, identical core,
  RAM, payload and seed).  Evidence:
  `research/partial-reconfig/gowin-role-delta.json`.  The two streams are not even
  frame-comparable — 13,910 frames against 15,190, so the role changes the
  layout of the configuration stream itself, not merely its contents.  Of the
  frames that do line up, 9,811 of 11,072 CRAM frames differ (88.6%), and the
  changed frames span 99.7% of the stream in 268 separate runs.  No contiguous
  slice of a Gowin `.fs` corresponds to the role window, so no partial image
  covering only the role can be cut from these files regardless of what the
  silicon may support.

  R1 therefore stays on the ECP5 path; the Primer contributes resident
  reconfiguration (R2/R3), not partial reconfiguration.

**Exit gate:** a live role swap changes only an approved region, the management
shell remains responsive, role isolation is asserted during loading, the new
role passes an identity and workload check, and a bad image is rejected or
rolled back without losing the recovery channel.

## R2 — Fast compute-personality transformation

“Board transformation” means changing the machine's **compute personality**;
the physical board does not become a different board.  The useful design space
has four progressively heavier mechanisms:

| Mechanism | What changes | Expected interruption | Current state |
|---|---|---:|---|
| Runtime parameters/program | microcode, descriptors, dataflow | sub-millisecond target | GPU program switch measured at about 0.46 ms of UART wire time |
| Resident morph fabric | PE operations, routes, schedules | sub-millisecond target | 13-word genome write; prototype fits the GW5A-25A only at one PE |
| Cached full image | complete FPGA image | seconds; CPU resets | available workflow |
| Partial image | physical role region | unknown until R1 hardware proof | research |

The first architecture to test is a small fixed management CPU plus a
coarse-grained “morph fabric” in the role window.  Keeping control, recovery,
and safety outside the changing fabric is more useful than attempting to turn
the only CPU itself into a GPU or TPU.  LUT-level mutation comes later only if
the coarse-grained experiment cannot answer the research question.

**Question:** how much scalar, SIMT, and systolic work can one resident datapath
support before its flexibility costs more area, frequency, energy, or
throughput than separate CPU/GPU/TPU implementations?

- [x] Establish one role ABI and reproducible CPU, GPU, and TPU Primer profiles.
- [x] Run separate CPU, GPU, and TPU images on the Tang Primer 25K and record
  physical evidence.
- [x] Replace and run GPU programs inside one resident FPGA image.  Evidence:
  the 42-byte switch frame is about 0.46 ms at 921600 baud; see
  [runtime-reconfiguration.md](runtime-reconfiguration.md).
- [x] **No hardware:** write a versioned personality descriptor and workload
  contract for three minimal modes: scalar control/ALU, 4-lane SIMT, and a
  small systolic matrix tile.  Evidence: the vendor-neutral
  [personality contract](personality-contract.md), six machine-readable
  examples under `research/personalities/`, and `make personality-check`.
- [x] **No hardware:** define the comparison matrix: correctness, switch
  latency, cycles/work item, LUT/FF/BSRAM/DSP use, Fmax, and energy when
  physical measurement becomes available.  Evidence: the
  [comparison contract](comparison-contract.md), versioned six-candidate R2
  plan, explicit non-evidence template, and `make comparison-check`.
- [x] **No hardware:** implement the smallest morph-fabric RTL prototype:
  configurable processing elements, local routing, state/register storage,
  and a bounded configuration memory behind the existing role ABI.  Evidence:
  `role.morph` (`components/role/morph/`).  Every PE computes one fused form,
  `out = (a + b) * c + d`, with four independently configured source muxes; the
  personality lives entirely in a 13-word genome, not in the fabric.
- [x] Simulate all three personalities from the same bitstream model and prove
  that invalid descriptors cannot access shell-owned state — **and confirm it
  on the board**.  The fabric ran on the Tang Primer on 2026-08-10 and printed
  `role morph: PASS (scalar, SIMT, systolic on one fabric; descriptors
  confined)`; evidence `research/comparisons/morph-1pe-primer-physical.json`,
  bitstream `8faf3d13...`, payload `0314c572...`, SRAM only.  Simulation
  evidence: `make -C sim/unit run-morph-fabric` and
  `make -C sw/baremetal check-morph`.  One fabric runs the scalar
  recurrence, the 4-lane SIMT SAXPY, and the 12x8x8 systolic GEMM, each exactly
  matching its reference.  Six descriptor classes — output stream, input
  stream, and reduction stride leaving the window, unknown mode, zero-sized
  job, and short genome — are all refused before BUSY rises, never advance the
  configuration generation, never modify the previous result, and leave the
  fabric able to run the last good genome.  Engine addresses are additionally
  truncated into the role's own buffer, so confinement is structural and not
  only a check.
- [x] **No hardware:** synthesize the morph fabric for the Primer and compare it
  with the existing hard GPU and TPU profiles using identical workloads and
  seeds.  Evidence: `research/comparisons/morph-{1,2,4}pe-primer-pnr.json` and
  `make comparison-check`.  **The flexibility does not pay for itself on this
  device.**  A one-PE fabric is the largest that places: 19,620/23,040 LUT4 at
  26.79 MHz.  Two PEs pack to 92% but find no legal placement; four PEs demand
  102% and do not fit at all.  For comparison, the hard four-lane GPU is
  18,280 LUT4 at 38.47 MHz and the hard TPU is 17,345 LUT4 at 32.65 MHz.  One
  morph PE therefore costs *more* area and *less* frequency than either hard
  role it would replace, while delivering a quarter of the GPU's lanes.  The
  one-PE build was then run on the board and passed, so this row is physical,
  not merely routed: 20,176 LUT4, 2,706 FF, 24 BSRAM, 3 DSP, 29.20 MHz.  A
  personality change writes 13 genome words — 52 bytes — with no bitstream
  reload.  Throughput was then measured on the board against an on-core
  reference reading the same operands from volatile arrays: the fabric returns
  8.5x (scalar) and 12.0x (systolic) over the management core, at 440/462/4,950
  cycles per job.  No SIMT speedup-against-core is claimed — that reference
  loop compiles differently in the two payloads that measure it, while the
  scalar reference cross-validates exactly at 3,732 cycles in both.  Reconfiguration costs 358-361 management-CPU cycles, about 14.3 us
  at 25 MHz, or roughly 0.61 ms if the genome were shipped over the 921600-baud
  UART instead — inside R2's sub-millisecond target either way.  So the fabric
  is genuinely faster than the core on all three personalities, but only by
  single-digit multiples, while costing more area and less frequency than the
  hard roles it would replace.
- [~] Compare the alternatives before scaling.  Three of four are now measured
  on the board, and a fourth alternative the original item did not name turned
  out to matter more than the ones it did.  Evidence:
  [alternatives.md](alternatives.md), `research/comparisons/`.
  - *Separate full images* and *the unified morph fabric*: measured.
  - *Program switch on the existing programmable role*: measured, and tested
    head-to-head against the fabric.  A one-lane `role.gpu-compute` is 13,426
    LUT4 at 33.15 MHz against the one-PE fabric's 20,176 at 29.20 MHz, and
    reconfigures in 206 cycles against 358 — but takes 1,479 cycles to the
    fabric's 462 on the same SAXPY, needs 64 separate jobs (4,955 cycles) for
    the recurrence the fabric does in one (440), and cannot express the GEMM in
    this window at all.  Neither design dominates: flexibility buys capability
    and throughput here, not efficiency.
  - *Composite hard GPU+TPU role*: still not built.  Area arithmetic says it
    will not fit, but that is an estimate, not evidence.
- [~] Run switching, workload, and power/energy experiments on the Primer.
  Switching and workload experiments are complete and reproduced on 2026-08-10;
  **power and energy remain unmeasured** because no current-sense fixture has
  been selected, and the Dock exposes no on-board rail telemetry.  Until a
  fixture exists this line cannot close.
  The connected board passed ten consecutive two-program iterations in one
  resident session after USB/IP reconnect. Loads were invariant at 198 cycles,
  executes at 1,354/1,022 cycles, and the complete host round trip measured
  36.86/38.62/42.16 ms min/mean/max at 921600 baud. Oversized and bad-CRC
  uploads were rejected before a valid retry; S1 recovered both a normal
  kernel reset and an upload stopped at 2,048/4,829 bytes. Each recovery was
  followed by three further passing runs. Power and energy measurement remain
  pending until a fixture is selected.

Provisional success targets for the first prototype are: one resident image;
all three minimal workloads correct; personality replacement below 1 ms at the
current UART rate; no shell reset; and an explicit overhead report versus the
hard roles.  These targets are experimental thresholds, not current claims.

Open decisions to settle from evidence:

- fixed management CPU plus morph role, or scalar CPU execution inside the
  morph fabric;
- coarse ALU/PE granularity, LUT granularity, or a hierarchy of both;
- shared-memory, scratchpad, and cache-coherency contract;
- primary objective: switch latency, throughput, area, energy, or fault
  tolerance.

## R3 — Live FPGA: adaptive logic

The safe first interpretation is a closed loop that measures itself, chooses
between bounded configurations, verifies a candidate, deploys it temporarily,
and rolls back on failure.  It is not initially arbitrary mutation of native
FPGA configuration bits.

Published evolvable-hardware research reports a real tradeoff: virtual
reconfiguration is flexible but costs area and delay, while dynamic partial
reconfiguration reduces fabric overhead but can be slower and less flexible.
Hybrid systems combine them.  That supports starting with a constrained
coarse-grained genome and reserving raw frame mutation for a much later gate.
See Yao et al.,
[“A General Low-Cost Fast Hybrid Reconfiguration Architecture for FPGA-Based Self-Adaptive System”](https://doi.org/10.1587/transinf.2017EDP7231),
and the later
[HexCell systolic-array work](https://scholarworks.boisestate.edu/electrical_facpubs/553/).

### Capability ladder

| Level | Capability | Safety boundary |
|---|---|---|
| L0 Observe | counters report correctness, stalls, throughput, errors, and resets | no configuration change |
| L1 Select | policy chooses among reviewed programs/parameters | allow-listed descriptors only |
| L2 Improve offline | host generates, simulates, scores, and stages a candidate | correctness gate plus rollback |
| L3 Adapt an overlay | optimizer changes a bounded PE/route genome at runtime | morph fabric only |
| L4 Mutate FPGA frames | native LUT/routing bits change in a partial region | requires R1 isolation and recovery proof |

- [x] **No hardware:** define the L0 telemetry schema and add deterministic
  counters for cycles, work completed, memory stalls, descriptor rejection,
  watchdog events, and configuration generation.  Evidence: the shell-owned
  [Live FPGA monitor and schema](live-fpga.md) and `make live-check`.
- [x] **No hardware:** split evolution policy from the immutable management
  kernel and provide `none`, `small`, `mid`, and `large` bounded services.
  Every supplied profile links and boots inside the Tang Primer's exact 32 KiB
  RAM envelope; policy state is statically capped and no tier can actuate FPGA
  configuration. Evidence: [kernel evolution service](live-fpga.md) and
  `make evolution-check`.
- [x] **No hardware:** define a deterministic fitness function over adjacent
  L0 snapshots with correctness as a hard gate. The versioned record preserves
  raw counters and oracle evidence, derives modular deltas and exact rational
  metrics, and emits a uint32 Q22.10 cycles/work score only for eligible
  trials. Evidence: [Live FPGA fitness record](live-fpga.md),
  `research/live-fpga/`, and `make fitness-check`.
- [x] **No hardware:** build an L1 host policy that selects among existing,
  reviewed GPU programs or tunable schedules and records why it switched. The
  first policy validates the exact `axhost --fast-switch` word streams, filters
  by role/workload/revision and fitness eligibility, rejects mixed objectives,
  applies a deterministic threshold, and emits a reasoned proposal with no
  actuation authority. Evidence: [L1 reviewed selection](live-fpga.md),
  `research/live-fpga/policy/`, and `make policy-check`.
- [x] **No hardware:** run the real bounded fitness and evolution components in
  a deterministic virtual FPGA loop. The model covers a valid improvement,
  faster incorrect output, watchdog timeout, proposal-only authority, a failed
  activation canary, and manager-owned rollback to the baseline. Evidence:
  [closed-loop virtual FPGA](live-fpga.md) and `make live-sim-check`.
- [x] **No hardware:** create a content-addressed candidate registry containing
  source/profile hash, tool versions, parent, mutation, test evidence, and
  deployment outcome. Candidate construction identity is immutable canonical
  JSON; evidence and deployment records have separate hashes so new trials do
  not rename the candidate. L1 proposals now carry both the logical program ID
  and registry content ID. Evidence: [candidate registry](live-fpga.md),
  `research/live-fpga/registry/`, and `make registry-check`.
- [x] **No hardware:** implement L2 shadow evaluation: simulate each candidate,
  run its oracle and safety checks, then produce a signed-off volatile test
  request rather than deploying automatically.  Evidence:
  `research/live-fpga/shadow/l2-polynomial-horner.json`, `make shadow-check`,
  and `make shadow-rebuild` to regenerate it from real RTL runs.  Five static
  gates run *before* anything is simulated — program length, opcode
  allow-list, explicit HALT, define-before-use, and an interval analysis
  proving every load/store address stays inside the role window — then the
  oracle gate runs on the simulated output.  `define-before-use` is a real
  isolation gate, not a formality: `gpu_engine.sv` never resets its per-lane
  registers, so a candidate reading an undefined register would observe the
  previous program's state.  The record is fully re-derivable: `check`
  recomputes every gate, verdict, and the request itself from the recorded
  program words, so authoring cannot disagree with validation.  A signed-off
  request carries `actuation: org.atomix.not-authorized` and the checker
  rejects any record that claims otherwise.
- [~] **No hardware:** add watchdog, canary workload, last-known-good selection,
  and rollback tests using fault-injected candidates.  Canary, last-known-good
  and rollback are done in both RTL and hardware — see the A/B trial below.
  The decisive case is a fault-injected candidate that clamps its input to the
  primary workload's value range: it passes every static gate *and* the primary
  oracle, and only the wider-range canary catches it.  **Still open: there is no
  fabric watchdog.**  `components/soc/reference/soc_top.sv` ties both
  `watchdog_event` and `role_reject_event` to `1'b0`, so the `axlivemon`
  counters for them can never advance in a real SoC; the deadline enforced
  today is host-side and is recorded as such.
- [x] Run volatile L1/L2 A/B trials on the Primer. Evidence:
  `tools/live_ab_trial.py`, `research/live-fpga/trials/ab-primer-2026-08-10.json`,
  and registry candidate `polynomial-i32-v2-horner`.  On the board, the
  reviewed baseline ran at 1,022 execute cycles and the L2-signed-off Horner
  candidate at 986 — 36 fewer, bit-identical output — over both the primary and
  canary workloads.  The injected candidate was correct on the primary (1,106
  cycles) and rejected by the canary, after which rollback to the last known
  good was verified on both workloads with zero deadline misses across eight
  jobs.  The same sequence passes in RTL at 699/667/787 cycles.
- [ ] After R2's morph fabric passes, encode its operations/routes as a bounded
  L3 genome and compare search strategies on deterministic workloads.
  **Now unblocked:** the fabric passes in simulation and its 13-word genome is
  already the bounded search space this item needs.
- [!] Explore L4 LUT/frame mutation only after R1 proves frame confinement,
  isolation, live recovery, and bad-image rejection.  **Blocked:** R1 exit gate.

Non-negotiable invariants for L2–L4:

- the management CPU, clock/reset, I/O, UART loader, watchdog, and isolation
  logic are immutable;
- candidates cannot address or route outside the role boundary;
- every candidate has an oracle, timeout, provenance record, and known-good
  rollback target;
- deployment is volatile until repeated trials justify an explicit promotion;
- improvement never means performance gained by weakening correctness.

**Exit gate for the first useful adaptive result (L2):** on one fixed workload
suite, the system measures a baseline, proposes at least one candidate, rejects
an intentionally wrong candidate, promotes a correct improvement only after
shadow evaluation, and returns to the known-good configuration after an
injected failure.

Alternative mechanisms across all three tracks — tested, refuted, and still
untested — are enumerated in [alternatives.md](alternatives.md).  That document
is the record of what was *not* tried and why, which is what keeps the measured
results above from reading as general claims.

## Cross-track dependencies

| Result | Depends on | Why |
|---|---|---|
| R3/L1 reviewed selection | existing runtime programs | safest first feedback loop |
| R3/L2 offline improvement | evidence registry and correctness oracles | candidates need reproducible provenance |
| R3/L3 adaptive overlay | R2 morph fabric | mutations stay inside a semantic, bounded configuration space |
| R3/L4 native LUT mutation | R1 exit gate | raw changes require proven physical confinement and recovery |

## Immediate queue without hardware

1. Build the composite hard GPU+TPU role — the last untested R2 alternative,
   and the only one that could still fit the GW5A-25A.
2. Wire a real fabric watchdog and role-reject line in `soc_top.sv` so the
   `axlivemon` counters stop reading zero by construction.
3. Encode the morph genome as a bounded L3 search space now that the fabric
   exists, and compare search strategies on deterministic workloads.
4. Validate the ECP5-85F frame-address map, which is the head of the whole R1
   queue and blocks every later partial-reconfiguration item.
5. Select a current-sense fixture so the R2 power and energy line can close.
