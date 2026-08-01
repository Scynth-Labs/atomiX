# Engineering checklist and evidence

This is the live completion checklist for atomiX.  It tracks evidence, not
just code presence: a checked item has a reproducible command or a recorded
physical observation behind it.

Status legend:

- `[x]` Verified by the listed automated evidence.
- `[~]` Implemented and simulation/synthesis tested; physical-board evidence is pending.
- `[ ]` Planned or intentionally deferred.

The architectural contract remains [DESIGN.md](../DESIGN.md); component
contracts and selections are in [components/](../components/).

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

Both opening questions are settled in [abi.md](abi.md): the ABI is the RISC-V
Linux subset, and the loader takes ELF directly rather than a pre-flattened
image — in both cases because it is what the toolchain already produces, and
deviating would cost work without buying capability.

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
- [ ] Keep physical claims separate from simulation and synthesis claims.

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
- [ ] Partial reconfiguration of the role region on a live bitstream —
  research staged in [partial-reconfig.md](partial-reconfig.md); no
  capability claim before its stage-4 board evidence.
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
- [x] Kernel-as-runtime-payload invariant: immutable UART ROM accepts a
  length-bounded CRC-32 `AXK1` frame into blank RAM and starts any compatible
  aXos personality. Evidence: `make -C sw/kernel check-uartboot` rejects
  corrupt/oversized uploads and boots the full kernel; `make runtime-primer`
  uploads the compact host-link kernel before its two-program accelerator test.
  The loader-only Primer image routes at 32.75 MHz for a 25 MHz constraint
  (16,532 LUT4, 44 BSRAM, 3 DSP); physical upload evidence is still pending.
- [x] Kernel-mediated userspace role ABI: `role_info`, token-returning
  `role_submit`, and retry-safe `role_wait`, using the same checked job
  encodings as the host link. The physical role window remains supervisor-only
  through a dedicated Sv32 alias, and device polling is bounded. Evidence:
  `make -C sw/kernel check-role-driver` (resident shell plus U-mode loopback
  job) and `make -C sw/kernel check-boot` (safe role absence on ISS/QEMU).
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
component work above.  It is the final platform-evidence gate.

- [~] ULX3S-85F board component, constraints, SDRAM/UART RTL, and synthesis
  preflight exist.  Evidence: `make fpga CONFIG=configs/ulx3s-85f.json` with
  the matched OSS CAD Suite environment.
- [~] Tang Nano 20K (Gowin GW2A-18C) board component, constraints, and Gowin
  flow exist; the design synthesises and fits.  Evidence:
  `make -C rtl/fpga synth COMPONENT_CONFIG=$PWD/configs/tangnano20k.json`
  produces a Yosys netlist in which the 32 KB main memory maps to 32 `DPB`
  block-RAM cells (not flip-flops) — the BRAM-only bring-up needs registered
  reads (`axram` `SYNC_READ=1`), verified functionally by `make -C sim/soc run
  CONFIG=configs/sim-bram.json SYNC_READ=1` (hello prints, one wait state per
  access).  Fit on the GW2A-18C: 32 DPB, ~2.7k FF, ~11k LUT4.  P&R and
  bitstream await the apicula tools (`nextpnr-himbaechel`, `gowin_pack`).
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
  Nano and ULX3S remain.
- [x] Program Tang Primer SRAM only for the first board test; confirm serial
  output and S1 reset behavior.
- [ ] Validate external SDRAM and SD read/write persistence on the physical
  board.
- [ ] Decide separately, and only after the SRAM path is proven, whether a
  persistent flash operation is appropriate.

The detailed, safe board procedures are
[tangprimer25k-bringup.md](tangprimer25k-bringup.md) and
[ulx3s-bringup.md](ulx3s-bringup.md).
