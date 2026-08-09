#ifndef AXOS_EVOLUTION_H
#define AXOS_EVOLUTION_H

#include <stdint.h>

enum evolution_tier {
  EVOLUTION_TIER_NONE = 0,
  EVOLUTION_TIER_SMALL = 1,
  EVOLUTION_TIER_MID = 2,
  EVOLUTION_TIER_LARGE = 3,
};

enum evolution_capability {
  EVOLUTION_CAP_RECORD = 1u << 0,
  EVOLUTION_CAP_PROPOSE = 1u << 1,
};

enum evolution_record_flag {
  /* Correctness is an input gate, never part of the numerical fitness. */
  EVOLUTION_RECORD_CORRECT = 1u << 0,
};

enum evolution_result {
  EVOLUTION_OK = 0,
  EVOLUTION_DISABLED = -1,
  EVOLUTION_INVALID = -2,
  EVOLUTION_FULL = -3,
  EVOLUTION_NO_PROPOSAL = -4,
  EVOLUTION_OBJECTIVE_MISMATCH = -5,
};

/* The caller owns the definition of fitness. Lower values rank first. The
 * evidence generation identifies the immutable telemetry snapshot/evidence
 * record from which that value was derived. */
struct evolution_record {
  uint32_t candidate_id;
  uint32_t fitness;
  uint32_t evidence_generation;
  uint32_t objective_id;
  uint32_t flags;
};

struct evolution_status {
  uint32_t tier;
  uint32_t capabilities;
  uint32_t capacity;
  uint32_t state_bytes;
  uint32_t records;
  uint32_t rejected;
  uint32_t overflows;
};

void evolution_init(void);
void evolution_get_status(struct evolution_status *out);
int evolution_record_candidate(const struct evolution_record *record);
int evolution_propose(struct evolution_record *out);
const char *evolution_tier_name(void);

#endif
