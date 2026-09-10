# Engineering checklist and evidence

This is the live completion checklist for atomiX.  It tracks evidence, not
just code presence: a checked item has a reproducible command or a recorded
physical observation behind it.

Status legend:

- `[x]` Verified by the listed automated evidence.
- `[~]` Implemented and simulation/synthesis tested; physical-board evidence is pending.
- `[ ]` Planned or intentionally deferred.

Current lab hardware is one Tang Primer 25K core board on its Dock.  It is the
only purchased and physically verified target.  ULX3S-85F and Tang Nano 20K
remain supported build/research targets, but synthesis, place-and-route, and
generated bitstreams for them must not be described as physical-board evidence.
Near-term hardware work therefore targets the Primer unless another board is
explicitly acquired or borrowed.

The architectural contract remains [DESIGN.md](../DESIGN.md); component
contracts and selections are in [components/](../components/).
Long-horizon hypotheses and experiments for partial reconfiguration, a shared
CPU/GPU/TPU-style compute fabric, and adaptive logic are tracked separately in
the [research checklist](research-checklist.md).  Items move here only when
their engineering scope and evidence gate are concrete.

## Next work in order

The [platform roadmap](roadmap.md) sets the direction: a user brings a workload,
explores software and hardware implementations, and shares a reproducible design
decision. Native software, model execution, FPGA, and eventual silicon use their
own [execution-target contracts](execution-targets.md).
The [priority boards](boards/README.md) own execution order, dependencies,
state, and ownership. AX-01 and AX-10 are closed: an experiment plan is a
versioned input, and one workload runs on a host CPU and on RTL through a
single adapter boundary. AX-02 (bounded, resumable sweeps) and AX-03
(explainable comparison and replay) are next on the
[delivery board](boards/delivery.md). Complete the experiment alpha before
widening its architecture catalog.

The platform gates below are new work, initially unchecked. Existing components,
benchmarks, browser machines, and evidence tools are their starting points;
their presence alone does not close these product gates. Asynchronous host-link
completion remains open under Platform expansion, but follows demonstrated
workload demand rather than heading the delivery queue.

The previous queue's SDRAM, build-identity, payload-failure, headless-example,
and evidence-view results remain recorded in their sections below. Browser
documentation and URL replay remain partial and are included in AX-08.

## Platform product gates

These gates define completion; [board rows](boards/delivery.md) define the first
slice to pull. Acceptance requires recorded results, including failures and
unavailable evidence. New commands belong in [workflow.md](workflow.md) when
implemented; the targets for the underlying tools do not prove a future feature.

<a id="ax-01"></a>

- [x] **AX-01 — Workload-driven experiment contract.** A versioned plan names
  the workload and oracle, input cases, implementation/build identity, execution
  targets and their profiles, required capabilities, measurement boundaries,
  and evaluation budget. Extend or adapt the existing personality/comparison
  contracts rather than creating a competing evidence format. Prove the first fixture with one
  `cpu_perf` payload across `sim-minimal`, `sim-bram`, and `sim-ax2`; reject an
  incompatible capability requirement and a mismatched workload revision.
  Add a shared-workload fixture binding native CPU and RTL implementations to
  `saxpy-i32`; their binaries may differ, their logical inputs and oracle may
  not. Define metric applicability so native candidates need no artificial FPGA
  resources, board, UART, or role-transition fields. Existing R2 plans must
  still validate through a deliberate schema version/extension. Record modeled
  cycles separately from native execution and simulator host duration, and
  require matched evidence for any resource claim.
  Evidence: `make experiment-check`, which validates
  [`research/experiments/`](../research/experiments/) and runs the contract's
  own gates -- an out-of-tree backend accepted, a capability the target does
  not provide refused, a pinned workload revision that does not exist refused,
  a LUT count on a native candidate refused, a plan requiring model cycles from
  a host-elapsed target refused, measured zero kept distinct from measured
  null, and an R2 comparison plan refused by this validator while
  `tools/comparison_contract.py` still accepts it.

<a id="ax-02"></a>

- [ ] **AX-02 — Bounded, resumable design-space execution.** Execute an AX-01
  plan through AX-10 adapters using their owning resolver/build/run interfaces.
  Enumerate a finite declared parameter space first; reject invalid combinations
  before building.
  User-declared evaluation/time limits, cancellation, and interrupted-run resume
  must preserve passed, failed, blocked, timed-out, and not-run outcomes.
  Result reuse must check source/tool/compiler/runtime and target identities,
  resolved profiles, workload, artifact hashes, and relevant seeds/options;
  hardware/model build identity must remain independent of runtime payload
  identity. Close with a clean replay,
  interruption/resume, stale-cache rejection, and a deliberately wrong result
  excluded from ranking. Exercise native CPU and RTL paths, a non-default
  parameter, and an evaluation bound.
  Generated sweep profiles and build trees stay outside tracked source.

<a id="ax-03"></a>

- [ ] **AX-03 — Explainable comparisons and replay.** A local report consumes
  AX-02 records and shows eligible candidates, rejected/missing cases, exact
  workload and machine identities, measurement methods, and a Pareto table
  over comparable metrics. A user can set a constraint and see either qualifying
  candidates or an explicit lack of evidence; missing area or energy must never
  pass a bound. Export a self-contained experiment description with artifact
  hashes and documented retrieval/build instructions, then reproduce its oracle
  outputs and deterministic cycles from a clean run. Reject changed or missing
  payloads and incompatible evidence levels. Do not collapse the table into a
  universal architecture score or infer physical performance from RTL cycles.
  Show same-binary and same-workload comparisons distinctly. Native elapsed
  time cannot be ranked against simulator wall time, ISS/emulator counters, or
  RTL cycles; explain which pairings and requested constraints lack comparable
  evidence. Replays compare each backend's declared observables: deterministic
  model cycles where defined, and separately reported timing distributions for
  actual host/device execution.

<a id="ax-04"></a>

- [ ] **AX-04 — Independent experiment-alpha pilot.** Provide a fresh-checkout
  walkthrough for M0, then record two reproductions by people other than its
  implementer. Each participant runs the comparison, changes a declared choice,
  explains one tradeoff and one evidence limitation, and replays another record.
  Include native-only execution without RISC-V/RTL/FPGA tools and the paired
  native/RTL fixture with its declared prerequisites. Both paths are required.
  Record prerequisite/setup, build, and interaction time separately, every
  failure, and any help required. Target a first comparison within 15 minutes
  after prerequisites; do not hide compilation time. Close after the target is
  met and blocking friction is fixed, or document a deliberate revised target
  and its reason before repeating. Lack of participants leaves this gate open;
  an implementer's walkthrough is preparation, not independent reproduction.

<a id="ax-05"></a>

- [ ] **AX-05 — External-component SDK and conformance example.** Package one
  small out-of-tree implementation with a manifest, parameter documentation,
  compatibility scope, license/attribution, profile, and runnable conformance
  checks. A fresh checkout must select it without editing the generic SoC or
  resolver, run its own correctness evidence at default and non-default values,
  and reject an unsupported combination. Document the boundary's compatibility
  and migration rules. Include an inventory of supplied replacements' supported
  modes and evidence gaps; close the existing Component discipline inventory
  item only when its full coverage criterion is met. This is an SDK example,
  not a claim that every external component is interchangeable or verified.

<a id="ax-06"></a>

- [ ] **AX-06 — Reproducible preview release preparation.** Name a supported
  subset of profiles, workloads, and public contracts; define compatibility,
  deprecation, and known limitations for that subset. Prepare a versioned
  release manifest binding source, tool requirements, component/profile inputs,
  software artifacts, and optional loader images to their hashes and evidence.
  Rebuild and replay on a clean supported host, recording any nondeterministic
  artifact differences. Keep loader and payload identities separate. Include
  onboarding, release notes, and failure/recovery instructions. Existing CI and
  scheduled checks for the chosen revision must have reviewed results, including
  skips/failures; any advertised physical image needs HW-01 evidence for that
  identity. Generated assets are packaged outside tracked source. This gate
  prepares a release; publication is a separate action.

<a id="ax-07"></a>

- [ ] **AX-07 — Experiment regression gate.** Add a small deterministic AX-02
  experiment and its comparison eligibility checks to the existing verification
  manifest. Check exact oracle outputs and declared cycle/size regression rules
  at matched identities; noisy host wall time is diagnostic rather than a CPU
  performance gate. Prove detection with an injected wrong result, stale input,
  and a threshold breach. Version thresholds in the owning experiment and
  explain baseline changes. Record stage cost; leave costly sweeps to an
  appropriate scheduled suite. The supported preview subset must be covered.
  Include native adapter conformance independently of the RTL toolchain. Treat
  host benchmark timing as a measured distribution; do not give it the exact
  cycle assertions reserved for deterministic RTL fixtures.

<a id="ax-08"></a>

- [ ] **AX-08 — Browser experiment handoff.** Reuse the existing WASM machine
  and AX-01/AX-03 records to open a documentation example or shared experiment,
  run it, and export a record native tools can replay. Preserve profile and
  payload identity, oracle outputs, and cycle counts across both paths. Reject
  malformed, incompatible, oversized, or stale records without silently choosing
  another machine. Validate the rendered interaction with an actual browser;
  headless machine checks alone do not close it, and missing dependencies are
  reported as blocked/skipped. Close the existing live-documentation and URL
  replay items only when their respective browser cases pass. The native
  experiment workflow remains usable without a browser or hosted service.

<a id="ax-10"></a>

- [x] **AX-10 — Native CPU and RTL execution adapters.** Implement the
  [execution-target boundary](execution-targets.md): capability/limit discovery,
  implementation preparation, bounded execution/cancellation, result collection,
  and identity-aware replay. Select adapters through their owning manifests or
  profiles without requiring native workloads to instantiate a fake SoC or
  board. Run the AX-01 integer workload as a native executable and as an RTL
  implementation against the same independent oracle, including overflow and
  tail cases. The native path must build/run with only its declared host tools
  in an environment without RISC-V, RTL, or FPGA tools. Reject unsupported
  semantics, stale artifacts, and unavailable prerequisites explicitly; test
  cancellation/timeout and a non-default limit. Record compiler/runtime/target
  identities and metric applicability. Keep FPGA activation and recovery under
  their existing shell authority. Neither a native Verilator binary nor WASM
  simulation counts as the native software implementation.
  Evidence: `make experiment-run` over both shipped plans, with records in
  [`research/experiments/records/`](../research/experiments/records/);
  `make experiment-replay RECORD=...`; and `make adapter-check`, which proves
  the native leg builds and runs with every RISC-V, Verilator, and FPGA tool
  shadowed by a stub that fails on sight, an absent tool blocks rather than
  selecting another target, a scalar beyond the engine's 17-bit immediate is
  refused before execution, a limit and a cancellation both reach the process
  group, a non-default limit reaches the record, and a replay rejects a changed
  artifact hash. That native-tool claim is proved by shadowing rather than by a
  container: the tools stay installed on the machine, and the check fails if
  the native path invokes one.

<a id="ax-11"></a>

- [ ] **AX-11 — Software/hardware co-design experiment.** Represent compiler
  choices, algorithm/layout variants, runtime policy, and machine parameters as
  separately identified candidate inputs. Start with two compiler configurations
  on one native workload; hold logical semantics and inputs constant and pass
  the independent oracle. Exercise a compiler-setting change that invalidates
  result reuse and record executable/library/tool identities. Then combine one
  software dimension with one compatible hardware/profile dimension, retaining
  controls that show which change caused each result. Close with a replayable
  comparison, correctness failures excluded, and an explicit tradeoff or null
  result. Do not require a new compiler IR or change aXos to run host workloads.

<a id="ax-12"></a>

- [ ] **AX-12 — ISS and emulator execution adapters.** Connect existing aXsim
  and QEMU entry points to the AX-10 contract. Run a shared supported software
  fixture with exact expected behavior, pin ISA/ABI/machine requirements, and
  reject unsupported role, privilege, or device requests before execution.
  Record each model's counter semantics separately from host duration and RTL
  cycle counts. Test missing-tool reporting, bounded execution, and failed-run
  replay. A missing emulator leaves its gate open rather than quietly selecting
  the ISS; the existing three-platform checks retain their original scope.

<a id="ax-13"></a>

- [ ] **AX-13 — External accelerator execution adapter.** Select one actually
  accessible GPU or other compute device/runtime through a capability survey.
  Provide an out-of-tree-selectable adapter using the workload contract, with
  independent implementation and device/compiler/runtime identities. Run exact
  integer oracle cases, check unsupported formats and unavailable devices, and
  separate transfer, synchronization, warmup, execution, and readback costs.
  Record repeated measurements and recovery/cancellation behavior. A CPU
  fallback must be separately identified and cannot close device execution.
  Close with reproducible results on the named device or leave the physical
  execution gate blocked. Selecting this backend does not add a vendor-specific
  dependency to other targets or imply mutable hardware configuration.

## Reference computer

- [x] RV32IM five-stage reference core with Zicsr, M/S/U privilege modes, and
  Sv32 translation.  Evidence: `make -C sim/unit test`,
  `make -C sim/cosim test`, and `make -C formal check`.
- [x] Golden ISS, Verilator lock-step harness, official ISA-suite integration,
  and randomized instruction/paging generation.  Evidence:
  `make -C sim/axsim test`, `make -C sim/testgen fuzz`, and
  `make -C sim/testgen paging`.
- [x] aXbus reference interconnect, UART, CLINT, boot ROM, finisher, BRAM,
  delayed memory, reference cache, SDRAM, and SPI/SD paths compose through
  checked-in profiles.  Evidence: `make component-test`.
- [x] Bare-metal image runs on ISS, QEMU, and RTL.  Evidence:
  `make -C sw/baremetal check-hello check-timer check-preempt`.
- [x] aXos boots through the selectable scheduler, VM, storage, and SD-boot
  services.  Evidence: `make -C sw/kernel kernel-component-test
  QEMU=/path/to/qemu-system-riscv32`.

## Component discipline

- [x] Selectable source implementations live under `components/`; `rtl/`
  contains generic synthesis flow and architecture signposts rather than a
  second source tree.
- [x] Profiles validate their chosen components.  Evidence:
  `make config-check-all`.
- [x] Stock component seams remain deliberately lenient so an out-of-tree
  implementation can replace a CPU, memory, peripheral, board, harness, or
  aXos service without copying the reference implementation.
- [ ] Every non-reference component must provide its own compatibility claim
  and verification evidence; selection alone never grants reference-machine
  verification status. Inventory each supplied replacement's supported modes,
  required companions, exercised parameter values, and missing evidence.
  Close this item when every replacement links to its own runnable checks and
  unsupported combinations are rejected or explicitly reported as unverified;
  a reference-core result must not fill another core's formal or privilege gap.
- [x] A non-reference functional unit demonstrates the swap-evidence path:
  `muldiv.fast-mul` passes the identical unit testbench, directed cosim, the
  rv32um ISA suite, and randomized fuzzing through the harness unit
  overrides.  Evidence: `make -C sim/unit run-muldiv-fastmul` and
  `make -C sim/cosim test rv32um
  MULDIV_SV=../../components/muldiv/fast-mul/muldiv.sv`.
