#pragma once

#include <stdint.h>

/* Shared aXbus/QEMU-virt-compatible device addresses. */
#define AX_TEST_BASE  0x00100000u
#define AX_CLINT_BASE 0x02000000u
#define AX_UART_BASE  0x10000000u
#define AX_SPI_BASE   0x10010000u
/* Accelerator role window (DESIGN.md §3.3): a fixed 64 KiB aXbus device that
 * vm_bootstrap_map exposes through a kernel-only virtual alias. */
#define AX_ROLE_BASE  0x40000000u
#define AX_ROLE_KERNEL_BASE 0x50000000u

static inline void mmio_write8(uint32_t addr, uint8_t value) {
  *(volatile uint8_t *)(uintptr_t)addr = value;
}

static inline uint8_t mmio_read8(uint32_t addr) {
  return *(volatile const uint8_t *)(uintptr_t)addr;
}

static inline uint32_t mmio_read32(uint32_t addr) {
  return *(volatile const uint32_t *)(uintptr_t)addr;
}

static inline void mmio_write32(uint32_t addr, uint32_t value) {
  *(volatile uint32_t *)(uintptr_t)addr = value;
}

static inline void uart_putchar(char c) {
  while (!(mmio_read8(AX_UART_BASE + 5) & 0x20)) {}
  mmio_write8(AX_UART_BASE, (uint8_t)c);
}

static inline void uart_puts(const char *s) {
  while (*s) uart_putchar(*s++);
}

static inline int uart_rx_ready(void) {
  return mmio_read8(AX_UART_BASE + 5) & 0x01;
}

static inline char uart_getchar(void) {
  while (!uart_rx_ready()) {}
  return (char)mmio_read8(AX_UART_BASE);
}

__attribute__((noreturn)) static inline void test_finish(unsigned code) {
  const uint32_t value = code ? (uint32_t)(code << 16) | 0x3333u : 0x5555u;
  mmio_write32(AX_TEST_BASE, value);
  for (;;) __asm__ volatile("wfi");
}
