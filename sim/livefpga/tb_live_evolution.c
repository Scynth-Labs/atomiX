#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "evolution.h"
#include "fitness.h"

struct virtual_fpga {
  struct fitness_snapshot counters;
  uint32_t active_candidate;
};

struct virtual_candidate {
  const char *name;
  uint32_t id;
  uint64_t cycles;
  uint64_t work;
  uint64_t memory_stalls;
  uint64_t descriptor_rejections;
  uint64_t watchdog_events;
  uint32_t oracle_pass;
};

static void fail(const char *message) {
  fprintf(stderr, "live evolution simulator: %s\n", message);
  exit(1);
}

static void check(int condition, const char *message) {
  if (!condition) fail(message);
}

static struct fitness_snapshot snapshot(struct virtual_fpga *fpga) {
  fpga->counters.sequence++;
  return fpga->counters;
}

/* Only the simulated immutable manager can call this. Neither fitness nor
 * evolution receives an activation callback or a pointer to this state. */
static void manager_activate(struct virtual_fpga *fpga, uint32_t candidate_id) {
  fpga->active_candidate = candidate_id;
  fpga->counters.configuration_generation++;
}

static struct fitness_result shadow_run(
    struct virtual_fpga *fpga, const struct virtual_candidate *candidate,
    uint32_t evidence_generation) {
  struct fitness_trial trial = {0};
  struct fitness_result result;

  trial.candidate_id = candidate->id;
  trial.evidence_generation = evidence_generation;
  trial.expected_work = 10;
  trial.oracle_pass = candidate->oracle_pass;
  trial.oracle_cases = 1;
  trial.before = snapshot(fpga);

  fpga->counters.cycles += candidate->cycles;
  fpga->counters.work_completed += candidate->work;
  fpga->counters.memory_stalls += candidate->memory_stalls;
  fpga->counters.descriptor_rejections += candidate->descriptor_rejections;
  fpga->counters.watchdog_events += candidate->watchdog_events;

  trial.after = snapshot(fpga);
  const int code = fitness_evaluate(&trial, &result);
  check(code == (result.rejection_mask == 0 ? FITNESS_ELIGIBLE
                                            : FITNESS_INELIGIBLE),
        "fitness return code disagrees with rejection mask");
  check(evolution_record_candidate(&result.record) == EVOLUTION_OK,
        "evolution service refused a simulated result");

  printf("  %-15s id=%u fitness=%u eligible=%s reject=0x%03x\n",
         candidate->name, candidate->id, result.record.fitness,
         code == FITNESS_ELIGIBLE ? "yes" : "no",
         result.rejection_mask);
  return result;
}

int main(void) {
  const struct virtual_candidate baseline = {
      "baseline", 1, 1200, 10, 200, 0, 0, 1};
  const struct virtual_candidate optimized = {
      "optimized", 2, 800, 10, 80, 0, 0, 1};
  const struct virtual_candidate cheating = {
      "wrong-fast", 3, 100, 10, 0, 0, 0, 0};
  const struct virtual_candidate timed_out = {
      "watchdog", 4, 700, 10, 50, 0, 1, 1};
  const struct virtual_candidate broken_canary = {
      "canary-fault", 2, 90, 10, 0, 0, 0, 0};
  struct virtual_fpga fpga = {0};
  struct evolution_record proposal;
  struct evolution_status status;

  evolution_init();
  manager_activate(&fpga, baseline.id);
  check(fpga.active_candidate == baseline.id &&
            fpga.counters.configuration_generation == 1,
        "baseline activation was not recorded");

  puts("virtual FPGA shadow trials:");
  const struct fitness_result base_result = shadow_run(&fpga, &baseline, 1);
  const struct fitness_result opt_result = shadow_run(&fpga, &optimized, 2);
  const struct fitness_result cheat_result = shadow_run(&fpga, &cheating, 3);
  const struct fitness_result timeout_result = shadow_run(&fpga, &timed_out, 4);

  check(base_result.record.fitness == 122880,
        "baseline Q22.10 score is wrong");
  check(opt_result.record.fitness == 81920,
        "optimized Q22.10 score is wrong");
  check((cheat_result.rejection_mask & FITNESS_REJECT_ORACLE) != 0,
        "incorrect fast candidate escaped its oracle gate");
  check((timeout_result.rejection_mask & FITNESS_REJECT_WATCHDOG) != 0,
        "timed-out candidate escaped its watchdog gate");

  const uint32_t generation_before_proposal =
      (uint32_t)fpga.counters.configuration_generation;
  check(evolution_propose(&proposal) == EVOLUTION_OK,
        "no initial proposal was produced");
  check(proposal.candidate_id == optimized.id,
        "evolver did not select the fastest correct candidate");
  check(fpga.active_candidate == baseline.id &&
            fpga.counters.configuration_generation == generation_before_proposal,
        "proposal changed virtual hardware without manager authority");
  printf("proposal: candidate=%u; active remains=%u\n",
         proposal.candidate_id, fpga.active_candidate);

  manager_activate(&fpga, proposal.candidate_id);
  check(fpga.active_candidate == optimized.id,
        "manager did not activate the proposal");

  puts("fault-injected activation canary:");
  const struct fitness_result canary_result =
      shadow_run(&fpga, &broken_canary, 5);
  check((canary_result.rejection_mask & FITNESS_REJECT_ORACLE) != 0,
        "fault-injected canary was accepted");
  check(evolution_propose(&proposal) == EVOLUTION_OK,
        "evolver did not produce a recovery proposal");
  check(proposal.candidate_id == baseline.id,
        "evolver did not fall back to the known correct candidate");
  check(fpga.active_candidate == optimized.id,
        "recovery proposal actuated before manager approval");

  manager_activate(&fpga, proposal.candidate_id);
  check(fpga.active_candidate == baseline.id &&
            fpga.counters.configuration_generation == 3,
        "manager rollback did not restore the baseline generation");

  evolution_get_status(&status);
  check(status.tier == EVOLUTION_TIER_SMALL && status.capacity == 4,
        "simulator is not exercising the small evolution tier");
  check(status.records == 4 && status.rejected == 3 && status.overflows == 0,
        "bounded evolution status is wrong after fault injection");

  printf("rollback: candidate=%u generation=%u\n", fpga.active_candidate,
         (uint32_t)fpga.counters.configuration_generation);
  puts("live evolution simulator: PASS");
  return 0;
}
