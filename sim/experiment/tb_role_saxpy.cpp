// RTL leg of the native/RTL saxpy comparison: role.gpu-compute driven through
// its own role window, with nothing else in the machine.
//
// This is an execution adapter's harness, not a unit test.  It stages one
// workload case, rings the doorbell, and reports the model cycles and the
// exact outputs so an independent oracle can judge them.  It deliberately
// makes no claim about wall time: what it measures is a property of the
// design, and what it costs to simulate is a property of the tool.
//
// The two cycle counts are the boundary the comparison contract requires.
// execute_cycles covers doorbell to observed DONE.  total_cycles adds the
// program upload, the input staging, and the checked readback -- the costs a
// real driver pays and a doorbell-only number hides.
//
// The kernel is an input, not a constant in this file.  --program names a
// word-per-line hex image, exactly as the SoC harness takes --ram-image: the
// Verilated model is the machine's build identity and the program is the
// payload identity, and a comparison that cannot tell those apart cannot say
// which one it changed.
//
// The harness and the kernel share one buffer convention, which is part of
// the workload's calling contract rather than something either side may
// choose alone: x occupies global words [0, items), y occupies
// [items, 2*items), and the kernel stores results into [2*items, 3*items).
//
// Lane count is a build-time define the adapter passes to Verilator, so this
// harness does not report it: the number is the adapter's own build input and
// belongs to the target's build identity, not to a value invented here.

#include "Vaxrole.h"
#include "verilated.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace {

constexpr uint32_t kBase = 0x40000000u;
constexpr uint32_t kDoorbell = 0x0008u;
constexpr uint32_t kStatus = 0x000cu;
constexpr uint32_t kNThreads = 0x0010u;
constexpr uint32_t kNInsn = 0x0014u;
constexpr uint32_t kProgram = 0x0100u;
constexpr uint32_t kData = 0x1000u;
constexpr uint32_t kRoleId = 0x0000u;
constexpr uint32_t kGpuComputeId = 0x47505543u;  // "GPUC"

// The engine's program memory is PROG_WORDS deep; a longer kernel would be
// silently clamped, so it is refused here instead.
constexpr int kProgramWords = 64;

enum ExitCode {
  kOk = 0,
  kUsage = 2,
  kInput = 3,
  kUnsupported = 4,
  kTimeout = 5,
};

Vaxrole *top = nullptr;
uint64_t cycles = 0;

void tick() {
  top->clk = 0;
  top->eval();
  top->clk = 1;
  top->eval();
  ++cycles;
}

struct BusResult {
  uint32_t data;
  bool error;
  bool timed_out;
};

BusResult bus(uint32_t offset, uint32_t data, uint32_t strobe) {
  top->d_valid = 1;
  top->d_addr = kBase + offset;
  top->d_wdata = data;
  top->d_wstrb = strobe;
  for (int guard = 0; guard < 64; ++guard) {
    top->clk = 0;
    top->eval();
    BusResult result{top->d_rdata, static_cast<bool>(top->d_err), false};
    const bool ready = top->d_ready;
    top->clk = 1;
    top->eval();
    ++cycles;
    if (ready) {
      top->d_valid = 0;
      top->d_wstrb = 0;
      top->clk = 0;
      top->eval();
      return result;
    }
  }
  top->d_valid = 0;
  return {0, true, true};
}

bool read_word(uint32_t offset, uint32_t *value) {
  BusResult result = bus(offset, 0, 0);
  if (result.error || result.timed_out) return false;
  *value = result.data;
  return true;
}

bool write_word(uint32_t offset, uint32_t value) {
  BusResult result = bus(offset, value, 0xf);
  return !result.error && !result.timed_out;
}

