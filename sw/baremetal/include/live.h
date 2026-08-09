#pragma once

#include <stdint.h>

#include "platform.h"

/* Live FPGA L0 telemetry is shell-owned and therefore remains readable while
 * the role window is isolated or replaced.  Counter words come from one
 * coherent snapshot; reading low then high cannot tear. */
#define AX_LIVE_BASE       0x10020100u
#define AX_LIVE_ID         (AX_LIVE_BASE + 0x00u)
#define AX_LIVE_VERSION    (AX_LIVE_BASE + 0x04u)
#define AX_LIVE_COMMAND    (AX_LIVE_BASE + 0x08u)
#define AX_LIVE_SEQUENCE   (AX_LIVE_BASE + 0x0cu)
#define AX_LIVE_CYCLES     (AX_LIVE_BASE + 0x10u)
#define AX_LIVE_WORK       (AX_LIVE_BASE + 0x18u)
#define AX_LIVE_STALLS     (AX_LIVE_BASE + 0x20u)
#define AX_LIVE_REJECTIONS (AX_LIVE_BASE + 0x28u)
#define AX_LIVE_WATCHDOGS  (AX_LIVE_BASE + 0x30u)
#define AX_LIVE_GENERATION (AX_LIVE_BASE + 0x38u)

#define AX_LIVE_MAGIC       0x61584c56u /* "aXLV" */
#define AX_LIVE_VERSION_1_0 0x00010000u

#define AX_LIVE_CMD_SNAPSHOT 1u
#define AX_LIVE_CMD_ACTIVATE 2u

struct ax_live_snapshot {
  uint32_t sequence;
  uint64_t cycles;
  uint64_t work_completed;
  uint64_t memory_stalls;
  uint64_t descriptor_rejections;
  uint64_t watchdog_events;
  uint64_t configuration_generation;
};

static inline uint64_t ax_live_read64(uint32_t address) {
  const uint64_t low = mmio_read32(address);
  return low | ((uint64_t)mmio_read32(address + 4u) << 32);
}

static inline void ax_live_snapshot(struct ax_live_snapshot *out) {
  mmio_write32(AX_LIVE_COMMAND, AX_LIVE_CMD_SNAPSHOT);
  out->sequence = mmio_read32(AX_LIVE_SEQUENCE);
  out->cycles = ax_live_read64(AX_LIVE_CYCLES);
  out->work_completed = ax_live_read64(AX_LIVE_WORK);
  out->memory_stalls = ax_live_read64(AX_LIVE_STALLS);
  out->descriptor_rejections = ax_live_read64(AX_LIVE_REJECTIONS);
  out->watchdog_events = ax_live_read64(AX_LIVE_WATCHDOGS);
  out->configuration_generation = ax_live_read64(AX_LIVE_GENERATION);
}
