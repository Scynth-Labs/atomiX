#include <cstdio>
#include <cstdint>

#include "Vaxsdram_test_top.h"
#include "verilated.h"

namespace {
void half_cycle(Vaxsdram_test_top* top, int level) {
  top->clk = level;
  top->eval();
}

bool wait_init(Vaxsdram_test_top* top) {
  for (unsigned cycle = 0; cycle < 100; ++cycle) {
    half_cycle(top, 0);
    half_cycle(top, 1);
    if (top->init_done) return true;
  }
  return false;
}

bool access(Vaxsdram_test_top* top, bool instruction, uint32_t addr,
            uint32_t wdata, uint8_t wstrb, uint32_t* rdata, bool* err) {
  if (instruction) {
    top->i_valid = 1;
    top->i_addr = addr;
    top->i_wdata = wdata;
    top->i_wstrb = wstrb;
  } else {
    top->d_valid = 1;
    top->d_addr = addr;
    top->d_wdata = wdata;
    top->d_wstrb = wstrb;
  }

  for (unsigned cycle = 0; cycle < 200; ++cycle) {
    half_cycle(top, 0);
    const bool ready = instruction ? top->i_ready : top->d_ready;
    if (ready) {
      *rdata = instruction ? top->i_rdata : top->d_rdata;
      *err = instruction ? top->i_err : top->d_err;
      half_cycle(top, 1);  // complete the aXbus handshake
      if (instruction) top->i_valid = 0;
      else top->d_valid = 0;
      half_cycle(top, 0);
      return true;
    }
    half_cycle(top, 1);
  }
  if (instruction) top->i_valid = 0;
  else top->d_valid = 0;
  return false;
}
}

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  auto* top = new Vaxsdram_test_top;
  top->clk = 0;
  top->rst = 1;
  top->i_valid = 0;
  top->d_valid = 0;
  top->i_addr = top->d_addr = 0;
  top->i_wdata = top->d_wdata = 0;
  top->i_wstrb = top->d_wstrb = 0;
  half_cycle(top, 0);
  half_cycle(top, 1);
  top->rst = 0;

  uint32_t value = 0;
  bool err = false;
  bool ok = wait_init(top);
  ok &= access(top, false, 0x80000000u, 0x11223344u, 0xf, &value, &err) && !err;
  ok &= access(top, false, 0x80000000u, 0, 0, &value, &err) && !err && value == 0x11223344u;
  ok &= access(top, false, 0x80000000u, 0xaabbccddu, 0x5, &value, &err) && !err;
  ok &= access(top, true, 0x80000000u, 0, 0, &value, &err) && !err && value == 0x11bb33ddu;
  // Exercise a different bank and the instruction-side arbiter path.
  ok &= access(top, true, 0x80800000u, 0xdeadbeefu, 0xf, &value, &err) && !err;
  ok &= access(top, false, 0x80800000u, 0, 0, &value, &err) && !err && value == 0xdeadbeefu;
  ok &= access(top, false, 0x80000002u, 0, 0, &value, &err) && err;

  // The pin counters make the traffic itself checkable, not just the values
  // that came back: a controller that satisfied every readback without ever
  // driving a command -- out of a latch, a cache, or a rearranged model --
  // passes each comparison above and fails here.
  std::fprintf(stderr,
               "tb_axsdram: pins activate=%u read=%u write=%u precharge=%u "
               "refresh=%u\n",
               top->cmd_activate, top->cmd_read, top->cmd_write,
               top->cmd_precharge, top->cmd_refresh);
  ok &= top->cmd_activate > 0 && top->cmd_read > 0 && top->cmd_write > 0;

  if (!ok) std::fprintf(stderr, "tb_axsdram: FAIL value=%08x err=%d\n", value, err);
  delete top;
  if (!ok) return 1;
  std::puts("tb_axsdram: PASS");
  return 0;
}
