#pragma once

/* The scheduler's timer quantum, in cycles of the CLINT's mtime.
 *
 * This machine's CLINT advances mtime once per clock, so the quantum is also
 * wall-clock cycles.  Both the M-mode shim in trap.S and the S-mode handler in
 * kernel.c arm the timer, and they must agree, so the value lives here rather
 * than as a literal in each -- an assembly constant and a C constant that
 * drifted apart would be invisible until a workload starved.
 *
 * It is a profile setting because it is neither the scheduler's nor the trap
 * code's alone: the shim acknowledges the interrupt, the handler decides who
 * runs next, and how long a task should run before being preempted depends on
 * the machine it runs on.  A hart on 32 MiB of SDRAM through a 16-line cache
 * services a tick in far more cycles than the same code on single-cycle
 * on-chip RAM, and the quantum has to be large enough to leave the resumed
 * task room to run.
 *
 * See docs/memory.md for the measurement that set the floor: with the quantum
 * armed at trap entry rather than on the way out, the SDRAM pin model retired
 * 785 user instructions across 787 handler entries -- one instruction per
 * quantum -- and never finished an ELF exec in 120 million cycles. */
#ifndef AXOS_TIMER_QUANTUM_CYCLES
#define AXOS_TIMER_QUANTUM_CYCLES 2000
#endif

#ifndef __ASSEMBLER__
/* Coarse bounds only: what is "long enough" is a property of the machine, and
 * the profile is where that is known.  Below the floor the trap entry sequence
 * itself cannot complete before the next deadline on any memory this project
 * targets; above the ceiling the delta approaches the range of the 32-bit
 * mtimecmp arithmetic the shim uses. */
_Static_assert(AXOS_TIMER_QUANTUM_CYCLES >= 256,
               "AXOS_TIMER_QUANTUM_CYCLES below 256 cannot cover trap entry");
_Static_assert(AXOS_TIMER_QUANTUM_CYCLES <= (1 << 24),
               "AXOS_TIMER_QUANTUM_CYCLES must stay well inside 32-bit mtime "
               "arithmetic");
#endif
