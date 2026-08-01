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

 private:
  ax_soc_top_t* top_ = nullptr;
  SpiSdCard sd_;
  uint64_t cycles_ = 0;
};

#endif  // AX_SOC_MACHINE_H
