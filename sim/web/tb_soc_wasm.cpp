// Browser/Node driver for the Verilated SoC.
//
// The native console runner owns its own loop and blocks on stdin; neither is
// available in a browser, where nothing may hold the thread and console bytes
// arrive from keyboard events. So this front end inverts control: the caller
// drives the machine in bounded slices and pumps bytes in and out between
// them. What it deliberately does *not* do is re-implement the machine -- the
// clocking, the UART handshake, and the SPI sampling edge all come from
// SocMachine, exactly as the batch and interactive runners get them, so a
// cycle count measured in a tab is the cycle count `make sim` reports.
//
// The RAM image is loaded by the model's own `$readmemh` when the model is
// constructed, from the absolute path Verilator compiled in. Construction is
// therefore deferred until ax_boot(): that leaves the caller a window to write
// a payload to that path in the in-memory filesystem first, which is how one
// build of this module boots more than one program.
#include <cstdint>
#include <cstring>
#include <deque>
#include <string>

#include <emscripten/emscripten.h>

#include "soc_machine.h"

#ifndef AX_RAM_INIT_PATH
#define AX_RAM_INIT_PATH ""
#endif
#ifndef AX_PROFILE_NAME
#define AX_PROFILE_NAME "unknown"
#endif

namespace {

SocMachine* g_machine = nullptr;
std::deque<uint8_t> g_rx;       // host -> machine, awaiting the UART
std::deque<uint8_t> g_tx;       // machine -> host, awaiting the caller

}  // namespace

extern "C" {

// Where a payload must be written before ax_boot() for the model to pick it up.
EMSCRIPTEN_KEEPALIVE const char* ax_ram_init_path(void) {
  return AX_RAM_INIT_PATH;
}

// The component profile this module was built from, so a page can label a
// machine with what it actually is rather than with what the page assumed.
EMSCRIPTEN_KEEPALIVE const char* ax_profile(void) { return AX_PROFILE_NAME; }

// Construct the machine and release reset. `sd_image` may be empty (or null)
// for a blank card. Booting again discards the previous machine, which is a
// genuine power cycle: nothing carries over, exactly as a fresh process would
// behave in batch mode.
EMSCRIPTEN_KEEPALIVE void ax_boot(const char* sd_image) {
  delete g_machine;
  g_rx.clear();
  g_tx.clear();
  g_machine = new SocMachine(sd_image ? std::string(sd_image) : std::string());
}

EMSCRIPTEN_KEEPALIVE void ax_shutdown(void) {
  delete g_machine;
  g_machine = nullptr;
  g_rx.clear();
  g_tx.clear();
}

// Queue console input. The UART holds one byte, so this is a queue rather than
// a register: a caller pasting a line must not lose all but the last keystroke.
EMSCRIPTEN_KEEPALIVE void ax_send(int byte) {
  g_rx.push_back((uint8_t)(byte & 0xff));
}

// Advance at most `budget` cycles, stopping early if the finisher fires.
// Returns the number of cycles actually run. The caller chooses the budget,
// which is what keeps a browser tab responsive: a slice sized to a frame
// yields between slices instead of freezing the page for a whole boot.
EMSCRIPTEN_KEEPALIVE int ax_run(int budget) {
  if (!g_machine) return 0;
  int ran = 0;
  for (; ran < budget; ++ran) {
    if (g_machine->finished()) break;
    // Stop early once the CPU parks with nothing queued to wake it. Since aXos
    // made the console interrupt-driven, an idle prompt is a hart in WFI whose
    // only wake source is a keystroke that has not been typed yet -- so the
    // rest of the budget would buy nothing but a higher cycle count. Returning
    // lets the caller wait on the user instead of on the clock.
    //
    // The cost of this is honest and small: simulated time stops advancing
    // while the machine is parked, so a `uptime` read after a long idle shows
    // fewer ticks than wall-clock would suggest. Nothing the hart can observe
    // differs -- it retires nothing either way -- and the alternative is
    // burning a core to make a counter look right.
    if (g_machine->cpu_idle() && g_rx.empty()) break;
    const int offered = g_rx.empty() ? -1 : int(g_rx.front());
    const SocMachine::Cycle step = g_machine->cycle(offered);
    if (step.rx_taken) g_rx.pop_front();
    if (step.tx_byte >= 0) g_tx.push_back((uint8_t)step.tx_byte);
  }
  return ran;
}

// Whether the CPU is parked. The page shows this rather than inferring idleness
// from the text on screen: matching a prompt string guesses at what the machine
// is doing, and this is the machine saying so.
EMSCRIPTEN_KEEPALIVE int ax_cpu_idle(void) {
  return g_machine && g_machine->cpu_idle() ? 1 : 0;
}

// Advance at most `budget` cycles, stopping on the cycle that transmits a
// console byte. A caller measuring *when* something appeared needs this: with
// plain ax_run the answer is only accurate to a slice, and a slice sized for a
// browser frame is several times a whole boot. Stopping on output instead
// gives the exact cycle at a cost proportional to bytes printed rather than to
// cycles run.
EMSCRIPTEN_KEEPALIVE int ax_run_to_output(int budget) {
  if (!g_machine) return 0;
  int ran = 0;
  for (; ran < budget; ++ran) {
    if (g_machine->finished()) break;
    // Same early-out as ax_run, and for a stronger reason here: a caller
    // waiting for output that will only follow input it has not sent would
    // otherwise clock the whole budget to discover nothing.
    if (g_machine->cpu_idle() && g_rx.empty()) break;
    const int offered = g_rx.empty() ? -1 : int(g_rx.front());
    const SocMachine::Cycle step = g_machine->cycle(offered);
    if (step.rx_taken) g_rx.pop_front();
    if (step.tx_byte >= 0) {
      g_tx.push_back((uint8_t)step.tx_byte);
      ++ran;
      break;
    }
  }
  return ran;
}

// Copy out and remove up to `max` transmitted bytes; returns how many. Byte
// count rather than a C string: console output is a byte pipe and may legally
// contain a NUL, and silently truncating there would be a real bug in a
// terminal that is also used to look at binary output.
EMSCRIPTEN_KEEPALIVE int ax_recv(char* dst, int max) {
  int count = 0;
  while (count < max && !g_tx.empty()) {
    dst[count++] = (char)g_tx.front();
    g_tx.pop_front();
  }
  return count;
}

// uint64 through a double: exact to 2^53 cycles, which at ~1.2M cycles/s is
// longer than any session, and avoids a BigInt boundary in the glue code.
EMSCRIPTEN_KEEPALIVE double ax_cycles(void) {
  return g_machine ? double(g_machine->cycles()) : 0.0;
}

EMSCRIPTEN_KEEPALIVE int ax_finished(void) {
  return g_machine && g_machine->finished() ? 1 : 0;
}

EMSCRIPTEN_KEEPALIVE int ax_exit_code(void) {
  return g_machine ? int(g_machine->exit_code()) : 0;
}

}  // extern "C"
