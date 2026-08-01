// Generic full-SoC runner used by bare-metal integration tests. The loaded
// program is expected to use the standard UART and sifive_test interfaces.
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <fstream>
#include <iterator>
#include <string>

// Interactive mode only: a non-blocking read of the console byte pipe. POSIX
// rather than portable C++ because there is no standard way to ask whether a
// stream has a byte ready without committing to block on it.
#include <fcntl.h>
#include <unistd.h>

#include "soc_machine.h"

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
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
