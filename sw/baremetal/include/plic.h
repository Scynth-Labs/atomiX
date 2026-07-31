#pragma once

#include "platform.h"

/* Shell PLIC (components/plic/qemu-virt): one target, hart 0 M-mode context 0,
 * level-sensitive sources numbered from 1.  Source 0 is reserved by the spec to
 * mean "no interrupt", so it is neither pending nor enableable. */
#define AX_PLIC_BASE 0x0c000000u

#define AX_PLIC_PRIORITY(s) (AX_PLIC_BASE + 4u * (s))
#define AX_PLIC_PENDING     (AX_PLIC_BASE + 0x001000u)
#define AX_PLIC_ENABLE      (AX_PLIC_BASE + 0x002000u)
#define AX_PLIC_THRESHOLD   (AX_PLIC_BASE + 0x200000u)
#define AX_PLIC_CLAIM       (AX_PLIC_BASE + 0x200004u)

/* Source assignment in the reference shell (components/soc/reference). */
#define AX_PLIC_SRC_UART 1u
#define AX_PLIC_SRC_ROLE 2u

/* mcause value for a machine external interrupt, and the MEIE bit in mie. */
#define AX_IRQ_MACHINE_EXTERNAL 0x8000000bu
#define AX_MIE_MEIE (1u << 11)

/* Route one source to this hart: give it a nonzero priority and enable it.
 * Delivery is "priority strictly greater than threshold", so a threshold of 0
 * with priority 1 is the minimal configuration that delivers. */
static inline void plic_enable(uint32_t source, uint32_t priority) {
  mmio_write32(AX_PLIC_PRIORITY(source), priority);
  mmio_write32(AX_PLIC_ENABLE, mmio_read32(AX_PLIC_ENABLE) | (1u << source));
  mmio_write32(AX_PLIC_THRESHOLD, 0u);
}

/* Take the winning source into service; 0 means nothing was eligible. */
static inline uint32_t plic_claim(void) { return mmio_read32(AX_PLIC_CLAIM); }

/* End service.  Sources are level-sensitive: if the device is still asserting
 * when this lands, it simply becomes pending again. */
static inline void plic_complete(uint32_t source) {
  mmio_write32(AX_PLIC_CLAIM, source);
}