bool read_program(const char *path, std::vector<uint32_t> *words) {
  std::FILE *stream = std::fopen(path, "r");
  if (!stream) {
    std::fprintf(stderr, "tb_role_saxpy: cannot open program %s\n", path);
    return false;
  }
  char token[32];
  bool ok = true;
  while (std::fscanf(stream, "%31s", token) == 1) {
    char *end = nullptr;
    const unsigned long long value = std::strtoull(token, &end, 16);
    if (!end || *end != '\0' || value > 0xffffffffull) {
      std::fprintf(stderr, "tb_role_saxpy: %s is not a 32-bit hex word\n", token);
      ok = false;
      break;
    }
    words->push_back(static_cast<uint32_t>(value));
  }
  std::fclose(stream);
  if (ok && words->empty()) {
    std::fprintf(stderr, "tb_role_saxpy: program %s is empty\n", path);
    ok = false;
  }
  if (ok && words->size() > static_cast<size_t>(kProgramWords)) {
    std::fprintf(stderr, "tb_role_saxpy: program has %zu words, engine holds %d\n",
                 words->size(), kProgramWords);
    ok = false;
  }
  return ok;
}

struct Input {
  long items = -1;
  long a = 0;
  std::vector<int32_t> x;
  std::vector<int32_t> y;
};

bool read_vector(std::FILE *stream, std::vector<int32_t> *values, long items,
                 const char *name) {
  values->clear();
  for (long index = 0; index < items; ++index) {
    long long value = 0;
    if (std::fscanf(stream, "%lld", &value) != 1) {
      std::fprintf(stderr, "tb_role_saxpy: vector %s ended after %ld of %ld values\n",
                   name, index, items);
      return false;
    }
    if (value < INT32_MIN || value > INT32_MAX) {
      std::fprintf(stderr, "tb_role_saxpy: %s[%ld] is outside int32\n", name, index);
      return false;
    }
    values->push_back(static_cast<int32_t>(value));
  }
  return true;
}

bool read_input(const char *path, Input *input) {
  std::FILE *stream = std::fopen(path, "r");
  if (!stream) {
    std::fprintf(stderr, "tb_role_saxpy: cannot open %s\n", path);
    return false;
  }
  char key[32];
  bool ok = true;
  bool have_x = false;
  bool have_y = false;
  while (std::fscanf(stream, "%31s", key) == 1) {
    if (std::strcmp(key, "items") == 0) {
      if (std::fscanf(stream, "%ld", &input->items) != 1 || input->items < 0) {
        ok = false;
        break;
      }
    } else if (std::strcmp(key, "a") == 0) {
      if (std::fscanf(stream, "%ld", &input->a) != 1) {
        ok = false;
        break;
      }
    } else if (std::strcmp(key, "repetitions") == 0) {
      long ignored = 0;
      // Model cycles are deterministic, so a repetition count is an input this
      // target has no use for. It is accepted and ignored rather than treated
      // as a format error, because the same case file feeds every adapter.
      if (std::fscanf(stream, "%ld", &ignored) != 1) {
        ok = false;
        break;
      }
    } else if (std::strcmp(key, "x") == 0 || std::strcmp(key, "y") == 0) {
      if (input->items < 0) {
        std::fprintf(stderr, "tb_role_saxpy: items must precede the %s vector\n", key);
        ok = false;
        break;
      }
      const bool is_x = key[0] == 'x';
      if (!read_vector(stream, is_x ? &input->x : &input->y, input->items, key)) {
        ok = false;
        break;
      }
      (is_x ? have_x : have_y) = true;
    } else {
      std::fprintf(stderr, "tb_role_saxpy: unknown input key %s\n", key);
      ok = false;
      break;
    }
  }
  std::fclose(stream);
  if (ok && (input->items < 0 || !have_x || !have_y)) {
    std::fprintf(stderr, "tb_role_saxpy: input needs items, x, and y\n");
    ok = false;
  }
  return ok;
}

}  // namespace

