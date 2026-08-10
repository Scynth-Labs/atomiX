/* Physical test for role.morph, the R2 coarse-grained reconfigurable fabric.
 *
 * The claim being tested on real silicon is the one simulation already makes:
 * a single resident fabric, reconfigured only by writing its 13-word genome,
 * computes a scalar recurrence, a SIMT SAXPY, and a systolic GEMM exactly, and
 * refuses any descriptor that would address outside its own window.  Nothing
 * about the fabric changes between personalities and no bitstream is reloaded.
 *
 * Every result is checked element-by-element against an on-core reference
 * computed here, so this is a regression test and not a demonstration.  Runs on
 * the RTL SoC and the board; the ISS does not model the role window. */
#include "bench_report.h"
#include "platform.h"
#include "role.h"

#define N_SCALAR 64
#define N_SAXPY  50
#define GEMM_M   12
#define GEMM_K    8
#define GEMM_N    8

/* Data-buffer layout, in words, inside the fabric's 256-word window. */
#define SCALAR_X   0
#define SCALAR_OUT 200
#define SAXPY_X    0
#define SAXPY_Y    64
#define SAXPY_OUT  128
#define GEMM_A     0
#define GEMM_B     96
#define GEMM_C     160

/* The on-core reference loops exist to be timed, so their results must be
 * observably consumed or -O2 deletes them and the comparison reads as an
 * impossible four cycles. */
static volatile uint32_t sink;

/* RAM copies of the same operands the fabric holds in its window.  The on-core
 * reference reads these rather than recomputing indices with a modulo, so the
 * comparison measures arithmetic against arithmetic instead of charging the
 * core for divisions the fabric never performs.
 *
 * They are volatile because every value here is a compile-time constant: given
 * plain arrays, -O2 folds the reference loops toward a closed form and the
 * "on-core" baseline stops measuring the work the fabric actually does.  The
 * fabric loads each operand from memory, so the reference must too. */
static volatile int32_t ref_scalar_x[N_SCALAR];
static volatile int32_t ref_saxpy_x[N_SAXPY], ref_saxpy_y[N_SAXPY];
static volatile int32_t ref_gemm_a[GEMM_M * GEMM_K];
static volatile int32_t ref_gemm_b[GEMM_K * GEMM_N];

static void fail(unsigned code, const char *what) {
  uart_puts("role morph: FAIL ");
  uart_puts(what);
  uart_puts("\n");
  test_finish(code);
}

static uint32_t pe_desc(uint32_t a, uint32_t b, uint32_t c, uint32_t d,
                        uint32_t rule) {
  return (rule << 12) | (d << 9) | (c << 6) | (b << 3) | a;
}

static void poke(uint32_t index, uint32_t value) {
  mmio_write32(AX_ROLE_MORPH_DATA + 4u * index, value);
}

static uint32_t peek(uint32_t index) {
  return mmio_read32(AX_ROLE_MORPH_DATA + 4u * index);
}

/* Writes the whole genome, then declares its length.  A job whose NCONFIG does
 * not match the fabric's CFG_WORDS is refused, which is what stops a partially
 * written personality from ever running. */
static void load_genome(uint32_t mode, uint32_t m, uint32_t n, uint32_t k,
                        uint32_t a_base, uint32_t a_row, uint32_t a_k,
                        uint32_t a_col, uint32_t b_base, uint32_t b_col,
                        uint32_t b_k, uint32_t c_base, uint32_t c_row,
                        uint32_t imm0, uint32_t imm1, uint32_t pe,
                        uint32_t acc_init) {
  const uint32_t genome[AX_ROLE_MORPH_CFG_WORDS] = {
      mode,
      (n << 16) | m,
      k,
      (a_row << 16) | a_base,
      (a_col << 16) | a_k,
      (b_col << 16) | b_base,
      b_k,
      (c_row << 16) | c_base,
      imm0,
      imm1,
      (pe << 14) | pe,
      (pe << 14) | pe,
      acc_init,
  };
  for (uint32_t i = 0; i < AX_ROLE_MORPH_CFG_WORDS; ++i)
    mmio_write32(AX_ROLE_MORPH_CFG + 4u * i, genome[i]);
  mmio_write32(AX_ROLE_MORPH_NCONFIG, AX_ROLE_MORPH_CFG_WORDS);
}

/* Rings the doorbell and waits for DONE.  Bounded so a fabric that never
 * finishes fails the test rather than hanging the board. */
