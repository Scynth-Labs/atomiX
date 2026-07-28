/* GPU-compute performance test for role.gpu-compute (and the reduced-lane
 * role.gpu-compute with lanes=4).  It sweeps the thread count over two kernels of
 * different arithmetic intensity, times each on the SIMT engine and on an
 * equivalent on-core loop, and reports cycles, speedup, and SIMT throughput
 * (threads retired per cycle).
 *
 * It is a regression *test*, not just a benchmark: every GPU result is checked
 * element-by-element against the on-core computation, and the run FAILs unless
 * the engine also clears a minimum speedup bar at the largest thread count.
 * The bar is set well below the observed margin so it holds for both the 8-lane
 * and 4-lane builds (fewer lanes only means more waves, i.e. more cycles, never
 * a wrong result).  Runs on the RTL SoC only; the ISS does not model the role
 * window. */
#include "bench_report.h"
#include "platform.h"
#include "role.h"

#define BASE_A   0
#define BASE_B   1024
#define BASE_C   2048
/* Speedup bars at the largest thread count, in tenths (x10).  saxpy is
 * memory-bound — its loads/stores serialize across lanes, so the SIMT win is
 * modest and we only require it to beat the CPU.  poly is arithmetic-bound
 * (4 multiplies/thread) — here the lanes run in parallel, so we require a real
 * multiple.  Both bars are set below the 8-lane and 4-lane observed margins. */
#define MIN_SAXPY_X10  10     /* >= 1.0x : memory-bound, must not lose to CPU  */
#define MIN_POLY_X10   40     /* >= 4.0x : compute-bound, proves SIMT parallelism */

static int32_t a_in[512], b_in[512], c_cpu[512];
static uint32_t rng = 0x1234abcdu;

static uint32_t rnd(void) { rng = rng * 1103515245u + 12345u; return rng >> 16; }

static void fail(unsigned code, const char *what) {
  uart_puts("gpu-perf: FAIL ");
  uart_puts(what);
  uart_puts("\n");
  test_finish(code);
}

static void putdec(uint32_t v) {
  char buf[10];
  int n = 0;
  if (v == 0) { uart_putchar('0'); return; }
  while (v) { buf[n++] = (char)('0' + v % 10); v /= 10; }
  while (n--) uart_putchar(buf[n]);
}
static void pad(uint32_t v, int width) {
  int d = 1; uint32_t t = v;
  while (t >= 10) { t /= 10; d++; }
  for (int i = d; i < width; ++i) uart_putchar(' ');
  putdec(v);
}

static void upload_prog(const uint32_t *prog, int n) {
  for (int i = 0; i < n; ++i)
    mmio_write32(AX_ROLE_GPU_PROG + 4u * (uint32_t)i, prog[i]);
}

/* Push A and B into the engine's global buffer and clear the C region. */
static void upload_inputs(int n) {
  for (int i = 0; i < n; ++i) {
    mmio_write32(AX_ROLE_GPU_DATA + 4u * (uint32_t)(BASE_A + i), (uint32_t)a_in[i]);
    mmio_write32(AX_ROLE_GPU_DATA + 4u * (uint32_t)(BASE_B + i), (uint32_t)b_in[i]);
    mmio_write32(AX_ROLE_GPU_DATA + 4u * (uint32_t)(BASE_C + i), 0u);
  }
}

struct gpu_timing {
  uint32_t upload;
  uint32_t compute;
  uint32_t readback;
  uint32_t total;
  uint32_t checksum;
};

/* Time the complete offload boundary. Upload includes program, operands, and
 * launch registers. Readback includes correctness verification and a stable
 * checksum suitable for comparing simulation with physical UART output. */
static struct gpu_timing run_gpu(const uint32_t *prog, int ninsn, int n,
                                 const int32_t *expected, unsigned code,
                                 const char *what) {
  struct gpu_timing timing;
  const uint32_t total0 = ax_bench_rdcycle();
  const uint32_t upload0 = ax_bench_rdcycle();
  upload_prog(prog, ninsn);
  upload_inputs(n);
  mmio_write32(AX_ROLE_GPU_NTHREADS, (uint32_t)n);
  mmio_write32(AX_ROLE_GPU_NINSN, (uint32_t)ninsn);
  timing.upload = ax_bench_rdcycle() - upload0;

  const uint32_t compute0 = ax_bench_rdcycle();
  role_ring_doorbell();
  role_wait_done();
  timing.compute = ax_bench_rdcycle() - compute0;
  mmio_write32(AX_ROLE_STATUS, AX_ROLE_STATUS_DONE);

  const uint32_t readback0 = ax_bench_rdcycle();
  timing.checksum = 2166136261u;
  for (int i = 0; i < n; ++i) {
    const uint32_t actual =
        mmio_read32(AX_ROLE_GPU_DATA + 4u * (uint32_t)(BASE_C + i));
    timing.checksum = ax_bench_checksum_step(timing.checksum, actual);
    if (actual != (uint32_t)expected[i]) fail(code, what);
  }
  timing.readback = ax_bench_rdcycle() - readback0;
  timing.total = ax_bench_rdcycle() - total0;
  return timing;
}

static void seed(int n) {
  for (int i = 0; i < n; ++i) {
    a_in[i] = (int32_t)(rnd() & 0xffu) - 128;
    b_in[i] = (int32_t)(rnd() & 0xffu) - 128;
  }
}

static int slen(const char *s) { int n = 0; while (s[n]) n++; return n; }

