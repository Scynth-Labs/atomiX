#include <stdint.h>
#include <stdio.h>

#include "evolution.h"

static int check(int condition, const char *message) {
  if (condition) return 0;
  fprintf(stderr, "evolution policy test: %s\n", message);
  return 1;
}

int main(void) {
  struct evolution_status status;
  struct evolution_record proposal;
  struct evolution_record record = {
      .candidate_id = 1,
      .fitness = 100,
      .evidence_generation = 7,
      .objective_id = 1,
      .flags = EVOLUTION_RECORD_CORRECT,
  };

  evolution_init();
  evolution_get_status(&status);
  if (check(status.tier == AX_EVOLUTION_TIER, "wrong tier") ||
      check(status.capacity == AX_EVOLUTION_CAPACITY, "wrong capacity") ||
      check(status.state_bytes == AX_EVOLUTION_STATE_BUDGET,
            "wrong state budget"))
    return 1;

#if AX_EVOLUTION_CAPACITY == 0
  if (check(evolution_record_candidate(&record) == EVOLUTION_DISABLED,
            "none tier accepted a record") ||
      check(evolution_propose(&proposal) == EVOLUTION_DISABLED,
            "none tier returned a proposal"))
    return 1;
#else
  if (check(evolution_record_candidate(&record) == EVOLUTION_OK,
            "initial record failed") ||
      check(evolution_propose(&proposal) == EVOLUTION_OK,
            "initial proposal failed") ||
      check(proposal.candidate_id == 1 && proposal.fitness == 100,
            "initial proposal changed"))
    return 1;

  record.flags = 0;
  if (check(evolution_record_candidate(&record) == EVOLUTION_OK,
            "incorrect result was not recorded") ||
      check(evolution_propose(&proposal) == EVOLUTION_NO_PROPOSAL,
            "incorrect candidate was proposed"))
    return 1;

  record.flags = EVOLUTION_RECORD_CORRECT;
  record.candidate_id = 2;
  record.objective_id = 2;
  if (check(evolution_record_candidate(&record) ==
                EVOLUTION_OBJECTIVE_MISMATCH,
            "mixed objective was accepted"))
    return 1;
  record.objective_id = 1;
  for (uint32_t id = 1; id <= AX_EVOLUTION_CAPACITY; ++id) {
    record.candidate_id = id;
    record.fitness = AX_EVOLUTION_CAPACITY - id + 1u;
    record.evidence_generation++;
    if (check(evolution_record_candidate(&record) == EVOLUTION_OK,
              "bounded table fill failed"))
      return 1;
  }
  record.candidate_id = AX_EVOLUTION_CAPACITY + 1u;
  if (check(evolution_record_candidate(&record) == EVOLUTION_FULL,
            "table overflow was not rejected") ||
      check(evolution_propose(&proposal) == EVOLUTION_OK,
            "filled table has no proposal") ||
      check(proposal.candidate_id == AX_EVOLUTION_CAPACITY &&
                proposal.fitness == 1,
            "fitness ranking was not deterministic"))
    return 1;

  evolution_get_status(&status);
  if (check(status.records == AX_EVOLUTION_CAPACITY, "wrong record count") ||
      check(status.rejected == 1, "wrong correctness rejection count") ||
      check(status.overflows == 1, "wrong overflow count"))
    return 1;
#endif

  printf("evolution policy: %s capacity=%u state=%u: PASS\n",
         evolution_tier_name(), status.capacity, status.state_bytes);
  return 0;
}