- [x] A scalable core family demonstrates the seam at performance granularity:
  `core.ax2-{s,m,l}` is a dual-issue in-order superscalar RV32IM machine-mode
  core (block-RAM instruction cache, bundle BTB, 4R2W register file) sharing the
  reference core's decoder/immdec/branch-comparator.  Tiers differ only in issue
  width, cache size, and BTB depth.  Evidence: `make -C sim/unit run-suite-ax2`
  (every tier against the official rv32ui + rv32um binaries on the RTL — 49
  tests × 3 tiers × 3 wait-state settings — plus the directed programs) and
  `make -C sw/baremetal check-suite-ax2` (SoC integration: interrupts, fence.i,
  IPC, and the gpu1 role).  Measured 2.53× core.minimal and 1.60× core.pipeline5
  on the mixed workload; see [hardware-capabilities.md](hardware-capabilities.md).
  It implements machine mode with physical addressing only — no Sv32/S/U — so it
  does not carry the reference core's lock-step cosim evidence.
- [x] The dual-issue core carries its own bounded formal evidence, on both
  retire channels: `ax2_core` drives a two-channel RVFI trace (`nret 2`), and
  `make -C formal check-ax2` proves `insn_add`, `insn_beq`, `insn_lw`, and
  `insn_sw` against it — the memory instructions and `add` on channel 0 *and*
  channel 1, which is where dual issue can go wrong.  Scope is deliberately
  stated rather than implied: 7 of the 84 generated checks, in the RV32I
  configuration (`ENABLE_M=0`) with the predictor disabled (`BTB_ENTRIES=0`),
  so branch prediction and RV32M carry only the ISA-suite and directed
  evidence above, exactly as they do for the reference core.  It uses the same
  built-in SAT engine and needs no extra solver, but it does need more memory
  than a 3 GB development box — ax2's block-RAM instruction cache dominates
  model construction — so this runs in the `formal.yml` workflow rather than as
  a local default.  `make -C formal full-ax2` runs all 84.
- [x] A whole-CPU swap demonstrates the same seam at core granularity:
  `core.minimal` is a compact multi-cycle RV32IM machine-mode core (no MMU/S/U,
  reusing the reference decoder/ALU/mul-div/regfile) built as an accelerator
  host.  Evidence: `make -C sw/baremetal check-suite-minimal` — one suite that
  runs `core.minimal` driving the CPU (hello), the GPU role, and the TPU role.
  It ships in the `tangnano20k-gpu` and `ulx3s-85f-gpu` profiles (minimal host +
  GPU).  It now also carries its own bounded formal evidence: `core.minimal`
  drives a one-retire RVFI trace and `make -C formal check-minimal` proves
  `insn_add`, `insn_beq`, `insn_lw`, and `insn_sw` against it — the same four
  the reference core gates on, in the same RV32I configuration, and unlike the
  ax2 suite this one completes on a 3 GB development box.  Lock-step cosim
  remains out of scope: without Sv32 and S/U there is no privileged
  architectural state to compare against the golden ISS.

- [x] Memory-system components sized and shaped for real workloads:
  `cache.writeback` (direct-mapped, write-back, write-allocate, drain-on-flush)
  and `muldiv.radix4` (single-cycle multiply, 16-cycle divide), plus cache
  geometry exposed as the `cache_lines` / `cache_words_per_line` profile
  settings — the stock 256-byte cache was a composition smoke size, not a
  working one.  Evidence: `make -C sim/unit run-muldiv-radix4` (the same
  latency-agnostic unit testbench the reference divider passes),
  `make -C sw/baremetal check-suite-ax2`, and `python3 tools/bench.py render`
  (2.91× on a renderer-shaped workload, of which the write-back policy is
  1.55×).  `cache.writeback` carries a documented constraint: it must not be
  paired with a core whose fetch port writes memory (the Sv32 walker).
- [x] Tunable components rather than near-duplicate variants: a component is
  the unit of *architecture*, and a size within it is a build-time parameter.
  `core.ax2` and `role.gpu1` are each one component; `role.gpu-compute` absorbed
  its lane variants the same way.  Parameters are declared in the manifest with
  the defaults that define the baseline, overridden per profile by name, and
  validated — an undeclared parameter is a configuration error naming what the
  component does declare.  This replaced eleven components with three.  Evidence:
  `make config-check-all` and the parameter sweeps in
  `make -C sim/unit run-suite-ax2` / `run-suite-gpu1`.

## Userspace ABI

aXos has a scheduler, an allocator, a filesystem, and a shell, but no way to
*run a program*: there is no syscall ABI, no loader, and no C library.  Nothing
compiled from C can target it today, which is the gap between "the CPU can run a
real program" and "the system can host one".  Two findings from the render
benchmark make the gap concrete: the bare-metal link has no libgcc (so a 64-bit
divide is an undefined `__udivdi3`). The former fixed 128 KiB link limit is now
parameterized by `RAM_BYTES`, so small Tang payloads get a matching stack top
and a link-time capacity check.

**Decision: follow the RISC-V Linux ABI where one exists, and make every layer
of it replaceable.**  Standard numbers and a standard ELF entry contract mean an
unmodified newlib or picolibc can be retargeted onto it and a program written
for it is not written for atomiX alone; inventing our own would cost a libc port
and buy nothing.  Tweakability comes from the seams rather than from the
numbering: the syscall table is a selectable component, sizes on it are
parameters, and `0x1000+` is a reserved private range for calls with no Linux
equivalent (the accelerator role driver being the first).  The full contract is
[abi.md](abi.md).

Staged so each step has its own evidence rather than landing as one large jump:

- [x] **ABI contract documented.** [abi.md](abi.md) fixes the calling
  convention (`a7` number, `a0`–`a5` arguments, `a0` return, `-errno` on
  failure), the asm-generic syscall numbers, the ELF entry contract and initial
  stack layout, the errno subset, the private range, and what is deliberately
  omitted (signals, `mmap`, threads, `ioctl`).  It also records two corrections
  the current kernel needs: `SYS_FORK`/`SYS_WAIT` are neither Linux numbers nor
  Linux semantics (RISC-V has `clone` and `wait4`), and `SYS_CONSOLE_PUTC` is
  just `write(1, &c, 1)`.
- [x] **Syscall component and dispatch.** `syscall.linux-compat` implements the
  asm-generic table behind a `syscall` component seam, so what an `ecall` means
  is selectable while the kernel keeps owning the trap.  The component decides
  numbers and error convention; how a task forks, how the console is driven, and
  how a user pointer is validated arrive through `struct syscall_ops`, so
  replacing the ABI does not mean reimplementing the kernel.  `sstatus.SUM` is
  left clear and every syscall pointer goes through the new
  `vm_translate_user` seam, which is what makes `-EFAULT` real rather than
  hoped-for.  `sw/kernel/user.S` is now a hand-written conformance test
  (`-ENOSYS` for an unknown number, `getpid`, `-EFAULT` on a bad pointer,
  `-EBADF` on a bad descriptor, then fork/wait through `clone`/`wait4`).
  Evidence: `make -C sw/kernel check-boot` — passes on the ISS, QEMU, and the
  RTL — plus `check-role-driver`, `check-hostlink`, and
  `kernel-component-test`.
- [x] **ELF loader.** `loader.elf32` behind a `loader` component seam: parses
  ET_EXEC ELF32 RISC-V, maps each `PT_LOAD` segment with its own `p_flags`
  permissions, zero-fills the `.bss` tail, builds the System V initial stack
  (argc/argv/envp/auxv), and enters at `e_entry`.  Static executables only —
  `PT_INTERP` and relocations are rejected rather than half-handled.  It needed
  two supporting changes: `vm_map_user_page` for arbitrary user mappings, and
  page-ownership tracking in the Sv32 PTE software bit, because the previous
  fixed teardown leaked every page a loader mapped.  Evidence:
  `make -C sw/kernel check-boot` runs `sw/kernel/userprog/hello.c` — built as
  its own freestanding ELF and reaching the kernel only as a byte array — on the
  ISS, QEMU, and the RTL; it verifies `.data`, `.bss`, `.rodata`, and segment
  writability, and the exit path asserts every page is returned.  The pairing is
  confirmed as predicted: this runs on `core.pipeline5`, since `core.ax2` has no
  S/U or Sv32.
- [x] **C library.** `libc.axlibc`, behind a `libc` component seam: `crt0`
  reading the System V frame, syscall wrappers with errno, string/memory
  primitives, a first-fit `malloc` over `sbrk`, and a console `printf` subset
  (no floating point — there is no FPU).  libgcc is linked, so 64-bit
  arithmetic resolves; that was the undefined `__udivdi3` the render benchmark
  tripped over.  `brk` became real to back it: the kernel maps heap pages
  between the image and a one-page guard below the stack.  Evidence:
  `make -C sw/kernel check-boot` runs `sw/kernel/userprog/hello.c` — an
  ordinary C `main()` using malloc/free/calloc/realloc, strings, 64-bit
  division, and `printf` — on the ISS, QEMU, and the RTL.
- [x] **Filesystem binding.** `openat`/`close`/`read`/`lseek`/`fstat` are
  backed rather than `-ENOSYS`.  The descriptor table lives in the syscall
  component, because which small integer a program gets back and what its offset
  does are ABI decisions; the filesystem seam widened from "print this file to
  the console" to `fs_lookup`/`fs_size`/`fs_read`, so the shell's `cat` and the
  `read` syscall now go through one implementation instead of two that can
  drift.  The shell's private ramdisk moved into the filesystem component as a
  built-in read-only root, which is what a diskless profile mounts — without it
  "can a program read a file" would be testable only where there is storage.
  Deliberate limits, each recorded in [abi.md](abi.md): read-only through the
  ABI (`-EROFS`) and `lseek` implemented in its real 32-bit `llseek` shape
  rather than a simplified one that would work only with this tree's libc.
  Descriptor state is now isolated per task slot and copied on `clone`.
  Evidence: `make -C sw/kernel check-boot` (ISS, QEMU, RTL,
  built-in root) and `make -C sw/kernel check-storage` (the same program reading
  the same file off a real AXFS card over SPI).
- [x] **Evidence.** A compiled C program that allocates, opens a file, reads it,
  seeks within it, stats it, and prints runs on aXos through the loader — on the
  ISS, QEMU, and the RTL, and against both the built-in root and an SD card.
  Mutation-tested: breaking the read offset, the descriptor release, `SEEK_END`,
  or the diskless root each makes it exit with the specific code for the check
  that caught it.  The original bar is met.  What remains is scale rather than
  capability: raise the 128 KiB image ceiling and run something substantial
  enough to be a real test of the ABI rather than a demonstration of it.
- [x] **Persistent process sessions.** The resident supervisor shell is now an
  explicit saved/idle context rather than a one-way launcher. `exec`/`run`
  build a real `argc`/`argv` frame, a root-process exit releases its pages and
  returns to the prompt, non-zero status is reported without halting the
  machine, and repeated runs prove task/descriptor cleanup. `wait4` reports
  encoded child status and descriptor tables are isolated by task slot.
  Evidence: `make -C sw/kernel check-shell`, `check-boot`,
  `kernel-component-test`, `check-storage`, and `check-sdboot`, which covers
  the same behaviour on the SDRAM pin model.

- [x] **Segment permissions that are real rather than intended.**  The loader
  had always mapped each `PT_LOAD` with its own `p_flags`, and the linker script
  had always page-aligned the sections "so the loader can give each its own
  permissions".  Both were true and the guarantee still did not hold: `ld`
  assigns sections to segments by flag compatibility, so `.rodata` (`A`) was
  landing in `.text`'s `R+E` segment and being mapped **executable**, with the
  alignment buying nothing.  A segment, not a page, is the unit permissions come
  from.  Nothing caught it because nothing could: every behavioural test passes
  either way, since an executable `.rodata` reads exactly like a read-only one.
  `user.ld` now declares three segments explicitly and the image is `R+X`, `R`,
  `R+W`; `check_boot.py` asserts that structurally, and reports `R+X R+W` if the
  sections ever merge again.

  The loader also now enforces **W^X**, rejecting a writable-and-executable
  `PT_LOAD` instead of mapping it — 12 bytes of text, and it keeps `perms_of` a
  translation rather than a policy.  The rejection is tested end to end against
  a hand-built ELF whose only defect is its flags: correct magic, `ET_EXEC`,
  `EM_RISCV`, an in-range vaddr, and a payload of three real instructions
  calling `exit(0)`.  Mutation-tested, and the mutation is the point — with the
  check removed the image **loads, runs, and exits 0**, so the W+X page was
  genuinely mappable rather than theoretically so.  The fixture lives on the
  AXFS image rather than the built-in root, so testing a rejection costs the
  shipped kernel nothing.  Evidence: `make -C sw/kernel check-loader-wx` and
  `check-boot`.

- [x] **The ABI attacked rather than demonstrated.**  The item above closed with
  "what remains is scale rather than capability", and that was the wrong axis.
  `hello.c` is a *demonstration*: it does what a well-behaved program does and
  checks the answers.  `userprog/torture.c` passes what the kernel is supposed
  to refuse — null and kernel-space pointers to every pointer-taking syscall, a
  buffer straddling the last mapped page, a read into a read-only page, an
  unterminated path, a full descriptor table, seeks that overflow a signed
  32-bit offset — and requires the *documented* error rather than merely "not a
  crash".  It runs from the AXFS image, so none of it costs the shipped kernel a
  byte.  Evidence: `make -C sw/kernel check-abi-torture`.

  It found three real defects on its first two runs, none of which any existing
  test could see:

  1. **`malloc` overflowed where `calloc` did not.**  `calloc` had always
     checked its multiply, with a comment calling it "the classic way this
     function becomes a bug"; `malloc` checked nothing.  `malloc(0xfffffff0)`
     computes `HEADER + want` as exactly **0**, and `sbrk(0)` is a *query* that
     returns the break and never `-1` — so the allocation appeared to succeed
     and a header claiming 0xfffffff0 bytes went into the free list.  Every
     later `malloc` then found that block big enough and handed out overlapping
     memory.  Silent heap corruption, not a failed allocation.  `realloc` had
     the same wrap in its `align_up` fast path.
  2. **`brk` had a ceiling and no floor.**  The shrink path unmaps and frees
     every page between the requested address and the current break, with no
     lower bound, so `brk(0x40000000)` unmaps the program's own text, rodata and
     data and the task faults on its next instruction fetch.  The task struct
     carried `brk_limit` and nothing at the other end; it now carries
     `brk_start`, set by the loader where it puts the heap.
  3. **`clone` never copied the heap bounds at all.**  `sys_fork` cloned the
     address space and then left `brk`/`brk_limit` at whatever the reused task
     slot held — zero for a fresh slot.  A child's `brk(0)` therefore returned
     0, making every `sbrk` in the child report `ENOMEM`: a forked child that
     could not allocate, for no stated reason.

  The first is a userspace bug and the other two are kernel bugs, which is
  itself the argument for the program: one adversarial consumer crosses seams
  that per-component tests do not.

