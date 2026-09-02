// All-mode L3 RTL shadow, volatile activation, and manager rollback trial.
//
// The manager is deliberately outside role.morph. It loads only bounded
// 13-word genomes generated from checked search results, checks both oracle
// fixtures for every reviewed mode, and owns the last-known-good rollback.
#include <cstdint>
#include <cstddef>
#include <cstdio>
#include <vector>

#include "Vmorph_fabric.h"
#include "morph_l3_trial_config.h"
#include "verilated.h"

static constexpr uint32_t kBase = 0x40000000u;
static constexpr uint32_t kDoorbell = kBase + 0x08;
static constexpr uint32_t kStatus = kBase + 0x0c;
static constexpr uint32_t kNitems = kBase + 0x10;
static constexpr uint32_t kNconfig = kBase + 0x14;
static constexpr uint32_t kCount = kBase + 0x18;
static constexpr uint32_t kGeneration = kBase + 0x20;
static constexpr uint32_t kCfgBase = kBase + 0x100;
static constexpr uint32_t kDataBase = kBase + 0x1000;
static constexpr uint32_t kBusy = 1u << 0;
static constexpr uint32_t kDone = 1u << 1;
static constexpr uint32_t kRejected = 1u << 2;

static int failures = 0;

static void check(bool condition, const char* description) {
  if (!condition) {
    std::fprintf(stderr, "FAIL: %s\n", description);
    failures++;
  }
}

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
    dut_.clk = 1;
    dut_.eval();
  }

  void write(uint32_t addr, uint32_t value) {
    dut_.d_valid = 1;
    dut_.d_addr = addr;
    dut_.d_wdata = value;
    dut_.d_wstrb = 0xf;
    bool completed = false;
    for (int guard = 0; guard < 16; guard++) {
      dut_.clk = 0;
      dut_.eval();
      const bool ready = dut_.d_ready;
      dut_.clk = 1;
      dut_.eval();
      if (ready) {
        completed = true;
        break;
      }
    }
    check(completed, "write completed within the aXbus bound");
    dut_.d_valid = 0;
    dut_.d_wstrb = 0;
    tick();
  }

  uint32_t read(uint32_t addr) {
    dut_.d_valid = 1;
    dut_.d_addr = addr;
    dut_.d_wstrb = 0;
    uint32_t value = 0;
    bool completed = false;
    for (int guard = 0; guard < 16; guard++) {
      dut_.clk = 0;
      dut_.eval();
      const bool ready = dut_.d_ready;
      if (ready) value = dut_.d_rdata;
      dut_.clk = 1;
      dut_.eval();
      if (ready) {
        completed = true;
        break;
      }
    }
    check(completed, "read completed within the aXbus bound");
    dut_.d_valid = 0;
    tick();
    return value;
  }

  bool run_job() {
    write(kDoorbell, 1);
    for (int guard = 0; guard < 200000; guard++) {
      const uint32_t status = read(kStatus);
      if (status & kRejected) return false;
      if ((status & kDone) && !(status & kBusy)) {
        write(kStatus, kDone);
        return true;
      }
    }
    check(false, "job completed within the manager deadline");
    return false;
  }

  void load_genome(const std::vector<uint32_t>& words) {
    for (uint32_t index = 0; index < words.size(); index++)
      write(kCfgBase + 4 * index, words[index]);
    write(kNconfig, static_cast<uint32_t>(words.size()));
  }

  void poke(uint32_t index, uint32_t value) {
    write(kDataBase + 4 * index, value);
  }

  uint32_t peek(uint32_t index) { return read(kDataBase + 4 * index); }

 private:
  Vmorph_fabric dut_;
};

template <std::size_t N>
static std::vector<uint32_t> genome_words(const uint32_t (&words)[N]) {
  static_assert(N == 13, "morph genomes must contain exactly thirteen words");
  return std::vector<uint32_t>(words, words + N);
}

static bool same_words(const std::vector<uint32_t>& actual,
                       const std::vector<uint32_t>& expected) {
  return actual == expected;
}

static std::vector<uint32_t> scalar_oracle(
    const std::vector<int32_t>& input) {
  uint32_t acc = 7;
  for (int32_t value : input)
    acc = (acc + static_cast<uint32_t>(value)) * 3u + 1u;
  return {acc};
}

static std::vector<uint32_t> run_scalar(Fabric& fabric,
                                        const std::vector<uint32_t>& genome,
                                        const std::vector<int32_t>& input,
                                        const char* completion) {
  for (uint32_t index = 0; index < input.size(); index++)
    fabric.poke(index, static_cast<uint32_t>(input[index]));
  fabric.poke(200, 0xdeadbeefu);
  fabric.load_genome(genome);
  fabric.write(kNitems, static_cast<uint32_t>(input.size()));
  check(fabric.run_job(), completion);
  return {fabric.peek(200)};
}

