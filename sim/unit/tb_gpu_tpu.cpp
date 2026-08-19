#include "Vaxrole.h"
#include "verilated.h"

#include <cstdint>
#include <cstdio>

static constexpr uint32_t kBase = 0x40000000u;
static constexpr uint32_t kRoleId = 0x0000u;
static constexpr uint32_t kDoorbell = 0x0008u;
static constexpr uint32_t kStatus = 0x000cu;
static constexpr uint32_t kArg0 = 0x0010u;
static constexpr uint32_t kArg1 = 0x0014u;
static constexpr uint32_t kCount = 0x0018u;
static constexpr uint32_t kProgram = 0x0100u;
static constexpr uint32_t kData = 0x1000u;
static constexpr uint32_t kTpuC = 0x2000u;
static constexpr uint32_t kCompositeId = 0xfff0u;
static constexpr uint32_t kCompositeVersion = 0xfff4u;
static constexpr uint32_t kCompositeCaps = 0xfff8u;
static constexpr uint32_t kSelect = 0xfffcu;

static constexpr uint32_t kGpuId = 0x47505543u;       // "GPUC"
static constexpr uint32_t kTpuId = 0x5450554cu;       // "TPUL"
static constexpr uint32_t kGpuTpuId = 0x47545043u;    // "GTPC"

static Vaxrole *top;
static int failures;

static void tick() {
  top->clk = 0;
  top->eval();
  top->clk = 1;
  top->eval();
}

struct BusResult {
  uint32_t data;
  bool error;
};

static BusResult bus(uint32_t off, uint32_t data, uint32_t strobe) {
  top->d_valid = 1;
  top->d_addr = kBase + off;
  top->d_wdata = data;
  top->d_wstrb = strobe;
  for (int guard = 0; guard < 64; ++guard) {
    top->clk = 0;
    top->eval();
    BusResult result{top->d_rdata, static_cast<bool>(top->d_err)};
    const bool ready = top->d_ready;
    top->clk = 1;
    top->eval();
    if (ready) {
      top->d_valid = 0;
      top->d_wstrb = 0;
      top->clk = 0;
      top->eval();
      return result;
    }
  }
  std::printf("FAIL: bus timeout at offset 0x%04x\n", off);
  ++failures;
  top->d_valid = 0;
  return {0, true};
}

static uint32_t rd(uint32_t off) {
  BusResult result = bus(off, 0, 0);
  if (result.error) {
    std::printf("FAIL: unexpected read error at offset 0x%04x\n", off);
    ++failures;
  }
  return result.data;
}

static void wr(uint32_t off, uint32_t value) {
  if (bus(off, value, 0xf).error) {
    std::printf("FAIL: unexpected write error at offset 0x%04x\n", off);
    ++failures;
  }
}

static void expect(uint32_t actual, uint32_t wanted, const char *what) {
  if (actual != wanted) {
    std::printf("FAIL: %s: got 0x%08x, wanted 0x%08x\n",
                what, actual, wanted);
    ++failures;
  }
}

static void wait_done(const char *what) {
  for (int guard = 0; guard < 10000; ++guard) {
    if (rd(kStatus) & 2u) return;
  }
  std::printf("FAIL: timeout waiting for %s\n", what);
  ++failures;
}

static uint32_t gpu_insn(uint32_t op, uint32_t rd_index,
                         uint32_t ra, uint32_t rb, int32_t imm) {
  return (op << 26) | (rd_index << 23) | (ra << 20) | (rb << 17) |
         (static_cast<uint32_t>(imm) & 0x1ffffu);
}

static uint32_t pack4(uint8_t a, uint8_t b, uint8_t c, uint8_t d) {
  return static_cast<uint32_t>(a) |
         (static_cast<uint32_t>(b) << 8) |
         (static_cast<uint32_t>(c) << 16) |
         (static_cast<uint32_t>(d) << 24);
}