- [x] **`clone` made to work for a loaded program at all.**  Extending the
  program above to fork found that it never had.  Four defects, each hidden
  behind the one before it:

  1. **`sys_fork` asserted one program's stack contents.**  It required
     `child->user_stack[0] == 0x51a00001`, a marker the hand-written assembly
     fixture writes, and called `test_finish(1)` otherwise — so any *other*
     program calling `clone` halted the machine.  The check is now guarded by
     the same `expect_fork_markers` flag that gates the fixture's other
     assertions.
  2. **Three more halts on the same path.**  Running out of task slots, out of
     pages for the kernel stack, or failing the address-space clone each called
     `test_finish(1)`.  Forking more times than there are slots is trivially
     reachable from userspace, so a program could stop the machine instead of
     getting an error.  They now return `EAGAIN`, `ENOMEM` and `ENOMEM`.
  3. **`vm_clone_user_space` was hardcoded to the fixture's memory layout.**  It
     allocated exactly one page and installed it at `user_pt[1]`, which is where
     `vm_create_user_space` puts the stack: code at index 0, stack at index 1.
     A loaded ELF has its text at indices 0 *and* 1 and its stack at 1023, so
     the clone overwrote the second page of the child's **text** with a copy of
     the parent's stack, and shared everything else.  The child executed
     whatever that page then held, took an undelegated trap, and
     `machine_trap_bad` in `trap.S` stopped the machine without a message —
     which is why this cost several bisection rounds to find.  Sharing was the
     other half of the same bug: `PTE_OWNED` was copied along with the PTEs, so
     both address spaces claimed the same physical pages and would have freed
     each one twice at exit.  Clone now walks the leaves and gives the child a
     private copy of every owned page, whatever the layout.
  4. **A 32-page ceiling on bootable RAM.**  `page_allocator_self_test`
     recorded every free page in a fixed `void *pages[32]` and halted the
     machine when there were more — so aXos could not boot with over 128 KiB of
     free RAM, and the symptom was a dead board rather than a message.  Found
     by raising `AXOS_RAM_BYTES` so that cloning an address space had room at
     all.  The array is now a sample size; exhaustion is tested by chaining the
     pages through themselves, at any pool size.

  The torture program now forks, checks the child sees the parent's heap bounds
  and can allocate, requires that neither a `.data` nor a heap write in the
  child is visible in the parent, fills the task table and requires `EAGAIN`,
  then reaps and forks again.  Evidence: `make -C sw/kernel check-abi-torture`,
  which links at 1 MiB precisely because the 128 KiB default leaves about
  fifteen free pages — too few to clone an address space.

- [x] **The ABI's three layers checked against each other.**
  `sw/kernel/check_abi_contract.py` requires every errno the kernel can return
  to be nameable by axlibc and published in [abi.md](abi.md), and every
  dispatched syscall number to appear in its table.  It found `ECHILD`, which
  `wait4` returns and which existed only in the kernel's private header: a
  program got `errno = 10` with no name for it.  `EAGAIN` was added the same
  way when `clone` gained a resource limit, and the check is what required it
  to reach all three layers rather than one.  It runs in under a second with no
  toolchain, so it gates the RTL run rather than the other way round.

Both opening questions are settled in [abi.md](abi.md): the ABI is the RISC-V
Linux subset, and the loader takes ELF directly rather than a pre-flattened
image — in both cases because it is what the toolchain already produces, and
deviating would cost work without buying capability.

## Configurability the build actually honours

The first goal of the project is that a user can replace the parts that matter
to them.  Three of the bugs above were the same failure of that goal: a literal
standing in for something a profile should decide.  `void *pages[32]` capped
bootable RAM at 128 KiB, `user_pt[1]` assumed one program's page-table layout,
and `0x51a00001` assumed one program's stack contents.  Each was invisible
until something changed the configuration.

- [x] **Every capacity is a knob, and every knob is wired, bounded and
  exercised.**  The mechanism mostly existed; what was missing was the last
  wire and any check that a knob did anything.

  *Wired.*  `sw/kernel/Makefile` now converts `COMPONENT_DEFINES` to `-D` the
  way `rtl/fpga/Makefile` already did.  Until it did, the syscall component's
  `max_fds`, `path_max`, `write_max`, `io_chunk` and `role_max_payload` — each
  declared in its manifest with a default and a `doc`, each resolved by
  `configure.py` — were dropped by the kernel build.  Setting one in a profile
  changed nothing and reported nothing.  `loader.elf32` now declares `arg_max`
  the same way, and the shell's own argument limit follows it rather than being
  a second literal that can disagree.  `TASK_SLOTS` is a profile *setting*
  rather than a component parameter, because the scheduler and the VM both only
  index what they are handed — it belongs to no single component.

  *Bounded.*  Settings were free-form: an unrecognised key became a make
  variable nothing read, so `task_slot` for `task_slots` silently kept the
  default and the profile appeared to work.  `configure.py` now carries a
  `SETTINGS` registry with types and ranges, rejects an unknown key (suggesting
  the near-miss), and rejects an out-of-range value.  Relationships *between*
  knobs are `_Static_assert`s beside their definitions, which is the only place
  that knows them: `TASK_SLOTS >= 2` because fork needs a slot for a child,
  `KERNEL_PROCESS_ARG_MAX <= LOADER_ARG_MAX` because the shell must not accept
  more arguments than the loader can place.

  *Exercised.*  `configs/kernel-small-caps.json` sets 2 task slots, 3
  descriptors, a 12-byte path limit and 3 argv entries, and
  `make -C sw/kernel check-abi-torture-small` runs the adversarial ABI program
  against it.  The program derives every limit from the same `-D` the kernel was
  built with — repeating them would be the same bug one level up — and asserts
  *exact* counts, so it forks `TASK_SLOTS - 1` times and no other number.
  Mutation-tested: re-hardcoding `TASK_SLOTS` in the kernel makes the
  small-capacity run fail while the default run still passes, which is what
  makes the two runs together evidence rather than a pair of green ticks.

  Two build defects surfaced while proving this, both the same shape.  User
  programs are compiled with the profile's capacities but were written to one
  `userprog/` directory, so switching profiles silently reused the previous
  profile's binary against the new kernel; and because make compares against a
  *different* `.mk` file per profile, switching back did not rebuild either.
  Artifacts are now keyed by profile, as `rtl/fpga` already keys bitstreams.

  The embedded program's *name* was also a literal, in two places that had to
  agree: `kernel.c` would only run a program called `hello.elf`, and the shell
  repeated the string as its default.  Both now read one define the Makefile
  derives from the embedded ELF's own filename.

  A third defect of the same shape hid inside that derivation and is worth
  recording, because it was green everywhere it could be.  `CPPFLAGS` is
  simply-expanded, so `+=` expands a reference on the line it is written on,
  and the define was written 36 lines above `USER_ELF` — every kernel compiled
  with `-DAXOS_EMBED_USER_NAME='""'`.  The `#ifndef` fallback in
  `include/process.h` cannot help: an empty `-D` is still a definition.  The
  result is a clean build whose embedded program answers to no name, so
  `run hello.elf` and `exec hello.elf` return ENOENT.  Nothing that inspects
  headers, symbols, or capacities could see it; only the two checks that read
  a shell transcript did, which is the argument for keeping transcript-level
  checks in CI at all.  The define now sits below `USER_ELF` and an empty value
  is a parse-time `$(error)` rather than a build that lies.  Evidence:
  `make -C sw/kernel check-shell` and `make -C sw/kernel check-boot`
  (the latter on ISS, QEMU, and RTL).

- [x] **Build identity across configuration switches.** The audit found the
  defect it was looking for. The kernel's outputs live at one set of paths
  under `build/` whatever profile and personality produced them, and the ELF's
  only configuration prerequisite was a per-profile `.mk` that was already
  older than it — so `make images KERNEL_CONFIG=A` followed by `=B` produced
  **byte-identical images**. A profile switch was a no-op that reported
  success: the same defect `check-sdboot` had at the hardware end, a selection
  that never reached the build. Two smaller ones came with it: `boot-disk`
  passed its own `KERNEL_CONFIG` to a sub-make that could not see it, and the
  hand-maintained header prerequisite list had already drifted
  (`include/console.h` was never in it, so editing it rebuilt nothing).

  Fixed by a build-identity stamp — profile path, kernel mode, storage and
  host-link personalities, block size, linked RAM envelope, compiler,
  architecture, and every resolved define — rewritten only when its content
  changes, so it forces a rebuild exactly when one is needed and never
  otherwise. Headers are now `$(wildcard include/*.h)`; `boot-disk` forwards
  the profile.

  `make -C sw/kernel check-build-identity` is the regression, and it is an
  A → B → A → C → A walk: six profiles and personalities, each built into a
  scratch tree that has never held another *and* into the shared tree after it
  held something different, with the two required to agree — a clean reference
  is what stops two builds that are stale in the same way from agreeing with
  each other and proving nothing. All six images must differ, and each must
  boot as itself on the ISS with its own exit code: the shell transcript for
  the three profile variants, silence and a failure exit for the storage
  personality with no card, `AXRD` and the instruction bound for the host-link
  personality, the monitor banner for the 32 KiB console. Tool selection is
  checked through the stamp rather than by building, so it does not need LLVM
  installed. The simulator half covers the case the kernel half cannot reach —
  a profile *edited in place*, same path and name — and proves the resolved
  parameter reaches the model by whether the progress counters appear. The
  check was confirmed to fail, naming the defect, with the stamp dependency
  removed. It runs in the `smoke`, `ci-quick`, and `nightly-integrated` suites
  and takes 78 s.

  Payload reuse is unchanged and still covered by
  `make -C sim/soc check-runtime-payload`: a software-only payload change
  reuses its model rather than rebuilding one.

## Documentation that cannot go stale silently

Prose drifts quietly; a diagram drifts *loudly* and still ships, because a
mermaid block with a typo renders as an error box on GitHub and no ordinary
build looks at it.  These items exist so the documentation carries the same
kind of evidence the machine does.

- [x] Fourteen mermaid diagrams across six documents (DESIGN.md 7,
  partial-reconfig 2, research-checklist 2, README, abi, components 1 each)
  are structurally checked on every run: unterminated fences, a missing or
  misspelled diagram type, a `class` naming a style that was never defined or a
  node that does not exist, and labels holding characters mermaid parses as
  syntax unless quoted.  Evidence: `make diagram-check` — no toolchain and no
  network, which is what makes it the check that runs every time, as the
  `diagrams` stage of `ci-quick` and `nightly-integrated`.
- [x] The structural check has been calibrated against the real thing rather
  than trusted: all fourteen blocks parse under `mermaid.parse()` itself, run
  through `jsdom` because `@mermaid-js/mermaid-cli` needs a headless Chrome.
  The reproduction is in [workflow.md](workflow.md); it needs Node and one npm
  install, which is why it is a documented route rather than a CI stage.
- [x] Diagrams are legible in both GitHub themes.  Node fills are 20% alpha
  tints of their stroke rather than opaque pastels, so the reader's own page
  colour shows through and whichever label colour the theme picked stays
  readable — measured at 8.9:1 or better against white *and* `#0d1117`, where
  the opaque fills they replaced left light-on-light at 1.4:1.  Strokes are
  mid-tones clearing 3.1:1 on both.  No `%%{init}%%` block, so nothing pins one
  theme.
- [x] Brand assets are derived, not maintained in parallel.  One master lockup
  carries the sample data; the square mark, the static mark, and the print
  lockup are cut from it, so the family cannot drift apart and a derived file is
  never hand-edited.  Evidence: `make brand-check`, as the `brand-assets` stage
  of `ci-quick` and `nightly-integrated`; `make brand` regenerates.  Details and
  the physics the mark is sampled from are in
  [docs/assets/README.md](assets/README.md).

- [x] **Evidence names the machine that ran.** A verification summary used to
  record what was *asked for* — stage id, command, exit code, duration — and
  nothing about what answered. `org.atomix.verification-result.v2` records the
  machine: each stage may declare the profiles it exercises, and the runner
  resolves them itself and stores the resolved name, core, memory, cache, role,
  harness, simulation top, board, scheduler, every setting, and the full define
  list. Alongside them the suite records its environment once — Verilator,
  Yosys, RISC-V GCC, clang, QEMU, make, node and Python versions, the host, the
  git revision, and whether the worktree was clean, which is recorded rather
  than refused because running a suite against a working tree is the normal
  case and it is the reader who needs to know.

  Three ways such a record could lie are now checked by
  `python3 tools/verify.py self-test`, wired into `make verification-check`,
  which runs a synthetic suite built to go wrong. A stage whose tool is not
  installed is `blocked`. A stage naming a configuration that does not resolve
  is `failed` **before its command runs** — proven by a marker file the command
  would have created — because a result labelled with a machine nothing could
  build is worse than no result. And the stages a suite asked for but never
  reached are now recorded as `not-run` instead of vanishing: a suite that
  stopped at stage 3 of 10 used to write three results and a failure, with
  nothing saying the other seven were never attempted. Every outcome is
  counted and printed by name — `outcomes: blocked=1, not-run=2, passed=1` —
  so a pass is never read off a list whose length changed.

  Payload hashes stay where they are already exact: the per-experiment records
  under `research/` and `sim/soc/build/runtime-payload-evidence.json` hash the
  loaded image rather than the ELF, which is not byte-reproducible across
  rebuilds of identical sources.
- [x] **Coverage gaps remain visible.** A document that lists ninety-six
  commands is making ninety-six claims, and until they are written down an
  advertised command with no home looks exactly like one CI has been running
  all along. `tests/coverage-map.json` is the inventory and
  `make coverage-map REPORT=1` prints it; every `make` invocation in a shell
  block of [workflow.md](workflow.md) must be accounted for as one of five
  things, and the first three are checked rather than believed: `suite` (a
  verification stage runs it — verified against the manifest), `via` (an
  aggregate a stage runs lists it — verified by reading the rule), `workflow`
  (a GitHub workflow runs it — verified by reading the workflow), `manual`
  (nothing runs it: must say what it needs *and* where its failure would
  surface), and `informational`. A newly advertised command with no entry
  fails, which is what keeps the inventory true; both that and a `via` claim
  whose aggregate stopped listing its target were confirmed to fail.

  Writing it down found six advertised checks that nothing ran, all of them
  cheap and all now in a suite: `check-memory`, `check-loader-wx`,
  `check-abi-torture` and `check-abi-torture-small` as the new
  `kernel-hardening` stage, `check-hostlink-stream` folded into `kernel-shell`,
  and `check-gpu-tpu` into `baremetal-accelerators`. `pr-gate-check` joined
  `research-contracts`. The count is now suite=53, via=7, workflow=3,
  manual=22, informational=11.

  **Bounded formal coverage** is recorded the same way, derived rather than
  described: `tools/formal_coverage.py` reads the check lists in
  `formal/Makefile`, the ISA, proof mode, retire channels and depths in each
  core's `.cfg`, and the `ENABLE_M` each RVFI wrapper elaborates, and compares
  the result against `research/formal-coverage.json`. All three cores prove
  **4 of 37 RV32I instructions** — `add`, `beq`, `lw`, `sw` — at depth 12,
  bounded-model-checking mode, on retire channel 0, and on ax2 also channel 1
  of its two-wide bundle. What is excluded is recorded with it: RV32M is not in
  the proved design at all (`ENABLE_M(1'b0)`), misaligned memory is excluded by
  `RISCV_FORMAL_ALIGNED_MEM`, no check proves a CSR access, trap, or privilege
  transition, and `bmc` means no counterexample *within the depth* rather than
  none at all. `make -C formal check-all` is explicitly not treated as
  evidence: it is the three targets the weekly workflow actually runs, and
  narrowing or widening any of them fails the record until it is rewritten
  deliberately — confirmed by removing one check and watching it fail.

  Both run in `make verification-check`, which the `verification-contract`
  stage runs in `smoke`, `ci-quick`, and `nightly-integrated`.

## Hardening: what is checked without running the machine

The verification above answers "does it do the right thing on the inputs we
thought of".  These three answer the other question.

