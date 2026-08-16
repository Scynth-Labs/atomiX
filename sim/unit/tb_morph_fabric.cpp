// Directed test for the R2 morph fabric.
//
// The research claim under test is narrow and falsifiable: one resident
// datapath, configured only by its bounded genome, produces exactly the
// reference results for a scalar recurrence, a SIMT SAXPY, and a systolic
// GEMM -- and a descriptor that would leave the role window is refused before
// it can run.  Every personality here writes the same thirteen configuration
// words and rings the same doorbell; nothing about the fabric changes between
// them, which is the whole point of the comparison against the hard roles.
#include <cstdint>
#include <cstdio>
#include <vector>

#include "Vmorph_fabric.h"
#include "verilated.h"

static constexpr uint32_t kBase = 0x40000000u;
static constexpr uint32_t kRoleId = kBase + 0x00;
static constexpr uint32_t kDoorbell = kBase + 0x08;
static constexpr uint32_t kStatus = kBase + 0x0c;
static constexpr uint32_t kNitems = kBase + 0x10;
static constexpr uint32_t kNconfig = kBase + 0x14;
static constexpr uint32_t kCount = kBase + 0x18;
static constexpr uint32_t kCaps = kBase + 0x1c;
static constexpr uint32_t kGeneration = kBase + 0x20;
static constexpr uint32_t kRejects = kBase + 0x24;
static constexpr uint32_t kCfgBase = kBase + 0x100;
static constexpr uint32_t kDataBase = kBase + 0x1000;

static constexpr uint32_t kBusy = 1u << 0;
static constexpr uint32_t kDone = 1u << 1;
static constexpr uint32_t kRejected = 1u << 2;

static constexpr int kPes = 4;
static constexpr int kDataWords = 256;
static constexpr int kCfgWords = 13;

// Source-mux and accumulator-rule encodings from morph_fabric.sv.
enum : uint32_t {
  SRC_A = 0, SRC_B = 1, SRC_ACC = 2, SRC_IMM0 = 3, SRC_IMM1 = 4,
  SRC_ZERO = 5, SRC_ONE = 6, SRC_CHAIN = 7,
};
enum : uint32_t { ACC_HOLD = 0, ACC_LOAD = 1 };

enum : uint32_t { MODE_SCALAR = 0, MODE_SIMT = 1, MODE_SYSTOLIC = 2 };

static int failures = 0;
// Concatenated little-endian outputs of the three personalities.  Hashing this
// is what lets an R2 comparison record carry a real correctness digest instead
// of a claim.
static std::vector<uint32_t> digest_words;

static void check(bool condition, const char* description) {
  if (!condition) {
    std::fprintf(stderr, "FAIL: %s\n", description);
    failures++;
  }
}

static uint32_t pe_desc(uint32_t srca, uint32_t srcb, uint32_t srcc,
                        uint32_t srcd, uint32_t accrule) {
  return (accrule << 12) | (srcd << 9) | (srcc << 6) | (srcb << 3) | srca;
}

namespace {

class Fabric {
 public:
  Fabric() {
    dut_.rst = 1;
    dut_.i_valid = 0;
    dut_.d_valid = 0;
    dut_.i_wstrb = 0;
    dut_.d_wstrb = 0;
    for (int i = 0; i < 4; i++) tick();
    dut_.rst = 0;
    tick();
  }

  void tick() {
    dut_.clk = 0;
    dut_.eval();
    sample_events();
    dut_.clk = 1;
    dut_.eval();
  }

  // The shell counts what this line reports, so the bench counts it the same
  // way the shell does: once per clock the pulse is high.  Sampled with the
  // clock low and the combinational outputs settled, which is the value the
  // rising edge will see.
  uint64_t reject_pulses() const { return reject_pulses_; }

