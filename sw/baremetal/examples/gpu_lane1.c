/* Head-to-head against the morph fabric: can the *existing* programmable role
 * express the same personalities, at less cost?
 *
 * role.morph was built on the assumption that scalar, SIMT and systolic work
 * need a reconfigurable datapath.  But role.gpu-compute is already
 * programmable, and a personality could just be a program for it.  If a
 * one-lane hard GPU matches the one-PE fabric on the same workload at less
 * area, the fabric does not earn its place and R2's answer changes.
 *
 * This runs the identical 50-element SAXPY the morph benchmark runs, with the
 * identical data layout and the identical doorbell-to-completion measurement,
 * so the two numbers are directly comparable.  It then measures the scalar
 * recurrence the only way this ISA can express it — one dependent step per
 * job — which is the honest cost of a straight-line SIMT engine meeting a
 * loop-carried dependency. */
#include "bench_report.h"
#include "platform.h"
#include "role.h"

#define N_SAXPY  50
#define N_SCALAR 64

/* Same buffer layout the morph benchmark uses. */
#define SAXPY_X   0
#define SAXPY_Y   64
#define SAXPY_OUT 128
/* Scratch cells for the recurrence: the accumulator and the current input. */
#define ACC_CELL  200
#define XCUR_CELL 201

/* gpu-compute opcodes (components/role/gpu-compute/gpu_engine.sv). */
#define OP_HALT 0u
#define OP_TID  1u
#define OP_LI   2u
#define OP_LDX  4u
#define OP_STX  5u
#define OP_ADD  6u
#define OP_ADDI 17u
#define OP_MULI 18u

static volatile uint32_t sink;
/* Volatile for the same reason as in morph.c: these operands are compile-time
 * constants and -O2 will otherwise fold the reference loop away. */
static volatile int32_t ref_x[N_SAXPY], ref_y[N_SAXPY];
static volatile int32_t ref_scalar_x[N_SCALAR];

static void fail(unsigned code, const char *what) {
  uart_puts("gpu lane1: FAIL ");
  uart_puts(what);
  uart_puts("\n");
  test_finish(code);
}

static uint32_t insn(uint32_t op, uint32_t rd, uint32_t ra, uint32_t rb,
                     uint32_t imm) {
  return (op << 26) | (rd << 23) | (ra << 20) | (rb << 17) | (imm & 0x1ffffu);
}

static void poke(uint32_t index, uint32_t value) {
  mmio_write32(AX_ROLE_GPU_DATA + 4u * index, value);
}
static uint32_t peek(uint32_t index) {
  return mmio_read32(AX_ROLE_GPU_DATA + 4u * index);
}

static void load_kernel(const uint32_t *words, uint32_t count) {
  for (uint32_t i = 0; i < count; ++i)
    mmio_write32(AX_ROLE_GPU_PROG + 4u * i, words[i]);
  mmio_write32(AX_ROLE_GPU_NINSN, count);
}

/* Doorbell to observed completion, measured exactly as the morph benchmark
 * measures it, including the same status polling. */
static void run_job(void) {
  mmio_write32(AX_ROLE_DOORBELL, 1u);
  for (uint32_t spins = 0; spins < 2000000u; ++spins) {
    uint32_t status = mmio_read32(AX_ROLE_STATUS);
    if ((status & AX_ROLE_STATUS_DONE) && !(status & AX_ROLE_STATUS_BUSY)) {
      mmio_write32(AX_ROLE_STATUS, AX_ROLE_STATUS_DONE);
      return;
    }
  }
  fail(20, "job never completed");
}

