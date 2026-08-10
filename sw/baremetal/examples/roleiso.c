/* R1 on real silicon: the role-window fence, exercised on the board.
 *
 * The R1 exit gate requires that "role isolation is asserted during loading".
 * `axroleiso` implements that fence and has been proven in Verilator, but it
 * had never run on hardware — and a fence is exactly the kind of logic whose
 * simulation model can be right while the routed design is not, because its
 * whole job is to keep a bus alive when the thing on the far side has stopped
 * answering.
 *
 * The sequence below is the swap protocol without the bitstream write, which
 * is the part the Gowin toolchain cannot express: quiesce, fence, hold the
 * role in reset as if its fabric were being rewritten, release, rediscover,
 * and prove the management shell never stopped. The role is the morph fabric,
 * so "rediscover and reconfigure" is a real personality change rather than a
 * no-op.
 *
 * Every stage prints before it acts. If the fence is broken the CPU stalls on
 * a bus transaction that never completes and the board goes silent, so the
 * last line printed identifies the failing step. */
#include "bench_report.h"
#include "platform.h"
#include "role.h"

#define N_SAXPY 50
#define SAXPY_X   0
#define SAXPY_Y   64
#define SAXPY_OUT 128

static volatile uint32_t sink;

static void fail(unsigned code, const char *what) {
  uart_puts("roleiso: FAIL ");
  uart_puts(what);
  uart_puts("\n");
  uart_drain();
  test_finish(code);
}

static void step(const char *what) {
  uart_puts("roleiso: ");
  uart_puts(what);
  uart_puts("\n");
  uart_drain();
}

static uint32_t pe_desc(uint32_t a, uint32_t b, uint32_t c, uint32_t d,
                        uint32_t rule) {
  return (rule << 12) | (d << 9) | (c << 6) | (b << 3) | a;
}

static void poke(uint32_t i, uint32_t v) {
  mmio_write32(AX_ROLE_MORPH_DATA + 4u * i, v);
}
static uint32_t peek(uint32_t i) {
  return mmio_read32(AX_ROLE_MORPH_DATA + 4u * i);
}

static void load_genome(uint32_t mode, uint32_t m, uint32_t n, uint32_t k,
                        uint32_t a_base, uint32_t a_row, uint32_t a_k,
                        uint32_t a_col, uint32_t b_base, uint32_t b_col,
                        uint32_t b_k, uint32_t c_base, uint32_t c_row,
                        uint32_t imm0, uint32_t imm1, uint32_t pe,
                        uint32_t acc_init) {
  const uint32_t g[AX_ROLE_MORPH_CFG_WORDS] = {
      mode, (n << 16) | m, k, (a_row << 16) | a_base, (a_col << 16) | a_k,
      (b_col << 16) | b_base, b_k, (c_row << 16) | c_base, imm0, imm1,
      (pe << 14) | pe, (pe << 14) | pe, acc_init};
  for (uint32_t i = 0; i < AX_ROLE_MORPH_CFG_WORDS; ++i)
    mmio_write32(AX_ROLE_MORPH_CFG + 4u * i, g[i]);
  mmio_write32(AX_ROLE_MORPH_NCONFIG, AX_ROLE_MORPH_CFG_WORDS);
}

static int run_job(void) {
  mmio_write32(AX_ROLE_DOORBELL, 1u);
  for (uint32_t s = 0; s < 2000000u; ++s) {
    uint32_t st = mmio_read32(AX_ROLE_STATUS);
    if (st & AX_ROLE_STATUS_REJECTED) return 0;
    if ((st & AX_ROLE_STATUS_DONE) && !(st & AX_ROLE_STATUS_BUSY)) {
      mmio_write32(AX_ROLE_STATUS, AX_ROLE_STATUS_DONE);
      return 1;
    }
  }
  return 0;
}

/* Runs the SIMT personality and checks every element. */
static void run_saxpy(unsigned code) {
  for (uint32_t i = 0; i < N_SAXPY; ++i) {
    poke(SAXPY_X + i, i + 1u);
    poke(SAXPY_Y + i, 100u + 2u * i);
    poke(SAXPY_OUT + i, 0u);
  }
  load_genome(AX_MORPH_MODE_SIMT, 1u, N_SAXPY, 1u, SAXPY_X, 0u, 0u, 1u,
              SAXPY_Y, 1u, 0u, SAXPY_OUT, 0u, 3u, 0u,
              pe_desc(AX_MORPH_SRC_A, AX_MORPH_SRC_ZERO, AX_MORPH_SRC_IMM0,
                      AX_MORPH_SRC_B, AX_MORPH_ACC_LOAD), 0u);
  mmio_write32(AX_ROLE_MORPH_NITEMS, N_SAXPY);
  if (!run_job()) fail(code, "job rejected or never completed");
  for (uint32_t i = 0; i < N_SAXPY; ++i)
    if (peek(SAXPY_OUT + i) != 3u * (i + 1u) + 100u + 2u * i)
      fail(code + 1, "result mismatch");
}