static std::vector<uint32_t> simt_oracle(const std::vector<int32_t>& x,
                                         const std::vector<int32_t>& y) {
  std::vector<uint32_t> expected(x.size());
  for (uint32_t index = 0; index < x.size(); index++)
    expected[index] = 3u * static_cast<uint32_t>(x[index]) +
                      static_cast<uint32_t>(y[index]);
  return expected;
}

static std::vector<uint32_t> run_simt(Fabric& fabric,
                                      const std::vector<uint32_t>& genome,
                                      const std::vector<int32_t>& x,
                                      const std::vector<int32_t>& y,
                                      const char* completion) {
  for (uint32_t index = 0; index < x.size(); index++) {
    fabric.poke(index, static_cast<uint32_t>(x[index]));
    fabric.poke(64 + index, static_cast<uint32_t>(y[index]));
    fabric.poke(128 + index, 0xdeadbeefu);
  }
  fabric.load_genome(genome);
  fabric.write(kNitems, static_cast<uint32_t>(x.size()));
  check(fabric.run_job(), completion);
  std::vector<uint32_t> output(x.size());
  for (uint32_t index = 0; index < output.size(); index++)
    output[index] = fabric.peek(128 + index);
  return output;
}

static std::vector<uint32_t> gemm_oracle(const std::vector<int32_t>& a,
                                         const std::vector<int32_t>& b) {
  static constexpr int kM = 12;
  static constexpr int kK = 8;
  static constexpr int kN = 8;
  std::vector<uint32_t> expected(kM * kN);
  for (int row = 0; row < kM; row++) {
    for (int col = 0; col < kN; col++) {
      uint32_t acc = 0;
      for (int kk = 0; kk < kK; kk++)
        acc += static_cast<uint32_t>(a[row * kK + kk]) *
               static_cast<uint32_t>(b[kk * kN + col]);
      expected[row * kN + col] = acc;
    }
  }
  return expected;
}

