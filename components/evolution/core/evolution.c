#include <stdint.h>

#include "evolution.h"

#ifndef AX_EVOLUTION_TIER
#error "evolution component must define AX_EVOLUTION_TIER"
#endif
#ifndef AX_EVOLUTION_CAPABILITIES
#error "evolution component must define AX_EVOLUTION_CAPABILITIES"
#endif
#ifndef AX_EVOLUTION_CAPACITY
#error "evolution component must define AX_EVOLUTION_CAPACITY"
#endif
#ifndef AX_EVOLUTION_STATE_BUDGET
#error "evolution component must define AX_EVOLUTION_STATE_BUDGET"
#endif

/* An evolving profile must contain the callable policy even before the
 * fitness producer is connected. The linker KEEP is conditional in practice:
 * the none implementation uses ordinary function sections and is collected. */
#if AX_EVOLUTION_CAPACITY > 0
#define EVOLUTION_API __attribute__((section(".text.evolution")))
#else
#define EVOLUTION_API
#endif

#if AX_EVOLUTION_CAPACITY > 0
struct evolution_state {
  uint32_t count;
  uint32_t rejected;
  uint32_t overflows;
  uint32_t objective_id;
  struct evolution_record records[AX_EVOLUTION_CAPACITY];
};

/* KEEP'd by the kernel linker even before a policy consumer is wired. This
 * makes selecting a tier reserve its real bounded state, while the ordinary
 * boot-time BSS clear initializes it without pulling policy code into the
 * Primer monitor. */
static struct evolution_state evolution_state_store
    __attribute__((section(".bss.evolution")));

_Static_assert(sizeof(evolution_state_store) <= AX_EVOLUTION_STATE_BUDGET,
               "evolution implementation exceeds its hard state budget");
#endif

EVOLUTION_API const char *evolution_tier_name(void) {
  switch (AX_EVOLUTION_TIER) {
    case EVOLUTION_TIER_NONE: return "none";
    case EVOLUTION_TIER_SMALL: return "small";
    case EVOLUTION_TIER_MID: return "mid";
    case EVOLUTION_TIER_LARGE: return "large";
    default: return "custom";
  }
}

EVOLUTION_API void evolution_init(void) {
#if AX_EVOLUTION_CAPACITY > 0
  uint32_t *word = (uint32_t *)(void *)&evolution_state_store;
  for (uint32_t i = 0; i < sizeof(evolution_state_store) / sizeof(*word); ++i)
    word[i] = 0;
#endif
}

EVOLUTION_API void evolution_get_status(struct evolution_status *out) {
  if (out == 0) return;
  out->tier = AX_EVOLUTION_TIER;
  out->capabilities = AX_EVOLUTION_CAPABILITIES;
  out->capacity = AX_EVOLUTION_CAPACITY;
  out->state_bytes = AX_EVOLUTION_STATE_BUDGET;
#if AX_EVOLUTION_CAPACITY > 0
  out->records = evolution_state_store.count;
  out->rejected = evolution_state_store.rejected;
  out->overflows = evolution_state_store.overflows;
#else
  out->records = 0;
  out->rejected = 0;
  out->overflows = 0;
#endif
}

EVOLUTION_API int evolution_record_candidate(const struct evolution_record *record) {
  if (record == 0 || record->candidate_id == 0 || record->objective_id == 0)
    return EVOLUTION_INVALID;
#if AX_EVOLUTION_CAPACITY == 0
  return EVOLUTION_DISABLED;
#else
  if (evolution_state_store.count != 0 &&
      evolution_state_store.objective_id != record->objective_id)
    return EVOLUTION_OBJECTIVE_MISMATCH;
  uint32_t slot = AX_EVOLUTION_CAPACITY;
  for (uint32_t i = 0; i < evolution_state_store.count; ++i) {
    if (evolution_state_store.records[i].candidate_id == record->candidate_id) {
      slot = i;
      break;
    }
  }
  if (slot == AX_EVOLUTION_CAPACITY) {
    if (evolution_state_store.count == AX_EVOLUTION_CAPACITY) {
      evolution_state_store.overflows++;
      return EVOLUTION_FULL;
    }
    slot = evolution_state_store.count++;
    if (slot == 0) evolution_state_store.objective_id = record->objective_id;
  }
  evolution_state_store.records[slot].candidate_id = record->candidate_id;
  evolution_state_store.records[slot].fitness = record->fitness;
  evolution_state_store.records[slot].evidence_generation =
      record->evidence_generation;
  evolution_state_store.records[slot].objective_id = record->objective_id;
  evolution_state_store.records[slot].flags = record->flags;
  if ((record->flags & EVOLUTION_RECORD_CORRECT) == 0)
    evolution_state_store.rejected++;
  return EVOLUTION_OK;
#endif
}

EVOLUTION_API int evolution_propose(struct evolution_record *out) {
  if (out == 0) return EVOLUTION_INVALID;
#if AX_EVOLUTION_CAPACITY == 0
  return EVOLUTION_DISABLED;
#else
  const struct evolution_record *best = 0;
  for (uint32_t i = 0; i < evolution_state_store.count; ++i) {
    const struct evolution_record *const candidate =
        &evolution_state_store.records[i];
    if ((candidate->flags & EVOLUTION_RECORD_CORRECT) == 0) continue;
    if (best == 0 || candidate->fitness < best->fitness ||
        (candidate->fitness == best->fitness &&
         candidate->candidate_id < best->candidate_id))
      best = candidate;
  }
  if (best == 0) return EVOLUTION_NO_PROPOSAL;
  out->candidate_id = best->candidate_id;
  out->fitness = best->fitness;
  out->evidence_generation = best->evidence_generation;
  out->objective_id = best->objective_id;
  out->flags = best->flags;
  return EVOLUTION_OK;
#endif
}
