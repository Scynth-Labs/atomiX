# atomiX platform roadmap

Planning baseline: 2026-09-10. These are proposed outcomes, not delivered
capabilities. Execution order lives on the [priority boards](boards/README.md);
completion evidence lives in the engineering and research checklists.

## The product to build

Make atomiX an **open hardware/software co-design platform**: bring a workload,
change the software or machine, measure the tradeoffs, and give someone else a
result they can reproduce. The RISC-V CPU, aXos, accelerators, simulator, and
FPGA shell form its reference implementation. FPGA is one execution target;
native software, emulation, external accelerators, and eventual ASIC targets
belong in the same platform through their own adapters and evidence.

The first audience is a developer or research team deciding which architecture
fits a workload. Their useful output is a justified design choice: which core,
cache, accelerator, memory configuration, compiler, or runtime meets their
constraints, what evidence supports that choice, and what remains unmeasured.
Teaching becomes an accessible entry point into the same workflow. Adaptive computing becomes
a further use of the same experiment and evidence infrastructure.

This direction is a product hypothesis. AX-04 tests whether people outside the
project can actually use it; RX-01 through RX-03 test whether the proposed
optimization and adaptation produce useful results.

## Capabilities worth building around

| Capability | What a user should accomplish | Foundation already in the repository | Missing product work |
|---|---|---|---|
| Architecture experiments | Run one workload across compatible implementations and choose from measured tradeoffs | Profiles, selectable cores/roles, `tools/bench.py`, comparison contracts | User-defined experiment plans, bounded sweeps, resumable runs, comparable result bundles |
| Software/hardware co-design | Explore algorithms, compilers, runtimes, and hardware choices together | Native reference code, aXos services, ISS/QEMU/RTL checks | Execution adapters, compiler/runtime identity, controlled joint sweeps, capability-scoped metrics |
| Reusable components | Add an implementation outside the source tree and establish its own compatibility claim | External manifests and component contracts | A worked SDK example, conformance entry point, capability/evidence inventory, compatibility policy |
| Safe adaptation | Show when changing a resident implementation pays for its transition and recovery costs | UART runtime loading, isolation, oracles, registry, L2/L3 simulation | Workload crossover evidence, held-out evaluation, complete telemetry, scoped physical trials |
| A path to silicon | Evaluate whether a reusable design merits a technology-specific implementation | Synthesizable RTL and component seams | FPGA dependency audit, memory/library adapters, small-block ASIC feasibility, later sign-off and fabrication decisions |

The [execution-target design](execution-targets.md) separates workload meaning,
implementation artifacts, execution mechanics, and evidence. A native executable
and an RTL role can implement the same workload without sharing an ISA, binary,
or programming model. Browser exploration reuses these records; it is an access
path, not another hardware target.

## Milestones and deliberate tradeoffs

| Milestone | Exit outcome | Required board cards | Scope boundary |
|---|---|---|---|
| M0: experiment alpha | A new user runs native software and RTL implementations of a shared workload, compares eligible results, and replays a run | AX-01 ✓ → AX-10 ✓ → AX-02 ✓ → AX-03 ✓ → AX-04 | Native CPU + RTL adapters; same-binary core comparison retained; no board required |
| M1: reusable preview release | A contributor installs an external component; a clean host reproduces the supported examples and release identities | AX-05, AX-06, AX-07 after M0 | Explicit supported subset; other profiles retain their own evidence status |
| M2: accessible evaluation | A reader opens the same experiment in a browser and can take its record back to native tools | AX-08 after M0 | WASM and browser checks required for this feature; native use stays independent |
| M3: measured adaptive value | A fixed or adaptive policy earns a workload-specific recommendation including transition and recovery costs | RX-02, RX-03; RX-04/RX-05 and HW-03 for a physical L3 claim | A negative result can settle the research question; it cannot establish adaptive benefit |
| Target expansion: software and accelerators | A compiler/runtime experiment and ISS/emulator adapters use the same controller; a further accelerator earns its own conformance | AX-11, AX-12; AX-13 with an accessible device | Separate ISA/runtime implementations; no automatic binary compatibility or device-access assumption |
| Silicon feasibility | A small audited block produces a technology-specific feasibility result or an explicit refutation | RX-08 → RX-09 | Research lane; no manufacturing, full-chip sign-off, or silicon claim |