int main(void) {
  step("discovering role and shell");
  if (mmio_read32(AX_ROLE_ID) != AX_ROLE_MORPH_ID) fail(1, "role is not MRPH");
  if (mmio_read32(AX_SHELL_ID) != AX_SHELL_ID_MAGIC)
    fail(2, "shell ID is not aXSH");
  if (mmio_read32(AX_SHELL_ISO_STATUS) & AX_ISO_STATUS_ISOLATED)
    fail(3, "fence is asserted out of reset");

  step("running a job before isolation");
  run_saxpy(10);

  /* The management shell must stay alive across the whole swap window, so
   * sample a free-running counter before fencing and require it to advance. */
  uint32_t cycles_before = ax_bench_rdcycle();

  step("asserting ISOLATE (bus must still complete)");
  mmio_write32(AX_SHELL_ISO_CTRL, AX_ISO_ISOLATE);
  if (!(mmio_read32(AX_SHELL_ISO_STATUS) & AX_ISO_STATUS_ISOLATED))
    fail(4, "ISO_STATUS did not report the fence");

  step("reading the fenced role window");
  /* This is the transaction that hangs a broken fence. Reaching the next line
   * at all is the property under test. */
  uint32_t fenced_id = mmio_read32(AX_ROLE_ID);
  uint32_t fenced_status = mmio_read32(AX_ROLE_STATUS);
  uint32_t fenced_data = peek(SAXPY_OUT);
  if (fenced_id != 0u)
    fail(5, "fenced role window did not read as absent");
  if (fenced_status != 0u || fenced_data != 0u)
    fail(6, "fenced role window returned non-zero");

  step("holding the role in reset, as a rewrite would");
  mmio_write32(AX_SHELL_ISO_CTRL, AX_ISO_ISOLATE | AX_ISO_ROLE_RESET);
  for (volatile int d = 0; d < 2000; ++d) sink = (uint32_t)d;
  if (mmio_read32(AX_ROLE_ID) != 0u)
    fail(7, "role visible while fenced and held in reset");

  step("proving the shell ran throughout");
  if (ax_bench_rdcycle() == cycles_before)
    fail(8, "management CPU cycle counter did not advance");
  if (mmio_read32(AX_LIVE_ID) != AX_LIVE_ID_MAGIC)
    fail(9, "Live FPGA monitor unreachable while fenced");

  step("releasing the fence and rediscovering");
  mmio_write32(AX_SHELL_ISO_CTRL, 0u);
  if (mmio_read32(AX_SHELL_ISO_STATUS) & AX_ISO_STATUS_ISOLATED)
    fail(10, "fence did not clear");
  if (mmio_read32(AX_ROLE_ID) != AX_ROLE_MORPH_ID)
    fail(11, "role did not reappear after the fence cleared");
  if (mmio_read32(AX_ROLE_MORPH_GENERATION) != 0u)
    fail(12, "role reset did not clear the configuration generation");

  step("reconfiguring the role after the swap window");
  run_saxpy(20);

  /* The Live FPGA monitor is shell-owned telemetry that had also only run in
   * simulation. Take a snapshot and require the counters to be readable. */
  step("snapshotting Live FPGA telemetry");
  if (mmio_read32(AX_LIVE_VERSION) == 0u) fail(30, "monitor version reads zero");
  uint32_t seq_before = mmio_read32(AX_LIVE_SEQUENCE);
  mmio_write32(AX_LIVE_COMMAND, AX_LIVE_CMD_SNAPSHOT);
  uint32_t seq_after = mmio_read32(AX_LIVE_SEQUENCE);
  if (seq_after == seq_before) fail(31, "snapshot did not advance the sequence");

  uart_puts("roleiso: telemetry seq=");
  ax_bench_putdec(seq_after);
  uart_puts(" cycles=");
  ax_bench_putdec(mmio_read32(AX_LIVE_CYCLES_LO));
  uart_puts(" rejects=");
  ax_bench_putdec(mmio_read32(AX_LIVE_REJECT_LO));
  uart_puts(" watchdogs=");
  ax_bench_putdec(mmio_read32(AX_LIVE_WATCH_LO));
  uart_puts(" generation=");
  ax_bench_putdec(mmio_read32(AX_LIVE_GEN_LO));
  uart_puts("\n");

  uart_puts("roleiso: PASS (fence contained the role, shell stayed live, "
            "role rediscovered and reconfigured)\n");
  uart_drain();
  test_finish(0);
  return 0;
}
