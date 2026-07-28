/* TPU-lite role proof against role.tpu-lite: discovery, weight/activation
 * loading, doorbell, completion polling, and result readback for three GEMM
 * jobs — plain, accumulating (the K > 8 tiling primitive), and ReLU — each
 * checked against a software reference computed on the core.  The same
 * reference matmul doubles as the DESIGN.md benchmark: the test prints the
 * cycle counts for the offloaded and the CPU matmul.  Runs on the RTL SoC
 * only; the ISS does not model the role window. */
#include "bench_report.h"
#include "platform.h"
#include "role.h"

#define TPU_M 12

static int8_t a_mat[TPU_M][8];
static int8_t w_mat[8][8];
static int32_t ref[TPU_M][8];
static uint32_t rng = 0x1234567u;

static uint32_t rnd(void) {
  rng = rng * 1103515245u + 12345u;
  return rng >> 16;
}

static void fail(unsigned code, const char *what) {
  uart_puts("role tpu-lite: FAIL ");
  uart_puts(what);
  uart_puts("\n");
  test_finish(code);
}

static uint32_t pack4(const int8_t *p) {
  return (uint32_t)(uint8_t)p[0] | ((uint32_t)(uint8_t)p[1] << 8) |
         ((uint32_t)(uint8_t)p[2] << 16) | ((uint32_t)(uint8_t)p[3] << 24);
}

static void randomize_inputs(void) {
  for (int r = 0; r < 8; ++r)
    for (int c = 0; c < 8; ++c) w_mat[r][c] = (int8_t)rnd();
  for (int m = 0; m < TPU_M; ++m)
    for (int k = 0; k < 8; ++k) a_mat[m][k] = (int8_t)rnd();
}

static void upload_inputs(void) {
  for (int r = 0; r < 8; ++r) {
    mmio_write32(AX_ROLE_TPU_W + 8u * (uint32_t)r, pack4(&w_mat[r][0]));
    mmio_write32(AX_ROLE_TPU_W + 8u * (uint32_t)r + 4u, pack4(&w_mat[r][4]));
  }
  for (int m = 0; m < TPU_M; ++m) {
    mmio_write32(AX_ROLE_TPU_A + 8u * (uint32_t)m, pack4(&a_mat[m][0]));
    mmio_write32(AX_ROLE_TPU_A + 8u * (uint32_t)m + 4u, pack4(&a_mat[m][4]));
  }
}

/* The software reference implements exactly the role's job semantics. */
static void reference(int acc, int relu) {
  for (int m = 0; m < TPU_M; ++m)
    for (int c = 0; c < 8; ++c) {
      int32_t s = acc ? ref[m][c] : 0;
      for (int r = 0; r < 8; ++r)
        s += (int32_t)a_mat[m][r] * (int32_t)w_mat[r][c];
      if (relu && s < 0) s = 0;
      ref[m][c] = s;
    }
}

static uint32_t compute_job(void) {
  const uint32_t start = ax_bench_rdcycle();
  role_ring_doorbell();
  role_wait_done();
  return ax_bench_rdcycle() - start;
}

static uint32_t run_job(uint32_t ctrl) {
  mmio_write32(AX_ROLE_TPU_CTRL, ctrl);
  mmio_write32(AX_ROLE_TPU_M, TPU_M);
  return compute_job();
}

static uint32_t readback_and_check(unsigned code, const char *what) {
  uint32_t checksum = 2166136261u;
  for (int m = 0; m < TPU_M; ++m) {
    for (int c = 0; c < 8; ++c) {
      const uint32_t address =
          AX_ROLE_TPU_C + 4u * (8u * (uint32_t)m + (uint32_t)c);
      const uint32_t actual = mmio_read32(address);
      checksum = ax_bench_checksum_step(checksum, actual);
      if (actual != (uint32_t)ref[m][c]) fail(code, what);
    }
  }
  return checksum;
}

struct tpu_timing {
  uint32_t upload;
  uint32_t compute;
  uint32_t readback;
  uint32_t total;
  uint32_t checksum;
};

static struct tpu_timing run_e2e(uint32_t ctrl, unsigned code,
                                 const char *what) {
  struct tpu_timing timing;
  const uint32_t total0 = ax_bench_rdcycle();
  const uint32_t upload0 = ax_bench_rdcycle();
  upload_inputs();
  mmio_write32(AX_ROLE_TPU_CTRL, ctrl);
  mmio_write32(AX_ROLE_TPU_M, TPU_M);
  timing.upload = ax_bench_rdcycle() - upload0;
  timing.compute = compute_job();
  const uint32_t readback0 = ax_bench_rdcycle();
  timing.checksum = readback_and_check(code, what);
  timing.readback = ax_bench_rdcycle() - readback0;
  timing.total = ax_bench_rdcycle() - total0;
  return timing;
}

int main(void) {
  if (role_id() != AX_ROLE_TPU_ID) fail(1, "discovery: ROLE_ID mismatch");
  if (mmio_read32(AX_ROLE_VERSION) == 0) fail(2, "VERSION reads zero");

  /* Job 1: plain GEMM, timed on both engines. */
  randomize_inputs();
  uint32_t cpu0 = ax_bench_rdcycle();
  reference(0, 0);
  uint32_t cpu_cycles = ax_bench_rdcycle() - cpu0;
  const struct tpu_timing first = run_e2e(0, 3, "plain GEMM mismatch");
  const uint32_t tpu_cycles = first.compute;
  if (mmio_read32(AX_ROLE_TPU_COUNT) != 1u) fail(4, "COUNT after first job");

  /* Clear DONE (write-1-to-clear) before reprogramming. */
  mmio_write32(AX_ROLE_STATUS, AX_ROLE_STATUS_DONE);
  if (mmio_read32(AX_ROLE_STATUS) & AX_ROLE_STATUS_DONE)
    fail(5, "DONE did not clear");

  /* Job 2: accumulate a second weight/activation tile into C — the K > 8
   * tiling primitive. */
  randomize_inputs();
  reference(1, 0);
  upload_inputs();
  (void)run_job(AX_ROLE_TPU_CTRL_ACC);
  (void)readback_and_check(6, "accumulate mismatch");
  if (mmio_read32(AX_ROLE_TPU_COUNT) != 2u) fail(7, "COUNT after second job");

  /* Job 3: fresh GEMM with the ReLU output stage. */
  randomize_inputs();
  reference(0, 1);
  upload_inputs();
  (void)run_job(AX_ROLE_TPU_CTRL_RELU);
  (void)readback_and_check(8, "relu mismatch");

  /* M = 0 completes immediately and leaves C untouched. */
  mmio_write32(AX_ROLE_TPU_M, 0u);
  role_ring_doorbell();
  role_wait_done();
  if (mmio_read32(AX_ROLE_TPU_COUNT) != 4u) fail(9, "COUNT after empty job");
  (void)readback_and_check(10, "empty job disturbed C");

  uart_puts("tpu gemm cycles: 0x");
  ax_bench_puthex(tpu_cycles);
  uart_puts("\ncpu gemm cycles: 0x");
  ax_bench_puthex(cpu_cycles);
  uart_puts("\ntpu e2e: upload="); ax_bench_putdec(first.upload);
  uart_puts(" compute="); ax_bench_putdec(first.compute);
  uart_puts(" readback+verify="); ax_bench_putdec(first.readback);
  uart_puts(" total="); ax_bench_putdec(first.total);
  uart_puts(" checksum=0x"); ax_bench_puthex(first.checksum);
  ax_bench_report_projected_time(first.total);
  uart_puts("\nrole tpu-lite: PASS\n");
  test_finish(0);
}