  // Both accessors follow the real aXbus handshake: hold the request until the
  // slave asserts ready, sample on that cycle, then release.  Driving fixed
  // cycle counts instead would hide exactly the multi-cycle-transaction bugs
  // this bench exists to catch.
  void write(uint32_t addr, uint32_t value) {
    dut_.d_valid = 1;
    dut_.d_addr = addr;
    dut_.d_wdata = value;
    dut_.d_wstrb = 0xf;
    for (int guard = 0; guard < 16; guard++) {
      dut_.clk = 0;
      dut_.eval();
      bool ready = dut_.d_ready;
      sample_events();
      dut_.clk = 1;
      dut_.eval();
      if (ready) break;
    }
    dut_.d_valid = 0;
    dut_.d_wstrb = 0;
    tick();
  }

  uint32_t read(uint32_t addr) {
    dut_.d_valid = 1;
    dut_.d_addr = addr;
    dut_.d_wstrb = 0;
    uint32_t value = 0;
    for (int guard = 0; guard < 16; guard++) {
      dut_.clk = 0;
      dut_.eval();
      bool ready = dut_.d_ready;
      if (ready) value = dut_.d_rdata;
      sample_events();
      dut_.clk = 1;
      dut_.eval();
      if (ready) break;
    }
    dut_.d_valid = 0;
    tick();
    return value;
  }

  // Rings the doorbell and waits for DONE, bounded so a fabric that never
  // finishes fails the test instead of hanging it.
  bool run_job(int budget = 200000) {
    write(kDoorbell, 1);
    for (int i = 0; i < budget; i++) {
      uint32_t status = read(kStatus);
      if (status & kRejected) return false;
      if ((status & kDone) && !(status & kBusy)) {
        write(kStatus, kDone);
        return true;
      }
    }
    check(false, "job did not complete within its cycle budget");
    return false;
  }

  int cycles_for_job(int budget = 200000) {
    int cycles = 0;
    write(kDoorbell, 1);
    for (int i = 0; i < budget; i++) {
      uint32_t status = read(kStatus);
      cycles += 2;
      if (status & kRejected) return -1;
      if ((status & kDone) && !(status & kBusy)) {
        write(kStatus, kDone);
        return cycles;
      }
    }
    return -1;
  }

  void load_genome(const std::vector<uint32_t>& genome) {
    for (size_t i = 0; i < genome.size(); i++)
      write(kCfgBase + 4 * static_cast<uint32_t>(i), genome[i]);
    write(kNconfig, static_cast<uint32_t>(genome.size()));
  }

  void poke(int index, uint32_t value) {
    write(kDataBase + 4 * static_cast<uint32_t>(index), value);
  }
  uint32_t peek(int index) {
    return read(kDataBase + 4 * static_cast<uint32_t>(index));
  }

  Vmorph_fabric dut_;

 private:
  void sample_events() { reject_pulses_ += dut_.reject_event ? 1 : 0; }