- [x] **Static analysis over every language, with nothing silently skipped.**
  `make static-analysis` runs Verilator lint across *every* profile in
  `configs/` (23 elaborated designs -- a component only some unbuilt profile
  selects is still linted), GCC's `-fanalyzer` over 42 freestanding translation
  units with each unit's own build flags, cppcheck over the host C++, ruff over
  ~9,000 lines of Python that nothing was checking at all, and shellcheck.  An
  analyzer whose tool is missing reports SKIPPED with the reason and exits
  non-zero; it never counts as a pass.  Findings carry a `content-addressed`
  label when they land in a file a record under `research/` pins by SHA-256,
  because fixing one of those needs the owning experiment re-sealed and that is
  a decision, not a cleanup.  Rule selection is in `ruff.toml` and is
  deliberately narrow: defects only, no style, because a linter that reports
  import order beside an undefined name teaches you to skim past both.

  "Nothing silently skipped" needed one repair to be true, found while closing
  SDRAM gate 4. `sim/soc`'s `lint` passed the BRAM/delayed top's elaboration
  parameters to whatever top a profile selected, and the pin-level SDRAM top
  declares none of them, so Verilator refused to elaborate every pin-level
  profile. Its refusal carries no `file:line`, so the finding parser saw
  nothing and the sweep counted those profiles as clean: the SDRAM harness top
  and its device model had never been linted at all. The parameter list now
  belongs to the selected top, and a lint that exits non-zero while producing
  no parsed finding is itself reported as `lint-did-not-run` -- a lint that
  could not run is not a lint that passed. Both pin-level profiles now lint
  clean, and the guard was confirmed to fire by stubbing that refusal in for
  all 23 profiles.
- [x] **The findings live in an issue, not a log, and not on the critical path.**
  `.github/workflows/analysis.yml` runs **nightly**, not on push and not on a
  pull request.  A static-analysis finding is not the same kind of thing as a
  failing test: it is usually a judgement call, sometimes a false positive, and
  occasionally something whose fix would invalidate a content-addressed evidence
  record.  None of that belongs between a change and `main`; overnight means a
  finding arrives with time attached to it.  It uploads SARIF to code scanning,
  and `tools/analysis_issue.py` keeps one issue in sync -- edited in place,
  commented on only when the finding *set* changes, closed when the report comes
  back clean.

  **The sanitizer reports go to the same issue.**  An ASan, LSan or UBSan report
  is the most actionable thing here and the easiest to lose in a log, so
  `tools/fuzz_report.py` parses all three -- and libFuzzer's own verdicts --
  into the same findings schema the static analysis emits, and the workflow
  hands both reports to the issue tool.  Each finding carries the file, the
  line, the allocating or faulting function, and the command that reproduces
  *that* kind of finding rather than a generic one.  One issue reused rather than one per run, because the failure mode
  of this kind of automation is a repository nobody can read.
- [x] **Grey-box fuzzing of the largest untrusted-input surface.**
  `make fuzz-loader` drives `loader.elf32` -- the component that parses an ELF
  arriving from an AXFS image or a UART upload -- under libFuzzer.  The harness
  models the page allocator and VM seam and asserts what the kernel depends on:
  no W+X mapping, no read outside the image, and no success return whose entry
  point or stack pointer is unmapped.  It runs under AddressSanitizer,
  LeakSanitizer and UndefinedBehaviorSanitizer, which answer different
  questions: heap corruption with a stack trace, a page the loader mapped and
  lost, and a misaligned load or signed overflow in a parser reading
  attacker-controlled offsets.  A hardware guard page sits alongside ASan rather
  than instead of it -- the image is copied to the end of a mapping whose next
  page is `PROT_NONE`, so an overread is deterministic where ASan would not
  poison an `mmap`'d region, and ASan is what turns the fault into a report
  naming a line.  Verified rather than assumed: injecting `image[size]` faults
  at the guard address.  **It found a real defect on its first run**, at 15,158 executions:
  the loader checked that `e_entry`'s page was mapped but not that it was
  executable, so an image entering rodata was accepted and the task died on its
  first instruction fetch instead of being rejected.  Fixed by tracking whether
  `e_entry` falls in a `PF_X` segment; the crashing input is checked in at
  `sim/fuzz/corpus/` so it is replayed on every run.
- [x] **White-box coverage of what the fuzzing actually reaches.**
  `make fuzz-coverage` replays the corpus under source-based instrumentation
  and reports the loader's own line and branch coverage: **93.60% of lines and
  81.11% of branches**.  "We fuzzed the parser" is a claim about effort; this is
  the claim about reach, and it is what says whether a corpus is exercising the
  rejection paths or bouncing off the magic check.
- [x] **A second compiler, as a defect finder rather than a migration.**
  `TOOLCHAIN=llvm` builds target code with clang and lld instead of GCC;
  `make toolchain-llvm` builds the kernel that way and runs it.  GCC remains the
  default and the toolchain every recorded size and fmax number was measured
  with -- the point is that two front ends see different things.  It earned its
  place on the first build, reporting an unused `static inline` in
  `sw/kernel/console.c` that GCC 10 does not warn about (GCC treats such a
  function as potentially used; clang does not).  It was genuinely dead and is
  gone.  It also made clang's own analyzer possible against the real target
  rather than a host approximation: `clang --analyze` at
  `--target=riscv32-unknown-elf` is now one of the static-analysis passes, and
  is a different engine from GCC's `-fanalyzer` rather than a second opinion
  from the same one.

  **One toolchain compiles every component.**  `RISCV_CC`, `RISCV_OBJCOPY` and
  `RISCV_STRIP` are now the only way target code is built anywhere in the tree,
  and `HOST_CXX` follows the same knob.  That took fixing: `sim/unit`,
  `sim/testgen` and `sim/livefpga` hardcoded `$(RISCV_PREFIX)gcc`, so an LLVM
  build produced a kernel from clang and directed regressions from GCC -- a
  configuration nobody selected, in which a defect only one front end emits gets
  attributed to the wrong one.  The compiler runtime is the deliberate
  exception: `libgcc.a` and `libclang_rt.builtins` are prebuilt support archives
  for arithmetic the ISA lacks, not components, and clang's own
  `--rtlib=libgcc` is the default on most Linux targets for the same reason.
  LLVM's `compiler-rt` is preferred when present; on Ubuntu 22.04 the clang
  package ships none for bare `riscv32`, so the build falls back to GCC's.

  Stated because it will otherwise be rediscovered: **clang 14 emits about 45%
  more text than GCC 10 here** -- 68,791 bytes against 47,220 for the default
  kernel.  The page pool is whatever RAM is left after the image, so at the
  default 128 KiB a clang-built kernel boots and runs correctly but leaves too
  few free pages for the ABI tests to allocate, and `sbrk` fails.  The same
  kernel passes at 256 KiB.  That is a size difference and not a
  miscompilation, which is why GCC stays the default rather than clang being
  called broken.
- [x] Extend binary-format parser regression testing to the remaining inputs.
  AXFS on-disk metadata and the AXK1 UART upload envelope are now covered by
  the production parser sources under libFuzzer, ASan, LSan, and UBSan.  AXFS
  runs both raw framing rejection and a reachable-directory pass, then
  exercises each parsed extent through `fs_read`.  AXK1 substitutes only the
  UART, RAM, and handoff hardware seams, so its actual ROM code processes raw
  byte streams and valid/corrupt envelopes through magic resynchronization,
  bounds, copy, CRC, and retry handling.  Evidence: `make fuzz-loader`; use
  `make -C sim/fuzz explore-axfs` or `make -C sim/fuzz explore-axk1-format`
  for an unbounded run.  The host-link service's request parser is also covered
  through its production source with only the byte pipe, role execution seam,
  and terminal action replaced: raw/partial requests, valid role frames, each
  status translation, oversize payload draining, resynchronization, and BYE
  all run in memory.  Use `make -C sim/fuzz explore-hostlink-format` for a
  longer run.  The loader was first because it is in the kernel's boot path and
  its input has the most complex framing; the other paths use the same technique
  against a smaller code path.

## Change-ready checklist

Use this for a substantive implementation or interface change:

- [ ] Link the owning board card and acceptance gate. Record the user outcome
  or research decision, and update its state and evidence when the gate closes;
  a completed implementation slice does not automatically close its parent.
- [ ] Update the component manifest and profile validation if source selection
  changes. For every new capacity, trace its owner, default/documentation,
  build define, bounds, and a test at a non-default value.
- [ ] Update the architecture/contract document at the affected boundary.
- [ ] Run the narrow unit or simulator test, then the relevant composition
  check and `make verify-smoke`; run formal after core/RVFI changes. When
  kernel composition or state budgets change, recheck the exact 32 KiB Primer
  fit gates for independently selected small, mid, and large evolution tiers.
- [ ] Record any new tool, timing, capacity, or hardware assumption in
  [dependencies.md](dependencies.md) or the appropriate board guide.
- [ ] Update [workflow.md](workflow.md) when a milestone adds or changes a
  build, test, or deploy command, or a build knob or profile users run.
- [ ] Run `make diagram-check` after touching a diagram and `make brand` after
  touching the master logo — never hand-edit a derived asset.
- [ ] Keep physical claims separate from simulation and synthesis claims.
- [ ] Check that an evidence command's *output* still says what the item claims,
  not just that it exits zero: a check whose external dependency is missing may
  skip the comparison the item rests on.

## Platform expansion

- [x] Role-MMIO contract: fixed 64 KiB window at `0x4000_0000` with
  `ROLE_ID`/`VERSION`/`DOORBELL`/`STATUS` header, selectable `role`
  components (`role.none` shell default, `role.loopback` proof), and a
  bare-metal driver path.  Evidence: `make -C sw/baremetal check-role` and
  `make component-test`.
- [x] First real accelerator role, TPU-lite (folded 24-MAC int8
  GEMM), attached behind the role window.  Evidence:
  `make -C sw/baremetal check-tpu` (verifies plain, accumulate, and ReLU GEMM
  jobs against an on-core reference and prints the role-versus-CPU cycle
  counts).
- [x] Second real accelerator role, GPU-compute (an 8-lane SIMT vector engine
  with a straight-line kernel ISA, per-lane register files, flat global memory,
  and tail-thread predication), sharing the same doorbell/descriptor driver
  model.  Evidence: `make -C sw/baremetal check-gpu` (verifies saxpy, fused
  multiply+ReLU, and a masked-tail reduction-style kernel against an on-core
  reference and prints the role-versus-CPU cycle counts).
- [x] Scalable accelerator role family, `role.gpu1-{s,m,l,xl}`: the SIMT engine
  rebuilt around **banked global memory** (NBANKS interleaved block RAMs behind
  a lane→bank crossbar with round-based conflict serialisation) and a real
  control ISA (structured IF/ELSE/ENDIF divergence, uniform and any-lane
  branches, compare-set, integer divide, cross-lane shuffle, displaced
  addressing).  Banking is what makes lane count worth scaling: the previous
  single-port engine gained only 1.18× going from 8 to 16 lanes, where gpu1
  gains 1.69–1.82× per doubling and is 2.70× the old engine at equal lane count.
  Geometry is published in a CAPS register, so one driver and one oracle serve
  every tier.  Evidence: `make -C sim/unit run-suite-gpu1` (all four tiers
  against a C++ interpreter of the ISA, including the maximal-bank-conflict and
  worst-case-serialisation kernels that pin the store-ordering invariant) and
  `make -C sw/baremetal check-gpu1` (the same battery driven on-core through the
  shell window).
- [x] aXos in-kernel role driver: the management kernel (not a bare-metal test
  program) discovers the role through the window device-mapped into its S-mode
  address space and drives a job end-to-end from the resident shell — the first
  piece of the shell control plane, on which the host-link service will sit.
  Evidence: `make -C sw/kernel check-role-driver` (the `role` command discovers
  and drives `role.loopback` through the RTL shell).
- [x] Role-window isolation, the decoupling boundary a live role swap needs.
  `axroleiso` sits between the address decoders and the role, with its control
  register at `0x1002_0000` — in *shell* space, because a register inside the
  window it fences is unreachable at exactly the moment it is needed.
  `ISO_CTRL.ISOLATE` holds `valid` low into the role, answers the bus with
  ready/zero/no-error, and masks the role's completion line so fabric in an
  unknown state cannot storm the PLIC with a level-sensitive source nothing
  will clear; `ISO_CTRL.ROLE_RESET` holds the region in reset so rewritten
  fabric starts defined.  Two decisions are load-bearing rather than
  convenient.  Isolation is immediate and unconditional: the role it protects
  against is the one that has stopped answering, so a fence that waits for an
  in-flight transaction to retire deadlocks on the failure it exists to
  contain — quiescing stays the driver's job one level up.  And an isolated
  window reads as zero because zero is already `ROLE_ID`'s "no role present"
  encoding, so an isolated role is indistinguishable from `role.none` and
  re-running discovery after a swap needs no new software path.  Out of reset
  the fence is transparent, so a profile that never writes the register behaves
  exactly as it did before it existed.  Evidence: `make -C sim/unit
  run-axroleiso`, whose central case holds a role's `ready` low forever — what
  half-configured fabric looks like from the bus — and requires the bus to
  complete anyway once fenced, plus the decode, IRQ-masking, reset, and
  restore-after-de-isolation cases; `make -C sw/baremetal check-role` and
  `check-role-irq` and `make -C sw/kernel check-role-driver` confirm the
  unfenced path is unchanged.
- [ ] Partial reconfiguration of the role region on a live bitstream —
  research staged in [partial-reconfig.md](partial-reconfig.md); no
  capability claim before its stage-4 board evidence.  Stage 1 (ECP5
  place-and-route to a `.bit`) now passes at 28.42 MHz against the 25 MHz
  constraint; it had never run before 2026-08-01 because the board `.lpf`
  wrapped `SYSCONFIG` with a backslash continuation nextpnr does not accept.
  Stage 2 is also measured: `make -C rtl/fpga pr-delta` builds `role.none` and
  `role.loopback` at the same seed and finds 8,603/13,294 CRAM frames changed
  across every one of the 85F's 126 frame groups, proving unconstrained P&R
  perturbs the whole shell rather than a plausible role region.  The same run
  exposed the next prerequisite: upstream `ecppack --delta` has a hard-coded
  45F address encoder and refuses to emit an 85F partial bitstream, so stage 3
  now includes validating the 85F frame-address map before shell locking.
  The track runs on ULX3S/ECP5 and not the Primer in hand because `ecppack`
  ships `--delta` and `--background` while the open Gowin flow has no partial
  path at all.  Stage-3 tool research can continue without hardware, but the
  stage-4 live-load gate is deliberately deferred until an ULX3S is acquired
  or temporarily available; it is not part of the current Primer board plan.
- [x] Host-link control plane (base): a framed request/response protocol
  ([host-protocol.md](host-protocol.md)), an aXos host-link service that
  dispatches frames to the in-kernel role driver above, and the host-side
  `axhost` driver — a host PC discovers the role and runs a job on it over the
  link, end-to-end in simulation through the virtual-pipe (console byte-pipe)
  transport.  Evidence: `make -C sw/kernel check-hostlink`.
- [x] Per-role host-link job opcodes: `TPU_GEMM` (folded int8 GEMM) and
  `GPU_RUN` (an uploaded SIMT kernel over a flat data buffer) on the same frame
  format, backed by in-kernel TPU-lite and GPU-compute drivers.  A host PC drives
  all three real accelerators over the link, each checked against a host-side
  reference.  Evidence: `make -C sw/kernel check-hostlink` (loopback, TPU-lite,
  and GPU-compute profiles).
- [x] Fast runtime accelerator switching: `GPU_LOAD` replaces resident
  microcode independently of `GPU_EXEC`, so synthesis, P&R, bitstream loading,
  and aXos reboot are outside the normal module/benchmark loop. Evidence:
  `make -C sw/kernel check-primer-runtime` loads and verifies SAXPY and
  polynomial kernels in one 32 KiB aXos/RTL session; the nine-instruction
  switch frame is 42 UART bytes (about 0.46 ms in the 921600-baud runtime
  profile).