int main(void) {
  if (mmio_read32(AX_ROLE_ID) != AX_ROLE_GPU_ID)
    fail(1, "discovery: ROLE_ID is not GPUC");

  for (uint32_t i = 0; i < N_SAXPY; ++i) {
    ref_x[i] = (int32_t)i + 1;
    ref_y[i] = 100 + 2 * (int32_t)i;
  }
  for (uint32_t i = 0; i < N_SCALAR; ++i) ref_scalar_x[i] = (int32_t)i - 8;

  /* ---- SIMT SAXPY: y[i] = 3 * x[i] + y[i], the morph fabric's workload. ---- */
  const uint32_t saxpy[] = {
      insn(OP_TID, 0, 0, 0, 0),
      insn(OP_LDX, 1, 0, 0, 0),
      insn(OP_ADDI, 2, 0, 0, SAXPY_Y),
      insn(OP_LDX, 3, 2, 0, 0),
      insn(OP_MULI, 1, 1, 0, 3),
      insn(OP_ADD, 1, 1, 3, 0),
      insn(OP_ADDI, 4, 0, 0, SAXPY_OUT),
      insn(OP_STX, 0, 4, 1, 0),
      insn(OP_HALT, 0, 0, 0, 0),
  };
  const uint32_t saxpy_len = sizeof(saxpy) / sizeof(saxpy[0]);

  for (uint32_t i = 0; i < N_SAXPY; ++i) {
    poke(SAXPY_X + i, (uint32_t)ref_x[i]);
    poke(SAXPY_Y + i, (uint32_t)ref_y[i]);
    poke(SAXPY_OUT + i, 0u);
  }

  uint32_t t0 = ax_bench_rdcycle();
  load_kernel(saxpy, saxpy_len);
  mmio_write32(AX_ROLE_GPU_NTHREADS, N_SAXPY);
  uint32_t t1 = ax_bench_rdcycle();
  run_job();
  uint32_t t2 = ax_bench_rdcycle();
  uint32_t simt_reconfig = t1 - t0, simt_job = t2 - t1;

  for (uint32_t i = 0; i < N_SAXPY; ++i)
    if (peek(SAXPY_OUT + i) != 3u * (uint32_t)ref_x[i] + (uint32_t)ref_y[i])
      fail(10, "SAXPY mismatch");

  t0 = ax_bench_rdcycle();
  {
    uint32_t sum = 0u;
    for (uint32_t i = 0; i < N_SAXPY; ++i)
      sum += 3u * (uint32_t)ref_x[i] + (uint32_t)ref_y[i];
    sink = sum;
  }
  uint32_t simt_core = ax_bench_rdcycle() - t0;

  /* ---- Scalar recurrence: acc = (acc + x[i]) * 3 + 1, seeded at 7.
   *
   * The ISA is straight-line with no branches and PROG_WORDS is 64, so the
   * 64-step dependent chain cannot be unrolled into one kernel (it would need
   * roughly 4 instructions per step).  The only expressible form is one
   * dependent step per job: load the kernel once, then ring the doorbell 64
   * times with the host advancing the input.  That cost is the finding. */
  const uint32_t recur[] = {
      insn(OP_LI, 0, 0, 0, ACC_CELL),
      insn(OP_LDX, 1, 0, 0, 0),
      insn(OP_LI, 2, 0, 0, XCUR_CELL),
      insn(OP_LDX, 3, 2, 0, 0),
      insn(OP_ADD, 1, 1, 3, 0),
      insn(OP_MULI, 1, 1, 0, 3),
      insn(OP_ADDI, 1, 1, 0, 1),
      insn(OP_STX, 0, 0, 1, 0),
      insn(OP_HALT, 0, 0, 0, 0),
  };
  const uint32_t recur_len = sizeof(recur) / sizeof(recur[0]);

  poke(ACC_CELL, 7u);
  t0 = ax_bench_rdcycle();
  load_kernel(recur, recur_len);
  mmio_write32(AX_ROLE_GPU_NTHREADS, 1u);
  t1 = ax_bench_rdcycle();
  for (uint32_t i = 0; i < N_SCALAR; ++i) {
    poke(XCUR_CELL, (uint32_t)ref_scalar_x[i]);
    run_job();
  }
  t2 = ax_bench_rdcycle();
  uint32_t scalar_reconfig = t1 - t0, scalar_job = t2 - t1;

  uint32_t want = 7u;
  for (uint32_t i = 0; i < N_SCALAR; ++i)
    want = (want + (uint32_t)ref_scalar_x[i]) * 3u + 1u;
  if (peek(ACC_CELL) != want) fail(11, "scalar recurrence mismatch");

  t0 = ax_bench_rdcycle();
  {
    uint32_t a = 7u;
    for (uint32_t i = 0; i < N_SCALAR; ++i)
      a = (a + (uint32_t)ref_scalar_x[i]) * 3u + 1u;
    sink = a;
  }
  uint32_t scalar_core = ax_bench_rdcycle() - t0;

  uart_puts("gpu1lane simt     reconfig=");
  ax_bench_putdec(simt_reconfig);
  uart_puts(" fabric=");
  ax_bench_putdec(simt_job);
  uart_puts(" core=");
  ax_bench_putdec(simt_core);
  uart_puts(" items=");
  ax_bench_putdec(N_SAXPY);
  uart_puts("\n");

  uart_puts("gpu1lane scalar   reconfig=");
  ax_bench_putdec(scalar_reconfig);
  uart_puts(" fabric=");
  ax_bench_putdec(scalar_job);
  uart_puts(" core=");
  ax_bench_putdec(scalar_core);
  uart_puts(" items=");
  ax_bench_putdec(N_SCALAR);
  uart_puts(" jobs=64\n");

  uart_puts("gpu lane1: PASS (SIMT in one job; recurrence needs one job per "
            "step; GEMM does not fit this window)\n");
  uart_drain();
  test_finish(0);
  return 0;
}
