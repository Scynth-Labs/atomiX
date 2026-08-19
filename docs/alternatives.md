# Alternatives considered

The research checklist records what was tried and what it showed.  It does not
record what *else* could have been tried, which means a reader cannot tell
whether a result is the answer to a question or merely the outcome of the first
mechanism someone reached for.  This document closes that gap: for each track,
the mechanism the project committed to, the alternatives that exist in the same
design space, and which of them have actually been tested against evidence.

An untested alternative is not a weakness to hide.  It is the honest boundary of
a result, and naming it is what stops a measured number from being read as a
general claim.

## R1 — replacing the accelerator while the shell stays alive

The checklist frames R1 as *partial reconfiguration*.  That is a mechanism, not
the question.  The question is whether the accelerator can be replaced while the
management CPU, memory, UART and loader stay alive and unchanged, and several
mechanisms could satisfy it.

| Mechanism | Status | Evidence |
|---|---|---|
| Vendor dynamic-function-exchange flow | not available | no open ECP5/GW5A flow exposes it |
| ECP5-85F frame surgery via Trellis | blocked | 85F partial-frame encoder is a hardcoded `FIXME`; see R1 notes |
| **ECP5-45F frame surgery via Trellis** | **working** | `--delta` supported; partial bitstream emitted for the real role swap |
| GW5A frame surgery via Apicula | **refuted** | `research/partial-reconfig/gowin-role-delta.json` |
| Resident overlay reconfiguration | **achieved** | morph fabric personality swap in 14.3 us, shell never resets |
| BSRAM-only frame reload | untested | see below |
| Multi-boot / stored-image selection | untested | seconds-scale, CPU resets |
| JTAG readback plus targeted writeback | untested | no tool support surveyed |

Two things follow that the checklist alone does not make visible.

**The blocked half is the easy half.** What makes role replacement safe is
quiescing the window, fencing the bus so no master stalls on a region under
change, holding the role in reset, and recovering when the new contents are
bad.  That machinery exists and is tested — `axroleiso`, its stuck-role test,
the watchdog and rollback paths.  What is blocked is only the delivery
mechanism for new configuration bits.  R1's *value* is therefore already
partly realised, and the frame-surgery work is an optimisation of delivery
rather than a prerequisite for the capability.

**Changing the target beat defeating the tool.**  Considerable effort went
into reverse-engineering the 85F frame-address encoding before anyone asked
whether the 85F was required.  It was not: `ecppack --delta` fully supports the
45F, the ULX3S ships one on the same package and pinout, and the design uses
13,782 of the 45F's 44,000 LUT4.  A board-manifest change produced a working
partial bitstream immediately.  The general lesson is worth more than the
result — when a tool blocks a mechanism, check whether the *requirement* that
selected that tool is real before trying to extend the tool.

**One measurement suggests an untested opening.** The Gowin role-swap delta
changed 88.6% of the 536-bit CRAM frames but only 3.3% of the 4,608 208-bit
BSRAM frames.  If those BSRAM frames are separately addressable, reloading only
them would be a narrow but real partial-reconfiguration capability — enough to
replace an overlay's contents without touching routing.  Nothing here tests
whether Apicula or openFPGALoader can express that, and it should not be
assumed: the frame-count difference between the two builds shows the stream
layout is not a fixed frame array.

## R2 — compute personality transformation

Four mechanisms sit in this space, and the project has now built and measured
all four.  Three have board evidence; the composite currently stops at
simulation and place-and-route.

| Mechanism | Interruption | Status |
|---|---|---|
| Separate full images | seconds; CPU resets | measured on the board (CPU/GPU/TPU) |
| Program switch on the existing programmable role | 38-byte frame, ~0.46 ms UART | measured on the board |
| Unified morph fabric | 52-byte genome, 14.3 us local | measured on the board |
| Composite hard GPU+TPU role | register switch after quiesce; no CPU reset | simulation + P&R; board run pending |

### The falsification that mattered

The morph fabric was built on an assumption that went unexamined for most of
its development: that scalar, SIMT and systolic work need a *reconfigurable
datapath*.  But `role.gpu-compute` is already programmable, and a personality
could simply be a program for it.  If a one-lane hard GPU matched the one-PE
fabric, the fabric would not earn its area and R2's answer would change.

That test was run rather than argued — identical 50-element SAXPY, identical
buffer layout, identical doorbell-to-completion measurement, one lane against
one PE, both synthesised and run on the board.  The fabric survived it on
capability and lost on cost:

| | morph, 1 PE | gpu-compute, 1 lane |
|---|---:|---:|
| LUT4 | 20,176 (87%) | **13,426 (58%)** |
| Routed fmax | 29.20 MHz | **33.15 MHz** |
| Reconfigure | 358 cyc | **206 cyc** |
| SIMT SAXPY job | **462 cyc** | 1,479 cyc |
| Scalar recurrence | **440 cyc, 1 job** | 4,955 cyc, 64 jobs |
| Systolic GEMM | **4,950 cyc** | not expressible |

Detail behind the throughput rows:

- **SIMT:** 462 cycles on the fabric against 1,479 on the one-lane GPU (337 vs
  1,466 in simulation).  The
  GPU serialises each lane's loads onto a single buffer port and pays a wave per
  thread group; the fabric's operand registers absorb that.
