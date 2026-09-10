// Generic full-SoC runner used by bare-metal integration tests. The loaded
// program is expected to use the standard UART and sifive_test interfaces.
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <fstream>
#include <iterator>
#include <sstream>
#include <string>
#include <vector>

// Interactive mode only: a non-blocking read of the console byte pipe. POSIX
// rather than portable C++ because there is no standard way to ask whether a
// stream has a byte ready without committing to block on it.
#include <fcntl.h>
#include <unistd.h>

#include "soc_machine.h"

// How large the RAM the model elaborated is, so an image that does not fit can
// be refused rather than silently truncated.  Compiled from the same variable
// the RTL's RAM_BYTES parameter comes from; zero means the build did not say,
// and the capacity check is then reported as not performed rather than passed.
#ifndef AX_RAM_BYTES
#define AX_RAM_BYTES 0
#endif

// Refuse an image the model would mis-load rather than hand it to $readmemh.
//
// $readmemh is forgiving in exactly the wrong way for a payload loader: a stray
// non-hex character ends the read where it stands, an `@` record moves the
// following words somewhere else in the array, and more words than the array
// holds are dropped off the end.  Each of those boots *something* -- a
// half-loaded program that traps somewhere unrelated, or worse, one that runs
// and produces a wrong answer -- and the run reports the cycle count of
// whatever that was.  A refusal naming the line is the only outcome that
// cannot be mistaken for a result.
static bool validate_ram_image(const std::string& path, std::string* why) {
  std::ifstream stream(path);
  if (!stream) {
    *why = "cannot read " + path;
    return false;
  }
  const uint64_t capacity_words = uint64_t(AX_RAM_BYTES) / 4u;
  uint64_t words = 0;
  std::string line;
  for (unsigned number = 1; std::getline(stream, line); ++number) {
    const size_t comment = line.find("//");
    if (comment != std::string::npos) line.erase(comment);
    if (line.find("/*") != std::string::npos) {
      *why = path + ":" + std::to_string(number) +
             ": block comments are not supported by this loader";
      return false;
    }
    std::istringstream tokens(line);
    std::string token;
    while (tokens >> token) {
      if (token[0] == '@') {
        *why = path + ":" + std::to_string(number) +
               ": address records (@" + token.substr(1) + ") would place the "
               "words that follow somewhere other than the start of RAM";
        return false;
      }
      if (token.size() > 8 ||
          token.find_first_not_of("0123456789abcdefABCDEF") != std::string::npos) {
        *why = path + ":" + std::to_string(number) + ": '" + token +
               "' is not a 32-bit hexadecimal word";
        return false;
      }
      ++words;
    }
  }
  if (words == 0) {
    *why = path + " contains no words";
    return false;
  }
  if (capacity_words != 0 && words > capacity_words) {
    *why = path + " holds " + std::to_string(words) + " words, but this "
           "machine's RAM is " + std::to_string(capacity_words) + " words";
    return false;
  }
  return true;
}

