# Execution targets and hardware/software co-design

This is the architectural direction for the [platform roadmap](roadmap.md).
It defines the boundary to implement through [AX-10–AX-13](boards/targets.md).
AX-01 and AX-10 are now implemented: the adapter boundary lives in
[`tools/execution/`](../tools/execution/) and the experiment contract in
[`tools/experiment_contract.py`](../tools/experiment_contract.py). Everything
this document says about AX-11 to AX-13 and about ASIC work remains a design
direction rather than a claim about existing code.

## What belongs to the platform

An experiment connects four independently versioned things:

| Layer | Owns | Examples |
|---|---|---|
| Workload | Inputs, arithmetic, output semantics, correctness oracle, logical work | SAXPY with explicit integer wrap, matrix multiply, a kernel ABI scenario |
| Implementation | Algorithm, data layout, compiler/lowering choices, executable or configuration | Host executable, RISC-V ELF, GPU kernel, RTL design, overlay program |
| Execution target | Capabilities, build/run mechanism, resource limits, observable metrics | Host process, ISS, emulator, RTL simulator, FPGA device, technology-mapped design |
| Experiment controller | Candidate selection, budgets, records, comparison, replay | Finite sweep, later replaceable search or selection policy |

The common interface must not require a board, UART, aXos, a bitstream, or a
particular ISA. RISC-V is the reference machine's ISA. A native host CPU or
commercial accelerator can implement the same workload using another ISA or
programming model, with its own correctness and performance evidence.

The existing [component system](components.md) still owns machine composition.
An execution adapter invokes its owning tools; it does not replace the resolver
or turn every host process into a pretend SoC profile. A target manifest/profile
owns implementation selection and configurable build limits. Experiment inputs
own sweep bounds and scheduling budgets.

## Targets and the claims they can earn

| Target | Intended use | Current foundation / next gate | Limits of its evidence |
|---|---|---|---|
| Native host CPU | Software baseline; algorithm, compiler, and runtime experiments | Native reference/oracle code exists; common experiment adapter is AX-10 | Measures the actual host implementation; says nothing about atomiX RTL timing |
| ISS and system emulator | Fast software, ABI, privilege, and OS experiments | aXsim and QEMU checks exist; common adapters are AX-12 | Functional behavior and explicitly defined model counters; emulator speed is not target clock speed |
| RTL simulation, native or WASM | Architecture behavior and model cycle comparisons | Verilator and web machine exist; common adapter is AX-10 | Model cycles and correctness; not physical frequency, power, or manufactured behavior |
| FPGA | Rapid hardware iteration and measured resident execution | Existing flows and Primer results; lab queue remains separate | Exact device, image, payload, and procedure only |
| Host GPU or other accessible accelerator | Heterogeneous workload implementation and runtime selection | AX-13; no common backend or device availability claimed | Actual selected device/runtime only; no silent CPU fallback presented as accelerator evidence |
| ASIC implementation flow | Technology-specific area/timing feasibility for reusable RTL | RX-08 dependency audit, then RX-09 small-block physical-design experiment | Estimates and implementation checks at named libraries/corners; no silicon claim |
| Fabricated silicon | Validate a manufactured design and its software | Later decision after pre-silicon closure and resources | Requires a real device and bring-up; no current capability or tapeout commitment |

The first portability proof is **native CPU + RTL simulation** on a shared
integer workload, with distinct implementation artifacts. The existing
same-binary three-core comparison remains a separate architecture fixture.
Neither a Verilated model running on a laptop nor its WASM build constitutes
the native software implementation required by AX-10.

## An adapter's minimum responsibilities

AX-10 turned these requirements into a versioned, tested contract; each
numbered responsibility below maps to a method on `tools/execution.Adapter`
and to a gate in `make adapter-check`:

1. Describe capabilities, supported workload/implementation formats, prerequisites,
   limits, and available measurements. Compatibility includes arithmetic and
   execution semantics, not just a matching file extension.
2. Prepare an implementation using its own declared tools and profile. Record
   compiler flags, source, libraries/runtime, target identity, and artifact hashes.
   Reuse a machine build independently from the workload payload when applicable.
3. Execute bounded work and report outputs, status, timing boundaries, and
   measurement methods. Cancellation and timeout must terminate the adapter's
   owned work or report failure to do so, according to the target's contract.
4. Export enough inputs and environment identity for replay. Missing tools,
   devices, artifacts, or required capabilities must be explicit failures or
   blockers, with no fallback that changes the target invisibly.

The experiment controller requests work; target adapters own its mechanics.
An ordinary host execution requires no fictional isolation register or UART
loader. A target that supports reconfiguration must advertise its transition,
recovery, and authority contract. For Live FPGA that remains the immutable
shell, loader, isolation, watchdog, oracle, provenance, and rollback contract.
Successful host cancellation cannot discharge a hardware recovery gate.

## Comparable work does not require identical binaries

Use two distinct fixtures and name which claim each makes:

- **Same executable, different compatible machines:** the three RISC-V core
  profiles run the same bytes, isolating the effect of architecture choices.
- **Same workload, different implementations:** a native executable, a GPU
  kernel, or an RTL role implements the same input/output semantics. Record
  each compiler, algorithm, layout, and artifact; their differences are part
  of the experiment, and ISA/ABI compatibility is not implied.

Start with exact integer arithmetic so overflow, layout, and tail behavior are
testable. Host C code must implement the declared wrapping semantics without
relying on signed-overflow behavior. If floating point is introduced, define
precision, tolerances, and reduction ordering before evaluating candidates.

Compare elapsed time only between actual executions with compatible workload
boundaries and an explicit environment. Preserve warmup, repetitions,
concurrency, host/device synchronization, transfer, and verification costs.
Do not rank native elapsed time against RTL simulation wall time, QEMU speed,
or cycles converted using an unmeasured clock. Model counters keep their own
definitions; equal counter names are insufficient evidence of equal meaning.

The current [comparison contract](comparison-contract.md) requires the R2 FPGA
metric matrix. AX-01 did not stretch it to cover host processes: the
[experiment contract](../research/experiments/README.md) is a sibling schema
with per-target-class metric applicability, and the R2 documents keep their own
validator unchanged. New applicability rules must
distinguish measured zero, unavailable measurement, and an inapplicable metric.
A host CPU must not acquire fictitious LUT counts, FPGA clocks, or configuration
switches just to satisfy a schema. No new status token is accepted merely
because it appears in this design document.

## A path to silicon

RX-08 inventories the assumptions tied to FPGA memories, DSPs, initialization,
clock/reset, and I/O. Replaceable technology adapters should own memory macros,
library mapping, pads, and constraints. Keep existing FPGA implementations and
their fit/evidence boundaries intact while selecting alternatives explicitly.

RX-09 starts with one small block and an accessible technology/library flow.
Record source/library/tool identities, constraints, timing corners, physical
reports, and unresolved checks. A mapped netlist is not a routed result; routed
area/timing is not sign-off or a fabricated chip. Full-chip verification,
testability, power integrity, packaging, manufacturing access, and bring-up
become explicit work only after that feasibility result supports proceeding.
This permits ASIC research now without making fabrication the first milestone.
