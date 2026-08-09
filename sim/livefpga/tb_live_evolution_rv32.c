#include <stdint.h>

#include "evolution.h"
#include "fitness.h"

volatile uint32_t tohost __attribute__((section(".tohost"), aligned(16)));

struct virtual_fpga {
  struct fitness_snapshot counters;
  uint32_t active_candidate;
};

struct virtual_candidate {
  uint32_t id;
  uint64_t cycles;
  uint64_t work;
  uint64_t memory_stalls;
  uint64_t watchdog_events;
  uint32_t oracle_pass;
};

static struct virtual_fpga fpga;
static struct fitness_trial trial;
static struct fitness_result result;

static void finish(uint32_t code) __attribute__((noreturn));

static void finish(uint32_t code) {
  tohost = code == 0 ? 1u : (code << 1) | 1u;
  for (;;) __asm__ volatile("wfi");
}

static void require(int condition, uint32_t code) {
  if (!condition) finish(code);
}

static struct fitness_snapshot snapshot(struct virtual_fpga *fpga) {
  fpga->counters.sequence++;
  return fpga->counters;
}

static void manager_activate(struct virtual_fpga *fpga, uint32_t candidate_id) {
  fpga->active_candidate = candidate_id;
  fpga->counters.configuration_generation++;
}

static struct fitness_result run(struct virtual_fpga *fpga,
                                 const struct virtual_candidate *candidate,
                                 uint32_t evidence_generation) {
  trial.candidate_id = candidate->id;
  trial.evidence_generation = evidence_generation;
  trial.expected_work = 10;
  trial.oracle_pass = candidate->oracle_pass;
  trial.oracle_cases = 1;
  trial.energy_valid = 0;
  trial.energy_picojoules = 0;
  trial.before = snapshot(fpga);
  fpga->counters.cycles += candidate->cycles;
  fpga->counters.work_completed += candidate->work;
  fpga->counters.memory_stalls += candidate->memory_stalls;
  fpga->counters.watchdog_events += candidate->watchdog_events;
  trial.after = snapshot(fpga);
  (void)fitness_evaluate(&trial, &result);
  require(evolution_record_candidate(&result.record) == EVOLUTION_OK, 10);
  return result;
}

int main(void) {
  const struct virtual_candidate baseline = {1, 1200, 10, 200, 0, 1};
  const struct virtual_candidate optimized = {2, 800, 10, 80, 0, 1};
  const struct virtual_candidate wrong_fast = {3, 100, 10, 0, 0, 0};
  const struct virtual_candidate watchdog = {4, 700, 10, 50, 1, 1};
  const struct virtual_candidate canary_fault = {2, 90, 10, 0, 0, 0};
  struct evolution_record proposal;
  struct evolution_status status;

  evolution_init();
  manager_activate(&fpga, baseline.id);
  const struct fitness_result baseline_result = run(&fpga, &baseline, 1);
  const struct fitness_result optimized_result = run(&fpga, &optimized, 2);
  const struct fitness_result wrong_result = run(&fpga, &wrong_fast, 3);
  const struct fitness_result watchdog_result = run(&fpga, &watchdog, 4);

  require(baseline_result.record.fitness == 122880, 11);
  require(optimized_result.record.fitness == 81920, 12);
  require((wrong_result.rejection_mask & FITNESS_REJECT_ORACLE) != 0, 13);
  require((watchdog_result.rejection_mask & FITNESS_REJECT_WATCHDOG) != 0, 14);
  require(evolution_propose(&proposal) == EVOLUTION_OK &&
              proposal.candidate_id == optimized.id,
          15);
  require(fpga.active_candidate == baseline.id &&
              fpga.counters.configuration_generation == 1,
          16);

  manager_activate(&fpga, proposal.candidate_id);
  const struct fitness_result canary_result = run(&fpga, &canary_fault, 5);
  require((canary_result.rejection_mask & FITNESS_REJECT_ORACLE) != 0, 17);
  require(evolution_propose(&proposal) == EVOLUTION_OK &&
              proposal.candidate_id == baseline.id,
          18);
  require(fpga.active_candidate == optimized.id, 19);
  manager_activate(&fpga, proposal.candidate_id);

  evolution_get_status(&status);
  require(fpga.active_candidate == baseline.id &&
              fpga.counters.configuration_generation == 3,
          20);
  require(status.records == 4 && status.rejected == 3 &&
              status.overflows == 0,
          21);
  finish(0);
}