- [x] Software-as-runtime-payload invariant (was "kernel-as-runtime-payload";
  the loader never cared which). Immutable UART ROM accepts a length-bounded
  CRC-32 `AXK1` frame into blank RAM and starts any compatible payload —
  `uart_boot()` copies bytes to `0x8000_0000` and jumps, so a bare-metal game
  is the same kind of thing to it as an aXos personality.  **No software change
  may cause synthesis or P&R, and no program may become part of a bitstream's
  identity.**  This is an invariant rather than a convenience because the Gowin
  flow *can* bake the payload into the netlist, and when it does, every program
  carries its own placement, timing, and hash: `role.tpu-lite` has already
  stopped fitting because of a software-side change, and every earlier board
  claim silently re-opens.  Baking is now reserved for first bring-up of a
  profile that has no loader image, and is labelled as such wherever it
  appears — including in `DESIGN.md`, `AGENTS.md`, and both skills, so a future
  change cannot reintroduce the coupling by accident.  The shipping path is
  `configs/tangprimer25k-runtime.json` (loader-only: `role.none`, blank RAM,
  reset at the ROM), built by `make fpga-loader-primer` and fed by
  `make load PROGRAM=<name>`.  Evidence: `make -C sw/kernel check-uartboot`
  rejects corrupt/oversized uploads and boots the full kernel;
  `make -C sw/baremetal check-snake-loader` boots from the ROM into blank
  32 KiB RAM, uploads the game as an `AXK1` frame, and requires the *identical*
  final checksum the baked image produces — which is what makes "a program is a
  payload, not a hardware revision" a tested statement; `make runtime-primer`
  uploads the compact host-link kernel before its two-program accelerator test.
  The deterministic seed-3 loader-only Primer image routes at 29.30 MHz for a
  25 MHz constraint (18,417 LUT4, 3,853 DFF, 44 BSRAM, 3 DSP). Its immutable
  ROM physically accepted the kernel and the resulting aXos session completed
  the two-program `FAST SWITCH PASS` gate.
- [x] Kernel-mediated userspace role ABI: `role_info`, token-returning
  `role_submit`, and retry-safe `role_wait`, using the same checked job
  encodings as the host link. The physical role window remains supervisor-only
  through a dedicated Sv32 alias, and device polling is bounded. Evidence:
  `make -C sw/kernel check-role-driver` (resident shell plus U-mode loopback
  job) and `make -C sw/kernel check-boot` (safe role absence on ISS/QEMU).
- [x] Put the boot ROM in block RAM.  The loader bitstream used to cost about
  1,534 LUT4 more than a baked one (15,425 against 13,891 on the same profile),
  essentially all of it the 4 KiB ROM: `axrom` read combinationally, and an
  asynchronous read cannot map to a Gowin BSRAM, so 1,024 words became a LUT
  ROM.  That gave a profile near the device limit a real reason to refuse the
  decoupling, which would have left the invariant true only where it was free.
  `axrom` now carries the same `SYNC_READ` parameter `axram` does, and
  `soc_top` passes the board's value through, so the read and its completion
  are registered and the array infers BSRAM.  With no write port at all it is a
  0W2R memory, and both read ports map onto the same two initialised blocks
  rather than a duplicated bank each.  The cost is one wait state on ROM
  fetches, paid only while the loader itself is executing.

  **The registered ROM is scoped to profiles that reset into it**, via
  `localparam ROM_SYNC_READ = (RESET_PC == ROM_BASE) ? SYNC_READ : 0` in
  `soc_top`.  That is not a tuning knob, it is the fix for a regression this
  change caused: a profile resetting at RAM carries a baked payload and never
  fetches a ROM word, and with no `ROM_INIT_FILE` the asynchronous ROM
  optimises away completely while the registered one leaves a handshake behind
  and re-rolls packing.  The effect was erratic rather than uniform — measured
  at −252 LUT4 on `cpu`, −51 on `tpu`, but **+427 on `morph-1pe`** — and it was
  enough to push both `role.tpu-lite` (78% LUT4, 85% BSRAM) and `role.morph`
  (87% LUT4) off a legal placement at seed 1, turning two locked `expect: pass`
  rows into failures.  Both place at HEAD.  Deriving the condition in RTL rather
  than plumbing a build flag means no profile can set it wrong and no Makefile
  variable can drift from the config that selects it.

  Evidence (P&R, seed 1, one build at a time, OSS CAD Suite yosys 0.67+102 /
  nextpnr-0.10-105): with the scope in place every baked profile is
  *bit-identical* to HEAD — `cpu` 13,844 LUT4 / 3,138 DFF / 36 BSRAM /
  31.76 MHz, `tpu` 17,637 / 3,720 / 48 / 31.74, `morph-1pe` 18,660 / 2,706 /
  24 / 33.65, each matching field for field — while `tangprimer25k-runtime`
  keeps 13,387 LUT4 / 38 BSRAM / 31.21 MHz, against 15,425 / 36 / 29.72 locked
  before the ROM moved.  That is −2,038 LUT4 and +1.49 MHz on the shipping
  image, and it is **457 LUT4 below the baked `cpu` image it replaces**.
  Evidence (simulation): `make -C sim/unit run-axrom` builds the ROM in both
  read timings from one testbench and requires the same contents, the same
  out-of-range/misaligned/below-base errors, and the same refusal to be written
  — one wait state apart; `make -C sw/baremetal check-snake-loader` now runs at
  the board's `SYNC_READ=1` rather than the simulator default, boots from the
  registered ROM into blank RAM, uploads snake, and still reaches the baked
  image's exact `checksum=0xd824f761`.  `make -C sw/kernel check-uartboot`
  continues to cover the combinational timing.
- [x] Finish decoupling every Primer profile that fits the device from its
  payload.
  `tangprimer25k-{ax2,gpu,tpu}` baked `hello`/`gpu_perf`/`tpu` into synthesis,
  so their board evidence named a program and a change to that program
  re-opened the claim.  Each now has a loader variant retaining the matching
  component selection and capacity; `reset_pc` declares a runtime profile, and
  `rtl/fpga/Makefile` derives blank RAM and a correctly sized UART ROM from it,
  so the profiles name no payload at all:
  `tangprimer25k-runtime-ax2.json`, `-runtime-gpu4.json` (the 4-lane minimal
  host of `-gpu.json`; `-runtime-gpu.json` was already taken by the 1-lane
  reference-core aXos platform, which is a different machine), and
  `-runtime-tpu.json`.  `make fpga-loader LOADER_CONFIG=<profile>` builds any
  of them and refuses a profile that does not reset into the ROM; all three are
  sweep presets (`runtime-ax2`, `runtime-gpu4`, `runtime-tpu`).
  Re-locked from a full 11-profile sweep on 2026-08-12
  (`research/benchmarks/tangprimer25k-synth-2026-08-12.json`, seed 1,
  `--jobs 3`, OSS CAD Suite yosys 0.67+102 / nextpnr-0.10-105).  Routed, each
  loader against the baked row it replaces:

  | pair | baked | loader | loader cost |
  |---|---|---|---|
  | `cpu` → `runtime` | 13,844 LUT4 / 36 BSRAM / 31.76 MHz | **13,387 / 38 / 31.21** | **−457 LUT4**, +2 BSRAM |
  | `gpu` → `runtime-gpu4` | 16,892 / 40 / 33.91 | 17,721 / 42 / 30.56 | +829 LUT4, +2 BSRAM |
  | `tpu` → `runtime-tpu` | 17,637 / 48 / 31.74 | 18,403 / 50 / 32.75 (seed 2) | +766 LUT4, +2 BSRAM |
  | `ax2` → `runtime-ax2` | fails, 111% LUT4 | fails | — |

  **Every Primer profile except `ax2` can now run the loader.**  On the shipping
  profile it is *cheaper* than a single-program image, so decoupling is no
  longer a cost to justify; on the 4-lane GPU and the TPU it costs about 800
  LUT4 out of 23,040, and the TPU actually routes 1.01 MHz faster.

  Two caveats belong with those numbers.  `runtime-tpu` is **placement-fragile**:
  it fits at 80% LUT4 / 89% BSRAM but legalises on only one seed in five
  (FAIL/PASS/FAIL/FAIL/FAIL for seeds 1–5), so the profile pins `pnr_seed: 2`
  as `-runtime-gpu` pins 3.  Nothing there is over capacity — 50 block RAMs and
  24 multipliers must land in fixed GW5A columns, and that is what runs out.  A
  failure is a re-rolled placer, not a size regression, so re-seed rather than
  shrink; but the pinned seed is not guaranteed to survive an unrelated RTL edit.

  `ax2` is excluded by arithmetic, not luck, and no seed can help: **25,569
  LUT4 against 23,040 (111%)**.  Measured by parameter, the 64-entry BTB costs
  5,820 LUT-family cells and the second issue slot 8,086, against a 19,286-cell
  single-issue baseline.  The BTB is 64 × 60 = 3,840 bits read combinationally
  at two indices (`look_idx` at fetch, `upd_idx` at retire) with a write port,
  so it maps to a LUT register file at ~1.5 cells per bit.  It cannot take the
  `SYNC_READ` treatment that fixed `axram` and `axrom`: `ax2_icache.sv` needs
  the lookup combinational "so the prediction is available in time to choose the
  *next* fetch address", and registering it would cost the bubble the predictor
  exists to avoid.  **`core.ax2` is therefore a profile for a larger part**, and
  is kept in the Primer baseline only to record how far over it is — shrinking
  it to fit would mean dropping prediction, which changes what it measures.  Its
  loader variant fails for the same reason and says nothing about the loader.
  Measuring AX2 properly needs a bigger board (the ULX3S-85F is the obvious
  candidate); until one is in hand, both rows stay locked `expect: fail`.
  The runtime CPU profile independently raises its UART baud for host-managed
  use, and runtime TPU pins its documented seed; those are operational profile
  choices, not payload coupling.  Current profile resolution passed for all
  four runtime variants on 2026-09-04 (`make config-check CONFIG=...`).
- [x] Chunked host-link buffer transfer: `GPU_WRITE`, `GPU_LAUNCH` and
  `GPU_READ` move a job's data through the role window in frame-sized pieces,
  so a job's size is bounded by the accelerator's own memory rather than by
  what one request frame and one kernel stack frame can hold.  The staged
  `GPU_RUN`/`GPU_EXEC` encoding is unchanged and still carries whole jobs that
  fit a frame.  This also closes a knob that read as configurable and was not:
  `role.gpu-compute` declares 4096 data words and a host could address 200 of
  them, because the kernel's cap was a literal.  It is now the `role_data_words`
  profile setting, bounded 64..15360 — the window's own geometry — with no
  default at all: a profile that does not declare it does not get the chunked
  ops, which are not compiled and answer `BAD_OP`.  A capacity the build was
  never told is not one to guess, and guessing high is the dangerous direction,
  because `role.gpu-tpu` presents the same `"GPUC"` identity over a quarter of
  the memory and a role answers an access past its buffer with a bus error.
  Two neighbouring knobs moved to their real owner in the same change:
  `role_max_payload` left `syscall.linux-compat`, because the syscall
  component's `role_submit`, the role dispatcher and the host-link service all
  stage the same encoded job through the same `role_execute` and the host-link
  personality contains no syscalls at all; and the staged path's 200-word bound
  became `role_staged_words`, since it sizes `role_execute`'s stack arrays
  rather than the accelerator.  Both relations between them are `_Static_assert`
  beside their definitions, which is the only place that sees both.
  Evidence: `make -C sw/kernel check-hostlink-stream` runs the same SoC profile
  three times, changing only what the kernel profile declares, so what it
  proves is the kernel's bound and not the role's.  4096 declared streams a
  768-word job and rejects word 4096; 1024 declared — with the payload cap at
  320 and the staged cap at 48, all three off their defaults — bounds at
  exactly 1024 on that same 4096-word hardware and rejects an 80-word chunk
  against the 79 a 320-byte frame allows; a profile declaring nothing answers
  `BAD_OP` on all three ops.  The host derives every limit from the profile and
  from the header's defaults and probes the device for its own edge, so no
  number appears in the test; re-hardcoding any of them fails a leg.  The host side is a new `sw/host/axstream.py`
  rather than an extension of `axhost.py`, because `axhost.py` is pinned by
  seven `research/live-fpga/` records including physical Primer evidence, and
  `make registry-check` is what caught the attempt to edit it.  `make -C sw/kernel check-hostlink` and
  `make -C sw/kernel check-primer-runtime` are unchanged, and no synthesis is
  involved, so no board claim moves.
- [ ] **Asynchronous host-link completion — no hardware required.** Specify
  submit/poll/fetch ownership, bounded outstanding work, completion tokens,
  timeout/reconnect behavior, and compatibility with the shared userspace
  `role_submit` dispatcher before changing blocking `GPU_LAUNCH`. Close with
  RTL/host tests for successful completion, busy rejection, stale tokens,
  duplicate fetch, timeout, and recovery, while existing blocking and chunked
  transfers still pass. Any queue capacity must be wired to its owning profile
  or component and exercised at a non-default value.
- [ ] **Concurrent console and host-link — hardware-dependent.** Define the
  transport contract in simulation first, then select a second byte pipe and
  explicit board pins when the required hardware is available. Closure needs
  simultaneous console traffic and host jobs without corruption or loss of
  recovery access, plus fresh P&R and SRAM-board evidence for the changed
  hardware profile. Preserve the existing single-pipe profile.
- [ ] **Prebuilt hardware-image selection — staged research.** Define a
  registry artifact class binding a bitstream to its device, immutable shell,
  profile, toolchain, and verification evidence, separately from payload
  identity. Offline tests must reject mismatched and stale images. Physical
  activation depends on an available board and supported load mechanism;
  full reload and partial loading must retain distinct recovery contracts.
  This does not grant the Primer a partial-reconfiguration capability.
- [x] PLIC/role interrupt integration.  The shell's PLIC (`plic.qemu-virt`:
  per-source priority, enable, threshold, claim/complete, level-sensitive
  gateway) arbitrates two sources — UART receive and role completion.  Every
  role drives a level-sensitive `irq` line held for exactly as long as
  `STATUS.DONE` stands, so completion can be waited on instead of polled and
  clearing DONE is what deasserts it; `role.none` ties it low, so a profile
  with no accelerator still presents a defined source.  Evidence:
  `make -C sim/unit run-plic` (the register contract, priority/threshold
  gating, lowest-id tie-break at equal priority, and the level-sensitive
  re-arm — a source still asserted at complete becomes pending again) and
  `make -C sw/baremetal check-role-irq` (end to end on the RTL: the job is
  started with the source masked to prove nothing reaches the core, then
  routing it delivers, and the CPU parks in `wfi` so it can only finish
  through the interrupt).  Both are mutation-tested: reverting the source to
  tied-low no longer builds, and a role that never asserts fails with the
  specific check that caught it.
