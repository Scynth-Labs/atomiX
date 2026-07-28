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
registers, rings the doorbell, and currently polls `STATUS`. A role interrupt
replaces that polling when the PLIC integration lands. Roles do **not** execute
RISC-V; they consume descriptors that aXos feeds them.
`sw/baremetal/include/role.h` is the software-side
header; `components/role/loopback/axrole.sv` is the reference device shape.

Current and planned roles:

- `role.none` — empty window; the shell default, proves discovery-of-absence.
- `role.loopback` — copies buffers inside its window; proves the framework
  (evidence: `make -C sw/baremetal check-role`).
- `role.tpu-lite` — the first real accelerator: an int8 weight-stationary
  8×8 systolic GEMM engine with 32-bit accumulation, an accumulate mode for
  K > 8 tiling, and a ReLU output stage (evidence:
  `make -C sw/baremetal check-tpu`, which also prints the measured
  role-versus-CPU matmul cycle counts).
- `role.gpu-compute` — the second real accelerator: an 8-lane SIMT vector
  engine.  Software uploads a short straight-line kernel (a small load/store +
  integer ALU ISA) and a flat global data buffer, sets the thread count, and
  rings the doorbell; 8 lanes run the kernel in lockstep across
  ceil(threads/8) waves, with out-of-range threads predicated off — the same
  descriptor driver model as the other roles, but programmable rather than
  fixed-function (evidence: `make -C sw/baremetal check-gpu`, which verifies
  saxpy, fused multiply+ReLU, and a gather kernel against an on-core
  interpreter of the ISA and prints role-versus-CPU cycle counts).

Role swapping today means selecting a different `role` component (one profile
line) or, at runtime, reloading role programs/descriptors through the window.
Swapping the fabric of a live board without a full bitstream reload is the
research track in [docs/partial-reconfig.md](../../docs/partial-reconfig.md).

A role must never require shell RTL changes; if it seems to, the role
interface spec is what gets amended.