int main(int argc, char **argv) {
  Verilated::commandArgs(argc, argv);
  const char *path = nullptr;
  const char *program_path = nullptr;
  uint64_t max_cycles = 2000000;
  const char *usage =
      "usage: %s --input <case-file> --program <hex-words> [--max-cycles N]\n";
  for (int i = 1; i < argc; ++i) {
    if (std::strcmp(argv[i], "--input") == 0 && i + 1 < argc) {
      path = argv[++i];
    } else if (std::strcmp(argv[i], "--program") == 0 && i + 1 < argc) {
      program_path = argv[++i];
    } else if (std::strcmp(argv[i], "--max-cycles") == 0 && i + 1 < argc) {
      max_cycles = std::strtoull(argv[++i], nullptr, 10);
    } else {
      std::fprintf(stderr, usage, argv[0]);
      return kUsage;
    }
  }
  if (!path || !program_path) {
    std::fprintf(stderr, usage, argv[0]);
    return kUsage;
  }

  Input input;
  if (!read_input(path, &input)) return kInput;
  std::vector<uint32_t> program;
  if (!read_program(program_path, &program)) return kUnsupported;

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
  for (int i = 0; i < 8; ++i) tick();
  top->rst = 0;
  for (int i = 0; i < 4; ++i) tick();

  uint32_t role_id = 0;
  if (!read_word(kRoleId, &role_id) || role_id != kGpuComputeId) {
    std::fprintf(stderr, "tb_role_saxpy: role window reports 0x%08x, not GPUC\n",
                 role_id);
    delete top;
    return kUnsupported;
  }

  const uint64_t total_started = cycles;

  const int program_words = static_cast<int>(program.size());

  bool staged = true;
  for (int i = 0; i < program_words && staged; ++i) {
    staged = write_word(kProgram + 4u * static_cast<uint32_t>(i),
                        program[static_cast<size_t>(i)]);
  }
  for (long i = 0; i < input.items && staged; ++i) {
    staged = write_word(kData + 4u * static_cast<uint32_t>(i),
                        static_cast<uint32_t>(input.x[static_cast<size_t>(i)]));
  }
  for (long i = 0; i < input.items && staged; ++i) {
    staged = write_word(kData + 4u * static_cast<uint32_t>(input.items + i),
                        static_cast<uint32_t>(input.y[static_cast<size_t>(i)]));
  }
  staged = staged && write_word(kNInsn, static_cast<uint32_t>(program_words));
  staged = staged && write_word(kNThreads, static_cast<uint32_t>(input.items));
  if (!staged) {
    std::fprintf(stderr, "tb_role_saxpy: role window rejected the staged job\n");
    delete top;
    return kInput;
  }

  const uint64_t execute_started = cycles;
  if (!write_word(kDoorbell, 1)) {
    std::fprintf(stderr, "tb_role_saxpy: doorbell write failed\n");
    delete top;
    return kInput;
  }
  bool done = false;
  while (cycles < max_cycles) {
    uint32_t status = 0;
    if (!read_word(kStatus, &status)) {
      std::fprintf(stderr, "tb_role_saxpy: status read failed\n");
      delete top;
      return kInput;
    }
    if (status & 2u) {
      done = true;
      break;
    }
  }
  const uint64_t execute_cycles = cycles - execute_started;
  if (!done) {
    std::fprintf(stderr,
                 "tb_role_saxpy: no completion within %llu cycles\n",
                 static_cast<unsigned long long>(max_cycles));
    delete top;
    return kTimeout;
  }

  std::vector<int32_t> out;
  out.reserve(static_cast<size_t>(input.items));
  for (long i = 0; i < input.items; ++i) {
    uint32_t value = 0;
    if (!read_word(kData + 4u * static_cast<uint32_t>(2 * input.items + i), &value)) {
      std::fprintf(stderr, "tb_role_saxpy: readback failed at element %ld\n", i);
      delete top;
      return kInput;
    }
    out.push_back(static_cast<int32_t>(value));
  }
  const uint64_t total_cycles = cycles - total_started;

  std::printf("{\"workload\":\"org.atomix.workload.saxpy-i32\",\"items\":%ld,\"a\":%ld,",
              input.items, input.a);
  std::printf("\"program_words\":%d,\"out\":[", program_words);
  for (size_t i = 0; i < out.size(); ++i) {
    std::printf("%s%d", i ? "," : "", out[i]);
  }
  std::printf("],\"execute_cycles\":%llu,\"total_cycles\":%llu}\n",
              static_cast<unsigned long long>(execute_cycles),
              static_cast<unsigned long long>(total_cycles));

  delete top;
  return kOk;
}
