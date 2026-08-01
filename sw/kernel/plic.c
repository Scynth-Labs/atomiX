#include "plic.h"

#include "platform.h"
/* Devices that can own a PLIC source declare their handler in their own
 * header, so the dispatch table below cannot drift from the implementation. */
#include "console.h"
#include "role.h"

/* Recoverable MMIO probe, shared with the role window: a load-access fault
 * while mmio_probe_active is set returns 0 instead of trapping the kernel. */
extern int mmio_probe_read32(uint32_t addr, uint32_t *value);

static int plic_available;

int plic_init(void) {
  uint32_t ignored;
  /* PENDING is read-only and side-effect free, which makes it the safe probe
   * target: reading CLAIM would take a source into service. */
  plic_available = mmio_probe_read32(AX_PLIC_PENDING, &ignored);
  if (plic_available) mmio_write32(AX_PLIC_S_THRESHOLD, 0u);
  return plic_available;
}

int plic_present(void) { return plic_available; }

void plic_route(uint32_t source, uint32_t priority) {
  if (!plic_available) return;
  mmio_write32(AX_PLIC_PRIORITY(source), priority);
  mmio_write32(AX_PLIC_S_ENABLE,
               mmio_read32(AX_PLIC_S_ENABLE) | (1u << source));
  mmio_write32(AX_PLIC_S_THRESHOLD, 0u);
}

void plic_set_enabled(uint32_t source, int enabled) {
  if (!plic_available) return;
  const uint32_t mask = 1u << source;
  const uint32_t current = mmio_read32(AX_PLIC_S_ENABLE);
  mmio_write32(AX_PLIC_S_ENABLE, enabled ? (current | mask) : (current & ~mask));
}

uint32_t plic_claim(void) {
  return plic_available ? mmio_read32(AX_PLIC_S_CLAIM) : 0u;
}

void plic_complete(uint32_t source) {
  if (plic_available) mmio_write32(AX_PLIC_S_CLAIM, source);
}

void plic_dispatch(void) {
  /* Bounded: a source whose device handler fails to quiet its line would
   * otherwise re-arm forever and never let the trap return.  Every source may
   * legitimately be served once, plus one re-arm each, so the bound follows the
   * shell's source count rather than being a number picked to look safe. */
  for (unsigned served = 0; served < 2u * AX_PLIC_SOURCES; ++served) {
    const uint32_t source = plic_claim();
    if (source == 0u) return;
    /* Quiet the device first, then complete: the source is level-sensitive,
     * so completing a device that is still asserting simply re-arms it. */
    if (source == AX_PLIC_SRC_UART) console_irq_drain();
    if (source == AX_PLIC_SRC_ROLE) role_irq_complete();
    plic_complete(source);
  }
}