static uint32_t run_job(void) {
  uint32_t cycles = 0;
  mmio_write32(AX_ROLE_DOORBELL, 1u);
  for (;;) {
    uint32_t status = mmio_read32(AX_ROLE_STATUS);
    if (status & AX_ROLE_STATUS_REJECTED) return 0u;
    if ((status & AX_ROLE_STATUS_DONE) && !(status & AX_ROLE_STATUS_BUSY)) {
      mmio_write32(AX_ROLE_STATUS, AX_ROLE_STATUS_DONE);
      return cycles ? cycles : 1u;
    }
    if (++cycles > 2000000u) fail(20, "job never completed");
  }
}

/* Expects the descriptor to be refused rather than executed.  Polling for a
 * definite outcome instead of sampling STATUS once keeps the check immune to
 * bus read latency: the descriptor either rejects or it completes a job, and
 * only the first is acceptable. */
static void expect_reject(const char *what) {
  mmio_write32(AX_ROLE_DOORBELL, 1u);
  for (uint32_t spins = 0; spins < 2000000u; ++spins) {
    uint32_t status = mmio_read32(AX_ROLE_STATUS);
    if (status & AX_ROLE_STATUS_REJECTED) {
      if (status & AX_ROLE_STATUS_BUSY) fail(31, "rejected descriptor set BUSY");
      mmio_write32(AX_ROLE_STATUS, AX_ROLE_STATUS_REJECTED);
      return;
    }
    if (status & AX_ROLE_STATUS_DONE) fail(30, what);
  }
  fail(32, "descriptor neither rejected nor completed");
}

