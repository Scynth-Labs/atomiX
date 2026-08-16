/* Live FPGA telemetry, end to end through a real SoC.
 *
 * `axlivemon` counts what the shell hands it, and for a long time the shell
 * handed it nothing: `soc_top.sv` tied `role_reject_event` and `watchdog_event`
 * to zero, so DESCRIPTOR_REJECTIONS and WATCHDOG_EVENTS could not advance for
 * any input at all.  Reading zero from a counter that cannot count is not
 * evidence of a well-behaved role, and every fitness trial that consumed those
 * deltas was consuming a constant.
 *
 * The unit benches prove the fence's own logic.  What they cannot prove is the
 * wiring: that in an assembled SoC a descriptor the *role* refuses reaches the
 * *shell's* counter.  That is this program.  It runs on the RTL with
 * `configs/sim-morph.json`, whose role is the morph fabric -- the one role that
 * has descriptors to refuse.
 *
 * The negative case matters as much as the positive one.  Reading the fenced
 * role window is the documented way to rediscover a role after a swap, so those
 * reads must not be charged to DESCRIPTOR_REJECTIONS: a nonzero rejection delta
 * makes a trial ineligible for fitness, and a normal swap would otherwise
 * disqualify itself.
 *
 * Not covered here: the watchdog.  It fires when a role stops answering the
 * bus, and no software running on this SoC can make a working morph fabric do
 * that -- the fault has to be injected at the role's ready line, which is what
 * `sim/unit/tb_axroleiso.cpp` does.  This program reads the counter and
 * requires it to stay still, which is the strongest statement available from
 * software: no healthy role trips it. */
#include "bench_report.h"
#include "platform.h"
#include "role.h"

#define N_SAXPY 50
#define SAXPY_X   0
#define SAXPY_Y   64
#define SAXPY_OUT 128
#define N_REFUSED 64

/* The buffer this role was built with, read from its own CAPS register rather
 * than assumed: `data_words` is a role parameter a profile chooses, so a
 * program that hard-codes 256 tests a configuration instead of a mechanism and
 * silently stops refusing anything on a profile with a larger buffer. */
static uint32_t data_words;

/* First word past the end of that buffer, so an N_REFUSED-item output stream
 * starting here runs off it whatever size it is. The fabric must refuse the
 * descriptor before BUSY rises. */
static uint32_t out_of_window(void) { return data_words - (N_REFUSED / 8u); }

static void fail(unsigned code, const char *what) {
  uart_puts("livecount: FAIL ");
  uart_puts(what);
  uart_puts("\n");
  uart_drain();
  test_finish(code);
}

static void step(const char *what) {
  uart_puts("livecount: ");
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

/* Takes a telemetry snapshot; every counter read afterwards belongs to the
 * same clock edge, which is what makes the deltas below comparable. */
static void snapshot(void) {
  mmio_write32(AX_LIVE_COMMAND, AX_LIVE_CMD_SNAPSHOT);
}

static uint32_t rejections(void) { return mmio_read32(AX_LIVE_REJECT_LO); }
static uint32_t watchdogs(void) { return mmio_read32(AX_LIVE_WATCH_LO); }

/* The SAXPY the fabric accepts: one valid job, used to prove the counter does
 * not move for ordinary work. */
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
  mmio_write32(AX_ROLE_DOORBELL, 1u);
  for (uint32_t s = 0; s < 2000000u; ++s) {
    uint32_t st = mmio_read32(AX_ROLE_STATUS);
    if (st & AX_ROLE_STATUS_REJECTED) fail(code, "valid job was refused");
    if ((st & AX_ROLE_STATUS_DONE) && !(st & AX_ROLE_STATUS_BUSY)) {
      mmio_write32(AX_ROLE_STATUS, AX_ROLE_STATUS_DONE);
      for (uint32_t i = 0; i < N_SAXPY; ++i)
        if (peek(SAXPY_OUT + i) != 3u * (i + 1u) + 100u + 2u * i)
          fail(code + 1, "valid job produced a wrong result");
      return;
    }
  }
  fail(code + 2, "valid job never completed");
}

/* A descriptor whose output stream leaves the role window. The fabric must
 * refuse it before BUSY rises. */
