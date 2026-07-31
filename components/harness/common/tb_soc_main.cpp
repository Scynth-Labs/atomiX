// Generic full-SoC runner used by bare-metal integration tests. The loaded
// program is expected to use the standard UART and sifive_test interfaces.
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <deque>
#include <fstream>
#include <string>
#include <vector>

// Interactive mode only: a non-blocking read of the console byte pipe. POSIX
// rather than portable C++ because there is no standard way to ask whether a
// stream has a byte ready without committing to block on it.
#include <fcntl.h>
#include <unistd.h>

#ifdef AX_SOC_SDRAM
#include "Vsoc_sdram_test_top.h"
using ax_soc_top_t = Vsoc_sdram_test_top;
#else
#include "Vsoc_top.h"
using ax_soc_top_t = Vsoc_top;
#endif
#include "verilated.h"

// Small SPI-mode SDHC card model for Phase 6 software development.  It is a
// simulation device, deliberately kept out of synthesizable RTL.  CMD0,
// CMD8, CMD55/ACMD41, CMD16, CMD17, and CMD58 are enough for a polling,
// read-only block driver; an image is addressed in 512-byte SDHC sectors.
class SpiSdCard {
 public:
  explicit SpiSdCard(const std::string& image_path) {
    if (!image_path.empty()) {
      std::ifstream image(image_path, std::ios::binary);
      image_.assign(std::istreambuf_iterator<char>(image), {});
    }
    if (image_.empty()) image_.resize(512, 0);
    image_.resize((image_.size() + 511) & ~size_t(511), 0);
  }

  void set_cs_n(bool cs_n) {
    const bool selected = !cs_n;
    if (selected != selected_) {
      selected_ = selected;
      command_.clear();
      response_.clear();
      out_active_ = false;
      write_active_ = false;
      write_started_ = false;
      write_data_.clear();
      write_crc_bytes_ = 0;
      rx_bits_ = 0;
      rx_byte_ = 0;
    }
  }

  bool miso() const {
    return out_active_ ? ((out_byte_ >> out_bit_) & 1) : 1;
  }

  void rising_edge(bool mosi) {
    if (!selected_) return;
    if (out_active_) {
      if (out_bit_ == 0) out_active_ = false;
      else --out_bit_;
    }
    rx_byte_ = uint8_t((rx_byte_ << 1) | mosi);
    if (++rx_bits_ == 8) {
      receive_byte(rx_byte_);
      rx_bits_ = 0;
      rx_byte_ = 0;
    }
    load_output();
  }

 private:
  void queue(uint8_t byte) { response_.push_back(byte); }
  void load_output() {
    if (!out_active_ && !response_.empty()) {
      out_byte_ = response_.front();
      response_.pop_front();
      out_bit_ = 7;
      out_active_ = true;
    }
  }

  void receive_byte(uint8_t byte) {
    if (write_active_) {
      receive_write_byte(byte);
      return;
    }
    if (command_.empty()) {
      if ((byte & 0xc0) == 0x40) command_.push_back(byte);
      return;
    }
    command_.push_back(byte);
    if (command_.size() != 6) return;
    const unsigned cmd = command_[0] & 0x3f;
    const uint32_t arg = (uint32_t(command_[1]) << 24) |
                         (uint32_t(command_[2]) << 16) |
                         (uint32_t(command_[3]) << 8) | command_[4];
    command_.clear();
    switch (cmd) {
      case 0:  // GO_IDLE_STATE
        initialized_ = false;
        queue(0x01);
        break;
      case 8:  // SEND_IF_COND
        queue(initialized_ ? 0x00 : 0x01);
        queue(0x00); queue(0x00); queue(0x01); queue(0xaa);
        break;
      case 55: // APP_CMD prefix
        queue(initialized_ ? 0x00 : 0x01);
        break;
      case 41: // ACMD41: host uses HCS for SDHC
        initialized_ = true;
        queue(0x00);
        break;
      case 16: // SET_BLOCKLEN (accepted; sectors are fixed at 512 bytes)
        queue(initialized_ && arg == 512 ? 0x00 : 0x04);
        break;
      case 17: { // READ_SINGLE_BLOCK, SDHC block index
        if (!initialized_ || uint64_t(arg + 1) * 512 > image_.size()) {
          queue(0x04);
          break;
        }
        queue(0x00);
        queue(0xfe);
        const size_t offset = size_t(arg) * 512;
        for (size_t i = 0; i < 512; ++i) queue(image_[offset + i]);
        queue(0xff); queue(0xff);  // CRC is disabled after initialization.
        break;
      }
      case 24: // WRITE_SINGLE_BLOCK, SDHC block index
        if (!initialized_ || uint64_t(arg + 1) * 512 > image_.size()) {
          queue(0x04);
          break;
        }
        queue(0x00);
        write_active_ = true;
        write_started_ = false;
        write_block_ = arg;
        write_data_.clear();
        write_crc_bytes_ = 0;
        break;
      case 58: // READ_OCR, advertise SDHC/CCS
        queue(initialized_ ? 0x00 : 0x01);
        queue(0x40); queue(0x00); queue(0x00); queue(0x00);
        break;
      default:
        queue(0x04);  // illegal command
        break;
    }
  }