- **Scalar recurrence:** one job on the fabric (440 cycles) against 64 separate
  jobs on the GPU (4,955 cycles).  The gpu-compute ISA is straight-line with no
  branches and only 64 program words, so a 64-step dependent chain cannot be
  unrolled into one kernel; the only expressible form is one dependent step per
  doorbell.  This is a structural limit, not a tuning gap.
- **Systolic GEMM:** does not fit the window at all.  Per-thread row/column
  decomposition needs an integer divide the ISA does not have, and the
  precomputed index arrays that would replace it do not fit alongside A, B and
  C in 256 data words.

So neither design dominates, and that is the result.  The fabric is 3.2x faster
on SIMT, 11.3x faster on the recurrence, and the only one of the two that can
run the GEMM at all; the hard GPU is a third smaller, clocks 13% higher, and
reconfigures in fewer cycles.  Flexibility here buys capability and throughput,
not efficiency — and against the hard *four-lane* GPU (18,280 LUT4, 38.47 MHz)
the fabric is still both larger and slower.  A reader deciding whether to adopt
a morph fabric should be choosing on which of those axes binds, not on a single
headline number.

### The composite estimate was wrong

The hard GPU+TPU composite fits the GW5A-25A.  `role.gpu-tpu` keeps the one-lane
programmable GPU and folded TPU resident behind one fixed role window and
selects the exposed native register map at runtime.  Both workloads pass in a
single simulated session, each engine retains state while deselected, and the
selector refuses a change while an engine is executing or has an uncleared
completion.  The payload-agnostic Tang Primer loader profile places at 19,304
LUT4 (83.8%), 2,701 FF, 42 BSRAM and 15 of 28 large-DSP-site equivalents, and
routes at 33.18 MHz.  The earlier sum of standalone profile totals double
counted shared shell costs and ignored synthesis optimisation; it was useful as
a question, not evidence.  No physical-board execution or switching-energy
result is claimed.

### Alternatives still untested here

- **Time-multiplexed hard roles** with clock or power gating, trading area for
  switching energy.
- **A microcoded or VLIW sequencer** instead of the fabric's three
  mode-selected schedules, which would move personality expressiveness from
  fixed modes into the genome at some area cost.
- **A larger device.** Every area conclusion here is specific to the GW5A-25A.
  The fabric's cost relative to the hard roles is a ratio; the *fit* conclusions
  are not portable.

## R3 — adaptive logic

| Level | Mechanism | Status |
|---|---|---|
| L0 | shell-owned telemetry | done in simulation; watchdog derived by the fence, rejection carried on the role ABI, both proven wired in an assembled SoC — the board evidence still predates them |
| L1 | select among reviewed programs | done |
| L2 | shadow-evaluate a candidate, request a volatile trial | done |
| L3 | adapt a bounded overlay genome | 8,192-descriptor search measured; exhaustive and seeded permutation find exact proposals, greedy coordinate descent does not |
| L4 | mutate native configuration frames | blocked; on GW5A, refuted outright by the R1 result above |

### The alternative not taken at L2

L2 shadow evaluation was built and exercised against a **hand-authored**
candidate: a Horner-form polynomial that a person reasoned out.  The pipeline's
actual purpose is to gate candidates a *search* produced, and that has not been
tested.  A superoptimising search over the reviewed program space would use the
same gates, need no morph fabric, and would test whether the static gates and
the oracle survive adversarial rather than well-intentioned inputs.  This is the
most valuable untested item in R3.

### Fitness is narrower than it looks

Every fitness result so far scores on execute cycles.  The canary experiment
showed directly why that is insufficient: the fault-injected candidate was
*correct on the primary workload and faster than nothing it was compared to*,
and only a wider-range input caught it.  Cycle-count fitness is blind to
input-range validity, and the alternatives — program length, energy,
robustness across an input distribution — are all unmeasured.  Energy in
particular is blocked on the same missing current-sense fixture as R2.

## Methodological alternatives that changed results

Three times a measurement method, not the design, was the thing that was wrong.
Each is recorded because the same mistake is easy to repeat.

**A recorded number must reproduce from the command recorded beside it.** The
RTL fast-switch record claimed cycle counts its own command had never produced
at any commit.  Records are now resealed with
`tools/candidate_registry.py seal`, which refuses to refresh a tracked artifact
digest unless the caller states the test was re-run.

**A testbench that drives fixed cycle counts cannot see protocol bugs.** The
morph fabric's unit bench held each bus request for a fixed number of ticks, so
it never noticed that a registered `d_ready` made the master hold `valid` for a
second cycle and applied every register write twice — one doorbell counted two
rejections.  The bench now follows the real handshake, and the fabric drives
ready combinationally as the reference role does.

**An on-core baseline can be optimised away.** The reference loops that the
fabric is compared against read compile-time-known operands, and `-O2` folded
them toward a closed form, making the core look four times faster than it is.
The operands are now `volatile`, which forces the same loads the fabric
performs.  Cross-binary agreement is the check: the scalar reference now
measures 2,887 cycles in both payloads.  The SIMT reference still differs
between binaries by codegen, so **no SIMT speedup-against-core figure is
reported** — only fabric-against-fabric, which shares its measurement path.