int main(int argc, char** argv) {
  // Keep the image argument alive until after the model has consumed its
  // initial blocks. No hierarchy-specific access to a generated RAM array.
  std::vector<const char*> model_args(argv, argv + argc);
  std::string ram_image_arg;
  std::string input;
  std::string sd_image;
  unsigned max_cycles = 100000;
  // Batch runs consume a fixed script and print the transcript at the end,
  // which is all a self-checking test needs. An interactive run instead keeps
  // the console byte pipe open in both directions for the life of the process:
  // stdin becomes UART receive and UART transmit is streamed to stdout as it
  // is produced. That is what lets a session survive across many commands --
  // in batch mode every exchange would have to boot the machine from reset,
  // so there is no such thing as an interactive prompt.
  bool interactive = false;
  for (int i = 1; i < argc; ++i) {
    if (std::string(argv[i]) == "--ram-image") {
#ifdef AX_SOC_SDRAM
      std::fprintf(stderr, "[soc] --ram-image is unavailable for pin-level SDRAM; use the ROM loader\n");
      return 2;
#else
      if (i + 1 >= argc || !ram_image_arg.empty()) {
        std::fprintf(stderr, "[soc] --ram-image requires one image path, exactly once\n");
        return 2;
      }
      const std::string path = argv[++i];
      std::string why;
      if (!validate_ram_image(path, &why)) {
        std::fprintf(stderr, "[soc] rejected RAM image: %s\n", why.c_str());
        return 2;
      }
      ram_image_arg = "+atomix_ram_image=" + path;
      continue;
#endif
    }
    if (std::string(argv[i]) == "--uart-input" && i + 1 < argc) input = argv[++i];
    if (std::string(argv[i]) == "--uart-input-file" && i + 1 < argc) {
      std::ifstream stream(argv[++i], std::ios::binary);
      input.assign(std::istreambuf_iterator<char>(stream), {});
    }
    if (std::string(argv[i]) == "--uart-interactive") interactive = true;
    if (std::string(argv[i]) == "--max-cycles" && i + 1 < argc)
      max_cycles = std::strtoul(argv[++i], nullptr, 0);
    if (std::string(argv[i]) == "--sd-image" && i + 1 < argc)
      sd_image = argv[++i];
  }
  if (!ram_image_arg.empty()) model_args.push_back(ram_image_arg.c_str());
  Verilated::commandArgs(int(model_args.size()), model_args.data());
  if (interactive && fcntl(STDIN_FILENO, F_SETFL, O_NONBLOCK) < 0) {
    std::fprintf(stderr, "[soc] cannot make stdin non-blocking\n");
    return 1;
  }
  SocMachine machine(sd_image);

  std::string uart;
  unsigned cycles = 0;
  size_t input_pos = 0;
  bool stdin_eof = false;
  // Once the console is closed the machine gets a bounded chance to finish
  // whatever it was printing, rather than being cut off mid-line.
  const unsigned kDrainCycles = 200000;
  unsigned draining = 0;
  for (;; ++cycles) {
    if (machine.finished()) break;
    if (!interactive && cycles >= max_cycles) break;
    if (interactive) {
      // Refill from the console only when the script is exhausted, and only
      // every so often: a read() per simulated cycle would cost far more than
      // the simulation itself, and the UART consumes bytes orders of magnitude
      // more slowly than this polls.
      if (!stdin_eof && input_pos >= input.size() && (cycles & 0x1ff) == 0) {
        char buffer[256];
        const ssize_t got = ::read(STDIN_FILENO, buffer, sizeof buffer);
        if (got > 0) input.append(buffer, (size_t)got);
        else if (got == 0) stdin_eof = true;
        // A negative result is EAGAIN (nothing typed yet), which is not an
        // error: keep clocking so the machine stays responsive.
      }
      if (stdin_eof && input_pos >= input.size() && ++draining > kDrainCycles)
        break;
    }
    const int offered = input_pos < input.size() ? (unsigned char)input[input_pos] : -1;
    const SocMachine::Cycle step = machine.cycle(offered);
    if (step.rx_taken) ++input_pos;
    if (step.tx_byte >= 0) {
      const char byte = char(step.tx_byte);
      // Stream it: a caller waiting on a prompt cannot wait for the run to end.
      if (interactive) {
        std::fputc(byte, stdout);
        std::fflush(stdout);
      } else {
        uart.push_back(byte);
      }
    }
  }

  if (!interactive) std::fwrite(uart.data(), 1, uart.size(), stdout);
  // Name the memory the run actually used, before any pass/fail verdict, so a
  // transcript that claims a physical-SDRAM path can be checked against the
  // pins instead of believed.
  const SocMachine::SdramPins pins = machine.sdram_pins();
  if (pins.present) {
    std::fprintf(stderr,
                 "[soc] sdram-pins: present activate=%u read=%u write=%u "
                 "precharge=%u refresh=%u\n",
                 pins.activate, pins.read, pins.write, pins.precharge,
                 pins.refresh);
  } else {
    std::fprintf(stderr, "[soc] sdram-pins: absent (no pin-level SDRAM model "
                         "in this build)\n");
  }
  // One line per run, machine-readable, on stderr so a transcript comparison
  // is unaffected.  It is printed for a completed run and a cycle-limited one
  // alike: the whole point is to say what a run that did not finish spent its
  // cycles on.
  const SocMachine::Progress prog = machine.progress();
  if (prog.present) {
    std::fprintf(stderr,
                 "[soc] progress: cycles=%llu retired=%llu user=%llu "
                 "supervisor=%llu machine=%llu exceptions=%llu "
                 "user_exits=%llu user_entries=%llu timer_arrivals=%llu "
                 "timer_pending=%llu idle=%llu ifetch_stall=%llu "
                 "dmem_stall=%llu last_pc=0x%08x last_mip=0x%08x "
                 "last_mie=0x%08x last_prv=%u\n",
                 (unsigned long long)prog.cycles,
                 (unsigned long long)prog.retired,
                 (unsigned long long)prog.retired_user,
                 (unsigned long long)prog.retired_supervisor,
                 (unsigned long long)prog.retired_machine,
                 (unsigned long long)prog.exceptions,
                 (unsigned long long)prog.user_exits,
                 (unsigned long long)prog.user_entries,
                 (unsigned long long)prog.timer_arrivals,
                 (unsigned long long)prog.timer_pending_cycles,
                 (unsigned long long)prog.idle_cycles,
                 (unsigned long long)prog.ifetch_stall_cycles,
                 (unsigned long long)prog.dmem_stall_cycles,
                 prog.last_pc, prog.last_mip, prog.last_mie, prog.last_prv);
  }
  // A closed console is an ordinary way for an interactive session to end, so
  // it is not the failure that never reaching the finisher would be in a batch
  // run. A nonzero exit code still is.
  const bool ok = (machine.finished() || (interactive && stdin_eof)) &&
                  machine.exit_code() == 0;
  if (!ok) {
    std::fprintf(stderr, "[soc] FAIL finished=%d exit=%u cycles=%u\n",
                 machine.finished(), machine.exit_code(), cycles);
    return 1;
  }
  std::fprintf(stderr, "[soc] exit 0 (cycles=%u)\n", cycles);
  return 0;
}