- [x] The interrupt reaches aXos, not just a bare-metal program.  The PLIC now
  has two targets at the QEMU-virt strides — context 0 is hart 0's machine
  context and context 1 its supervisor context — and the reference core gained
  an `irq_s_external` input that drives `mip.SEIP`, so `mideleg` bit 9 delegates
  the interrupt and the S-mode kernel claims and completes with no M-mode round
  trip.  That is what keeps the same driver code running on QEMU, whose `virt`
  machine wires context 1 the same way.  `role_wait_done` sleeps in `wfi`
  instead of polling `STATUS`, closing the test-and-sleep race by dropping
  `sstatus.SIE` around it.  Deliberate limits, stated rather than implied: a
  syscall runs with interrupts masked, so the userspace `role_submit` path
  still polls rather than having interrupts re-enabled underneath a
  half-finished syscall; and the ISS models no PLIC, so it falls back to
  polling through the same recoverable-probe path the role window already uses.
  Evidence: `make -C sw/kernel check-role-irq` — two consecutive shell jobs
  reporting `irq=2 polled=0`, so the completions arrived as interrupts and
  `STATUS` was never read.  Mutation-tested: dropping the PLIC COMPLETE lets
  the first job pass and hangs the second, which is why the check runs two.
  `make -C sim/unit run-plic` covers the second context directly (independent
  enable, threshold, and claim state; a source claimed by one context stops
  being pending for the other).
- [x] The machine idles instead of spinning.  Two halves, and neither works
  alone.  In the core, `wfi` stopped retiring as a nop and now holds the
  pipeline until `mip` is nonzero — *pending*, not enabled, which is what the
  spec asks for and what lets a masked device wake the hart; the illegal case
  (`wfi` below M-mode with `mstatus.TW`) is excluded so it still traps rather
  than deadlocking.  In aXos, the shell's console stopped polling the UART's
  line status: the 16550's `irq_rx` was already wired to PLIC source 1 and
  simply unused, so the handler now drains bytes into a ring and the shell
  parks between keystrokes.  A full ring masks the source rather than dropping
  bytes — the UART's holding register then stops the sender, and completing a
  source the handler cannot quiet would re-trap and starve the only context
  able to make room.  Deliberate limits, measured rather than assumed: the
  driver spins until an interrupt has actually been delivered once, because
  routing a source is not proof it is the *right* source — aXos also runs on
  QEMU's `virt`, whose PLIC numbers devices differently, and parking on that
  assumption hung the cooperative profile.  The host-link personality stays
  polled: it streams framed binary with no idle to reclaim, and an interrupt
  handler on that path starves the poller of a one-byte register.  Arming a
  tick to guarantee a wake was tried and rejected — the M-mode shim re-arms at
  a fixed 2000 cycles, so a tick started for the console also fires through
  every shell command, and `exec` and the AXFS write/readback both overran
  their bounds.  Evidence: the shell's `console` command reports
  `irq 21 polled 0 stalls 0` after a session, so input demonstrably arrived as
  interrupts; `cpu_idle` leaves the core, the SoC, and both simulation tops so
  a board can gate a clock on it and a simulator can stop paying for cycles in
  which nothing can happen; and in the browser the same interaction that used
  to accumulate 10.1M cycles now costs 52,672 with the counter *stopping* at an
  idle prompt.  Cost, stated: the fork demo went from 492,933 cycles to
  504,702, because a hart that parks resumes on the next tick instead of
  spinning straight through — the bound in `check_boot.py` moved from 500,000
  to 700,000, which had only 1.4% margin and could no longer tell "slower" from
  "hung".
- [x] The interrupt map has one authority rather than a copy per consumer.
  Which device is which source, and which context carries which privilege, is
  declared once as `PLIC_SRC_*`/`PLIC_CTX_*` localparams in the selected `soc`
  component; `tools/gen_irq_map.py` derives the C header the bare-metal runtime
  and aXos both include, and the shell indexes its `sources` vector by id rather
  than concatenating it, so bit order cannot encode the numbering a second time.
  The generator validates that source ids cover `1..PLIC_SOURCES` and context
  ids `0..PLIC_CONTEXTS-1` exactly, so adding a source without bumping the count
  fails the build naming the problem instead of leaving the top source silently
  unreachable.  Evidence: renumbering the shell's sources and rebuilding moves
  every software tree with it — `check-role-irq` passes with role on source 1
  and no C file changed — and the three drift cases (extra source, duplicate id,
  shell with no map) each exit nonzero with a specific message.
- [ ] Evaluate A or C ISA extensions only when their enabling need is explicit;
  neither is required for the current single-hart reference machine.

## One shipped game per board

Every board atomiX supports ships with at least one playable game, built from
the same component/profile machinery as everything else.

**The class of game is a design choice scaled to the board, not a fixed
requirement.**  A game does not imply a screen.  A terminal game played over
the UART is a real game, and on a board with no video pins and no spare block
RAM it is the *right* game — not a consolation prize.  Boards with a display
and memory to back it earn a framebuffer or a tile engine.  The commitment is
one playable thing per board, chosen to fit what that board actually is.

The point is not decoration.  A game is the only workload that forces the
platform to be honest about what a research SoC can otherwise avoid forever: a
program that stays responsive, reads input as it arrives, holds state across
turns, and is judged by a person rather than by a checksum.  Every accelerator
result so far answers "did the output match".  None answers "is this pleasant
to use".

It is also what makes the platform trustworthy to someone arriving for the
first time.  A newcomer with a supported board should be able to load an image
and *play something* within minutes, on the hardware they already own, without
buying a display adapter or reading the RTL.  That is the difference between a
platform someone believes works and one they have to take on faith.

### Tiers, by board capability

The class of game is chosen from what a board can actually spare, and each tier
demonstrates something the tier below cannot.

- **Minimal tier — turn-based, for boards where fitting a CPU is already the
  achievement.** A 9K-LUT class part has no headroom for a live display loop
  once the SoC is in. One key per turn, redraw on change, a few kilobytes of
  payload. 2048 sits here: 7,695 bytes, and it proves the platform is real on
  parts where nothing else would fit.
- **Interactive tier — real-time, for a 25K-class part and up.** Continuous
  redraw on a frame clock rather than on keypress, non-blocking input, and a
  live panel in the manner of `htop`: score, frame time, cycles per frame, free
  memory. This is the tier that proves the machine stays *responsive*, which a
  turn-based game never has to. The Tang Primer 25K is an interactive-tier
  board and now ships one — snake, 9,556 bytes at 12 fps — with 2048 kept as
  the minimal-tier example for smaller parts. Building it also found the tier's
  real constraint, which is not the CPU: at 115200 baud a byte is 2,170 cycles,
  so what a frame *sends* dominates what a frame costs, and the differential
  redraw is what makes the tier reachable at all.
- **Framebuffer or tile tier — for boards with display pins and memory.**
  A 320x240x8bpp framebuffer is 76.8 KiB against roughly 126 KiB of BSRAM on a
  GW5A-25A with 24-48 of 56 blocks already spoken for, so this tier belongs to
  boards with external memory. ULX3S carries HDMI and audio that no manifest
  exposes yet.

### Web parity, so the comparison is honest

Every shipped game must also run in the browser build, booting the same payload
through the WebAssembly Verilated model. That is what turns a game into a
measurement: the same binary, the same SoC, one instance on real silicon at
25 MHz and one on a laptop, side by side. It answers the question a newcomer
actually has — *what is this supposed to feel like, and what does the hardware
cost me?* — instead of asking them to take a cycle count on trust.

The harness is already most of the way there. `sim/web/tb_soc_wasm.cpp` holds a
UART input **queue** rather than a single register, and inverts control
specifically so bytes can arrive from keyboard events; `boot.mjs` exposes
`send(text)`, and `sim/web/public/app.js` has bound keydown to it since the
console landed — the page was never output-only. The two things that were
missing are now separable: its terminal could not follow a game's cursor (fixed
below), and the model is roughly fourteen times slower than the board, which no
amount of page work changes. **Parity is therefore not free here, and claiming
it would be dishonest**: a real-time game is the first workload where a laptop
cannot keep up with a 25 MHz FPGA, and that finding is worth more than the
side-by-side screenshot the section was written to justify.

### Checklist

- [x] Define a `terminal` contract for games: how input arrives, how the screen
  is addressed, and how a game is packaged, so a second game needs no new
  platform work and a game runs unchanged on any board with a UART.  Evidence:
  `sw/baremetal/include/term.h`.  Input is polled single-byte reads from the
  16550 with blocking and non-blocking forms; the screen is a character grid
  addressed with ANSI escapes, so it needs no hardware support at all; a game
  is an ordinary bare-metal payload selected with `PROGRAM=<name>`.  The
  interactive tier grew it a second half rather than a second contract:
  absolute addressing (`term_goto`), a frame clock over `mcycle` with work,
  overrun, and worst-case accounting (`term_frame_*`), a counted output path
  so a game can report what a frame costs in bytes, and stack-painted free
  memory (`term_mem_*`).  The claim that a second game needs no platform work
  held on the way in: snake added nothing outside `term.h` and its own file.