HW-01 improves the existing Primer evidence in parallel when a lab session is
available. M0–M2 can ship with native-software and simulation evidence. A preview
advertising Primer hardware support additionally needs HW-01 for the exact images it ships.
M3 starts with simulation; its physical extension has a separate gate.

The first demonstration has two deliberately scoped fixtures: the existing
`cpu_perf` payload compares compatible RISC-V cores on model cycles; a shared
integer workload such as `saxpy-i32` runs as native software and through an RTL
implementation with separate binaries and the same oracle. The native leg must
run without RISC-V, RTL, or FPGA tools. That makes portability an early acceptance
test rather than a promise to generalize later.

Resource constraints join an answer only when matched implementation records
exist. Host execution time, simulator runtime, modeled cycles, FPGA timing, and
ASIC estimates remain different measurements. Cross-target comparisons use
compatible semantics and methods; a mixed table must explain unrankable pairs.

The architectural opportunity after that is a design-space explorer. Enumerate
a small valid space first. Then test whether a search policy finds comparably
good configurations with fewer evaluations. Keep the search strategy replaceable,
and keep the correctness oracle and deployment authority outside it.

## First development cycle

1. **AX-01:** define workload semantics and target-scoped measurement rules,
   including the same-binary core fixture and shared-workload native/RTL fixture.
2. **AX-10:** implement the native CPU and RTL adapter boundary. Exercise the
   native leg independently from any FPGA or RISC-V toolchain.
3. **AX-02:** run that plan through a small bounded sweep; inject a failed case
   and interrupt/resume the run to prove that neither becomes a passing result.
4. **AX-03:** produce a comparison with replayable inputs and visible exclusions.
   Demonstrate that a changed payload invalidates result reuse.
5. **AX-04:** have a new user perform the comparison and explain the evidence.
   Use observed friction to choose the next development cycle.

These are ordered delivery slices, not calendar promises. Aim for a reviewable
slice within one or two working days; split larger work into child tasks that
retain the parent gate. Do not mark the parent complete after only its first
slice. Fix onboarding friction before increasing the number of architectures.

## Measure whether the platform is becoming useful

Record the initial baseline during AX-04; the targets below are planning
targets, not measurements. Report unsuccessful attempts as well as successes.

| Signal | Initial target | Measurement |
|---|---|---|
| Time to first comparison | At most 15 minutes after documented prerequisites | Start at a fresh checkout; separately record setup and compilation time |
| Independent replay | Two people other than the implementer reproduce M0 | Same experiment/payload identities, oracle outputs, and deterministic cycle counts; list host differences |
| Target independence | Native workload execution needs no board, RISC-V compiler, or RTL tools | AX-10 native conformance in an environment lacking those tools; RTL checked separately |
| Evidence integrity | Every attempted configuration has an explicit outcome | Include failed, blocked, timed-out, and not-run cases; rank only eligible results |
| Useful decision | Each pilot explains one tradeoff and one limit of the evidence | Record the answer and any manual help needed, not just whether a page opened |
| Component onboarding | One external implementation without editing the generic SoC or resolver | AX-05 conformance evidence, unsupported modes, and a non-default parameter test |
| Feedback speed | Establish narrow-check and smoke duration baselines, then reduce the slowest repeated step | Record build reuse, test duration, and failures without removing coverage |

The repository remains the planning authority. Pilot recruitment, public
hosting, release publication, and purchases are future execution actions;
writing these cards does not perform them.

## Work that should wait

- Asynchronous host jobs should follow measured blocking/transfer costs
  (RX-02). More protocol surface is useful only if it unlocks a demonstrated
  workload or developer workflow.
- New ISA extensions, Linux, multicore, graphics, and additional boards need
  a named workload or adopter that the existing platform cannot serve.
- ECP5 partial reconfiguration stays a bounded confinement experiment. It
  does not block the experiment platform, and no Primer partial-load capability
  follows from it.
- A hosted marketplace, cluster scheduler, or automated RTL generation service
  needs successful external component use first. Begin with local, inspectable
  experiment records and existing build tools.
- Additional ISAs can enter through execution adapters when a workload needs
  them. ASIC portability research can start now; a full SoC tapeout follows a
  successful feasibility result and a separate resource/verification decision.
- Power rankings wait for a calibrated fixture. Persistent flash and autonomous
  promotion retain their existing authorization and safety boundaries.

Revisit these choices after each pilot or completed experiment. Move work up
because it removes a measured obstacle or answers a decisive question, and
record that reason on its board.
