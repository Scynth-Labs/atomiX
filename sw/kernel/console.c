#include "console.h"

#include "platform.h"
#include "plic.h"

/* Single producer (the interrupt handler), single consumer (whoever called
 * console_getchar).  A power-of-two ring lets head and tail advance freely and
 * wrap by masking, so neither side has to write the other's index. */
#define CONSOLE_RING 256u

static volatile uint8_t ring[CONSOLE_RING];
static volatile uint32_t ring_head;   /* written by the handler only */
static volatile uint32_t ring_tail;   /* written by the reader only */
static int console_irq_active;
/* Set the first time the handler actually delivers a byte.  Routing a source
 * is not proof that it is the right source: AX_PLIC_SRC_UART comes from the
 * shell that wires this SoC, and aXos also runs on QEMU's virt machine, whose
 * PLIC numbers its devices differently.  Parking the hart on the assumption
 * that an interrupt will arrive is therefore unsafe until one has, and the
 * failure mode is not degraded performance but a console that never returns. */
static int console_irq_proven;
static uint32_t irq_bytes;
static uint32_t polled_bytes;
static uint32_t ring_full_stalls;

static inline uint32_t csr_read_sstatus_(void) {
  uint32_t value;
  __asm__ volatile("csrr %0, sstatus" : "=r"(value));
  return value;
}

/* SSTATUS.SIE. Kept local rather than shared: this file is the only place that
 * needs to close the window between "the ring looked empty" and "the hart went
 * to sleep", and a general-purpose interrupt-disable helper would invite that
 * pattern somewhere it is not this carefully reasoned about. */
#define SSTATUS_SIE (1u << 1)

static inline void sie_clear(void) {
  __asm__ volatile("csrc sstatus, %0" :: "r"(SSTATUS_SIE));
}

static inline void sie_set(void) {
  __asm__ volatile("csrs sstatus, %0" :: "r"(SSTATUS_SIE));
}

static inline int ring_empty(void) { return ring_head == ring_tail; }

void console_irq_drain(void) {
  /* Drain everything the device holds.  The line is level-sensitive, so
   * leaving a byte behind would re-assert the source the moment it is
   * completed and spin the handler instead of the shell. */
  while (uart_rx_ready()) {
    const uint32_t next = (ring_head + 1u) & (CONSOLE_RING - 1u);
    if (next == ring_tail) {
      /* Full.  Leave the byte in the device and mask the source instead of
       * dropping it.  Dropping would silently corrupt input; draining anyway
       * would overwrite unread bytes; and completing a source this handler
       * cannot quiet would re-trap immediately, starving the only context that
       * can make room.  Masking stops the interrupts, the UART's holding
       * register stops the sender, and console_getchar re-opens the line when
       * it has consumed something. */
      plic_set_enabled(AX_PLIC_SRC_UART, 0);
      ring_full_stalls++;
      return;
    }
    ring[ring_head] = (uint8_t)mmio_read8(AX_UART_BASE);
    ring_head = next;
    console_irq_proven = 1;
  }
}

void console_init(void) {
  ring_head = 0;
  ring_tail = 0;
  if (!plic_present()) return;
  plic_route(AX_PLIC_SRC_UART, 1u);
  console_irq_active = 1;
  /* Anything typed before routing is still sitting in the device. */
  console_irq_drain();
}

char console_getchar(void) {
  if (!console_irq_active) {
    /* No controller at all: spin on the device, exactly as this code always
     * did.  Nothing here can park, so nothing here can fail to wake. */
    while (!uart_rx_ready()) {}
    polled_bytes++;
    return (char)mmio_read8(AX_UART_BASE);
  }

  for (;;) {
    /* Interrupts off around the test.  Otherwise a byte arriving between the
     * test and the WFI would be handled *before* the hart parked -- the source
     * would already be quiet, nothing would be left pending, and the hart
     * would sleep with input waiting in the ring.  WFI resumes on a *pending*
     * interrupt whether or not it is enabled, which is what makes testing with
     * interrupts disabled the safe way round. */
    sie_clear();

    if (!ring_empty()) {
      const uint8_t byte = ring[ring_tail];
      ring_tail = (ring_tail + 1u) & (CONSOLE_RING - 1u);
      /* There is room again, so re-open a line the handler had to mask and
       * take whatever the device is still holding.  Unconditional because it
       * is cheap, and because tracking whether a mask is outstanding is one
       * more piece of state that could disagree with the controller. */
      plic_set_enabled(AX_PLIC_SRC_UART, 1);
      console_irq_drain();
      sie_set();
      irq_bytes++;
      return (char)byte;
    }

    /* The handler has not delivered this byte -- either it has not run yet, or
     * this platform does not deliver on the source we routed.  Either way the
     * device itself is authoritative, so read it rather than wait for a
     * messenger that may never come. */
    if (uart_rx_ready()) {
      const uint8_t byte = (uint8_t)mmio_read8(AX_UART_BASE);
      sie_set();
      polled_bytes++;
      return (char)byte;
    }

    /* Park only once the handler has proved it delivers on this platform.
     *
     * Routing a source is not proof that it is the right source:
     * AX_PLIC_SRC_UART comes from the shell that wires this SoC, and aXos also
     * runs on QEMU's virt machine, whose PLIC numbers its devices differently.
     * Until a byte has actually arrived through the handler, fall through and
     * spin -- a slower wait is a cost, a wait that never ends is a hang, and
     * QEMU's cooperative-scheduler profile really does hang on the difference.
     *
     * Nothing here tries to be cleverer than that.  Arming a timer to
     * guarantee a wake was measured and rejected: the M-mode shim re-arms at a
     * fixed 2000-cycle period, so a tick started for the console's benefit
     * also fires through every shell command, and `exec` and the AXFS
     * write/readback both overran their cycle bounds because of it.
     */
    if (console_irq_proven) __asm__ volatile("wfi");
    sie_set();
  }
}

uint32_t console_irq_bytes(void) { return irq_bytes; }
uint32_t console_polled_bytes(void) { return polled_bytes; }
uint32_t console_ring_stalls(void) { return ring_full_stalls; }
