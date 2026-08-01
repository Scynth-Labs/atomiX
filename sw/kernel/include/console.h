/* Interrupt-driven console input.
 *
 * The shell used to wait for a keystroke by spinning on the UART's line status
 * register.  That is correct and costs everything: the hart fetches and
 * retires a poll loop for as long as the user is thinking, which burns power
 * on a board and host time under simulation, and it is why a machine sitting
 * at an idle prompt still accumulated millions of cycles.
 *
 * The hardware was already wired for better.  The 16550 exposes a
 * level-sensitive "a byte is waiting" line, and the shell's PLIC carries it as
 * source AX_PLIC_SRC_UART.  This driver claims that source, drains bytes into
 * a ring in the interrupt handler, and parks the hart in WFI while the ring is
 * empty.
 *
 * When no controller is present -- the ISS, or a board without a PLIC -- there
 * is nothing to route and the driver falls back to polling the device
 * directly.  Even then it parks in WFI between checks and lets the scheduler
 * tick wake it, so the poll costs a few instructions per timer tick rather
 * than every cycle.
 */
#pragma once

#include <stdint.h>

/* Route the UART's PLIC source and start buffering.  Safe to call when no
 * controller is present; the driver then stays in polling mode. */
void console_init(void);

/* PLIC dispatch entry for AX_PLIC_SRC_UART.  Drains every byte the device has
 * so the level-sensitive line is quiet before the source is completed. */
void console_irq_drain(void);

/* Next console byte, blocking.  Parks the hart rather than spinning. */
char console_getchar(void);

/* Diagnostics: bytes taken from the ring (interrupt-fed) versus read straight
 * off the device (polled).  `console` in the shell reports these, which is the
 * only way to see from outside that the console is genuinely interrupt-driven
 * rather than merely working. */
uint32_t console_irq_bytes(void);
uint32_t console_polled_bytes(void);

/* How many times the ring filled and the source had to be masked.  Nonzero is
 * not an error -- backpressure working is the design -- but it is the number
 * that says the ring is undersized for how this console is being used. */
uint32_t console_ring_stalls(void);