  void receive_write_byte(uint8_t byte) {
    if (!write_started_) {
      if (byte == 0xfe) write_started_ = true;
      return;
    }
    if (write_data_.size() < 512) {
      write_data_.push_back(byte);
      return;
    }
    if (++write_crc_bytes_ != 2) return;
    const size_t offset = size_t(write_block_) * 512;
    for (size_t i = 0; i < 512; ++i) image_[offset + i] = write_data_[i];
    queue(0x05);  // data accepted; no artificial busy delay is needed here.
    write_active_ = false;
  }

  std::vector<uint8_t> image_;
  std::deque<uint8_t> response_;
  std::vector<uint8_t> command_;
  bool selected_ = false, initialized_ = false, out_active_ = false;
  bool write_active_ = false, write_started_ = false;
  uint8_t out_byte_ = 0xff, rx_byte_ = 0;
  int out_bit_ = 7, rx_bits_ = 0, write_crc_bytes_ = 0;
  uint32_t write_block_ = 0;
  std::vector<uint8_t> write_data_;
};

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
  SpiSdCard sd_card(sd_image);
  ax_soc_top_t* top = new ax_soc_top_t;
  top->rst = 1;
  top->clk = 0;
  top->irq_external = 0;
  top->uart_tx_ready = 1;
  top->uart_rx_valid = 0;
  top->uart_rx_data = 0;
  top->spi_miso = sd_card.miso();
#ifndef AX_SOC_SDRAM
  top->sdram_dq_i = 0;
#endif
  top->eval();
  top->clk = 1;
  top->eval();
  top->clk = 0;
  top->rst = 0;
  top->eval();

  std::string uart;
  unsigned cycles = 0;
  size_t input_pos = 0;
  bool stdin_eof = false;
  // Once the console is closed the machine gets a bounded chance to finish
  // whatever it was printing, rather than being cut off mid-line.
  const unsigned kDrainCycles = 200000;
  unsigned draining = 0;
  for (;; ++cycles) {
    if (top->finished) break;
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
    top->clk = 0;
    top->uart_rx_valid = input_pos < input.size() && top->uart_rx_ready;
    if (top->uart_rx_valid) top->uart_rx_data = (unsigned char)input[input_pos++];
    top->eval();
    sd_card.set_cs_n(top->spi_cs_n);
    top->spi_miso = sd_card.miso();
    const bool sclk_before = top->spi_sclk;
    top->clk = 1;
    top->eval();
    if (!sclk_before && top->spi_sclk) sd_card.rising_edge(top->spi_mosi);
    if (top->uart_tx_valid) {
      const char byte = char(top->uart_tx_data);
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
  const bool ok = (top->finished || (interactive && stdin_eof)) &&
                  top->exit_code == 0;
  if (!ok) {
    std::fprintf(stderr, "[soc] FAIL finished=%d exit=%u cycles=%u\n",
                 top->finished, top->exit_code, cycles);
    delete top;
    return 1;
  }
  std::fprintf(stderr, "[soc] exit 0 (cycles=%u)\n", cycles);
  delete top;
  return 0;
}
