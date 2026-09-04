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
  verification status.
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
  `kernel-component-test`, `check-storage`, and `check-sdboot`.

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

## Hardening: what is checked without running the machine

The verification above answers "does it do the right thing on the inputs we
thought of".  These three answer the other question.

- [x] **Static analysis over every language, with nothing silently skipped.**
  `make static-analysis` runs Verilator lint across *every* profile in
  `configs/` (20 elaborated designs -- a component only some unbuilt profile
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
- [~] Extend binary-format parser regression testing to the remaining inputs.
  AXFS on-disk metadata is now covered by the production filesystem component
  under
  libFuzzer, ASan, LSan, and UBSan; every input runs both raw framing rejection
  and a reachable-directory pass, then exercises each parsed extent through
  `fs_read`.  Evidence: `make fuzz-loader`; use `make -C sim/fuzz
  explore-axfs` for an unbounded run.  The AXK1 kernel-upload envelope and
  host-link request decoder remain open.  The loader was first because it is in
  the kernel's boot path and its input has the most complex framing; the others
  use the same technique against a smaller code path.

## Change-ready checklist

Use this for a substantive implementation or interface change:

- [ ] Update the component manifest and profile validation if source selection
  changes.
- [ ] Update the architecture/contract document at the affected boundary.
- [ ] Run the narrow unit or simulator test, then the relevant composition
  check; run formal after core/RVFI changes.
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
- [ ] Finish decoupling the remaining Primer profiles from their payloads.
  `tangprimer25k-{ax2,gpu,tpu}` baked `hello`/`gpu_perf`/`tpu` into synthesis,
  so their board evidence named a program and a change to that program
  re-opened the claim.  Each now has a loader variant carrying identical
  hardware and differing only in `reset_pc`, which is what declares a runtime
  profile — `rtl/fpga/Makefile` derives blank RAM and a correctly sized UART
  ROM from it, so the profiles name no payload at all:
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
- [ ] Remaining host-link enhancements: a dedicated second byte pipe so console
  and host-link coexist; buffer/stream and asynchronous-completion ops; cached
  prebuilt-bitstream selection for physical datapath changes.
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
- [ ] Publish the side-by-side comparison: the same payload in the browser and
  on the board, with the frame-time panel visible in both, so the cost of real
  silicon at 25 MHz is shown rather than asserted.  Blocked on the decision
  above, and the finding has already turned the intended argument around: the
  interesting number is not what the FPGA costs against a laptop but that a
  laptop cannot keep up with a 25 MHz machine on this workload at all.
- [x] Write the "run a game on atomiX" guide, aimed at someone who has never
  built the project: load the image, open the port, play.  Evidence:
  [games.md](games.md).
- [ ] Only then, define `video`, `input`, and `audio` component kinds on role-
  window terms — identity register, geometry/mode registers, a framebuffer or
  tile aperture — so a board without sound selects `audio.none` rather than
  failing to build.  A game's source must not change when the board does.
- [ ] Add the physical pins to the manifests of boards that have the hardware,
  starting with ULX3S HDMI and audio.
- [ ] Ship the first graphical game on the board with the most headroom, so the
  contracts are shaped by the comfortable case before being squeezed.
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
  continuous machine; `make -C sw/kernel check-role-driver` and `check-sdboot`
  confirm the batch and physical-SDRAM paths still pass.
- [ ] **Runtime payload selection.** `RAM_INIT_FILE` is compiled into the model
  at elaboration, so changing the program today means rebuilding it.  A session
  that boots different payloads needs the image loaded at run time instead.
  Solved for the browser target only, and without touching the RTL: the path
  compiled in there is virtual (`/payload.hex`), and the model's `$readmemh`
  reads it out of Emscripten's in-memory filesystem when the machine is
  constructed, so the caller stages an image first and one compiled machine
  boots any of them.  The native model still rebuilds per payload, so this stays
  open — but the browser console, which is what needed it, no longer blocks on
  it.
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
- [ ] **Toolchain currency.** The measured baseline used Verilator 4.038 (2020),
  and newer releases are materially faster.  Sequenced after the spike rather
  than before it: Verilator 5 is stricter, `sim/soc/Makefile` already carries a
  `-Wno-UNUSEDPARAM` guard for it that has never been exercised here, and an
  upgrade risks the whole suite before answering the question that matters.
  Install beside the packaged one (`VERILATOR=` overrides per invocation) so a
  regression is one flag to undo.
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
- [ ] **Machines side by side.** The same program on several component
  selections at once, each with its own cycle count — the demonstration the
  component system exists for.
- [ ] **Live documentation.** Code blocks that boot the machine they describe,
  so an example cannot drift from what it claims, and a bug report can be a URL
  that boots the machine that failed.
- [ ] **Verification made visible.** Formal counterexample traces, lock-step
  cosim divergence, and injected-fault results rendered rather than printed.
  The rigor above is the credential for every number on screen and is currently
  legible only in terminal output.

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
  transcript.  Keep the procedure in
  [tangprimer25k-bringup.md](tangprimer25k-bringup.md) authoritative.
- [ ] Decide whether persistent Primer flash programming is useful only after
  the runtime SRAM regression above is repeatable.  Until then, `flash` remains
  intentionally unused.
- [ ] Treat external SDRAM, USB host, PMOD, and removable-storage validation as
  optional Primer expansion work.  Do not make it a gate for the current
  core-board-plus-Dock target; add a specific profile and evidence item if the
  corresponding module is acquired.

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