static std::vector<uint32_t> run_gemm(Fabric& fabric,
                                      const std::vector<uint32_t>& genome,
                                      const std::vector<int32_t>& a,
                                      const std::vector<int32_t>& b,
                                      const char* completion) {
  for (uint32_t index = 0; index < a.size(); index++)
    fabric.poke(index, static_cast<uint32_t>(a[index]));
  for (uint32_t index = 0; index < b.size(); index++)
    fabric.poke(96 + index, static_cast<uint32_t>(b[index]));
  for (uint32_t index = 0; index < 96; index++)
    fabric.poke(160 + index, 0xdeadbeefu);
  fabric.load_genome(genome);
  fabric.write(kNitems, 96);
  check(fabric.run_job(), completion);
  std::vector<uint32_t> output(96);
  for (uint32_t index = 0; index < output.size(); index++)
    output[index] = fabric.peek(160 + index);
  return output;
}

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  Fabric fabric;
  const auto scalar_candidate = genome_words(ATOMIX_L3_SCALAR_CANDIDATE_GENOME);
  const auto scalar_rollback = genome_words(ATOMIX_L3_SCALAR_ROLLBACK_GENOME);
  const auto simt_candidate = genome_words(ATOMIX_L3_SIMT_CANDIDATE_GENOME);
  const auto simt_rollback = genome_words(ATOMIX_L3_SIMT_ROLLBACK_GENOME);
  const auto systolic_candidate =
      genome_words(ATOMIX_L3_SYSTOLIC_CANDIDATE_GENOME);
  const auto systolic_rollback =
      genome_words(ATOMIX_L3_SYSTOLIC_ROLLBACK_GENOME);
  const auto fault_genome = genome_words(ATOMIX_L3_FAULT_GENOME);

  std::vector<int32_t> scalar_primary(64), scalar_canary(64);
  for (int index = 0; index < 64; index++) {
    scalar_primary[index] = index - 8;
    scalar_canary[index] = (index % 11) - 5;
  }
  const auto scalar_primary_oracle = scalar_oracle(scalar_primary);
  const auto scalar_canary_oracle = scalar_oracle(scalar_canary);

  std::vector<int32_t> simt_x_primary(50), simt_y_primary(50);
  std::vector<int32_t> simt_x_canary(50), simt_y_canary(50);
  for (int index = 0; index < 50; index++) {
    simt_x_primary[index] = index + 1;
    simt_y_primary[index] = 100 + 2 * index;
    simt_x_canary[index] = (index % 17) - 8;
    simt_y_canary[index] = -101 + 7 * index;
  }
  const auto simt_primary_oracle = simt_oracle(simt_x_primary, simt_y_primary);
  const auto simt_canary_oracle = simt_oracle(simt_x_canary, simt_y_canary);

  std::vector<int32_t> a_primary(96), b_primary(64);
  std::vector<int32_t> a_canary(96), b_canary(64);
  for (int index = 0; index < 96; index++) {
    a_primary[index] = (index % 7) - 3;
    a_canary[index] = (index % 13) - 6;
  }
  for (int index = 0; index < 64; index++) {
    b_primary[index] = (index % 5) - 2;
    b_canary[index] = (index % 9) - 4;
  }
  const auto gemm_primary_oracle = gemm_oracle(a_primary, b_primary);
  const auto gemm_canary_oracle = gemm_oracle(a_canary, b_canary);

  // Every reviewed mode gets its known-good primary/canary and its searched
  // candidate primary/canary on this one resident fabric.
  check(same_words(run_scalar(fabric, scalar_rollback,
                              scalar_primary, "scalar rollback primary"),
                   scalar_primary_oracle),
        "scalar rollback passes primary");
  check(same_words(run_scalar(fabric, scalar_rollback,
                              scalar_canary, "scalar rollback canary"),
                   scalar_canary_oracle),
        "scalar rollback passes canary");
  check(same_words(run_scalar(fabric, scalar_candidate,
                              scalar_primary, "scalar candidate primary"),
                   scalar_primary_oracle),
        "scalar candidate passes primary RTL shadow");
  check(same_words(run_scalar(fabric, scalar_candidate,
                              scalar_canary, "scalar candidate canary"),
                   scalar_canary_oracle),
        "scalar candidate passes canary RTL shadow");

  check(same_words(run_simt(fabric, simt_rollback,
                            simt_x_primary, simt_y_primary,
                            "SIMT rollback primary"),
                   simt_primary_oracle),
        "SIMT rollback passes primary");
  check(same_words(run_simt(fabric, simt_rollback,
                            simt_x_canary, simt_y_canary,
                            "SIMT rollback canary"),
                   simt_canary_oracle),
        "SIMT rollback passes canary");
  check(same_words(run_simt(fabric, simt_candidate,
                            simt_x_primary, simt_y_primary,
                            "SIMT candidate primary"),
                   simt_primary_oracle),
        "SIMT candidate passes primary RTL shadow");
  check(same_words(run_simt(fabric, simt_candidate,
                            simt_x_canary, simt_y_canary,
                            "SIMT candidate canary"),
                   simt_canary_oracle),
        "SIMT candidate passes canary RTL shadow");

  check(same_words(run_gemm(fabric, systolic_rollback,
                            a_primary, b_primary, "GEMM rollback primary"),
                   gemm_primary_oracle),
        "systolic rollback passes primary");
  check(same_words(run_gemm(fabric, systolic_rollback,
                            a_canary, b_canary, "GEMM rollback canary"),
                   gemm_canary_oracle),
        "systolic rollback passes canary");
  check(same_words(run_gemm(fabric, systolic_candidate,
                            a_primary, b_primary, "GEMM candidate primary"),
                   gemm_primary_oracle),
        "systolic candidate passes primary RTL shadow");
  check(same_words(run_gemm(fabric, systolic_candidate,
                            a_canary, b_canary, "GEMM candidate canary"),
                   gemm_canary_oracle),
        "systolic candidate passes canary RTL shadow");

  // After shadow gates, the manager permits a scalar volatile trial.
  check(same_words(run_scalar(fabric, scalar_candidate,
                              scalar_primary, "scalar volatile trial"),
                   scalar_primary_oracle),
        "scalar candidate passes manager-approved volatile trial");

  // The semantic fault passes an all-zero narrow primary and fails the
  // searched nonzero primary used as its canary.
  const std::vector<int32_t> fault_primary(64, 0);
  check(same_words(run_scalar(fabric, fault_genome, fault_primary,
                              "fault narrow primary"),
                   scalar_oracle(fault_primary)),
        "fault injection passes deliberately narrow primary");
  check(!same_words(run_scalar(fabric, fault_genome, scalar_primary,
                               "fault canary"),
                    scalar_primary_oracle),
        "canary detects semantic fault before promotion");

  // The manager reloads the reviewed scalar genome and re-verifies both cases.
  check(same_words(run_scalar(fabric, scalar_rollback,
                              scalar_primary, "rollback recovery primary"),
                   scalar_primary_oracle),
        "manager rollback restores scalar primary");
  check(same_words(run_scalar(fabric, scalar_rollback,
                              scalar_canary, "rollback recovery canary"),
                   scalar_canary_oracle),
        "manager rollback restores scalar canary");

  check(fabric.read(kCount) == 17,
        "all seventeen jobs completed on one resident fabric");
  check(fabric.read(kGeneration) == 17,
        "each accepted genome advanced generation exactly once");

  if (failures) {
    std::fprintf(stderr, "tb_morph_l3: %d failure(s)\n", failures);
    return 1;
  }
  std::printf(
      "tb_morph_l3: PASS candidates=%u/%u/%u jobs=17 modes=3 cases=6 "
      "rollback=verified\n",
      ATOMIX_L3_SCALAR_CANDIDATE_DESC, ATOMIX_L3_SIMT_CANDIDATE_DESC,
      ATOMIX_L3_SYSTOLIC_CANDIDATE_DESC);
  return 0;
}
