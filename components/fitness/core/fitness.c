#include <stdint.h>

#include "fitness.h"

#ifndef AX_FITNESS_ENABLED
#error "fitness component must define AX_FITNESS_ENABLED"
#endif
#ifndef AX_FITNESS_OBJECTIVE
#error "fitness component must define AX_FITNESS_OBJECTIVE"
#endif

#if AX_FITNESS_ENABLED
#define FITNESS_API __attribute__((section(".text.fitness")))
#else
#define FITNESS_API
#endif

static uint64_t delta64(uint64_t before, uint64_t after) {
  return after - before;
}

/* Freestanding RV32 builds must not acquire an implicit libgcc dependency
 * merely to score a candidate. The divisor is the declared uint32 work count;
 * this fixed 64-step divider has bounded time and identical host/RV32 results. */
#if AX_FITNESS_ENABLED
static uint64_t divide_u64_u32(uint64_t numerator, uint32_t denominator,
                               uint32_t *remainder) {
  const uint32_t numerator_high = (uint32_t)(numerator >> 32);
  const uint32_t numerator_low = (uint32_t)numerator;
  uint32_t quotient_high = 0;
  uint32_t quotient_low = 0;
  uint64_t rest = 0;
  for (uint32_t bit = 32; bit != 0; --bit) {
    rest = (rest << 1) | ((numerator_high >> (bit - 1u)) & 1u);
    if (rest >= denominator) {
      rest -= denominator;
      quotient_high |= UINT32_C(1) << (bit - 1u);
    }
  }
  for (uint32_t bit = 32; bit != 0; --bit) {
    rest = (rest << 1) | ((numerator_low >> (bit - 1u)) & 1u);
    if (rest >= denominator) {
      rest -= denominator;
      quotient_low |= UINT32_C(1) << (bit - 1u);
    }
  }
  *remainder = (uint32_t)rest;
  return ((uint64_t)quotient_high << 32) | quotient_low;
}
#endif

FITNESS_API int fitness_evaluate(const struct fitness_trial *trial,
                                 struct fitness_result *out) {
  if (trial == 0 || out == 0) return FITNESS_INVALID;

  out->record.candidate_id = trial->candidate_id;
  out->record.fitness = UINT32_MAX;
  out->record.evidence_generation = trial->evidence_generation;
  out->record.objective_id = AX_FITNESS_OBJECTIVE;
  out->record.flags = 0;
  out->objective = AX_FITNESS_OBJECTIVE;
  out->rejection_mask = 0;
  out->cycles = delta64(trial->before.cycles, trial->after.cycles);
  out->work_completed = delta64(trial->before.work_completed,
                                trial->after.work_completed);
  out->memory_stalls = delta64(trial->before.memory_stalls,
                               trial->after.memory_stalls);
  out->descriptor_rejections = delta64(
      trial->before.descriptor_rejections,
      trial->after.descriptor_rejections);
  out->watchdog_events = delta64(trial->before.watchdog_events,
                                 trial->after.watchdog_events);
  out->configuration_generations = delta64(
      trial->before.configuration_generation,
      trial->after.configuration_generation);

#if !AX_FITNESS_ENABLED
  return FITNESS_DISABLED;
#else
  if (trial->candidate_id == 0 || trial->expected_work == 0)
    out->rejection_mask |= FITNESS_REJECT_INPUT;
  if ((uint32_t)(trial->after.sequence - trial->before.sequence) != 1u)
    out->rejection_mask |= FITNESS_REJECT_SEQUENCE;
  if (trial->oracle_pass != 1u || trial->oracle_cases == 0)
    out->rejection_mask |= FITNESS_REJECT_ORACLE;
  if (out->work_completed != trial->expected_work)
    out->rejection_mask |= FITNESS_REJECT_WORK;
  if (out->descriptor_rejections != 0)
    out->rejection_mask |= FITNESS_REJECT_DESCRIPTOR;
  if (out->watchdog_events != 0)
    out->rejection_mask |= FITNESS_REJECT_WATCHDOG;
  if (out->configuration_generations != 0)
    out->rejection_mask |= FITNESS_REJECT_GENERATION;
  if (out->cycles == 0 || out->memory_stalls > out->cycles)
    out->rejection_mask |= FITNESS_REJECT_COUNTERS;

  if (out->rejection_mask == 0) {
    uint32_t remainder;
    const uint32_t work = (uint32_t)out->work_completed;
    const uint64_t whole = divide_u64_u32(out->cycles, work, &remainder);
    if (whole > UINT32_MAX / 1024u) {
      out->rejection_mask |= FITNESS_REJECT_SCORE_RANGE;
    } else {
      const uint64_t scaled_remainder = remainder * 1024u;
      uint32_t fraction_remainder;
      const uint64_t fraction =
          divide_u64_u32(scaled_remainder, work, &fraction_remainder) +
          (fraction_remainder != 0);
      const uint64_t score = whole * 1024u + fraction;
      if (score > UINT32_MAX) {
        out->rejection_mask |= FITNESS_REJECT_SCORE_RANGE;
      } else {
        out->record.fitness = (uint32_t)score;
        out->record.flags = EVOLUTION_RECORD_CORRECT;
      }
    }
  }
  return out->rejection_mask == 0 ? FITNESS_ELIGIBLE : FITNESS_INELIGIBLE;
#endif
}