- [x] Ship a terminal-tier game.  Evidence: `sw/baremetal/examples/game2048.c`,
  3,596 bytes of image plus 76 of state (the 7,695 recorded here previously was
  the size of the ASCII `$readmemh` file, not of the program — a distinction
  that matters now that a payload's budget is its size), and
  `make -C sw/baremetal check-game2048`, which replays a fixed
  key sequence through `UART_INPUT_FILE` and asserts the exact final state
  (`score=164 checksum=0x243eb403`).  Determinism is the point: a game that
  cannot be replayed cannot be regression-tested.  The Tang Primer image builds
  and routes at 13,891/23,040 LUT4 (60%), 36/56 BSRAM.  The 26.17 MHz recorded
  here is nextpnr's post-*placement* estimate rather than its routed number:
  rebuilding the identical design for snake reports 26.17 MHz at that stage and
  31.76 MHz after routing, and the routed figure is the one every other profile
  in this tree quotes.
- [~] Play 2048 on the Tang Primer over the Dock UART.  The image is built and
  routed; the board detached from USB/IP before the play session, so this line
  stays open until a transcript exists.
- [x] Ship an interactive-tier game for the Tang Primer.  Evidence:
  `sw/baremetal/examples/snake.c` (9,556 bytes of image, 1,652 of state) and
  `make -C sw/baremetal check-snake`.  It redraws on a 12 fps clock paced from
  `mcycle`, reads input without blocking, and carries the `htop`-style panel:
  score, length, level, frame time, work cycles against the frame budget,
  dropped frames, bytes sent per frame, and free RAM — measured by painting
  the gap between the image and the stack and scanning what survived, so it is
  memory the program has never touched rather than a link-map constant.
  Three things are load-bearing rather than decoration.

  *The redraw is differential.*  Repainting the 28x14 field costs about 4 KB
  the way it draws — an address escape per cell — which is 47 ms on the
  921600-baud loader profile and 370 ms at 115200, four frames to draw one.  A
  moving snake changes three cells, so a frame sends about 205 bytes and the
  panel reports it.  On the board the serial link, not the CPU, is what a frame
  costs, and the game deliberately does not know the baud rate: it reports
  cycles, which is the same fact without the assumption.

  *The check asserts responsiveness, not only state.*  It requires `drops=0` —
  no frame overran its budget — alongside the exact final checksum, and that
  checksum folds a per-frame trace rather than hashing the last picture,
  because a restart resets the game to something that owes nothing to what came
  before it.  The game takes at most one key per frame, which makes the key
  file a frame-by-frame tape; `sw/baremetal/make_snake_tape.py` plays a host
  model of the same rules to generate one that eats, levels up, pauses, dies,
  and restarts, and independently predicts the checksum the machine then
  produced (`0xd824f761`).

  *Scope, stated rather than implied.*  The simulated UART is a byte pipe with
  no baud rate, so `check-snake` proves the compute keeps its deadline and not
  that the game is playable; the frame's real cost is the link, and only the
  board can measure it.  The check also builds the game at a compressed frame
  clock, because at 12 fps one frame is 2.08M simulated cycles — the pacing
  mechanism is what it exercises, not the constant.  The worst frame used
  25,751 of its 50,000-cycle budget.

  *Delivery: the game is a payload, not a bitstream.*  It is uploaded to a
  board already running the loader image (`make load PROGRAM=snake`), and
  `check-snake-loader` proves an uploaded copy reaches the same state a baked
  one does.  Its budget is therefore size — 9,556 bytes against the loader's
  28,672-byte limit at 32 KiB — and not logic.  A baked build was made first,
  as a bring-up datapoint: 13,891/23,040 LUT4 (60%), 3,138 DFF, 36/56 BSRAM,
  0 DSP, 31.76 MHz at seed 1.  It matches the 2048 image to the LUT while
  `hello` on the same profile is 14,326, which is the coupling in one line —
  one hardware profile, three programs, three different placements.  That is
  the reason games do not ship this way.  No board session has run either
  image, so nothing here is a physical claim.
- [~] Make the browser build able to render a game.  The item as written was
  wrong about where the gap was: `sim/web/public/app.js` has bound keydown to
  the input queue since the browser console landed.  What was missing was the
  other direction — its terminal implemented exactly what the aXos shell emits,
  so it treated *every* `ESC[r;cH` as "home" and a game's differential redraw
  stacked into the top-left corner.  It now honours absolute addressing,
  ignores the cursor-visibility privates instead of printing them, and sends
  arrow keys as the `ESC [ A..D` the games already decode, so the page and the
  board agree on what a key means.  Stays `[~]` until a game is actually booted
  in the page: the browser has no automated check that covers the terminal, and
  the point below about frame rate has to be settled first.
- [ ] Decide what a real-time game in the browser is *for*.  Measured: the
  native model sustains 1.78M cycles/s on this host (5,302,972 cycles of
  `check-snake` in 2.98 s) and the browser is 0.94–1.37× native, against the
  25M cycles/s a 12 fps frame clock asks for — the shipped image would play at
  under one frame a second.  This is the first workload in
  the project where the browser is not a substitute for hardware — a boot or an
  accelerator job finishes either way — so either the page boots a
  browser-paced build (`TERM_CPU_HZ` is already the override that would do it)
  and says so plainly, or it does not ship a real-time game at all.  Do not
  publish a side-by-side that quietly compares two different frame clocks.
  Close the decision with a documented intended use, measured host rate, and
  either a clearly labelled browser pacing profile or an explicit deferral.
- [ ] Publish the side-by-side comparison: the same payload in the browser and
  on the board, with the frame-time panel visible in both, so the cost of real
  silicon at 25 MHz is shown rather than asserted.  Blocked on the decision
  above, and the finding has already turned the intended argument around: the
  interesting number is not what the FPGA costs against a laptop but that a
  laptop cannot keep up with a 25 MHz machine on this workload at all.
  Closure requires payload and loader hashes, the same input replay and
  simulated frame clock, exact final-state agreement, and both workload-cycle
  and wall-clock measurements. A differently paced browser build must be a
  separate result, not the comparison's baseline.
- [x] Write the "run a game on atomiX" guide, aimed at someone who has never
  built the project: load the image, open the port, play.  Evidence:
  [games.md](games.md).
- [ ] Only after the game/pacing decision, define `video`, `input`, and `audio` component kinds on role-
  window terms — identity register, geometry/mode registers, a framebuffer or
  tile aperture — so a board without sound selects `audio.none` rather than
  failing to build.  A game's source must not change when the board does.
  Close with documented discovery, format, buffering, ownership, and timing
  contracts, a headless/absent-device profile, and the same game exercised on
  two simulated selections without board-specific source branches.
- [ ] Add the physical pins to the manifests of boards that have the hardware,
  recording connector, voltage, and clock constraints from the board procedure.
  ULX3S HDMI/audio remains a design target until that board is acquired; close
  physical validation only on the exact connected board and peripheral.
- [ ] Ship the first graphical game on the board with the most headroom, so the
  contracts are shaped by the comfortable case before being squeezed. Select
  that board from measured fit/timing and hardware availability. Closure needs
  loader-based payload delivery, an exact RAM fit, deterministic replay,
  input/frame timing, and reset/reload recovery on the selected board.
- [~] Give every shipped game the same evidence treatment as a benchmark
  profile: a pinned baseline, and a deterministic timing measurement — frame
  time where there are frames, turn latency where there are not.

  The original wording said "a pinned resource baseline in the synthesis lock",
  and acting on it literally was a mistake worth recording: a locked LUT/fmax
  row per *game* only makes sense if a game is part of the bitstream, which is
  precisely the coupling that has to go.  A payload's budget is **size**, not
  logic.  So the locked row is the loader bitstream every payload runs on
  (`runtime` in the Primer sweep), and a game is gated on fitting the loader's
  `RAM_BYTES - 4096` limit — `check_payload_boot.py` reports an over-budget
  payload as its own specific failure rather than as a rejected upload.  Snake
  is 9,556 bytes against 28,672.

  Done: frame time is pinned by `check-snake` requiring `drops=0` and reporting
  `maxwork`.  Also fixed the more basic gap — neither game check ran in *any*
  suite, so both, plus the loader-boot gate, are now the `baremetal-games`
  stage in `ci-integration` and `nightly-integrated`; an unrun check is not
  evidence.  Open: 2048 has no turn-latency measurement, which is the same
  claim for a turn-based game.

**Explicit non-goal:** cycle-accurate reimplementation of existing consoles.
That is what the established FPGA retro community builds, it is a far larger
project than this one, and competing there would misrepresent what atomiX is.
atomiX offers a documented, replaceable RISC-V SoC to write *new* software for.

## Interactive exploration (next milestone)

The strongest thing about this project is also its least visible.  Three cores
with real cycle differences, a cache policy worth 2.91× on a renderer workload,
accelerator microcode that reloads in 0.46 ms — every one of those is a measured
claim, and seeing any of them costs an afternoon: install a toolchain, build
Verilator, resolve a profile, run a check, read a terminal.  Nobody evaluates a
project that way, so the evidence persuades only the people who already stayed.

Two measurements make a different shape possible.  A cold boot to an aXos shell
prompt is 29,634 cycles, and the Verilated model sustains roughly 1.2M cycles/s
on a developer machine, so booting an entire computer costs about **25 ms** —
less than a page repaint.  A shell `role` command, including the accelerator
job and the interrupt that reports it, is 1,882 cycles (about 1.6 ms), and the
cost is linear: three of them are 5,647 cycles.  The model binary is 216 KB.

**Decision: make the unit of interaction a machine rather than a command.**  At
25 ms a boot is not something to wait for, which means a page can boot several
different machines while it renders — the same program on `core.pipeline5`,
`core.minimal`, and `core.ax2`, with three honest cycle counts beside each
other.  That is the argument for the component system, and it cannot be made in
prose or a screenshot.  It also fixes what the project is *for*: not another
RV32 SoC, but the place you go to change the machine rather than the program.

This is deliberately a reach-and-presentation milestone.  It adds no hardware
capability, and no claim here may substitute for the simulation, formal, or
board evidence above.

Staged so each step has its own evidence rather than landing as one large jump:

- [x] **Interactive sessions.** The Verilator harness keeps the console byte
  pipe open in both directions for the life of the process
  (`--uart-interactive`): stdin becomes UART receive and UART transmit is
  streamed as produced rather than buffered to the end.  Batch runs are
  unchanged and remain what every `check-*` target uses.  Because a session must
  outlive one command, building and launching are separate targets —
  `make -C sim/soc model-path` prints the model's path for a caller that spawns
  it once and keeps it open.  This is the prerequisite for everything below: in
  batch mode each exchange is its own process, so the machine reboots between
  commands and nothing carries over.  Evidence: a live session runs the shell's
  `role` twice and reports `irq=1` then `irq=2`, which is only possible on one
  continuous machine; `make -C sw/kernel check-role-driver` confirms the batch
  path still passes. The `check-sdboot` physical-SDRAM interpretation that this
  work exposed has since been corrected; see SDRAM gate 1 below.
- [x] **Runtime payload selection.** Both front ends can boot different
  programs on one compiled machine. The browser stages `/payload.hex` in its
  virtual filesystem; the native runner accepts `--ram-image <path>` before
  the first reset evaluation. Build with `model-path` and omit `RAM_INIT_FILE`
  to keep payload identity out of the model. Existing baked defaults still
  work, and a runtime image overrides them for one launch. The simulator-only
  initialization branch covers BRAM at both read timings and delayed memory;
  with the native runner's define omitted, the preprocessed memory text is
  unchanged from the previous implementation. Pin-level SDRAM retains the ROM
  loader and rejects this argument. Evidence:
  `make -C sim/soc check-runtime-payload` boots hello →
  timer → hello on the same executable, replaces an image at the same path,
  requires exact UART and cycle parity with baked initialization, rejects
  invalid paths, and runs two role commands in one interactive aXos session
  (`irq=1` then `irq=2`). Executable and payload hashes accompany the compact
  JSON in `sim/soc/build/runtime-payload-evidence.json`. This is simulation
  evidence; it makes no new synthesis or physical-board claim.
- [x] **SDRAM gate 1 — prove the test selects SDRAM.** `check-sdboot` now
  selects `configs/sim-sdram.json`, resolves it, and refuses to run unless the
  resolved memory, harness, top, runner, and `USE_SDRAM` are the pin-level
  machine — printing all of them. Selecting the *profile* and letting
  `run-config` dispatch to the runner the memory component declares is what
  removes the original defect: a target's name is no longer a selection.
  Two independent refusals are exercised against a real BRAM profile before
  each run, not asserted: `run-sdram` exits 2 rather than building a machine
  with no SDRAM pins, and a transcript produced by that machine is rejected as
  evidence. The pin path is demonstrated rather than named — the behavioural
  model counts what the controller drove, every run prints
  `[soc] sdram-pins:`, and the check requires a nonzero ACTIVATE. The shell run
  reports activate=263,368 read=348,888 write=177,848 precharge=263,369
  refresh=37,336; fork reports activate=354,544. `run-axsdram` gained the same
  counters and now checks that its CAS-2 agreement came from real commands
  (activate=6 read=6 write=6 precharge=7 refresh=5). Labels in
  [workflow.md](workflow.md), [memory.md](memory.md),
  [toolchain.md](toolchain.md), [ulx3s-bringup.md](ulx3s-bringup.md),
  `sim/soc/README.md`, `sw/kernel/README.md`, and `sw/bootrom/README.md` were
  corrected with the test. Exec was not in this target when gate 1 closed; it
  joined in gate 4, once the progress failure was understood and fixed.
- [x] **SDRAM gate 2 — establish the progress failure.** `soc.reference` gained
  an optional progress monitor (`progress_monitor`, default 0, `omit_when_zero`
  so a declining profile compiles the text it compiled before) that observes
  the core's commit trace, the timer line, and both bus ports and drives
  nothing. `tools/sdram_progress_probe.py` boots the same kernel, SD image, ROM
  loader and input script on all three memories and records retirements by
  privilege mode, handler entries and exits, timer arrivals, pending interrupt
  bits, and fetch/data stall cycles.
  The discriminator is **user instructions retired per handler entry**: 53.7 on
  BRAM, 1.51 on delayed memory, 0.997 on the pin model at 120M cycles. A
  uniform slowdown would leave that ratio alone. A stalled transaction is
  refuted directly — between 40M and 120M the SDRAM run retired a further 3.78M
  instructions and drove 11.06M row activations. Slow work alone is refuted
  too: the machine is 3.5x slower per instruction than BRAM, which would put
  exec near 22M cycles, and kernel code keeps retiring at 55.5 instructions per
  thousand cycles while user code retires 0.007. Evidence:
  `research/benchmarks/sdram-exec-progress.json`. Simulation only.
  No board claim moves: with the parameter at its default, `axprogmon.sv`
  contributes zero modules to a Yosys read, and the elaborated statistics for
  the whole `tangprimer25k` design — every module's wires, cells, and ports —
  are identical to HEAD's, line for line.
- [x] **SDRAM gate 3 — fix timer behavior through its owning boundary.** The
  defect was where the quantum was armed, not how long it was: the M-mode shim
  armed the next deadline at trap *entry*, so the shim, the delegated S-mode
  handler, the scheduler's page-table switch and its `sfence` were spent out of
  the interval the resumed task was supposed to get. The S-mode handler now
  arms it on the way out, after `schedule()` has chosen who runs next, so the
  quantum measures the resumed task's own execution and forward progress does
  not depend on how expensive service happens to be. The shim's arming still
  stands for every path that does not reach the handler's exit, so a missed
  rearm cannot stop the tick.
  The interval is the `timer_quantum_cycles` profile setting (default 2,000),
  bounded 256..16,777,216 in `tools/configure.py` and again by
  `_Static_assert` in `sw/kernel/include/timer.h`, whose `#ifndef` default is
  where the value lives — one define reaching both `kernel.c` and `trap.S`,
  which must agree because both arm the same CLINT register. Exercised at the
  default and at 64,000 (`configs/kernel-slow-memory.json`).
  Demonstrated: exec completes on the pin model at 39.1M cycles at the default
  quantum and 9.86M with the slow-memory profile, where the user task retires
  159 instructions per handler entry rather than one; preemption still
  interleaves parent and child in the fork demo (`PCW`/`CPW`) on all three
  memories at both quanta; the shell transcript and console are unchanged; idle
  cycles are still spent in WFI (126,869 on the pin model at 64,000). Trap
  correctness is untouched — `check-boot` passes on ISS, QEMU and RTL, and
  `check-shell`, `check-memory`, `check-storage`, `check-storage-write`
  pass. The fix helps every memory: delayed exec went from 13.97M to 8.33M
  cycles at the same quantum. Evidence:
  `research/benchmarks/timer-quantum-fix.json`.
- [x] **SDRAM gate 4 — close the composed regression.** `check-sdboot` now
  passes shell, fork, exec and return-to-shell on the actual pin model, with
  every budget derived from a measurement on this machine rather than a BRAM
  assumption: 7.54M, 8.47M and 9.86M measured against 12M/12M/15M bounds, and a
  1,800 s wall-clock timeout against a ~47 s observed cost. The retained
  failure case is `check-sdboot-exec`: the same exec at the *default*
  2,000-cycle quantum, measured 39,112,456 cycles against a 60M bound. That is
  the condition under which user progress was lost entirely, so it fails if the
  quantum is ever armed at trap entry again; it is in the
  `kernel-storage-mutation` suite. Rechecked: `check-boot` (ISS/QEMU/RTL),
  `check-shell`, `check-memory`, `check-storage`, `check-storage-write`,
  `check-role-driver`, `check-role-irq`, `evolution-check` for the 32 KiB
  Primer fit gates, `sim/soc check-runtime-payload`, `sim/unit run-axsdram`,
  and `make verify-smoke`.

  Starting evidence for these four gates: discovered while
  checking runtime payload isolation: `check-sdboot` called `run-sdram` without
  selecting `sim-sdram`, so its default BRAM run could not support its printed
  physical-SDRAM claim (corrected in gate 1). Explicitly selecting the SDRAM
  pin-model profile boots
  the shell in 7,543,477 cycles (exceeding the old 3M bound) and passes fork,
  but `exec hello.elf one two` fails to complete at 15M, 27M, and 120M cycles.
  A diagnostic run sampled the PC every million cycles through 40M; after
  entering exec, the samples stay in `supervisor_trap_entry` and the timer arm
  of `supervisor_trap`. This points to timer-service starvation at SDRAM
  timing, with the M-mode shim rearming every 2,000 cycles; starvation remained
  a hypothesis until the progress measurements above distinguished its cause —
  and they did, though not quite as guessed: the hart was not failing to leave
  the handler, it was leaving and being preempted again after about one
  instruction. The commands and identities for this negative result are in
  `research/benchmarks/sdram-exec-followup.json`; this is simulation evidence,
  not a board result.
- [x] **WASM spike.** Build one profile with Emscripten, boot aXos headless
  under Node, and compare against the 29,634-cycle / 25 ms native baseline
  recorded above.  The bet is that a 1.5–4× slowdown still leaves boot
  imperceptible; the point of the spike is to find out cheaply rather than to
  design around a guess.  Deliberately attempted against the *existing*
  Verilator first: the suite is green on 4.038, and proving the idea costs
  nothing if the generated C++ happens to compile.  It did — the Verilated C++
  compiled under `emcc` unmodified, and no RTL, harness source, or elaboration
  parameter differs from the native model.  Measured against the native build on
  the same host (`make web-bench`), profile `sim-role-loopback`, three runs:
  **27,509 cycles to the aXos prompt in 25–35 ms, against 26–27 ms native —
  0.94–1.37× wall-clock**, effectively parity and well inside imperceptible.
  Absolute rates move with host load and the ratio does not, so the ratio is
  the claim.  The cycle count is identical between the two by
  construction; it is 27,509 rather than the recorded 29,634 because the RTL and
  the payload have moved since that measurement, which `boot.mjs` reports rather
  than hides.  Bundle: 374 KB total — 177 KB WASM, 61 KB glue, 117 KB aXos
  payload, 18 KB page.  Evidence:
  `make web-check` boots headless, waits for the prompt, then runs the shell's
  `role` twice and requires `irq=1` then `irq=2`.
- [x] **Toolchain currency.** The measured baseline used Verilator 4.038 (2020),
  and the fear was that Verilator 5 is stricter — `sim/soc/Makefile` carried a
  `-Wno-UNUSEDPARAM` guard for it that had never been exercised.  Installed
  beside the packaged one (`VERILATOR=` overrides per invocation, so a
  regression is one flag to undo) and measured rather than argued about:
  **5.050 is green.**  `make verify-smoke` and `make component-test` both pass
  on it, the guard turned out to be exactly what was needed and nothing else
  was, and `sim-role-loopback` and `sim-bram` run `cpu_perf` **cycle-identical
  to 4.038** — 42,978 cycles, checksum `0xe9266745`.  The claim this replaces
  was worse than out of date: `tools/web.sh` said 5.x "fails to elaborate
  role.loopback", which nothing had rechecked since it was written, and a
  requirement nobody re-runs is how a workaround outlives its bug.  So the
  versions are now explicit and single-sourced in
  [`tools/requirements.json`](../tools/requirements.json), separating *supported*
  (accepted without comment) from *tested* (actually run, with the evidence
  named beside each version).  4.038 stays the default where both are installed
  — not for compatibility, but because every recorded cycle count and
  wall-clock ratio was measured on it and a host with both should reproduce
  those rather than quietly produce its own.  Evidence: `make
  requirements-check`, as the `requirements` stage of `smoke`, `ci-quick` and
  `nightly-integrated`; `make doctor` reports this host against the same file.
  Not yet answered: whether 5.x is *faster* here, which is the other half of
  "currency" and needs a like-for-like build-and-run timing.
- [x] **Browser console.** A terminal over the same byte pipe the interactive
  session already exposes, so a reader boots aXos in a tab with no toolchain,
  no FPGA, and no install.  `make web` serves it.  What makes the byte pipe
  genuinely *the same* one is that the per-cycle body of the machine — clocking,
  the UART handshake, the SPI sampling edge — moved into
  `components/harness/common/soc_machine.h`, which the batch runner, the
  interactive session, and the browser driver now all use unmodified; a front
  end that re-implemented any of it could drift without a test noticing.  The
  browser owns only what is browser-shaped: control is inverted so nothing holds
  the thread, the machine is clocked in slices sized to a frame, and the page
  throttles hard while the shell polls at an idle prompt rather than pinning a
  core in a background tab.  Evidence: `make web-check`, and the same `role`
  twice → `irq=1`, `irq=2` continuity proof holding in the page.
- [x] **Machines side by side.** The same program on several component
  selections at once, each with its own cycle count — the demonstration the
  component system exists for.  `make web-compare` stages one WASM bundle per
  selection and boots all of them in one page on one binary; `make
  web-compare-check` is the headless half.  The default set is `sim-minimal`,
  `sim-bram`, and `sim-ax2`, which differ in *exactly one* component, so the
  spread is attributable to the core rather than merely observed beside it, and
  the payload is `cpu_perf`, which reports retired instructions and cycles per
  workload and prints a checksum every core must agree on.  Machines are
  advanced in lock-step *simulated* cycles on a timer rather than in equal
  wall-clock slices:
  that is the difference between racing the machines and racing the host, and
  it makes the console that finishes first the machine that needed fewer
  cycles.  Measured, one `cpu_perf` binary, checksum `0xe9266745` on all three:
  **`core.minimal` 70,650 cycles, `core.pipeline5` 42,978 (1.64×),
  `core.ax2` 25,729 (2.75×)** — identical to the native run of the same
  profiles, because it is the same RTL.  No new claim: `python3
  tools/bench.py cpu` already sweeps this natively, and this milestone is the
  presentation of it.  The failure worth guarding is not a wrong number but a
  convincing one — three bundles that are secretly one machine under three
  labels, which is why each bundle gets its own module export name, is asked
  what it is (`ax_profile()`) and checked against the label it was staged
  under, and why equal cycle counts and disagreeing checksums both fail the
  check.  Evidence: `make web-compare-check` for the machines and
  `make web-page-check` for the pages — the latter drives both of them in a
  headless browser and reads the rendered scoreboard back, which is the only
  way to check the page rather than the machine.  Both guards were confirmed to
  fire by staging the ax2 bundle under the `sim-bram` label: the headless run
  refuses it, and so does the page.
- [x] **Runtime payload failure coverage.** The reuse gate proved the good
  path; this is the other one. `$readmemh` is forgiving in exactly the wrong
  way for a payload loader — a stray non-hex character ends the read where it
  stands, an `@` record moves the words after it somewhere else in the array,
  and words past the end of the array are dropped — so each of those boots
  *something* and reports a cycle count for whatever that was. The runner now
  validates an image before handing it over and refuses it by line and reason:
  a token that is not a 32-bit hexadecimal word, one wider than 32 bits, an
  address record, a block comment, and an image holding more words than the
  machine's RAM (`AX_RAM_BYTES`, compiled from the same variable the RTL's
  `RAM_BYTES` comes from, so the runner cannot disagree with the model about
  the capacity). All five are exercised on all three memory configurations, and
  after each refusal a valid launch must still be bit-for-bit the run it was
  before — a rejected launch has to leave nothing behind.

  The entry half of the same question is an invariant rather than a case: a
  runtime image lands at word zero of the RAM array, so it is only under the
  reset PC while that PC is the RAM base. Every profile that accepts the
  argument is checked for that. The one profile whose reset PC is the ROM is
  the pin-level SDRAM machine, and it refuses the argument outright — now
  checked, not assumed, because a model that accepted it and booted its own
  previous contents would look like a pass.

  The initialization boundary is checked by preprocessing rather than by
  reading the guard: `verilator -E` over the memory sources with and without
  `AX_RUNTIME_RAM_IMAGE` must show the runtime branch **absent** from the text
  a unit bench, the browser bundle, or a synthesis flow compiles, and the
  original `$readmemh` path still present. Confirmed to fail when the guard is
  widened. Payload-independent executable identity is unchanged and still
  asserted. Evidence: `make -C sim/soc check-runtime-payload`, recorded as
  `org.atomix.runtime-payload-check.v2` in
  `sim/soc/build/runtime-payload-evidence.json`.
