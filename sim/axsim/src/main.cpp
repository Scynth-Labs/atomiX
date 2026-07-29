#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <inttypes.h>
#include <vector>

#include "bus.h"
#include "cpu.h"
#include "elf.h"

static void usage(const char* argv0) {
  fprintf(stderr,
          "aXsim — atomiX instruction-set simulator (RV32IM)\n"
          "usage: %s [options] --bin FILE\n"
          "  --bin FILE   program image: ELF (auto-detected; entry point and\n"
          "               riscv-tests tohost honored) or flat binary\n"
          "  --base ADDR  flat-binary load address (default 0x80000000)\n"
          "  --pc ADDR    reset PC (default: ELF entry / --base)\n"
          "  --ram MB     RAM size in MiB     (default 32)\n"
          "  --ram-bytes N  exact RAM size in bytes (overrides --ram)\n"
          "  --max N      stop after N instructions (default: unlimited)\n"
          "  --uart-input TEXT  bytes available through the 16550 RX register\n"
          "  --uart-input-file FILE  bytes available through the 16550 RX register\n"
          "  --trace      log every retired instruction to stderr\n",
          argv0);
}

static uint32_t parse_u32(const char* s) { return (uint32_t)strtoul(s, nullptr, 0); }

int main(int argc, char** argv) {
  const char* bin_path = nullptr;
  uint32_t base = map::RAM_BASE;
  uint32_t reset_pc = 0;
  bool have_pc = false;
  size_t ram_mib = 32;
  size_t ram_bytes = 0;
  uint64_t max_insns = 0;
  bool trace = false;
  const char* uart_input = nullptr;
  const char* uart_input_file = nullptr;

  for (int i = 1; i < argc; i++) {
    auto arg_val = [&](const char* name) -> const char* {
      if (strcmp(argv[i], name)) return nullptr;
      if (++i >= argc) { usage(argv[0]); exit(1); }
      return argv[i];
    };
    if (const char* v = arg_val("--bin")) bin_path = v;
    else if (const char* v = arg_val("--base")) base = parse_u32(v);
    else if (const char* v = arg_val("--pc")) { reset_pc = parse_u32(v); have_pc = true; }
    else if (const char* v = arg_val("--ram")) ram_mib = parse_u32(v);
    else if (const char* v = arg_val("--ram-bytes")) ram_bytes = parse_u32(v);
    else if (const char* v = arg_val("--max")) max_insns = strtoull(v, nullptr, 0);
    else if (const char* v = arg_val("--uart-input")) uart_input = v;
    else if (const char* v = arg_val("--uart-input-file")) uart_input_file = v;
    else if (!strcmp(argv[i], "--trace")) trace = true;
    else { usage(argv[0]); return 1; }
  }
  if (!bin_path) { usage(argv[0]); return 1; }
  if (!have_pc) reset_pc = base;

  Bus bus(ram_bytes ? ram_bytes : ram_mib << 20);
  if (uart_input) bus.set_uart_input(uart_input);
  if (uart_input_file) {
    std::ifstream stream(uart_input_file, std::ios::binary);
    std::string input((std::istreambuf_iterator<char>(stream)), {});
    bus.set_uart_input(input.c_str());
  }

  if (is_elf(bin_path)) {
    ElfInfo info;
    if (!load_elf(bin_path, bus, info)) return 1;
    if (!have_pc) reset_pc = info.entry, have_pc = true;
    if (info.has_tohost) {
      bus.tohost_en = true;
      bus.tohost_addr = info.tohost;
    }
  } else {
    FILE* f = fopen(bin_path, "rb");
    if (!f) { perror(bin_path); return 1; }
    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);
    std::vector<uint8_t> image(len > 0 ? (size_t)len : 0);
    if (!image.empty() &&
        fread(image.data(), 1, image.size(), f) != image.size()) {
      fprintf(stderr, "[axsim] short read: %s\n", bin_path);
      return 1;
    }
    fclose(f);
    if (!bus.load_image(image.data(), image.size(), base)) {
      fprintf(stderr, "[axsim] image does not fit at 0x%08x (%zu bytes)\n",
              base, image.size());
      return 1;
    }
  }

  Cpu cpu(bus, reset_pc);
  cpu.trace = trace;

  while (max_insns == 0 || cpu.retired() < max_insns) {
    if (cpu.step() == Stop::Fault)
      return 127;  // diagnostics already printed by the CPU
    if (bus.exit_req) {
      fprintf(stderr, "[axsim] exit %d (finisher/tohost, retired=%" PRIu64 ")\n",
              bus.exit_code, cpu.retired());
      return bus.exit_code;
    }
  }
  fprintf(stderr, "[axsim] max instruction count reached (%" PRIu64 ")\n",
          max_insns);
  return 3;
}
