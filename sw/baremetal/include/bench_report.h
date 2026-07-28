#pragma once

#include <stdint.h>

#include "platform.h"

/* Keep benchmark images board-independent. Tang Nano and Tang Primer bake the
 * same binary into BRAM, so report projected time at both board clocks rather
 * than compiling a clock-specific payload. Physical timing closure remains the
 * authority for the frequency a board can actually run. */
enum {
  AX_BENCH_NANO_MHZ = 27u,
  AX_BENCH_PRIMER_MHZ = 50u,
};

static inline uint32_t ax_bench_rdcycle(void) {
  uint32_t value;
  __asm__ volatile("csrr %0, mcycle" : "=r"(value));
  return value;
}

static inline void ax_bench_putdec(uint32_t value) {
  char buffer[10];
  int length = 0;
  if (value == 0) {
    uart_putchar('0');
    return;
  }
  while (value) {
    buffer[length++] = (char)('0' + value % 10u);
    value /= 10u;
  }
  while (length) uart_putchar(buffer[--length]);
}

static inline void ax_bench_puthex(uint32_t value) {
  for (int shift = 28; shift >= 0; shift -= 4) {
    const uint32_t digit = (value >> shift) & 0xfu;
    uart_putchar((char)(digit < 10u ? '0' + digit : 'a' + digit - 10u));
  }
}

static inline void ax_bench_put_tenths(uint32_t value_x10) {
  ax_bench_putdec(value_x10 / 10u);
  uart_putchar('.');
  uart_putchar((char)('0' + value_x10 % 10u));
}

static inline void ax_bench_report_projected_time(uint32_t cycles) {
  uart_puts(" us@27MHz=");
  ax_bench_put_tenths(
      (cycles * 10u + AX_BENCH_NANO_MHZ / 2u) / AX_BENCH_NANO_MHZ);
  uart_puts(" us@50MHz=");
  ax_bench_put_tenths(
      (cycles * 10u + AX_BENCH_PRIMER_MHZ / 2u) / AX_BENCH_PRIMER_MHZ);
}

static inline uint32_t ax_bench_checksum_step(uint32_t checksum,
                                              uint32_t value) {
  return (checksum ^ value) * 16777619u;
}