int main(void) {
  if (mmio_read32(AX_ROLE_ID) != AX_ROLE_MORPH_ID)
    fail(1, "discovery: ROLE_ID is not MRPH");
  if (mmio_read32(AX_ROLE_VERSION) == 0u) fail(2, "VERSION reads zero");

  uint32_t caps = mmio_read32(AX_ROLE_MORPH_CAPS);
  uint32_t pes = caps >> 24;
  uint32_t cfg_words = (caps >> 16) & 0xffu;
  uint32_t data_words = caps & 0xffffu;
  if (cfg_words != AX_ROLE_MORPH_CFG_WORDS) fail(3, "CAPS genome size");
  if (pes == 0u || data_words == 0u) fail(4, "CAPS reports an empty fabric");
  if (mmio_read32(AX_ROLE_MORPH_GENERATION) != 0u)
    fail(5, "generation did not start at zero");

  /* ---- Personality 1: acc = (acc + x[i]) * 3 + 1, seeded at 7. ---- */
  for (uint32_t i = 0; i < N_SCALAR; ++i)
    poke(SCALAR_X + i, (uint32_t)((int32_t)i - 8));
  load_genome(AX_MORPH_MODE_SCALAR, 1u, 1u, N_SCALAR,
              SCALAR_X, 0u, 1u, 0u, 0u, 0u, 0u, SCALAR_OUT, 0u,
              3u, 1u,
              pe_desc(AX_MORPH_SRC_ACC, AX_MORPH_SRC_A, AX_MORPH_SRC_IMM0,
                      AX_MORPH_SRC_IMM1, AX_MORPH_ACC_LOAD),
              7u);
  mmio_write32(AX_ROLE_MORPH_NITEMS, N_SCALAR);
  if (!run_job()) fail(10, "scalar personality was rejected");
  uint32_t acc = 7u;
  for (uint32_t i = 0; i < N_SCALAR; ++i)
    acc = (acc + (uint32_t)((int32_t)i - 8)) * 3u + 1u;
  if (peek(SCALAR_OUT) != acc) fail(11, "scalar recurrence mismatch");

  /* ---- Personality 2: y[i] = 3 * x[i] + y[i]. ---- */
  for (uint32_t i = 0; i < N_SAXPY; ++i) {
    poke(SAXPY_X + i, i + 1u);
    poke(SAXPY_Y + i, 100u + 2u * i);
    poke(SAXPY_OUT + i, 0u);
  }
  load_genome(AX_MORPH_MODE_SIMT, 1u, N_SAXPY, 1u,
              SAXPY_X, 0u, 0u, 1u, SAXPY_Y, 1u, 0u, SAXPY_OUT, 0u,
              3u, 0u,
              pe_desc(AX_MORPH_SRC_A, AX_MORPH_SRC_ZERO, AX_MORPH_SRC_IMM0,
                      AX_MORPH_SRC_B, AX_MORPH_ACC_LOAD),
              0u);
  mmio_write32(AX_ROLE_MORPH_NITEMS, N_SAXPY);
  if (!run_job()) fail(12, "SIMT personality was rejected");
  for (uint32_t i = 0; i < N_SAXPY; ++i)
    if (peek(SAXPY_OUT + i) != 3u * (i + 1u) + 100u + 2u * i)
      fail(13, "SIMT SAXPY mismatch");

  /* ---- Personality 3: C[12x8] = A[12x8] * B[8x8]. ---- */
  for (uint32_t i = 0; i < GEMM_M * GEMM_K; ++i)
    poke(GEMM_A + i, (uint32_t)((int32_t)(i % 7u) - 3));
  for (uint32_t i = 0; i < GEMM_K * GEMM_N; ++i)
    poke(GEMM_B + i, (uint32_t)((int32_t)(i % 5u) - 2));
  for (uint32_t i = 0; i < GEMM_M * GEMM_N; ++i) poke(GEMM_C + i, 0u);
  load_genome(AX_MORPH_MODE_SYSTOLIC, GEMM_M, GEMM_N, GEMM_K,
              GEMM_A, GEMM_K, 1u, 0u, GEMM_B, 1u, GEMM_N, GEMM_C, GEMM_N,
              0u, 0u,
              pe_desc(AX_MORPH_SRC_A, AX_MORPH_SRC_ZERO, AX_MORPH_SRC_B,
                      AX_MORPH_SRC_ACC, AX_MORPH_ACC_LOAD),
              0u);
  mmio_write32(AX_ROLE_MORPH_NITEMS, GEMM_M * GEMM_N);
  if (!run_job()) fail(14, "systolic personality was rejected");
  for (uint32_t row = 0; row < GEMM_M; ++row) {
    for (uint32_t col = 0; col < GEMM_N; ++col) {
      uint32_t want = 0u;
      for (uint32_t k = 0; k < GEMM_K; ++k)
        want += (uint32_t)((int32_t)((row * GEMM_K + k) % 7u) - 3) *
                (uint32_t)((int32_t)((k * GEMM_N + col) % 5u) - 2);
      if (peek(GEMM_C + row * GEMM_N + col) != want)
        fail(15, "systolic GEMM mismatch");
    }
  }

  if (mmio_read32(AX_ROLE_MORPH_COUNT) != 3u)
    fail(16, "three personalities did not complete on one fabric");
  if (mmio_read32(AX_ROLE_MORPH_GENERATION) != 3u)
    fail(17, "configuration generation did not advance three times");

  /* ---- Descriptor confinement on real silicon. ---- */
  uint32_t generation = mmio_read32(AX_ROLE_MORPH_GENERATION);
  uint32_t rejects = mmio_read32(AX_ROLE_MORPH_REJECTS);
  uint32_t canary = peek(GEMM_C);
  uint32_t simt_pe = pe_desc(AX_MORPH_SRC_A, AX_MORPH_SRC_ZERO,
                             AX_MORPH_SRC_IMM0, AX_MORPH_SRC_B,
                             AX_MORPH_ACC_LOAD);

  /* Output stream would run past the end of the window. */
  load_genome(AX_MORPH_MODE_SIMT, 1u, 64u, 1u, 0u, 0u, 0u, 1u, 64u, 1u, 0u,
              250u, 0u, 3u, 0u, simt_pe, 0u);
  mmio_write32(AX_ROLE_MORPH_NITEMS, 8u);
  expect_reject("output stream leaving the window was accepted");

  /* Input stream would run past the end of the window. */
  load_genome(AX_MORPH_MODE_SIMT, 1u, 64u, 1u, 250u, 0u, 0u, 1u, 64u, 1u, 0u,
              128u, 0u, 3u, 0u, simt_pe, 0u);
  mmio_write32(AX_ROLE_MORPH_NITEMS, 8u);
  expect_reject("input stream leaving the window was accepted");

  /* Unknown personality mode. */
  load_genome(9u, 1u, 8u, 1u, 0u, 0u, 0u, 1u, 64u, 1u, 0u, 128u, 0u, 3u, 0u,
              simt_pe, 0u);
  mmio_write32(AX_ROLE_MORPH_NITEMS, 8u);
  expect_reject("unknown personality mode was accepted");

  /* Genome shorter than the fabric requires. */
  load_genome(AX_MORPH_MODE_SIMT, 1u, 8u, 1u, 0u, 0u, 0u, 1u, 64u, 1u, 0u,
              128u, 0u, 3u, 0u, simt_pe, 0u);
  mmio_write32(AX_ROLE_MORPH_NCONFIG, AX_ROLE_MORPH_CFG_WORDS - 1u);
  mmio_write32(AX_ROLE_MORPH_NITEMS, 8u);
  expect_reject("short genome was accepted");

  if (mmio_read32(AX_ROLE_MORPH_GENERATION) != generation)
    fail(18, "a rejected descriptor advanced the generation");
  if (mmio_read32(AX_ROLE_MORPH_REJECTS) != rejects + 4u)
    fail(19, "rejections were not counted");
  if (peek(GEMM_C) != canary)
    fail(21, "a rejected descriptor modified the previous result");

  /* A rejection must not poison the fabric: the last good genome still runs. */
  for (uint32_t i = 0; i < 16u; ++i) {
    poke(SAXPY_X + i, i + 1u);
    poke(SAXPY_Y + i, 10u * i);
    poke(SAXPY_OUT + i, 0u);
  }
  load_genome(AX_MORPH_MODE_SIMT, 1u, 16u, 1u,
              SAXPY_X, 0u, 0u, 1u, SAXPY_Y, 1u, 0u, SAXPY_OUT, 0u,
              3u, 0u, simt_pe, 0u);
  mmio_write32(AX_ROLE_MORPH_NITEMS, 16u);
  if (!run_job()) fail(22, "fabric did not recover after a rejection");
  for (uint32_t i = 0; i < 16u; ++i)
    if (peek(SAXPY_OUT + i) != 3u * (i + 1u) + 10u * i)
      fail(23, "post-rejection result mismatch");

  /* ---- Cost of the flexibility, measured on the same silicon. ----
   *
   * R2 needs three numbers per personality, not just area: what it costs to
   * become that personality, what the fabric then takes to finish the job, and
   * what the management core would have taken to do the same work itself.
   *
   * `reconfig` is the whole personality change — thirteen genome words plus
   * NITEMS — so it is directly comparable to a bitstream reload.  `fabric` is
   * doorbell to observed completion, which includes the polling reads the host
   * actually pays, not an idealized engine time.  `core` is the same arithmetic
   * in a plain loop on the management CPU. */
  uint32_t t0, t1, t2;
  uint32_t reconfig[3], fabric[3], core[3];
  uint32_t simt_pe2 = pe_desc(AX_MORPH_SRC_A, AX_MORPH_SRC_ZERO,
                              AX_MORPH_SRC_IMM0, AX_MORPH_SRC_B,
                              AX_MORPH_ACC_LOAD);

  for (uint32_t i = 0; i < N_SCALAR; ++i) ref_scalar_x[i] = (int32_t)i - 8;
  for (uint32_t i = 0; i < N_SAXPY; ++i) {
    ref_saxpy_x[i] = (int32_t)i + 1;
    ref_saxpy_y[i] = 100 + 2 * (int32_t)i;
  }
  for (uint32_t i = 0; i < GEMM_M * GEMM_K; ++i)
    ref_gemm_a[i] = (int32_t)(i % 7u) - 3;
  for (uint32_t i = 0; i < GEMM_K * GEMM_N; ++i)
    ref_gemm_b[i] = (int32_t)(i % 5u) - 2;

  for (uint32_t i = 0; i < N_SCALAR; ++i)
    poke(SCALAR_X + i, (uint32_t)((int32_t)i - 8));
  t0 = ax_bench_rdcycle();
  load_genome(AX_MORPH_MODE_SCALAR, 1u, 1u, N_SCALAR,
              SCALAR_X, 0u, 1u, 0u, 0u, 0u, 0u, SCALAR_OUT, 0u, 3u, 1u,
              pe_desc(AX_MORPH_SRC_ACC, AX_MORPH_SRC_A, AX_MORPH_SRC_IMM0,
                      AX_MORPH_SRC_IMM1, AX_MORPH_ACC_LOAD), 7u);
  mmio_write32(AX_ROLE_MORPH_NITEMS, N_SCALAR);
  t1 = ax_bench_rdcycle();
  if (!run_job()) fail(40, "scalar benchmark was rejected");
  t2 = ax_bench_rdcycle();
  reconfig[0] = t1 - t0;
  fabric[0] = t2 - t1;
  t0 = ax_bench_rdcycle();
  {
    uint32_t a = 7u;
    for (uint32_t i = 0; i < N_SCALAR; ++i)
      a = (a + (uint32_t)ref_scalar_x[i]) * 3u + 1u;
    sink = a;
  }
  core[0] = ax_bench_rdcycle() - t0;

  for (uint32_t i = 0; i < N_SAXPY; ++i) {
    poke(SAXPY_X + i, i + 1u);
    poke(SAXPY_Y + i, 100u + 2u * i);
  }
  t0 = ax_bench_rdcycle();
  load_genome(AX_MORPH_MODE_SIMT, 1u, N_SAXPY, 1u,
              SAXPY_X, 0u, 0u, 1u, SAXPY_Y, 1u, 0u, SAXPY_OUT, 0u, 3u, 0u,
              simt_pe2, 0u);
  mmio_write32(AX_ROLE_MORPH_NITEMS, N_SAXPY);
  t1 = ax_bench_rdcycle();
  if (!run_job()) fail(41, "SIMT benchmark was rejected");
  t2 = ax_bench_rdcycle();
  reconfig[1] = t1 - t0;
  fabric[1] = t2 - t1;
  t0 = ax_bench_rdcycle();
  {
    uint32_t sum = 0u;
    for (uint32_t i = 0; i < N_SAXPY; ++i)
      sum += 3u * (uint32_t)ref_saxpy_x[i] + (uint32_t)ref_saxpy_y[i];
    sink = sum;
  }
  core[1] = ax_bench_rdcycle() - t0;

  for (uint32_t i = 0; i < GEMM_M * GEMM_K; ++i)
    poke(GEMM_A + i, (uint32_t)((int32_t)(i % 7u) - 3));
  for (uint32_t i = 0; i < GEMM_K * GEMM_N; ++i)
    poke(GEMM_B + i, (uint32_t)((int32_t)(i % 5u) - 2));
  t0 = ax_bench_rdcycle();
  load_genome(AX_MORPH_MODE_SYSTOLIC, GEMM_M, GEMM_N, GEMM_K,
              GEMM_A, GEMM_K, 1u, 0u, GEMM_B, 1u, GEMM_N, GEMM_C, GEMM_N,
              0u, 0u,
              pe_desc(AX_MORPH_SRC_A, AX_MORPH_SRC_ZERO, AX_MORPH_SRC_B,
                      AX_MORPH_SRC_ACC, AX_MORPH_ACC_LOAD), 0u);
  mmio_write32(AX_ROLE_MORPH_NITEMS, GEMM_M * GEMM_N);
  t1 = ax_bench_rdcycle();
  if (!run_job()) fail(42, "systolic benchmark was rejected");
  t2 = ax_bench_rdcycle();
  reconfig[2] = t1 - t0;
  fabric[2] = t2 - t1;
  t0 = ax_bench_rdcycle();
  {
    uint32_t sum = 0u;
    for (uint32_t row = 0; row < GEMM_M; ++row)
      for (uint32_t col = 0; col < GEMM_N; ++col) {
        uint32_t want = 0u;
        for (uint32_t k = 0; k < GEMM_K; ++k)
          want += (uint32_t)ref_gemm_a[row * GEMM_K + k] *
                  (uint32_t)ref_gemm_b[k * GEMM_N + col];
        sum += want;
      }
    sink = sum;
  }
  core[2] = ax_bench_rdcycle() - t0;

  static const char *const names[3] = {"scalar   ", "simt     ", "systolic "};
  static const uint32_t items[3] = {N_SCALAR, N_SAXPY, GEMM_M * GEMM_N};
  for (uint32_t p = 0; p < 3u; ++p) {
    uart_puts("morph ");
    uart_puts(names[p]);
    uart_puts(" reconfig=");
    ax_bench_putdec(reconfig[p]);
    uart_puts(" fabric=");
    ax_bench_putdec(fabric[p]);
    uart_puts(" core=");
    ax_bench_putdec(core[p]);
    uart_puts(" items=");
    ax_bench_putdec(items[p]);
    uart_puts(" fabric_cycles_per_item_x10=");
    ax_bench_putdec((fabric[p] * 10u + items[p] / 2u) / items[p]);
    uart_puts("\n");
  }

  uart_puts("role morph: PASS (scalar, SIMT, systolic on one fabric; "
            "descriptors confined)\n");
  uart_drain();
  test_finish(0);
  return 0;
}
