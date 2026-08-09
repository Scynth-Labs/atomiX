# rtl/roles/ — swappable accelerator roles

Mode-specific accelerators for the shell + role platform (DESIGN.md §3.3).
Role implementations are selectable `role` components living under
[components/role/](../../components/role/); this directory remains the
architecture signpost.

**Role contract (implemented):** a role is an aXbus MMIO slave in the fixed
64 KiB window at `0x4000_0000` with a common header — `ROLE_ID` (zero means
no role present), `VERSION`, `DOORBELL`, and `STATUS` (BUSY/DONE, DONE is
write-1-to-clear) — followed by role-defined registers and windows.  Software
discovers the role by reading `ROLE_ID`, programs role-defined descriptor
registers, and rings the doorbell.  Roles do **not** execute RISC-V; they
consume descriptors that aXos feeds them.
`sw/baremetal/include/role.h` is the software-side
header; `components/role/loopback/axrole.sv` is the reference device shape.

**Completion is either polled or taken as an interrupt.** Every role also
drives one level-sensitive `irq` output, asserted for exactly as long as
`STATUS.DONE` stands.  It reaches the core as a machine external interrupt
through the shell's PLIC (`plic.qemu-virt`), where role completion is source 2
and the UART receiver is source 1.  Because the line is level-driven, clearing
`STATUS.DONE` is what deasserts it — the same write a polling driver already
performs — so a handler must clear DONE *before* writing COMPLETE or the source
simply becomes pending again.  `role.none` ties the line low, so a profile with
no accelerator still presents a well-defined source.
`sw/baremetal/include/plic.h` is the software-side header.  Evidence:
`make -C sim/unit run-plic` (the register contract, priority/threshold gating,
lowest-id tie-break, and the level-sensitive re-arm) and
`make -C sw/baremetal check-role-irq` (the whole path on the RTL, with the CPU
parked in `wfi` so it can only finish through the interrupt).

Implemented roles:

- `role.none` — empty window; the shell default, proves discovery-of-absence.
  Ties its `irq` line low.
- `role.loopback` — copies buffers inside its window; proves the framework
  (evidence: `make -C sw/baremetal check-role` polled, and
  `check-role-irq` interrupt-driven).  Buffer accesses while `STATUS.BUSY` is
  set return a bus error, enforcing the ownership rule that also lets FPGA
  synthesis map its 4 KiB buffer to block RAM.
- `role.tpu-lite` — the first real accelerator: an int8 weight-stationary
  8×8 systolic GEMM engine with 32-bit accumulation, an accumulate mode for
  K > 8 tiling, and a ReLU output stage (evidence:
  `make -C sw/baremetal check-tpu`, which also prints the measured
  role-versus-CPU matmul cycle counts).
- `role.gpu-compute` — the second real accelerator: a SIMT vector engine whose
  lane count is a component parameter (`NLANES`, 8 by default; the shipped Tang
  profiles use 4 and 6).  Software uploads a short straight-line kernel (a
  small load/store + integer ALU ISA) and a flat global data buffer, sets the
  thread count, and rings the doorbell; the lanes run the kernel in lockstep
  across ceil(threads/NLANES) waves, with out-of-range threads predicated off —
  the same descriptor driver model as the other roles, but programmable rather
  than fixed-function (evidence: `make -C sw/baremetal check-gpu`, which
  verifies saxpy, fused multiply+ReLU, and a masked-tail reduction-style kernel
  against an on-core interpreter of the ISA and prints role-versus-CPU cycle
  counts).
- `role.gpu1` — the scalable successor, tiered `-{s,m,l,xl}`: the SIMT engine
  rebuilt around **banked global memory** (NBANKS interleaved block RAMs behind
  a lane→bank crossbar with round-based conflict serialisation) and a real
  control ISA — structured IF/ELSE/ENDIF divergence, uniform and any-lane
  branches, compare-set, integer divide, cross-lane shuffle, and displaced
  addressing.  Banking is what makes lane count worth scaling: the single-port
  engine above gained only 1.18× from 8 to 16 lanes, where gpu1 gains
  1.69–1.82× per doubling and is 2.70× the old engine at equal lane count.
  Geometry is published in a CAPS register, so one driver and one oracle serve
  every tier (evidence: `make -C sim/unit run-suite-gpu1` against a C++
  interpreter of the ISA, and `make -C sw/baremetal check-gpu1` on-core).

Role swapping today means selecting a different `role` component (one profile
line) or, at runtime, reloading role programs/descriptors through the window.
Swapping the fabric of a live board without a full bitstream reload is the
research track in [docs/partial-reconfig.md](../../docs/partial-reconfig.md).
The shell-owned `axlivemon` observes completion, stalls, rejection, watchdog,
and verified activation events across those changes; its versioned contract is
the [Live FPGA track](../../docs/live-fpga.md).

A role must never require shell RTL changes; if it seems to, the role
interface spec is what gets amended.
