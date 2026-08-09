#ifndef AXOS_FITNESS_H
#define AXOS_FITNESS_H

#include <stdint.h>

#include "evolution.h"

enum fitness_objective {
  FITNESS_OBJECTIVE_NONE = 0,
  FITNESS_OBJECTIVE_CYCLES_PER_WORK_Q10 = 1,
};

enum fitness_result_code {
  FITNESS_ELIGIBLE = 0,
  FITNESS_INELIGIBLE = 1,
  FITNESS_DISABLED = -1,
  FITNESS_INVALID = -2,
};

enum fitness_rejection {
  FITNESS_REJECT_INPUT = 1u << 0,
  FITNESS_REJECT_SEQUENCE = 1u << 1,
  FITNESS_REJECT_ORACLE = 1u << 2,
  FITNESS_REJECT_WORK = 1u << 3,
  FITNESS_REJECT_DESCRIPTOR = 1u << 4,
  FITNESS_REJECT_WATCHDOG = 1u << 5,
  FITNESS_REJECT_GENERATION = 1u << 6,
  FITNESS_REJECT_COUNTERS = 1u << 7,
  FITNESS_REJECT_SCORE_RANGE = 1u << 8,
};

struct fitness_snapshot {
  uint32_t sequence;
  uint64_t cycles;
  uint64_t work_completed;
  uint64_t memory_stalls;
  uint64_t descriptor_rejections;
  uint64_t watchdog_events;
  uint64_t configuration_generation;
};

struct fitness_trial {
  uint32_t candidate_id;
  uint32_t evidence_generation;
  uint32_t expected_work;
  uint32_t oracle_pass;
  uint32_t oracle_cases;
  uint32_t energy_valid;
  uint64_t energy_picojoules;
  struct fitness_snapshot before;
  struct fitness_snapshot after;
};

struct fitness_result {
  struct evolution_record record;
  uint32_t objective;
  uint32_t rejection_mask;
  uint64_t cycles;
  uint64_t work_completed;
  uint64_t memory_stalls;
  uint64_t descriptor_rejections;
  uint64_t watchdog_events;
  uint64_t configuration_generations;
};

int fitness_evaluate(const struct fitness_trial *trial,
                     struct fitness_result *out);

#endif
