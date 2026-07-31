#pragma once

#include "platform.h"

/* Shell PLIC (plic.qemu-virt, components/plic/qemu_virt) as a bare-metal M-mode
 * program sees it: the machine context, with level-sensitive sources numbered
 * from 1.  Source 0 is reserved by the spec to mean "no interrupt", so it is
 * neither pending nor enableable.  The supervisor context belongs to aXos; see
 * sw/kernel/include/plic.h. */
/* Source and context numbering comes from the shell that wires it, generated
 * into the build directory by tools/gen_irq_map.py: AX_PLIC_SRC_*,
 * AX_PLIC_SOURCES, AX_PLIC_CTX_*, AX_PLIC_CONTEXTS.  There is deliberately no
 * copy of those numbers here -- a second copy is a second thing to get wrong. */
#include "ax_irq_map.h"

#define AX_PLIC_BASE 0x0c000000u

/* Register layout, with the QEMU-virt strides between contexts. */
#define AX_PLIC_PRIORITY(s)  (AX_PLIC_BASE + 4u * (s))
#define AX_PLIC_PENDING      (AX_PLIC_BASE + 0x001000u)
#define AX_PLIC_ENABLE(c)    (AX_PLIC_BASE + 0x002000u + 0x80u * (c))
#define AX_PLIC_THRESHOLD(c) (AX_PLIC_BASE + 0x200000u + 0x1000u * (c))
#define AX_PLIC_CLAIM(c)     (AX_PLIC_THRESHOLD(c) + 4u)

/* A bare-metal program runs in M-mode, so it owns the machine context and
 * should leave the supervisor one (aXos's) alone. */
#define AX_PLIC_M_ENABLE    AX_PLIC_ENABLE(AX_PLIC_CTX_M)
#define AX_PLIC_M_THRESHOLD AX_PLIC_THRESHOLD(AX_PLIC_CTX_M)
#define AX_PLIC_M_CLAIM     AX_PLIC_CLAIM(AX_PLIC_CTX_M)

/* mcause value for a machine external interrupt, and the MEIE bit in mie. */
#define AX_IRQ_MACHINE_EXTERNAL 0x8000000bu
#define AX_MIE_MEIE (1u << 11)

/* Route one source to this hart: give it a nonzero priority and enable it.
 * Delivery is "priority strictly greater than threshold", so a threshold of 0
 * with priority 1 is the minimal configuration that delivers. */
static inline void plic_enable(uint32_t source, uint32_t priority) {
  mmio_write32(AX_PLIC_PRIORITY(source), priority);
  mmio_write32(AX_PLIC_M_ENABLE,
               mmio_read32(AX_PLIC_M_ENABLE) | (1u << source));
  mmio_write32(AX_PLIC_M_THRESHOLD, 0u);
}

/* Take the winning source into service; 0 means nothing was eligible. */
static inline uint32_t plic_claim(void) { return mmio_read32(AX_PLIC_M_CLAIM); }

/* End service.  Sources are level-sensitive: if the device is still asserting
 * when this lands, it simply becomes pending again. */
static inline void plic_complete(uint32_t source) {
  mmio_write32(AX_PLIC_M_CLAIM, source);
}
