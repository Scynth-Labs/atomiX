// Exact counter and coherent-snapshot regression for the Live FPGA L0 monitor.
#include <cstdint>
#include <cstdio>

#include "Vaxlivemon.h"
#include "verilated.h"

static int failures = 0;

static void check(bool condition, const char* description) {
  if (!condition) {
    std::fprintf(stderr, "FAIL: %s\n", description);
    failures++;
  }
}

static void tick(Vaxlivemon* top) {
  top->clk = 0;
  top->eval();
  top->clk = 1;
  top->eval();
  top->clk = 0;
  top->eval();
}

static void clear_events(Vaxlivemon* top) {
  top->snapshot_event = 0;
  top->work_completed_event = 0;
  top->memory_stall_event = 0;
  top->descriptor_rejected_event = 0;
  top->watchdog_event = 0;
  top->configuration_activated_event = 0;
}

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  Vaxlivemon top;
  top.clk = 0;
  top.rst = 1;
  clear_events(&top);
  tick(&top);

  check(top.snapshot_sequence == 0, "reset clears snapshot sequence");
  check(top.snapshot_cycles == 0, "reset clears cycle snapshot");
  check(top.snapshot_configuration_generation == 0,
        "reset clears configuration generation snapshot");

  top.rst = 0;
  top.snapshot_event = 1;
  tick(&top);
  clear_events(&top);
  check(top.snapshot_sequence == 1, "first snapshot increments sequence");
  check(top.snapshot_cycles == 1, "snapshot includes its own edge");

  top.work_completed_event = 1;
  tick(&top);  // active edge 2
  clear_events(&top);

  top.memory_stall_event = 1;
  top.descriptor_rejected_event = 1;
  tick(&top);  // active edge 3
  clear_events(&top);

  top.watchdog_event = 1;
  top.configuration_activated_event = 1;
  tick(&top);  // active edge 4
  clear_events(&top);

  top.snapshot_event = 1;
  top.work_completed_event = 1;
  top.memory_stall_event = 1;
  top.descriptor_rejected_event = 1;
  top.watchdog_event = 1;
  top.configuration_activated_event = 1;
  tick(&top);  // active edge 5, all events included in this snapshot
  clear_events(&top);

  check(top.snapshot_sequence == 2, "second snapshot increments sequence");
  check(top.snapshot_cycles == 5, "cycle count is exact");
  check(top.snapshot_work_completed == 2, "work events count exactly");
  check(top.snapshot_memory_stalls == 2, "stall events count exactly");
  check(top.snapshot_descriptor_rejections == 2,
        "rejection events count exactly");
  check(top.snapshot_watchdog_events == 2, "watchdog events count exactly");
  check(top.snapshot_configuration_generation == 2,
        "activation events count exactly");

  const uint64_t stable_cycles = top.snapshot_cycles;
  top.work_completed_event = 1;
  tick(&top);  // active edge 6
  clear_events(&top);
  tick(&top);  // active edge 7
  tick(&top);  // active edge 8
  check(top.snapshot_cycles == stable_cycles,
        "snapshot remains coherent while live counters advance");
  check(top.snapshot_work_completed == 2,
        "snapshot event counters remain stable too");

  top.snapshot_event = 1;
  tick(&top);  // active edge 9
  clear_events(&top);
  check(top.snapshot_sequence == 3, "third snapshot increments sequence");
  check(top.snapshot_cycles == 9, "later snapshot sees elapsed cycles");
  check(top.snapshot_work_completed == 3,
        "later snapshot sees events after the previous snapshot");

  // Reset wins over snapshot and every event on the same edge.
  top.rst = 1;
  top.snapshot_event = 1;
  top.work_completed_event = 1;
  top.memory_stall_event = 1;
  top.descriptor_rejected_event = 1;
  top.watchdog_event = 1;
  top.configuration_activated_event = 1;
  tick(&top);
  check(top.snapshot_sequence == 0, "reset has priority over snapshot");
  check(top.snapshot_cycles == 0, "reset has priority over cycle counting");
  check(top.snapshot_work_completed == 0, "reset has priority over events");
  check(top.snapshot_configuration_generation == 0,
        "reset has priority over activation");

  if (failures) {
    std::fprintf(stderr, "tb_axlivemon: %d FAILURE(S)\n", failures);
    return 1;
  }
  std::puts("tb_axlivemon: PASS");
  return 0;
}
