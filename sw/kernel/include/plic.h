#pragma once

#include <stdint.h>

#include "platform.h"

/* Shell PLIC (plic.qemu-virt), seen from S-mode.
 *
 * aXos runs in S-mode, so it owns context 1 -- hart 0's supervisor context --
 * and never touches context 0, which belongs to M-mode.  These are the
 * QEMU-virt addresses, which is the whole point: the same claim/complete
 * sequence runs on QEMU `-machine virt` and on the RTL shell.
 *
 * Sources are level-sensitive.  A device holds its line until the handler
 * makes the device drop it, so the handler must quiet the device *before*
 * writing COMPLETE; completing first re-arms the source immediately. */
/* Register layout, with the QEMU-virt strides between contexts.  Deriving the
 * S-mode block from AX_PLIC_CTX_S rather than writing 0x2080/0x201000 means a
 * shell that numbers its contexts differently still lands on the right ones. */
#define AX_PLIC_PRIORITY(s)  (AX_PLIC_BASE + 4u * (s))
#define AX_PLIC_PENDING      (AX_PLIC_BASE + 0x001000u)
#define AX_PLIC_ENABLE(c)    (AX_PLIC_BASE + 0x002000u + 0x80u * (c))
#define AX_PLIC_THRESHOLD(c) (AX_PLIC_BASE + 0x200000u + 0x1000u * (c))
#define AX_PLIC_CLAIM(c)     (AX_PLIC_THRESHOLD(c) + 4u)

/* aXos runs in S-mode, so it owns the supervisor context and only that one. */
#define AX_PLIC_S_ENABLE    AX_PLIC_ENABLE(AX_PLIC_CTX_S)
#define AX_PLIC_S_THRESHOLD AX_PLIC_THRESHOLD(AX_PLIC_CTX_S)
#define AX_PLIC_S_CLAIM     AX_PLIC_CLAIM(AX_PLIC_CTX_S)

/* Source and context numbering comes from the shell that wires it, generated
 * into the build directory by tools/gen_irq_map.py: AX_PLIC_SRC_*,
 * AX_PLIC_SOURCES, AX_PLIC_CTX_*, AX_PLIC_CONTEXTS.  There is deliberately no
 * copy of those numbers here -- a second copy is a second thing to get wrong. */
#include "ax_irq_map.h"

/* scause value for a supervisor external interrupt, and the SEIE bit. */
#define AX_SCAUSE_S_EXTERNAL 0x80000009u
#define AX_SIE_SEIE (1u << 9)

/* Probe for the controller once, before interrupts matter.  The ISS models no
 * PLIC at all, so its absence is an ordinary configuration rather than a
 * failure: everything below is inert and callers fall back to polling.
 * Returns nonzero when a PLIC responded. */
int plic_init(void);
int plic_present(void);

/* Route a source to this hart's supervisor context: nonzero priority, enabled,
 * threshold 0.  Delivery is "priority strictly greater than threshold". */
void plic_route(uint32_t source, uint32_t priority);

/* Claim returns the winning source id, or 0 when nothing is eligible.
 * Complete ends its service. */
uint32_t plic_claim(void);
void plic_complete(uint32_t source);

/* Called from supervisor_trap on a supervisor external interrupt: claims,
 * dispatches to the owning device, and completes.  Loops until the claim
 * returns 0, because a second source may have arrived while servicing the
 * first and the level-sensitive gateway would otherwise leave it asserted. */
void plic_dispatch(void);
