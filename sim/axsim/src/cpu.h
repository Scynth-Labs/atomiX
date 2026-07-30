#pragma once
#include <cstdint>
#include "bus.h"

// Why step() ended the simulation. Exceptions no longer stop the sim — they
// take a real trap. Fault remains only for "cannot continue" situations
// (trap taken while the target tvec is still 0, i.e. before the program
// installed a handler).
enum class Stop { None, Fault };

// Privileged CSR state (phase 4: M + S + U). mcycle/minstret are served from
// the retired-instruction counter (cycle == instret in an ISS) plus a
// per-counter write offset; see Cpu::counter.
struct Csrs {
  uint32_t mstatus = 0x1800;  // MPP=M; everything else clear at reset
  uint32_t mtvec = 0;
  uint32_t mepc = 0;
  uint32_t mcause = 0;
  uint32_t mtval = 0;
  uint32_t mscratch = 0;
  uint32_t mie = 0;
  uint32_t mip = 0;
  uint32_t medeleg = 0;
  uint32_t mideleg = 0;
  uint32_t mcounteren = 0;
  uint32_t stvec = 0;
  uint32_t sepc = 0;
  uint32_t scause = 0;
  uint32_t stval = 0;
  uint32_t sscratch = 0;
  uint32_t scounteren = 0;
  uint32_t satp = 0;
};

// Architectural result of the most recent step.  This is deliberately a
// machine-readable counterpart to trace mode: cosim consumes it directly
// instead of scraping stderr.  A trap is an executed instruction but not a
// retirement, matching the RTL commit-point convention.
struct StepTrace {
  bool valid = false;
  bool retired = false;
  bool trap = false;
  uint32_t pc = 0;
  uint32_t insn = 0;
  uint32_t cause = 0;
  uint32_t tval = 0;
  bool rd_we = false;
  uint32_t rd = 0;
  uint32_t rd_val = 0;
};

class Cpu {
 public:
  Cpu(Bus& bus, uint32_t reset_pc) : pc(reset_pc), bus(bus) {}

  // Fetch/decode/execute one instruction. An enabled interrupt is entered
  // after that instruction retires, mirroring aXcore's MEM commit point.
  Stop step();

  uint32_t pc;
  uint32_t x[32] = {};       // x0 held at zero by the writeback path
  uint32_t prv = 3;          // current privilege: 0=U, 1=S, 3=M
  Csrs csr;
  StepTrace last;
  bool trace = false;        // one line per retired instruction, to stderr
  // Phase 4 S/U + Sv32 support. The RTL is still M-only, so the cosim
  // harness clears this to keep lock-step semantics identical; standalone
  // aXsim models the full privileged architecture.
  bool ext_su = true;
  uint64_t retired() const { return ninsn; }

 private:
  Stop trap(uint32_t cause, uint32_t tval);
  Stop interrupt(uint32_t cause, uint32_t resume_pc);
  Stop enter_trap(uint32_t cause, uint32_t tval, uint32_t trap_pc,
                  bool is_interrupt);
  bool csr_read(uint32_t addr, uint32_t& val);
  bool csr_write(uint32_t addr, uint32_t val);
  bool ctr_ok(unsigned bit) const;
  // mcycle/minstret (and their high halves) are M-mode writable. The retired
  // count is the free-running source; a write records the offset that maps it
  // onto the architectural value. addr bit 1 selects instret over cycle.
  uint64_t counter(uint32_t addr) const;
  void write_counter(uint32_t addr, bool high, uint32_t val);
  // Sv32 translation for acc 0=fetch/1=load/2=store. On success returns
  // true with the physical address in pa; on failure returns false with the
  // page/access-fault cause in cause (tval is the caller's virtual address).
  bool translate(uint32_t va, int acc, uint32_t& pa, uint32_t& cause);

  Bus& bus;
  uint64_t ninsn = 0;
  uint64_t cycle_off = 0;    // architectural counter value minus ninsn
  uint64_t instret_off = 0;
  uint32_t soft_ip = 0;      // software-writable mip bits (SSIP/STIP/SEIP)
};