int main(int argc, char **argv) {
  Verilated::commandArgs(argc, argv);
  top = new Vaxrole;
  top->clk = 0;
  top->rst = 1;
  top->i_valid = 0;
  top->i_addr = 0;
  top->i_wdata = 0;
  top->i_wstrb = 0;
  top->d_valid = 0;
  top->d_addr = 0;
  top->d_wdata = 0;
  top->d_wstrb = 0;
  tick();
  tick();
  top->rst = 0;
  tick();

  expect(rd(kCompositeId), kGpuTpuId, "composite identity");
  expect(rd(kCompositeVersion), 1, "composite version");
  expect(rd(kCompositeCaps), 3, "composite capabilities");
  expect(rd(kSelect), 0, "reset personality");
  expect(rd(kRoleId), kGpuId, "GPU identity after reset");

  // One-lane GPU kernel: data[tid] = tid + 5 for four threads.
  wr(kProgram + 0, gpu_insn(1, 0, 0, 0, 0));   // TID r0
  wr(kProgram + 4, gpu_insn(17, 1, 0, 0, 5));  // ADDI r1,r0,5
  wr(kProgram + 8, gpu_insn(5, 0, 0, 1, 0));   // STX [r0],r1
  wr(kProgram + 12, gpu_insn(0, 0, 0, 0, 0));  // HALT
  wr(kArg0, 4);
  wr(kArg1, 4);
  wr(kDoorbell, 1);
  if (!bus(kSelect, 1, 0xf).error) {
    std::printf("FAIL: selector accepted a switch while GPU was executing\n");
    ++failures;
  }
  if (!top->reject_event) {
    std::printf("FAIL: busy switch did not pulse reject_event\n");
    ++failures;
  }
  tick();
  wait_done("GPU job");
  expect(rd(kCount), 1, "GPU job count");
  for (uint32_t index = 0; index < 4; ++index)
    expect(rd(kData + 4 * index), index + 5, "GPU result");

  // DONE must be acknowledged before the selector can hide this engine.
  if (!bus(kSelect, 1, 0xf).error) {
    std::printf("FAIL: selector accepted a switch with GPU DONE pending\n");
    ++failures;
  }
  if (!top->reject_event) {
    std::printf("FAIL: refused switch did not pulse reject_event\n");
    ++failures;
  }
  tick();
  wr(kStatus, 2);
  expect(top->irq, 0, "GPU IRQ after DONE clear");
  wr(kSelect, 1);
  expect(rd(kRoleId), kTpuId, "TPU identity after switch");

  // One-row identity GEMM. W is I8 and A is [1..8], so C must match A.
  for (uint32_t row = 0; row < 8; ++row) {
    uint8_t lo[4] = {0, 0, 0, 0};
    uint8_t hi[4] = {0, 0, 0, 0};
    if (row < 4) lo[row] = 1;
    else hi[row - 4] = 1;
    wr(kProgram + 8 * row, pack4(lo[0], lo[1], lo[2], lo[3]));
    wr(kProgram + 8 * row + 4, pack4(hi[0], hi[1], hi[2], hi[3]));
  }
  wr(kData + 0, pack4(1, 2, 3, 4));
  wr(kData + 4, pack4(5, 6, 7, 8));
  wr(kArg0, 0);  // CTRL
  wr(kArg1, 1);  // M
  wr(kDoorbell, 1);
  wait_done("TPU job");
  expect(rd(kCount), 1, "TPU job count");
  for (uint32_t col = 0; col < 8; ++col)
    expect(rd(kTpuC + 4 * col), col + 1, "TPU result");

  if (!bus(kSelect, 0, 0xf).error) {
    std::printf("FAIL: selector accepted a switch with TPU DONE pending\n");
    ++failures;
  }
  wr(kStatus, 2);
  wr(kSelect, 0);
  expect(rd(kRoleId), kGpuId, "GPU identity after return switch");
  expect(rd(kCount), 1, "GPU state retained across TPU job");
  expect(rd(kData), 5, "GPU memory retained across TPU job");

  if (!bus(kSelect, 2, 0xf).error) {
    std::printf("FAIL: selector accepted invalid personality 2\n");
    ++failures;
  }
  if (!bus(kCompositeCaps, 0, 0xf).error) {
    std::printf("FAIL: composite read-only metadata accepted a write\n");
    ++failures;
  }

  delete top;
  if (failures) {
    std::printf("tb_gpu_tpu: FAIL (%d checks)\n", failures);
    return 1;
  }
  std::printf("tb_gpu_tpu: PASS (GPU and TPU workloads, guarded switching, retained state)\n");
  return 0;
}
