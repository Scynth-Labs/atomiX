// SPI-mode SDHC card model shared by every simulation harness that presents a
// card at the SoC's SPI pins.  The Verilator console runner and the browser
// (WebAssembly) driver are different front ends onto the same machine, so the
// device they see has to be the same device rather than two copies that can
// drift apart.
#ifndef AX_SPI_SD_CARD_H
#define AX_SPI_SD_CARD_H

#include <cstddef>
#include <cstdint>
#include <deque>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

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

#endif  // AX_SPI_SD_CARD_H
