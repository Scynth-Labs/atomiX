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
  outside it.  Evidence: `axrole` plus `axroleiso` and their tests.
- [x] Build same-seed ECP5-85F `role.none` and `role.loopback` baselines and
  measure their delta.  The unconstrained result changes 8,603/13,294 CRAM
  frames across all 126 frame groups, proving that ordinary P&R is not a
  usable partial image.  Evidence: `make -C rtl/fpga pr-delta`.
- [~] Decode the ECP5-85F changed-frame set.  The analysis works, but the
  current 45F-only partial-frame encoder refuses to emit an 85F image.
- [ ] **No hardware:** validate or implement the ECP5-85F frame-address map
  against full bitstreams and deliberately small placement changes.
- [ ] **No hardware:** lock shell placement and routing, constrain the role to
  whole-frame-compatible columns, and show that two role implementations have
  identical shell frames and boundary routing.
- [ ] **No hardware:** generate a candidate delta, unpack it, and prove it
  addresses only the allowed region; reject truncated, out-of-region, and
  wrong-shell inputs before any physical load attempt.
- [!] Load `role.none` and `role.loopback` partial images on an active ULX3S,
  prove the UART/CPU survive, and test isolation during the swap.  **Blocked:**
  no ULX3S is currently available.
- [ ] **No hardware, parallel feasibility spike:** document GW5A-25A
  configuration frames and test whether the open Gowin/Apicula tool surface
  can safely express a Primer partial image.  Treat “not feasible with the
  current tools” as a valid outcome; the current
  [Apicula project](https://github.com/YosysHQ/apicula) exposes ordinary
  pack/unpack flow, not a supported partial-reconfiguration workflow.

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
| Resident morph fabric | PE operations, routes, schedules | sub-millisecond target | research |
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
- [ ] **No hardware:** implement the smallest morph-fabric RTL prototype:
  configurable processing elements, local routing, state/register storage,
  and a bounded configuration memory behind the existing role ABI.
- [ ] **No hardware:** simulate all three personalities from the same bitstream
  model and prove that invalid descriptors cannot access shell-owned state.
- [ ] **No hardware:** synthesize the morph fabric for the Primer and compare it
  with the existing hard GPU and TPU profiles using identical workloads and
  seeds.
- [ ] **No hardware:** compare three alternatives before scaling: a composite
  hard GPU+TPU role, the unified morph fabric, and separate full images.
- [!] Run switching, workload, and power/energy experiments on the Primer.
  **Blocked:** connect the already-owned board and establish the measurement
  method; latency can be measured before a power fixture exists.

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
- [ ] **No hardware — next:** create a content-addressed candidate registry containing
  source/profile hash, tool versions, parent, mutation, test evidence, and
  deployment outcome.
- [ ] **No hardware:** implement L2 shadow evaluation: simulate each candidate,
  run its oracle and safety checks, then produce a signed-off volatile test
  request rather than deploying automatically.
- [ ] **No hardware:** add watchdog, canary workload, last-known-good selection,
  and rollback tests using fault-injected candidates.
- [!] Run volatile L1/L2 A/B trials on the Primer and confirm reset/power-cycle
  recovery.  **Blocked:** board connection.
- [ ] After R2's morph fabric passes, encode its operations/routes as a bounded
  L3 genome and compare search strategies on deterministic workloads.
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

## Cross-track dependencies

| Result | Depends on | Why |
|---|---|---|
| R3/L1 reviewed selection | existing runtime programs | safest first feedback loop |
| R3/L2 offline improvement | evidence registry and correctness oracles | candidates need reproducible provenance |
| R3/L3 adaptive overlay | R2 morph fabric | mutations stay inside a semantic, bounded configuration space |
| R3/L4 native LUT mutation | R1 exit gate | raw changes require proven physical confinement and recovery |

## Immediate queue without hardware

1. Create the content-addressed R3 candidate registry and connect its evidence
   identity to L1 proposals.
2. Generalise the R2 evidence identity into the shared record used by all three
   research tracks.
3. Implement the R2 morph fabric when L3 overlay adaptation needs it; L0/L1 can
   proceed independently.
