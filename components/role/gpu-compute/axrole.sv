// Lane count is a build-time knob: the engine, ISA, window layout and
// driver contract are identical at every lane count, so a different width
// is a sizing choice rather than a different component.
`ifndef GPU_COMPUTE_LANES
  `define GPU_COMPUTE_LANES 8
`endif
`ifndef GPU_COMPUTE_DATA_WORDS
  `define GPU_COMPUTE_DATA_WORDS 4096
`endif
// GPU-compute role: an 8-lane SIMT data-parallel vector engine.
//
// The second real accelerator behind the shell role window (DESIGN.md §3.3).
// Where TPU-lite is a fixed-function folded GEMM, this role is programmable:
// software uploads a short straight-line kernel and a flat global data buffer,
// sets the thread count, and rings the doorbell.  The engine then runs the
// kernel across all threads the way a GPU does — Single Instruction, Multiple
// Threads: NLANES lanes execute the same instruction stream in lockstep, each
// on its own thread index, over ceil(NTHREADS / NLANES) waves.  It shares the
// exact doorbell/status/descriptor driver model the loopback and TPU roles
// proved.
//
// This file is the reference role: a thin wrapper setting NLANES.  The
// lane-parameterized implementation lives in gpu_engine.sv; role.gpu-compute-
// lite reuses it at a smaller lane count to fit a small FPGA.  Lane count only
// changes wave grouping, never the result of a conflict-free kernel.
//
// Window layout (common role header per DESIGN.md §3.3 first):
//
//   0x0000  ROLE_ID   RO  "GPUC"
//   0x0004  VERSION   RO  role programming-model revision
//   0x0008  DOORBELL  WO  any write starts a job when idle
//   0x000c  STATUS    R/W1C  bit0 BUSY, bit1 DONE (write 1 to clear DONE)
//   0x0010  NTHREADS  R/W  thread count for the next job; latched at doorbell,
//                     clamped to NLANES*4096.  Launches ceil(NTHREADS/NLANES)
//                     waves; 0 completes immediately.
//   0x0014  NINSN     R/W  kernel length (instructions); clamps to PROG_WORDS
//   0x0018  COUNT     RO   completed-job counter
//   0x0100  program memory, PROG_WORDS instruction words (see ISA below)
//   0x1000  global data buffer, DATA_WORDS 32-bit words, word-addressed;
//           kernel loads/stores index it (low bits, so it wraps like the
//           loopback buffer).  Software lays its input and output arrays out
//           here at chosen base offsets.
//
// SIMT execution model.  A job runs waves 0..ceil(NTHREADS/NLANES)-1.  In wave
// w, lane L runs thread tid = NLANES*w + L; a lane whose tid >= NTHREADS is
// *inactive* (predicated off) — the classic SIMT tail: its stores are
// suppressed and its loads return zero, but it still steps the shared program
// counter in lockstep.  The kernel is straight-line (no branches), so all lanes
// share one PC and never diverge; per-instruction global-store order is
// (wave, instruction, ascending lane), which the on-core reference in gpu.c
// reproduces exactly.
//
// Instruction encoding (32-bit): op[31:26] rd[25:23] ra[22:20] rb[19:17]
// imm[16:0] (sign-extended).  ALU-class instructions retire one per cycle
// across all lanes; memory instructions serialize the lanes onto the single
// engine buffer port (2 cycles/lane for a load, 1 for a store), which is how
// memory divergence costs cycles on real hardware.  Per-lane registers r0..r7
// are flip-flops (x0 is a normal register here, not hardwired).
//
//   op  mnemonic         effect (per active lane)
//   0   HALT             end the wave early
//   1   TID   rd         rd = tid
//   2   LI    rd,imm     rd = imm
//   3   MOV   rd,ra      rd = ra
//   4   LDX   rd,ra      rd = gmem[ra]            (inactive lane -> rd = 0)
//   5   STX   ra,rb      gmem[ra] = rb            (inactive lane: suppressed)
//   6   ADD   rd,ra,rb   rd = ra + rb
//   7   SUB   rd,ra,rb   rd = ra - rb
//   8   MUL   rd,ra,rb   rd = (ra * rb)[31:0]     (signed, low word)
//   9   AND   rd,ra,rb   rd = ra & rb
//   10  OR    rd,ra,rb   rd = ra | rb
//   11  XOR   rd,ra,rb   rd = ra ^ rb
//   12  SLL   rd,ra,rb   rd = ra << rb[4:0]
//   13  SRL   rd,ra,rb   rd = ra >> rb[4:0]       (logical)
//   14  SRA   rd,ra,rb   rd = ra >>> rb[4:0]      (arithmetic)
//   15  MIN   rd,ra,rb   rd = signed_min(ra, rb)
//   16  MAX   rd,ra,rb   rd = signed_max(ra, rb)  (ReLU = MAX(x, zero-reg))
//   17  ADDI  rd,ra,imm  rd = ra + imm
//   18  MULI  rd,ra,imm  rd = (ra * imm)[31:0]
//
// The program memory and data buffer keep the block-RAM discipline of the
// other roles: two synchronous ports each (data-port MMIO and the engine),
// full-word MMIO writes only (partial strobes error), one aXbus wait state on
// reads, and the fetch port sees only the register page.  NTHREADS, NINSN, the
// program, and the buffer are writable only while idle, and software must not
// touch them while BUSY.
module axrole #(
  parameter logic [31:0] BASE = 32'h4000_0000
) (
  input  logic        clk,
  input  logic        rst,
  input  logic        i_valid,
  input  logic [31:0] i_addr,
  input  logic [31:0] i_wdata,
  input  logic [3:0]  i_wstrb,
  output logic        i_ready,
  output logic [31:0] i_rdata,
  output logic        i_err,
  input  logic        d_valid,
  input  logic [31:0] d_addr,
  input  logic [31:0] d_wdata,
  input  logic [3:0]  d_wstrb,
  output logic        d_ready,
  output logic [31:0] d_rdata,
  output logic        d_err
);
  gpu_engine #(
    .BASE(BASE),
    .NLANES(`GPU_COMPUTE_LANES),
    .DATA_WORDS(`GPU_COMPUTE_DATA_WORDS)
  ) u_engine (.*);
endmodule