static void run_refused(unsigned code) {
  load_genome(AX_MORPH_MODE_SIMT, 1u, N_REFUSED, 1u, SAXPY_X, 0u, 0u, 1u,
              SAXPY_Y, 1u, 0u, out_of_window(), 0u, 3u, 0u,
              pe_desc(AX_MORPH_SRC_A, AX_MORPH_SRC_ZERO, AX_MORPH_SRC_IMM0,
                      AX_MORPH_SRC_B, AX_MORPH_ACC_LOAD), 0u);
  mmio_write32(AX_ROLE_MORPH_NITEMS, N_REFUSED);
  mmio_write32(AX_ROLE_DOORBELL, 1u);
  uint32_t st = mmio_read32(AX_ROLE_STATUS);
  if (!(st & AX_ROLE_STATUS_REJECTED))
    fail(code, "out-of-window descriptor was not refused");
  if (st & AX_ROLE_STATUS_BUSY) fail(code + 1, "refused descriptor went BUSY");
  mmio_write32(AX_ROLE_STATUS, AX_ROLE_STATUS_REJECTED);
}

int main(void) {
  step("discovering role, shell, and monitor");
  if (mmio_read32(AX_ROLE_ID) != AX_ROLE_MORPH_ID) fail(1, "role is not MRPH");
  if (mmio_read32(AX_SHELL_ID) != AX_SHELL_ID_MAGIC) fail(2, "shell is not aXSH");
  if (mmio_read32(AX_LIVE_ID) != AX_LIVE_ID_MAGIC) fail(3, "monitor is not aXLV");

  /* CAPS packs {NPE, CFG_WORDS, DATA_WORDS}; take the buffer this build has. */
  data_words = mmio_read32(AX_ROLE_MORPH_CAPS) & 0xffffu;
  if (data_words < SAXPY_OUT + N_SAXPY)
    fail(4, "role buffer too small for this program's workload");

  snapshot();
  const uint32_t rejects_start = rejections();
  const uint32_t watchdogs_start = watchdogs();
  const uint32_t role_rejects_start = mmio_read32(AX_ROLE_MORPH_REJECTS);

  step("running an accepted job");
  run_saxpy(10);
  snapshot();
  if (rejections() != rejects_start)
    fail(13, "an accepted job was counted as a rejection");
  if (mmio_read32(AX_LIVE_WORK_LO) == 0u)
    fail(14, "a completed job did not reach the work counter");

  step("submitting a descriptor that leaves the role window");
  run_refused(20);
  snapshot();
  if (rejections() != rejects_start + 1u)
    fail(22, "a refused descriptor did not reach the shell's counter");

  /* Twice, because one increment could be an artefact of the first snapshot;
   * and against the role's own REJECTS register, because the two counters
   * describe the same events from opposite sides of the role boundary and
   * disagreeing would mean one of them is measuring something else. */
  step("submitting a second refused descriptor");
  run_refused(30);
  snapshot();
  if (rejections() != rejects_start + 2u)
    fail(32, "the second refused descriptor was not counted");
  if (mmio_read32(AX_ROLE_MORPH_REJECTS) != role_rejects_start + 2u)
    fail(33, "the role's own rejection count disagrees with the shell's");

  /* The role must still work: a refused descriptor is refused, not fatal. */
  step("running an accepted job after the refusals");
  run_saxpy(40);

  /* The negative case: rediscovery traffic against a fenced window is not a
   * descriptor rejection, however much of it software does. */
  step("reading the fenced window during a swap window");
  snapshot();
  const uint32_t rejects_before_fence = rejections();
  mmio_write32(AX_SHELL_ISO_CTRL, AX_ISO_ISOLATE);
  for (uint32_t i = 0; i < 8u; ++i) {
    if (mmio_read32(AX_ROLE_ID) != 0u) fail(50, "fenced role window not absent");
    if (peek(SAXPY_OUT) != 0u) fail(51, "fenced role data not absent");
  }
  mmio_write32(AX_SHELL_ISO_CTRL, 0u);
  if (mmio_read32(AX_ROLE_ID) != AX_ROLE_MORPH_ID)
    fail(52, "role did not reappear after the fence cleared");
  snapshot();
  if (rejections() != rejects_before_fence)
    fail(53, "fenced-window reads were counted as descriptor rejections");

  /* No role here can stop answering the bus, so this must not have moved. */
  if (watchdogs() != watchdogs_start)
    fail(60, "a healthy role raised a watchdog event");

  uart_puts("livecount: rejections=");
  ax_bench_putdec(rejections());
  uart_puts(" role_rejects=");
  ax_bench_putdec(mmio_read32(AX_ROLE_MORPH_REJECTS));
  uart_puts(" work=");
  ax_bench_putdec(mmio_read32(AX_LIVE_WORK_LO));
  uart_puts(" stalls=");
  ax_bench_putdec(mmio_read32(AX_LIVE_STALL_LO));
  uart_puts(" watchdogs=");
  ax_bench_putdec(watchdogs());
  uart_puts("\n");

  uart_puts("livecount: PASS (role rejections reach the shell counter, "
            "fenced reads do not)\n");
  uart_drain();
  test_finish(0);
  return 0;
}
