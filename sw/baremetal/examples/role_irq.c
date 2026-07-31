/* Role completion delivered as an interrupt, end to end: role.loopback drives
 * its level-sensitive line while STATUS.DONE stands, the shell PLIC arbitrates
 * it as source 2, and the core takes it as a machine external interrupt.  This
 * is the counterpart to examples/role.c, which polls the same completion.
 *
 * What makes it a real test of the path rather than of the doorbell: the job is
 * started with interrupts enabled and the CPU then parks in wfi, so the only
 * way execution resumes is through the PLIC.  Runs on the RTL SoC only; the ISS
 * does not model the role device. */
#include "csr.h"
#include "platform.h"
#include "plic.h"
#include "role.h"

extern void trap_entry(void);

enum { COPY_WORDS = 64u, SRC_WORD = 0u, DST_WORD = 256u };

static volatile uint32_t completions;
static volatile uint32_t bad_claim;
static volatile uint32_t bad_cause;
static volatile uint32_t spurious;

static uint32_t pattern(uint32_t i) { return 0x5a000000u + i * 0x01010101u; }

static void fail(unsigned code, const char *what) {
  uart_puts("role irq: FAIL ");
  uart_puts(what);
  uart_puts("\n");
  test_finish(code);
}

uint32_t *machine_trap(uint32_t *frame) {
  if (csr_read_mcause() != AX_IRQ_MACHINE_EXTERNAL) {
    bad_cause = 1u;
    test_finish(10);
  }
  const uint32_t source = plic_claim();
  if (source != AX_PLIC_SRC_ROLE) {
    bad_claim = source ? source : 0xffffffffu;
    test_finish(11);
  }
  /* The role holds its line until DONE is cleared, so clearing DONE *before*
   * completing is what actually drops the request.  Completing first would
   * re-arm it immediately and the handler would be re-entered forever -- the
   * ordering is the point of a level-sensitive source. */
  if (!(mmio_read32(AX_ROLE_STATUS) & AX_ROLE_STATUS_DONE)) spurious = 1u;
  mmio_write32(AX_ROLE_STATUS, AX_ROLE_STATUS_DONE);
  plic_complete(source);
  completions++;
  return frame;
}

static void start_copy(uint32_t seed) {
  for (uint32_t i = 0; i < COPY_WORDS; ++i)
    mmio_write32(AX_ROLE_LOOP_BUF + 4u * (SRC_WORD + i), pattern(seed + i));
  mmio_write32(AX_ROLE_LOOP_SRC, 4u * SRC_WORD);
  mmio_write32(AX_ROLE_LOOP_DST, 4u * DST_WORD);
  mmio_write32(AX_ROLE_LOOP_LEN, COPY_WORDS);
  role_ring_doorbell();
}

/* Park until the handler reports the job, with a bounded escape so a broken
 * interrupt path fails the test instead of hanging the simulation out to its
 * cycle limit with no diagnosis. */
static void wait_for(uint32_t target, unsigned code, const char *what) {
  for (uint32_t spin = 0; spin < 2000000u; ++spin) {
    if (completions >= target) return;
    __asm__ volatile("wfi");
  }
  fail(code, what);
}

int main(void) {
  if (role_id() != AX_ROLE_LOOP_ID) fail(1, "discovery: ROLE_ID mismatch");

  /* Nothing is routed yet, so a completion must NOT reach the core: this is
   * what proves the later interrupt came through the PLIC rather than from a
   * line wired straight to irq_external. */
  csr_write_mtvec((uint32_t)(uintptr_t)trap_entry);
  csr_set_mie(AX_MIE_MEIE);
  csr_set_mstatus(1u << 3); /* MIE */
  start_copy(0u);
  role_wait_done();
  for (volatile int settle = 0; settle < 2000; ++settle) { }
  if (completions != 0u) fail(2, "interrupt delivered while PLIC source masked");
  if (!(mmio_read32(AX_PLIC_PENDING) & (1u << AX_PLIC_SRC_ROLE)))
    fail(3, "role completion is not pending at the PLIC");

  /* Route it. The line is already asserted, so enabling must deliver
   * immediately -- a level-sensitive source does not need a fresh edge. */
  plic_enable(AX_PLIC_SRC_ROLE, 1u);
  wait_for(1u, 4, "no interrupt after enabling an already-asserted source");
  if (spurious) fail(5, "handler entered with STATUS.DONE clear");

  for (uint32_t i = 0; i < COPY_WORDS; ++i)
    if (mmio_read32(AX_ROLE_LOOP_BUF + 4u * (DST_WORD + i)) != pattern(i))
      fail(6, "copied data mismatch");

  /* A second job proves the handler left the gateway re-armable rather than
   * wedged in service, which is the failure a missing complete would cause. */
  start_copy(0x40u);
  wait_for(2u, 7, "second completion did not interrupt");
  for (uint32_t i = 0; i < COPY_WORDS; ++i)
    if (mmio_read32(AX_ROLE_LOOP_BUF + 4u * (DST_WORD + i)) != pattern(0x40u + i))
      fail(8, "second copy data mismatch");
  if (mmio_read32(AX_ROLE_LOOP_COUNT) != 2u) fail(9, "COUNT after two jobs");

  /* Quiet afterwards: DONE was cleared, so the source must not still be
   * pending and the handler must not run again. */
  if (mmio_read32(AX_PLIC_PENDING) & (1u << AX_PLIC_SRC_ROLE))
    fail(12, "role source still pending after DONE cleared");
  for (volatile int settle = 0; settle < 5000; ++settle) { }
  if (completions != 2u) fail(13, "spurious extra interrupt");

  uart_puts("role irq: PASS\n");
  test_finish(0);
}