- [~] **Live documentation — reproducible examples.** The record exists and
  the headless half is closed. `tests/examples.json` names, for each example,
  the document and section that shows it, the command as that document spells
  it, the profile, the payload and the build that produces it, the entry the
  payload is loaded under, the console script, the exact transcript, and a
  cycle bound. `make example-replay` boots all four on the machine each names
  and compares every byte, writing payload hashes and resolved identities to
  `build/examples/replay.json`.

  Two checks beyond the transcript, because they are how such a record goes
  stale without the transcript changing: the command must still appear in the
  document that shows it, so an example cannot outlive the text around it; and
  the entry the record names must be the reset PC the profile resolves to,
  since a payload loaded where the machine does not start could still print
  the right thing for the wrong reason. Confirmed to fail on a drifted
  transcript, a moved entry, and a payload that cannot be built. A missing tool
  is refused in the open rather than skipped — an example nobody can replay is
  not one that passed. Software stays separate from the machine: the model is
  built with no baked image and the payload arrives as `--ram-image`, so the
  four examples share two models. `make -C sw/kernel run-rtl` and the other
  commands in [workflow.md](workflow.md) stay authoritative; the records quote
  them rather than replacing them. Runs as the `doc-examples` stage in
  `ci-integration` and `nightly-integrated`.

  What remains is the *live* half: a documentation code block that boots the
  machine in the reader's browser. That needs the WebAssembly bundle, and
  therefore emscripten, which this environment does not have — the replay above
  is the same record driving the same machine headlessly, and is what the
  browser view would have to agree with.
- [~] **Live documentation — replayable bug reports.** The exported-record
  half is closed; the URL half waits on the browser bundle. `make bug-report
  EXAMPLE=<name>` runs a session and writes a record that carries everything a
  replayer would otherwise supply silently: the profile *and* its resolved
  identity, the payload with its SHA-256 and the command that built it, the
  entry, the keystrokes inline rather than by reference, the cycle budget, what
  the machine printed, and whether it finished — alongside the Verilator
  version, the git revision, and whether the worktree was clean.
  `make bug-report RECORD=<path>` rebuilds the machine from that and compares.

  The payload hash is the part that matters. A report naming a path and
  replaying whatever is at that path today would reproduce the *current*
  program's behaviour and present it as the reported one, which is worse than
  not reproducing at all because it looks like an answer. So a payload whose
  bytes have moved on is refused by name with both hashes and the build command
  that would restore it, and one that is not there at all is refused the same
  way. If the profile has since resolved differently, each changed field is
  printed before the replay rather than after it.

  `make bug-report-check` is the regression, run by the `doc-examples` stage:
  a session that finished reproduces, a session that ran out of cycles
  reproduces *its failure* — the case a bug report actually exists for — and
  both the stale and the missing payload are refused instead of substituted.

  What remains is the URL: reconstructing the same selection from a link needs
  the WebAssembly bundle and therefore emscripten, which this environment does
  not have. The exported record is the same reconstruction by another route,
  and is what such a URL would have to encode.
- [x] **Verification made visible.** `make evidence-views` renders three views
  into `build/evidence/` from records that already exist, each self-contained
  and theme-aware, with a `views.json` naming what was rendered from what.

  **Formal** comes from `research/formal-coverage.json`, so it shows the
  coverage the configuration actually has: for each core, the command, the
  `.cfg`, the wrapper, the ISA and proof mode, `ENABLE_M`, the retire channels
  proved out of those declared, and the depths — then every instruction, the
  four proved and the thirty-three not, the latter rendered as **unsupported**
  rather than left out, because a coverage view that lists only the covered
  part is the most misleading kind there is. What is outside the proofs
  entirely (RV32M, misaligned memory, CSR and privilege, liveness) is rendered
  the same way. With `FORMAL_LOG=` pointing at a failing check's `result.log`
  it also renders the counterexample: the check, the depth the solver was
  given, the step, the failing assertion, and the signal table that is the
  model. The adapter is matched to Yosys's actual wording rather than a guess
  at it, and was validated both ways against real solver output — a real
  passing log yields no counterexample, and a real `model found: FAIL!` log
  yields the depth, assertion, step and table.

  **Cosim** summarises a whole `make -C sim/cosim test` run — the programs, the
  launches, the total events compared — and, if there was one, the *first*
  divergence with its event index, field, and both values, because everything
  after the first is a consequence.

  **L3** renders `research/live-fpga/l3/morph-rtl-trial.json`: the faulted
  canary digest against its oracle, the manager and role that owned the trial,
  whether the resident RTL and bitstream were touched, the verified rollback
  and the oracle coverage — and what the trial does *not* authorize, rendered
  as unsupported alongside the rest.

  `make evidence-views` runs the fixtures first: known records are rendered and
  the output must contain each identity, scope, location and status they hold,
  the uncovered instructions must render as unsupported, the unauthorized
  actions must render as unsupported, and a divergence must never render with a
  passing status. Runs as part of the `doc-examples` stage.

## Final physical FPGA gate

Hardware availability is intentionally not a blocker for the simulation and
component work above.  It is the final platform-evidence gate.  The Tang Primer
25K Dock is the only board currently in the lab; every other board entry below
is explicitly non-physical until hardware becomes available.

### Tang Primer 25K — current hardware priority

- [x] Tang Primer 25K Dock (Gowin GW5A-25A) board component, official
  clock/UART/S1 pins, GW5A open-flow flags, and a BRAM-only profile exist.
  Evidence: `make -C rtl/fpga synth
  COMPONENT_CONFIG=$PWD/configs/tangprimer25k.json BUILD=build-primer25k`
  completes with zero design-check errors; its 32 KB main memory maps to block
  RAM. On 2026-07-29 the baseline routed at 32.23 MHz, programmed into SRAM,
  printed its UART hello transcript, and restarted from S1.
- [x] Tang Primer GPU and TPU profiles are measured on hardware.
  `tangprimer25k-ax2`
  is peaked at 2-wide/2 KiB I$/64-entry BTB: 20,893 LUTs and 25,729 measured
  workload cycles. `tangprimer25k-gpu` explicitly maps three GW5A DSPs per
  multiplier. The verified 4-lane GPU routes at 18,280 LUT4 and 38.47 MHz with
  12 DSPs; its UART run checked two kernels at four thread counts and ended in
  `gpu-perf: PASS`. The attempted 8-lane profile overflowed and six lanes
  could not be legally placed, establishing four as the shipped board width.
  `tangprimer25k-tpu` folds K=8 over 24 MACs and makes its C buffer infer
  BSRAM: 17,345 LUT4, 24 DSPs, and 189 compute cycles versus 42,995 on CPU.
  It routes at 32.65 MHz and ended in `role tpu-lite: PASS` on the board.
- [x] Use volatile SRAM configuration for board development and confirm the
  recovery controls: the baseline UART transcript appears, S1 restarts the
  SoC, and no persistent flash write is needed for CPU/GPU/TPU testing.
- [x] Complete the no-hardware resident-runtime preflight.  `make
  primer-runtime-preflight` boots the UART loader, uploads the compact aXos
  kernel, switches and verifies two GPU programs in RTL, builds the exact
  blank-RAM/immutable-ROM Primer image, checks 25 MHz timing, and writes a
  hashed `evidence.json` beside the bitstream.  This is reproducible build
  evidence only; it does not claim that the image ran on the Dock.
- [x] Close the Primer resident-runtime hardware gate. The immutable loader
  emitted `AXOK`, the initialized aXos kernel emitted `AXRD`, and the physical
  921600-baud run ended in `FAST SWITCH PASS`. SAXPY and polynomial programs
  were loaded and executed in one aXos session, and every result matched the
  host reference. Release hashes and results are recorded in the
  [Tang Primer achievement record](achievements/tangprimer25k.md).
- [x] Repeat resident-runtime recovery checks. A complete USB/IP detach/attach
  preserved the running FPGA/aXos session; fresh-ROM tests rejected oversized
  and bad-CRC uploads before accepting a valid retry; and S1 recovered both a
  running kernel and a deliberately interrupted 2,048/4,829-byte upload. A
  physical power cycle restored the prior flash image; JTAG detection, an
  SRAM-only runtime reload, loader-error retries, valid kernel boot, and ten
  exact-output switch rounds all passed afterward.
- [ ] Capture a reproducible Primer evidence bundle: exact core/Dock revision,
  OSS CAD Suite and programmer versions, bitstream/profile identity, timing
  and utilisation summary, serial-device identity, and complete UART
  transcript. Keep loader-bitstream identity separate from every runtime
  payload, identify which earlier observations apply to which image, and link
  recovery runs and failures. Close with a repeat from the lab procedure whose
  identities and outputs match the compact record; retain hashes and summaries
  in the repository rather than generated build trees. Keep the procedure in
  [tangprimer25k-bringup.md](tangprimer25k-bringup.md) authoritative.
- [ ] Decide whether persistent Primer flash programming is useful only after
  the runtime SRAM regression above is repeatable. Close the decision with a
  concrete need, recovery procedure, and expected power-on behavior; deferral
  is an acceptable outcome. A decision to support it is not authorization to
  program flash: each such operation still needs explicit current-turn approval.
- [ ] Treat external SDRAM, USB host, PMOD, and removable-storage validation as
  optional Primer expansion work.  Do not make it a gate for the current
  core-board-plus-Dock target; add a specific profile and evidence item if the
  corresponding module is acquired. Each new item must name the actual module,
  pin/clock/electrical contract, isolated bring-up test, and recovery path;
  simulation or a connector listed in a manifest cannot close its board gate.

### Supported targets not currently in the lab

- [~] ULX3S-85F board component, constraints, SDRAM/UART RTL, synthesis
  preflight, and ECP5 P&R evidence exist.  Evidence: `make fpga
  CONFIG=configs/ulx3s-85f.json` with the matched OSS CAD Suite environment.
  No ULX3S is currently owned, so UART, SDRAM, SD, reset, and live partial-load
  observations remain unverified and are not near-term hardware gates.
- [~] Tang Nano 20K (Gowin GW2A-18C) board component, constraints, and Gowin
  flow exist; the design synthesises and fits.  Evidence:
  `make -C rtl/fpga synth COMPONENT_CONFIG=$PWD/configs/tangnano20k.json`
  produces a Yosys netlist in which the 32 KB main memory maps to 32 `DPB`
  block-RAM cells (not flip-flops) — the BRAM-only bring-up needs registered
  reads (`axram` `SYNC_READ=1`), verified functionally by `make -C sim/soc run
  CONFIG=configs/sim-bram.json SYNC_READ=1` (hello prints, one wait state per
  access).  Fit on the GW2A-18C: 32 DPB, ~2.7k FF, ~11k LUT4.  No Tang Nano is
  currently owned; physical P&R/programming evidence is deferred.
- [~] Attach an accelerator role on the Tang Nano.  The parameterized SIMT
  engine (gpu_engine.sv, `NLANES`) fits: the shipped `configs/tangnano20k-gpu.json`
  (minimal host + 6-lane) synthesises to ~20.2k LUT4, 32 DPB, 6 DSP — inside the
  GW2A-18C at 97% (tight); `role.gpu-compute` at 4 lanes fits comfortably
  (~18.9k).  Functional equivalence to the 8-lane reference is checked by
  `make -C sw/baremetal check-gpu`, and throughput by `check-gpu-perf` (poly
  kernel ~12.9× vs on-core).  Per-hardware fit:
  [hardware-capabilities.md](hardware-capabilities.md); still-open TPU/all-three
  cases: [tangnano-capacity.md](tangnano-capacity.md).
- [~] Run ECP5 / Gowin place-and-route, generate the bitstream, and record
  timing and resource reports. Completed for Tang Primer CPU/GPU/TPU; Tang
  Nano remains; ULX3S has tool evidence but no physical-board validation.

The detailed, safe board procedures are
[tangprimer25k-bringup.md](tangprimer25k-bringup.md) and
[ulx3s-bringup.md](ulx3s-bringup.md).