  uint64_t reject_pulses_ = 0;
};

// Builds the thirteen-word genome.  Every personality below differs only in
// these values.
std::vector<uint32_t> genome(uint32_t mode, uint32_t m, uint32_t n, uint32_t k,
                             uint32_t a_base, uint32_t a_row, uint32_t a_k,
                             uint32_t a_col, uint32_t b_base, uint32_t b_col,
                             uint32_t b_k, uint32_t c_base, uint32_t c_row,
                             uint32_t imm0, uint32_t imm1, uint32_t pe,
                             uint32_t acc_init) {
  return {
      mode,
      (n << 16) | m,
      k,
      (a_row << 16) | a_base,
      (a_col << 16) | a_k,
      (b_col << 16) | b_base,
      b_k,
      (c_row << 16) | c_base,
      imm0,
      imm1,
      (pe << 14) | pe,
      (pe << 14) | pe,
      acc_init,
  };
}

}  // namespace

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  Fabric fabric;

  check(fabric.read(kRoleId) == 0x4D525048u, "ROLE_ID reads \"MRPH\"");
  uint32_t caps = fabric.read(kCaps);
  check((caps >> 24) == kPes, "CAPS reports the PE count");
  check(((caps >> 16) & 0xff) == kCfgWords, "CAPS reports the genome size");
  check((caps & 0xffff) == kDataWords, "CAPS reports the buffer size");
  check(fabric.read(kGeneration) == 0, "generation starts at zero");

  // ---------------------------------------------------------------------
  // Personality 1: scalar recurrence acc = (acc + x[i]) * 3 + 1, seed 7.
  // ---------------------------------------------------------------------
  {
    const int kItems = 64;
    std::vector<int32_t> x(kItems);
    for (int i = 0; i < kItems; i++) x[i] = i - 8;
    for (int i = 0; i < kItems; i++)
      fabric.poke(i, static_cast<uint32_t>(x[i]));

    uint32_t pe = pe_desc(SRC_ACC, SRC_A, SRC_IMM0, SRC_IMM1, ACC_LOAD);
    fabric.load_genome(genome(MODE_SCALAR, /*m=*/1, /*n=*/1, /*k=*/kItems,
                              /*a_base=*/0, /*a_row=*/0, /*a_k=*/1, /*a_col=*/0,
                              /*b_base=*/0, /*b_col=*/0, /*b_k=*/0,
                              /*c_base=*/200, /*c_row=*/0,
                              /*imm0=*/3, /*imm1=*/1, pe, /*acc_init=*/7));
    fabric.write(kNitems, kItems);
    check(fabric.run_job(), "scalar personality completed");

    uint32_t acc = 7;
    for (int i = 0; i < kItems; i++)
      acc = (acc + static_cast<uint32_t>(x[i])) * 3u + 1u;
    check(fabric.peek(200) == acc, "scalar recurrence matches the reference");
    digest_words.push_back(fabric.peek(200));
    check(fabric.read(kGeneration) == 1, "generation advanced once");
  }

  // ---------------------------------------------------------------------
  // Personality 2: SIMT SAXPY y[i] = 3 * x[i] + y[i] over 50 elements.
  // ---------------------------------------------------------------------
  {
    const int kItems = 50;
    std::vector<int32_t> x(kItems), y(kItems);
    for (int i = 0; i < kItems; i++) {
      x[i] = i + 1;
      y[i] = 100 + 2 * i;
      fabric.poke(i, static_cast<uint32_t>(x[i]));
      fabric.poke(64 + i, static_cast<uint32_t>(y[i]));
    }

    uint32_t pe = pe_desc(SRC_A, SRC_ZERO, SRC_IMM0, SRC_B, ACC_LOAD);
    fabric.load_genome(genome(MODE_SIMT, /*m=*/1, /*n=*/kItems, /*k=*/1,
                              /*a_base=*/0, /*a_row=*/0, /*a_k=*/0, /*a_col=*/1,
                              /*b_base=*/64, /*b_col=*/1, /*b_k=*/0,
                              /*c_base=*/128, /*c_row=*/0,
                              /*imm0=*/3, /*imm1=*/0, pe, /*acc_init=*/0));
    fabric.write(kNitems, kItems);
    check(fabric.run_job(), "SIMT personality completed");

    bool all_ok = true;
    for (int i = 0; i < kItems; i++) {
      uint32_t want = 3u * static_cast<uint32_t>(x[i]) +
                      static_cast<uint32_t>(y[i]);
      if (fabric.peek(128 + i) != want) all_ok = false;
    }
    check(all_ok, "SIMT SAXPY matches the reference for every element");
    for (int i = 0; i < kItems; i++) digest_words.push_back(fabric.peek(128 + i));
    // A tail that is not a multiple of the lane count must not write past the
    // logical element count.
    check(fabric.peek(128 + kItems) == 0, "SIMT tail lane wrote nothing");
    check(fabric.read(kGeneration) == 2, "generation advanced again");
  }

  // ---------------------------------------------------------------------
  // Personality 3: systolic GEMM C[12x8] = A[12x8] * B[8x8], i32 accumulate.
  // ---------------------------------------------------------------------
  {
    const int kM = 12, kK = 8, kN = 8;
    const int kABase = 0, kBBase = 96, kCBase = 160;
    std::vector<int32_t> a(kM * kK), b(kK * kN);
    for (int i = 0; i < kM * kK; i++) {
      a[i] = (i % 7) - 3;
      fabric.poke(kABase + i, static_cast<uint32_t>(a[i]));
    }
    for (int i = 0; i < kK * kN; i++) {
      b[i] = (i % 5) - 2;
      fabric.poke(kBBase + i, static_cast<uint32_t>(b[i]));
    }
    for (int i = 0; i < kM * kN; i++) fabric.poke(kCBase + i, 0);

    uint32_t pe = pe_desc(SRC_A, SRC_ZERO, SRC_B, SRC_ACC, ACC_LOAD);
    fabric.load_genome(genome(MODE_SYSTOLIC, kM, kN, kK,
                              /*a_base=*/kABase, /*a_row=*/kK, /*a_k=*/1,
                              /*a_col=*/0,
                              /*b_base=*/kBBase, /*b_col=*/1, /*b_k=*/kN,
                              /*c_base=*/kCBase, /*c_row=*/kN,
                              /*imm0=*/0, /*imm1=*/0, pe, /*acc_init=*/0));
    fabric.write(kNitems, kM * kN);
    check(fabric.run_job(), "systolic personality completed");

    bool all_ok = true;
    for (int row = 0; row < kM; row++) {
      for (int col = 0; col < kN; col++) {
        uint32_t want = 0;
        for (int kk = 0; kk < kK; kk++)
          want += static_cast<uint32_t>(a[row * kK + kk]) *
                  static_cast<uint32_t>(b[kk * kN + col]);
        if (fabric.peek(kCBase + row * kN + col) != want) all_ok = false;
      }
    }
    check(all_ok, "systolic GEMM matches the reference for every element");
    for (int i = 0; i < kM * kN; i++)
      digest_words.push_back(fabric.peek(kCBase + i));
    check(fabric.read(kGeneration) == 3, "generation advanced a third time");
    check(fabric.read(kCount) == 3, "three jobs completed on one fabric");
  }

  // ---------------------------------------------------------------------
  // Descriptor isolation.  Each of these must be refused before BUSY rises,
  // must not advance the generation, and must leave the previous result and
  // the shell-visible state untouched.
  // ---------------------------------------------------------------------
  {
    uint32_t generation_before = fabric.read(kGeneration);
    uint32_t count_before = fabric.read(kCount);
    uint32_t canary = fabric.peek(160);
    uint32_t rejects_before = fabric.read(kRejects);
    // The shell sees rejections only through this line, so it has to agree
    // with REJECTS exactly: the three accepted jobs above must have produced
    // no pulse at all, or the shell's counter reports refusals that never
    // happened and every trial spanning a normal job becomes ineligible.
    check(fabric.reject_pulses() == 0,
          "an accepted descriptor never pulses the shell's reject line");
    const uint64_t pulses_before = fabric.reject_pulses();
    uint32_t pe = pe_desc(SRC_A, SRC_ZERO, SRC_IMM0, SRC_B, ACC_LOAD);

    struct Case {
      const char* name;
      std::vector<uint32_t> genome;
      uint32_t nconfig_override;  // 0 means "use the genome length"
    };
    std::vector<Case> cases = {
        {"output stream leaves the role window",
         genome(MODE_SIMT, 1, 64, 1, 0, 0, 0, 1, 64, 1, 0,
                /*c_base=*/250, 0, 3, 0, pe, 0), 0},
        {"input stream leaves the role window",
         genome(MODE_SIMT, 1, 64, 1, /*a_base=*/250, 0, 0, 1, 64, 1, 0,
                128, 0, 3, 0, pe, 0), 0},
        {"reduction stride leaves the role window",
         genome(MODE_SYSTOLIC, 1, 1, 64, 0, 0, /*a_k=*/8, 0, 0, 0, 0,
                200, 0, 0, 0, pe, 0), 0},
        {"unknown personality mode",
         genome(/*mode=*/9, 1, 8, 1, 0, 0, 0, 1, 64, 1, 0, 128, 0, 3, 0, pe, 0),
         0},
        {"zero-sized job",
         genome(MODE_SIMT, 1, /*n=*/0, 1, 0, 0, 0, 1, 64, 1, 0, 128, 0, 3, 0,
                pe, 0), 0},
        {"short genome",
         genome(MODE_SIMT, 1, 8, 1, 0, 0, 0, 1, 64, 1, 0, 128, 0, 3, 0, pe, 0),
         kCfgWords - 1},
    };

    for (const auto& item : cases) {
      fabric.load_genome(item.genome);
      if (item.nconfig_override) fabric.write(kNconfig, item.nconfig_override);
      fabric.write(kNitems, 8);
      fabric.write(kDoorbell, 1);
      uint32_t status = fabric.read(kStatus);
      check((status & kRejected) != 0, item.name);
      check((status & kBusy) == 0, "rejected descriptor never asserted BUSY");
      fabric.write(kStatus, kRejected);
    }

    check(fabric.read(kGeneration) == generation_before,
          "rejected descriptors never advanced the configuration generation");
    check(fabric.read(kCount) == count_before,
          "rejected descriptors never completed a job");
    check(fabric.peek(160) == canary,
          "rejected descriptors never modified the previous result");
    check(fabric.read(kRejects) == rejects_before + cases.size(),
          "every rejection was counted");
    check(fabric.reject_pulses() == pulses_before + cases.size(),
          "every rejection raised exactly one pulse on the shell's line");
  }

  // A rejected descriptor must not poison the fabric: the last good genome
  // still runs and still produces the right answer.
  {
    const int kItems = 16;
    for (int i = 0; i < kItems; i++) {
      fabric.poke(i, static_cast<uint32_t>(i + 1));
      fabric.poke(64 + i, static_cast<uint32_t>(10 * i));
    }
    uint32_t pe = pe_desc(SRC_A, SRC_ZERO, SRC_IMM0, SRC_B, ACC_LOAD);
    fabric.load_genome(genome(MODE_SIMT, 1, kItems, 1, 0, 0, 0, 1, 64, 1, 0,
                              128, 0, 3, 0, pe, 0));
    fabric.write(kNitems, kItems);
    check(fabric.run_job(), "fabric still runs after a rejected descriptor");
    bool all_ok = true;
    for (int i = 0; i < kItems; i++)
      if (fabric.peek(128 + i) != static_cast<uint32_t>(3 * (i + 1) + 10 * i))
        all_ok = false;
    check(all_ok, "post-rejection results are still exact");
  }

  if (FILE* out = std::fopen("morph_outputs.bin", "wb")) {
    for (uint32_t word : digest_words) {
      unsigned char le[4] = {static_cast<unsigned char>(word & 0xff),
                             static_cast<unsigned char>((word >> 8) & 0xff),
                             static_cast<unsigned char>((word >> 16) & 0xff),
                             static_cast<unsigned char>((word >> 24) & 0xff)};
      std::fwrite(le, 1, 4, out);
    }
    std::fclose(out);
  }

  if (failures) {
    std::printf("tb_morph_fabric: FAIL (%d checks)\n", failures);
    return 1;
  }
  std::printf("tb_morph_fabric: PASS "
              "(scalar, SIMT, systolic on one fabric; descriptors confined)\n");
  return 0;
}
