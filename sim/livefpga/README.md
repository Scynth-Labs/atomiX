# Live FPGA closed-loop simulator

This deterministic test sits between a pure policy unit test and full RTL. It
models the immutable shell's counters, configuration generation, manager-only
activation path, workload oracle, and four virtual candidate behaviours. The
test links the same `fitness.c` and `evolution.c` used by aXos's
`kernel-evolve-small` profile; there is no second policy implementation.

The scenario proves that:

- a correct faster candidate outranks the correct baseline;
- an even faster wrong candidate cannot pass the oracle gate;
- a watchdog event makes a candidate ineligible;
- asking for a proposal cannot change the active virtual configuration;
- a failed post-activation canary invalidates that candidate;
- the evolver proposes the known correct baseline, after which only the
  simulated immutable manager performs rollback; and
- the four-record state remains inside the small tier's 96-byte bound.

Run it directly with `make -C sim/livefpga check`, or run the complete
hardware-free Live FPGA chain with `make live-sim-check`.

The target runs the scenario twice: a verbose native build for fast diagnosis,
then a freestanding RV32 build under aXsim with exactly 32 KiB of RAM. Both
executables link the production component sources. The RV32 leg catches target
compiler, ABI, arithmetic, and instruction-set differences without requiring
the physical board.

This is a behavioural fault model, not a timing model of Gowin configuration
frames. Passing it proves the bounded kernel decision path under the declared
telemetry contract. Verilator still covers synthesizable shell/role behaviour,
and the physical Primer remains the final electrical and configuration test.
