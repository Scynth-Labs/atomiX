#include <stdint.h>
#include <stdio.h>

#include "fitness.h"

static int check(int condition, const char *message) {
  if (condition) return 0;
  fprintf(stderr, "fitness policy test: %s\n", message);
  return 1;
}

static struct fitness_trial valid_trial(void) {
  const struct fitness_trial trial = {
      .candidate_id = 9,
      .evidence_generation = 12,
      .expected_work = 4,
      .oracle_pass = 1,
      .oracle_cases = 3,
      .before = {
          .sequence = 7,
          .cycles = 1000,
          .work_completed = 10,
          .memory_stalls = 100,
          .descriptor_rejections = 2,
          .watchdog_events = 3,
          .configuration_generation = 4,
      },
      .after = {
          .sequence = 8,
          .cycles = 1501,
          .work_completed = 14,
          .memory_stalls = 201,
          .descriptor_rejections = 2,
          .watchdog_events = 3,
          .configuration_generation = 4,
      },
  };
  return trial;
}

int main(void) {
  struct fitness_trial trial = valid_trial();
  struct fitness_result result;

#if !AX_FITNESS_ENABLED
  if (check(fitness_evaluate(&trial, &result) == FITNESS_DISABLED,
            "none policy evaluated a trial"))
    return 1;
  puts("fitness policy: none: PASS");
  return 0;
#else
  if (check(fitness_evaluate(&trial, &result) == FITNESS_ELIGIBLE,
            "valid trial was rejected") ||
      check(result.rejection_mask == 0, "valid trial has rejection bits") ||
      check(result.cycles == 501 && result.work_completed == 4,
            "counter deltas are wrong") ||
      check(result.record.fitness == 128256,
            "Q22.10 cycles/work score is wrong") ||
      check(result.record.flags == EVOLUTION_RECORD_CORRECT,
            "valid trial lacks correctness flag"))
    return 1;

  trial.oracle_pass = 0;
  if (check(fitness_evaluate(&trial, &result) == FITNESS_INELIGIBLE,
            "oracle failure was eligible") ||
      check((result.rejection_mask & FITNESS_REJECT_ORACLE) != 0,
            "oracle failure lacks reason") ||
      check(result.record.flags == 0, "oracle failure retained correctness"))
    return 1;

  trial = valid_trial();
  trial.after.descriptor_rejections++;
  trial.after.watchdog_events++;
  trial.after.configuration_generation++;
  if (check(fitness_evaluate(&trial, &result) == FITNESS_INELIGIBLE,
            "safety event trial was eligible") ||
      check((result.rejection_mask & FITNESS_REJECT_DESCRIPTOR) != 0,
            "descriptor rejection lacks reason") ||
      check((result.rejection_mask & FITNESS_REJECT_WATCHDOG) != 0,
            "watchdog event lacks reason") ||
      check((result.rejection_mask & FITNESS_REJECT_GENERATION) != 0,
            "generation change lacks reason"))
    return 1;

  trial = valid_trial();
  trial.before.sequence = UINT32_MAX;
  trial.after.sequence = 0;
  trial.before.cycles = UINT64_MAX - 9u;
  trial.after.cycles = 10;
  trial.before.work_completed = UINT64_MAX - 1u;
  trial.after.work_completed = 2;
  trial.before.memory_stalls = UINT64_MAX - 2u;
  trial.after.memory_stalls = 1;
  trial.expected_work = 4;
  if (check(fitness_evaluate(&trial, &result) == FITNESS_ELIGIBLE,
            "modular counter wrap was rejected") ||
      check(result.cycles == 20 && result.work_completed == 4 &&
                result.memory_stalls == 4,
            "modular counter deltas are wrong") ||
      check(result.record.fitness == 5120, "wrapped score is wrong"))
    return 1;

  puts("fitness policy: cycles-per-work: PASS");
  return 0;
#endif
}
