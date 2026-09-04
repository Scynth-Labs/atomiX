// Unit test for components/core/pipeline5/immdec.sv: known encodings from our directed tests
// plus random cross-check against the same extractors aXsim uses.
#include <cstdio>
#include <cstdint>
#include "Vimmdec.h"
#include "verilated.h"

enum { IMM_I, IMM_S, IMM_B, IMM_U, IMM_J };

static int failures = 0;

static uint32_t model(uint32_t i, int sel) {
  switch (sel) {
    case IMM_I: return (uint32_t)((int32_t)i >> 20);
    case IMM_S:
      return (uint32_t)((int32_t)(i & 0xFE000000) >> 20) | ((i >> 7) & 0x1F);
    case IMM_B:
      return ((i & 0x80000000u) ? 0xfffff000u : 0u) | ((i & 0x80) << 4) |
             ((i >> 20) & 0x7E0) | ((i >> 7) & 0x1E);
    case IMM_U: return i & 0xFFFFF000;
    case IMM_J:
      return ((i & 0x80000000u) ? 0xfff00000u : 0u) | (i & 0xFF000) |
             ((i >> 9) & 0x800) | ((i >> 20) & 0x7FE);
  }
  return 0;
}

static uint32_t xorshift(uint32_t& s) {
  s ^= s << 13; s ^= s >> 17; s ^= s << 5;
  return s;
}

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  Vimmdec* dut = new Vimmdec;

  auto run = [&](uint32_t insn, int sel, int32_t want_directed = 0,
                 bool directed = false) {
    dut->insn = insn; dut->sel = sel; dut->eval();
    const uint32_t want = directed ? (uint32_t)want_directed : model(insn, sel);
    if (dut->imm != want) {
      printf("FAIL insn=%08x sel=%d: got %08x want %08x\n", insn, sel,
             dut->imm, want);
      failures++;
    }
  };

  // known encodings from tests/directed (hand-verified immediates)
  run(0x04F00593, IMM_I, 79, true);       // addi a1, x0, 'O'
  run(0xFFF68693, IMM_I, -1, true);       // addi a3, a3, -1
  run(0xFE069CE3, IMM_B, -8, true);       // bne a3, x0, -8
  run(0xFFC392E3, IMM_B, -28, true);      // bne t2, t3, -28
  run(0x02E61063, IMM_B, 32, true);       // bne a2, a4, +32
  run(0x00B50023, IMM_S, 0, true);        // sb a1, 0(a0)
  run(0x10000537, IMM_U, 0x10000000, true);  // lui a0, 0x10000
  run(0x0000006F, IMM_J, 0, true);        // jal x0, 0

  // random cross-check against the aXsim extractors
  uint32_t seed = 0x1F2E3D4C;
  for (int i = 0; i < 100000; i++) {
    const uint32_t insn = xorshift(seed);
    run(insn, (int)(xorshift(seed) % 5));
  }

  delete dut;
  if (failures) { printf("tb_immdec: %d FAILURES\n", failures); return 1; }
  printf("tb_immdec: PASS\n");
  return 0;
}
