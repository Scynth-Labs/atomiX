// One complete SoC, clocked one cycle at a time, with the console byte pipe and
// the SPI card model attached to it.
//
// There are three front ends onto this machine -- the batch runner that every
// `check-*` target uses, the interactive console session, and the browser
// (WebAssembly) driver -- and the whole value of the last two is that they are
// the *same* machine as the one the evidence was measured on.  A front end that
// re-implemented the clocking, the UART handshake, or the SPI sampling edge
// could drift from the reference without any test noticing, so none of them do:
// they differ only in where console bytes come from and where they go.
#ifndef AX_SOC_MACHINE_H
#define AX_SOC_MACHINE_H

#include <cstdint>
#include <string>

#ifdef AX_SOC_SDRAM
#include "Vsoc_sdram_test_top.h"
using ax_soc_top_t = Vsoc_sdram_test_top;
#else
#include "Vsoc_top.h"
using ax_soc_top_t = Vsoc_top;
#endif
#include "verilated.h"

#include "spi_sd_card.h"

class SocMachine {
 public:
  // What one clock produced.  `rx_taken` reports whether the offered console
  // byte was actually consumed: the UART holds a single byte, so a caller must
  // not advance its script until the machine says it took one.
  struct Cycle {
    int tx_byte = -1;
    bool rx_taken = false;
  };

  explicit SocMachine(const std::string& sd_image) : sd_(sd_image) {
    top_ = new ax_soc_top_t;
    reset();
  }

  ~SocMachine() { delete top_; }

  SocMachine(const SocMachine&) = delete;
  SocMachine& operator=(const SocMachine&) = delete;

  // Hold reset across one clock, then release it, which is what the machine
  // sees on power-up.  The RAM image is loaded by the model's own `$readmemh`
  // at construction, so this is a reset rather than a reload.
  void reset() {
    top_->rst = 1;
    top_->clk = 0;
    top_->irq_external = 0;
    top_->uart_tx_ready = 1;
    top_->uart_rx_valid = 0;
    top_->uart_rx_data = 0;
    top_->spi_miso = sd_.miso();
#ifndef AX_SOC_SDRAM
    top_->sdram_dq_i = 0;
#endif
    top_->eval();
    top_->clk = 1;
    top_->eval();
    top_->clk = 0;
    top_->rst = 0;
    top_->eval();
    cycles_ = 0;
  }

  // Advance one clock.  `rx_byte` is a console byte on offer, or -1 for none.
  Cycle cycle(int rx_byte) {
    Cycle result;
    top_->clk = 0;
    top_->uart_rx_valid = rx_byte >= 0 && top_->uart_rx_ready;
    if (top_->uart_rx_valid) {
      top_->uart_rx_data = (unsigned char)rx_byte;
      result.rx_taken = true;
    }
    top_->eval();
    sd_.set_cs_n(top_->spi_cs_n);
    top_->spi_miso = sd_.miso();
    const bool sclk_before = top_->spi_sclk;
    top_->clk = 1;
    top_->eval();
    if (!sclk_before && top_->spi_sclk) sd_.rising_edge(top_->spi_mosi);
    if (top_->uart_tx_valid) result.tx_byte = int(top_->uart_tx_data) & 0xff;
    ++cycles_;
    return result;
  }

  bool finished() const { return top_->finished != 0; }
  unsigned exit_code() const { return top_->exit_code; }
  uint64_t cycles() const { return cycles_; }

  // True while the CPU is parked in WFI with nothing pending.  The machine is
  // still advancing -- time passes, the CLINT counts, a device may raise a
  // line -- but the hart will retire nothing until one of those happens, so a
  // caller with a cycle budget to spend knows it is buying nothing here.
  bool cpu_idle() const { return top_->cpu_idle != 0; }

  // Which memory actually carried the run.  A profile that resolves to on-chip
  // RAM has no pins to count, and says so; a pin-level build reports what the
  // controller drove.  Both front ends print it, so "physical SDRAM" is a
  // measurement of the build rather than a property of the target's name.
  struct SdramPins {
    bool present = false;
    uint32_t activate = 0;
    uint32_t read = 0;
    uint32_t write = 0;
    uint32_t precharge = 0;
    uint32_t refresh = 0;
  };

  // CPU progress, when the profile asked for the monitor.  Present is a
  // build fact, not a runtime one: a machine without the monitor has nothing
  // to report and says so, rather than reporting zeros that would read as a
  // hart that never retired anything.
  struct Progress {
    bool present = false;
    uint64_t cycles = 0;
    uint64_t retired = 0;
    uint64_t retired_user = 0;
    uint64_t retired_supervisor = 0;
    uint64_t retired_machine = 0;
    uint64_t exceptions = 0;
    uint64_t user_exits = 0;
    uint64_t user_entries = 0;
    uint64_t timer_arrivals = 0;
    uint64_t timer_pending_cycles = 0;
    uint64_t idle_cycles = 0;
    uint64_t ifetch_stall_cycles = 0;
    uint64_t dmem_stall_cycles = 0;
    uint32_t last_pc = 0;
    uint32_t last_mip = 0;
    uint32_t last_mie = 0;
    uint32_t last_prv = 0;
  };

  Progress progress() const {
    Progress p;
#ifdef AX_PROGRESS_MONITOR
    p.present = true;
    p.cycles = top_->progress_cycles;
    p.retired = top_->progress_retired;
    p.retired_user = top_->progress_retired_user;
    p.retired_supervisor = top_->progress_retired_supervisor;
    p.retired_machine = top_->progress_retired_machine;
    p.exceptions = top_->progress_exceptions;
    p.user_exits = top_->progress_user_exits;
    p.user_entries = top_->progress_user_entries;
    p.timer_arrivals = top_->progress_timer_arrivals;
    p.timer_pending_cycles = top_->progress_timer_pending_cycles;
    p.idle_cycles = top_->progress_idle_cycles;
    p.ifetch_stall_cycles = top_->progress_ifetch_stall_cycles;
    p.dmem_stall_cycles = top_->progress_dmem_stall_cycles;
    p.last_pc = top_->progress_last_pc;
    p.last_mip = top_->progress_last_mip;
    p.last_mie = top_->progress_last_mie;
    p.last_prv = top_->progress_last_prv;
#endif
    return p;
  }

  SdramPins sdram_pins() const {
    SdramPins pins;
#ifdef AX_SOC_SDRAM
    pins.present = true;
    pins.activate = top_->sdram_cmd_activate;
    pins.read = top_->sdram_cmd_read;
    pins.write = top_->sdram_cmd_write;
    pins.precharge = top_->sdram_cmd_precharge;
    pins.refresh = top_->sdram_cmd_refresh;
#endif
    return pins;
  }

 private:
  ax_soc_top_t* top_ = nullptr;
  SpiSdCard sd_;
  uint64_t cycles_ = 0;
};

#endif  // AX_SOC_MACHINE_H
