#pragma once

#include <stdint.h>

/* DESIGN.md §3.2 — deliberately QEMU-virt compatible. */
#define AX_TEST_BASE  0x00100000u
#define AX_CLINT_BASE 0x02000000u
#define AX_UART_BASE  0x10000000u

static inline void mmio_write8(uint32_t addr, uint8_t value) {
  *(volatile uint8_t *)(uintptr_t)addr = value;
}

static inline void mmio_write32(uint32_t addr, uint32_t value) {
  *(volatile uint32_t *)(uintptr_t)addr = value;
}

static inline uint8_t mmio_read8(uint32_t addr) {
  return *(volatile const uint8_t *)(uintptr_t)addr;
}

static inline uint32_t mmio_read32(uint32_t addr) {
  return *(volatile const uint32_t *)(uintptr_t)addr;
}

static inline void uart_putchar(char c) {
  /* 16550 LSR bit 5: THR empty. */
  while (!(mmio_read8(AX_UART_BASE + 5) & 0x20)) {}
  mmio_write8(AX_UART_BASE, (uint8_t)c);
}

static inline void uart_puts(const char *s) {
  while (*s) uart_putchar(*s++);
}

/* Wait for the shift register to empty, not just the holding register.  On the
 * board test_finish stops the CPU immediately, which truncates a long final
 * message mid-transmission; uart_putchar only guarantees THR was free. */
static inline void uart_drain(void) {
  /* 16550 LSR bit 6: transmitter empty (THR and shift register). */
  while (!(mmio_read8(AX_UART_BASE + 5) & 0x40)) {}
}

__attribute__((noreturn)) static inline void test_finish(unsigned code) {
  const uint32_t value = code ? (uint32_t)(code << 16) | 0x3333u : 0x5555u;
  mmio_write32(AX_TEST_BASE, value);
  for (;;) __asm__ volatile("wfi");
}