/* One benchmark row: GPU vs on-core, print cycles/speedup/throughput. */
static uint32_t bench(const char *name, const uint32_t *prog, int ninsn,
                      int n, void (*cpu)(int), unsigned code) {
  seed(n);
  uint32_t t0 = ax_bench_rdcycle();
  cpu(n);
  uint32_t c = ax_bench_rdcycle() - t0;
  const struct gpu_timing gpu =
      run_gpu(prog, ninsn, n, c_cpu, code, name);
  const uint32_t g = gpu.compute;

  uart_puts("  ");
  uart_puts(name);
  for (int i = 0; i < 8 - slen(name); ++i) uart_putchar(' ');
  uart_puts(" N="); pad((uint32_t)n, 4);
  uart_puts("  gpu="); pad(g, 7);
  uart_puts("  cpu="); pad(c, 7);
  uart_puts("  speedup="); pad((c * 10u) / g / 10u, 2);
  uart_putchar('.'); putdec((c * 10u) / g % 10u);
  uart_puts("x\n");

  uart_puts("    e2e upload="); putdec(gpu.upload);
  uart_puts(" compute="); putdec(gpu.compute);
  uart_puts(" readback+verify="); putdec(gpu.readback);
  uart_puts(" total="); putdec(gpu.total);
  uart_puts(" checksum=0x"); ax_bench_puthex(gpu.checksum);
  ax_bench_report_projected_time(gpu.total);
  uart_puts("\n");
  return (c * 10u) / g;   /* speedup x10 */
}

/* On-core references, matching the SIMT kernels below. */
static void cpu_saxpy(int n) {
  for (int i = 0; i < n; ++i) c_cpu[i] = 3 * a_in[i] + b_in[i];
}
static void cpu_poly(int n) {
  /* C = ((A*A + B)*A - B) * A  -- 4 multiplies, arithmetic-heavy */
  for (int i = 0; i < n; ++i) {
    int32_t a = a_in[i], b = b_in[i];
    c_cpu[i] = (((a * a + b) * a - b) * a);
  }
}

int main(void) {
  if (role_id() != AX_ROLE_GPU_ID) fail(1, "discovery: ROLE_ID mismatch");

  /* saxpy: C[tid] = 3*A[tid] + B[tid]  (memory-bound, little arithmetic). */
  const uint32_t saxpy[] = {
    gpu_insn(AX_GPU_TID,  0, 0, 0, 0),
    gpu_insn(AX_GPU_LDX,  1, 0, 0, 0),           /* r1 = A[tid]          */
    gpu_insn(AX_GPU_ADDI, 2, 0, 0, BASE_B),
    gpu_insn(AX_GPU_LDX,  3, 2, 0, 0),           /* r3 = B[tid]          */
    gpu_insn(AX_GPU_MULI, 1, 1, 0, 3),
    gpu_insn(AX_GPU_ADD,  1, 1, 3, 0),
    gpu_insn(AX_GPU_ADDI, 4, 0, 0, BASE_C),
    gpu_insn(AX_GPU_STX,  0, 4, 1, 0),
    gpu_insn(AX_GPU_HALT, 0, 0, 0, 0),
  };
  /* poly: C = (((A*A+B)*A - B)*A  (arithmetic-bound: 4 MULs per thread). */
  const uint32_t poly[] = {
    gpu_insn(AX_GPU_TID,  0, 0, 0, 0),
    gpu_insn(AX_GPU_LDX,  1, 0, 0, 0),           /* r1 = A               */
    gpu_insn(AX_GPU_ADDI, 2, 0, 0, BASE_B),
    gpu_insn(AX_GPU_LDX,  2, 2, 0, 0),           /* r2 = B               */
    gpu_insn(AX_GPU_MUL,  3, 1, 1, 0),           /* r3 = A*A             */
    gpu_insn(AX_GPU_ADD,  3, 3, 2, 0),           /* r3 = A*A + B         */
    gpu_insn(AX_GPU_MUL,  3, 3, 1, 0),           /* r3 = (A*A+B)*A       */
    gpu_insn(AX_GPU_SUB,  3, 3, 2, 0),           /* r3 = ...-B           */
    gpu_insn(AX_GPU_MUL,  3, 3, 1, 0),           /* r3 = (...)*A         */
    gpu_insn(AX_GPU_ADDI, 4, 0, 0, BASE_C),
    gpu_insn(AX_GPU_STX,  0, 4, 3, 0),
    gpu_insn(AX_GPU_HALT, 0, 0, 0, 0),
  };
  int saxpy_n = (int)(sizeof saxpy / sizeof saxpy[0]);
  int poly_n  = (int)(sizeof poly  / sizeof poly[0]);

  uart_puts("gpu-perf: SIMT engine vs on-core (cycles, lower is better)\n");
  const int sweep[] = {32, 64, 128, 256};
  uint32_t last_saxpy = 0, last_poly = 0;
  for (int s = 0; s < 4; ++s) {
    last_saxpy = bench("saxpy", saxpy, saxpy_n, sweep[s], cpu_saxpy, 2);
    last_poly  = bench("poly",  poly,  poly_n,  sweep[s], cpu_poly,  3);
  }

  /* Regression bar: both kernels must beat the CPU by the minimum margin at
   * the largest thread count.  poly (arithmetic-heavy) should win by more. */
  if (last_saxpy < MIN_SAXPY_X10) fail(4, "saxpy speedup below bar");
  if (last_poly  < MIN_POLY_X10)  fail(5, "poly speedup below bar");

  uart_puts("gpu-perf: PASS\n");
  test_finish(0);
}
