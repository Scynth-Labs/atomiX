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

## Userspace ABI (next milestone)

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
- [ ] Add PLIC/role interrupt integration when a second interrupt source
  exists.
- [ ] Evaluate A or C ISA extensions only when their enabling need is explicit;
  neither is required for the current single-hart reference machine.

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
