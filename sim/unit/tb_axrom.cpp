// Boot-ROM contract, run once per read-timing mode.
//
// SYNC_READ=0 is the simulator's combinational read; SYNC_READ=1 is the
// block-RAM template the boards select, which registers the read and inserts
// exactly one wait state.  Both must present the same ROM: the same words at
// the same addresses, an access error for anything out of range or misaligned,
// and an access error -- never a modification -- for a write.  The mode is
// argv[1] so one testbench stands behind both builds.
#include <cstdint>
#include <cstdio>
#include <cstdlib>

#include "Vaxrom.h"
#include "verilated.h"

static constexpr uint32_t kBase = 0x00001000u;
static constexpr uint32_t kBytes = 64;
static constexpr uint32_t kWord0 = 0xdeadbeefu;
static constexpr uint32_t kWord2 = 0x12345678u;
static constexpr uint32_t kLast = 0x00000000u;  // mem[15]

static int failures = 0;
static int sync_read = 0;

struct response {
  uint32_t data;
  bool error;
  int wait_cycles;
};

static void check(bool condition, const char* description) {
  if (!condition) {
    std::fprintf(stderr, "FAIL: [sync=%d] %s\n", sync_read, description);
    failures++;
  }
}

static void tick(Vaxrom* top) {
  top->clk = 0;
  top->eval();
  top->clk = 1;
  top->eval();
  top->clk = 0;
  top->eval();
}

// One aXbus transfer on the instruction port.  The master holds valid until
// ready, so the address stays stable across the registered read's extra cycle.
static response transact_i(Vaxrom* top, uint32_t address, uint32_t data,
                           uint8_t strobe) {
  top->i_addr = address;
  top->i_wdata = data;
  top->i_wstrb = strobe;
  top->i_valid = 1;
  top->eval();
  if (sync_read) {
    check(!top->i_ready, "registered read must not complete combinationally");
  } else {
    check(top->i_ready, "combinational read must complete in the same cycle");
  }

  int cycles = 0;
  while (!top->i_ready) {
    tick(top);
    check(++cycles <= 2, "I port response arrived too late");
    if (cycles > 2) break;
  }
  const response result = {top->i_rdata, bool(top->i_err), cycles};
  tick(top);  // Complete the ready/valid transfer at this rising edge.
  top->i_valid = 0;
  top->eval();
  return result;
}

static response transact_d(Vaxrom* top, uint32_t address, uint32_t data,
                           uint8_t strobe) {
  top->d_addr = address;
  top->d_wdata = data;
  top->d_wstrb = strobe;
  top->d_valid = 1;
  top->eval();

  int cycles = 0;
  while (!top->d_ready) {
    tick(top);
    check(++cycles <= 2, "D port response arrived too late");
    if (cycles > 2) break;
  }
  const response result = {top->d_rdata, bool(top->d_err), cycles};
  tick(top);
  top->d_valid = 0;
  top->eval();
  return result;
}

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  sync_read = (argc > 1) ? std::atoi(argv[1]) : 0;
  const int expected_wait = sync_read ? 1 : 0;

  Vaxrom top;
  top.clk = 0;
  top.rst = 1;
  top.i_valid = top.d_valid = 0;
  top.i_addr = top.d_addr = 0;
  top.i_wdata = top.d_wdata = 0;
  top.i_wstrb = top.d_wstrb = 0;
  tick(&top);
  top.rst = 0;
  top.eval();

  // Contents, on both ports, at both ends of the array.
  response r = transact_i(&top, kBase, 0, 0);
  check(!r.error && r.data == kWord0 && r.wait_cycles == expected_wait,
        "I port reads the first word");
  r = transact_d(&top, kBase + 8, 0, 0);
  check(!r.error && r.data == kWord2 && r.wait_cycles == expected_wait,
        "D port reads an interior word");
  r = transact_i(&top, kBase + kBytes - 4, 0, 0);
  check(!r.error && r.data == kLast, "I port reads the last word");

  // Both ports are read-only and independent, so a simultaneous pair completes
  // together in the mode's fixed latency.
  top.i_addr = kBase + 4;
  top.d_addr = kBase + 12;
  top.i_valid = top.d_valid = 1;
  top.eval();
  int cycles = 0;
  while (!(top.i_ready && top.d_ready)) {
    tick(&top);
    if (++cycles > 2) break;
  }
  check(cycles == expected_wait, "both ports complete in the same latency");
  check(top.i_rdata == 0x00c0ffeeu && top.d_rdata == 0x9abcdef0u,
        "simultaneous reads return their own words");
  tick(&top);
  top.i_valid = top.d_valid = 0;
  top.eval();

  // Faults.  A ROM write must report an access error and leave the word alone;
  // that is the property that makes the boot ROM immutable, and it has to hold
  // in the registered mode too, where the error is registered alongside ready.
  r = transact_i(&top, kBase + kBytes, 0, 0);
  check(r.error && r.wait_cycles == expected_wait,
        "out-of-range fetch reports an access error");
  r = transact_d(&top, kBase + 2, 0, 0);
  check(r.error && r.wait_cycles == expected_wait,
        "misaligned load reports an access error");
  r = transact_d(&top, kBase - 4, 0, 0);
  check(r.error, "an address below the ROM reports an access error");

  r = transact_d(&top, kBase, 0xffffffffu, 0xf);
  check(r.error && r.wait_cycles == expected_wait,
        "a word store to ROM reports an access error");
  r = transact_d(&top, kBase, 0x000000ffu, 0x1);
  check(r.error, "a byte store to ROM reports an access error");
  r = transact_i(&top, kBase, 0xffffffffu, 0xf);
  check(r.error, "a store on the instruction port reports an access error");

  r = transact_i(&top, kBase, 0, 0);
  check(!r.error && r.data == kWord0, "the rejected stores did not modify ROM");

  if (failures) {
    std::fprintf(stderr, "tb_axrom[sync=%d]: %d FAILURE(S)\n", sync_read,
                 failures);
    return 1;
  }
  std::printf("tb_axrom[sync=%d]: PASS\n", sync_read);
  return 0;
}
