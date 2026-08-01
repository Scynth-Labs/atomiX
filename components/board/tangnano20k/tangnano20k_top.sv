// Sipeed Tang Nano 20K board shell (Gowin GW2AR-LV18QN88C8/I7, GW2A-18C family).
//
// First bring-up target: BRAM-only.  Main memory is on-chip block RAM, so there
// is no external SDRAM and no vendor I/O primitive anywhere in this shell — it
// is entirely generic synthesizable SystemVerilog, the same portability that
// let the reference core move here from the ECP5 board unchanged.  The 27 MHz
// board oscillator clocks the SoC directly; the bare-metal program is
// initialised into block RAM at synthesis time (RAM_INIT_FILE) and the core
// resets straight into it at 0x8000_0000.
//
// External SDRAM (the GW2AR's embedded 64 Mbit die) and the microSD slot are
// deliberately left for a follow-up: they add the only board-specific timing
// and I/O-primitive work, and BRAM-only proves the core + SoC + UART + role
// path on real silicon first.
module tangnano20k_top #(
  parameter RAM_INIT_FILE = "../../sw/baremetal/build/hello.hex",
  // On-chip main memory size.  The FPGA flow overrides this from the selected
  // profile's `ram_bytes` (rtl/fpga/Makefile chparam), so a role-heavy profile
  // can trade main RAM for the block RAM its accelerator needs.
  parameter int unsigned RAM_BYTES = 32 * 1024
) (
  input  logic       clk_27mhz,
  output logic       uart_tx,     // FPGA -> BL616 USB-serial (pin 69)
  input  logic       uart_rx,     // BL616 -> FPGA           (pin 70)
  output logic [5:0] led_n        // active-low user LEDs    (pins 15-20)
);
  logic [15:0] reset_count = 16'b0;
  logic rst;
  logic uart_tx_valid, uart_tx_ready, uart_rx_valid, uart_rx_ready;
  logic [7:0] uart_tx_data, uart_rx_data;
  logic finished;
  logic [15:0] exit_code;

  // Power-on reset: hold until a free-running counter saturates.
  always_ff @(posedge clk_27mhz) begin
    if (!&reset_count) reset_count <= reset_count + 1'b1;
  end
  assign rst = !&reset_count;

  axuart_phy #(.CLOCK_HZ(27_000_000), .BAUD(115_200)) u_uart_phy (
    .clk(clk_27mhz), .rst(rst),
    .tx_valid(uart_tx_valid), .tx_data(uart_tx_data), .tx_ready(uart_tx_ready),
    .tx(uart_tx), .rx(uart_rx),
    .rx_valid(uart_rx_valid), .rx_data(uart_rx_data), .rx_ready(uart_rx_ready)
  );

  // cpu_idle is deliberately left unconnected: a board may gate a clock on it,
  // and this one does not.  Verilator makes a missing pin fatal under -Wall, so
  // say so here rather than wiring a signal nothing consumes.
  // verilator lint_off PINMISSING
  soc_top #(
    .RESET_PC(32'h8000_0000),
    .RAM_BYTES(RAM_BYTES),
    .USE_DRAM_MODEL(0),
    .USE_SDRAM(0),
    .USE_CACHES(0),
    // Registered-read main memory so the 32 KB array infers Gowin BSRAM
    // instead of a LUT mux tree (see axram.sv SYNC_READ).
    .SYNC_READ(1),
    .RAM_INIT_FILE(RAM_INIT_FILE),
    .ROM_INIT_FILE("")
  ) u_soc (
    .clk(clk_27mhz), .rst(rst), .irq_external(1'b0),
    .uart_tx_valid(uart_tx_valid), .uart_tx_data(uart_tx_data), .uart_tx_ready(uart_tx_ready),
    .uart_rx_valid(uart_rx_valid), .uart_rx_data(uart_rx_data), .uart_rx_ready(uart_rx_ready),
    // No SD wired in the BRAM-only bring-up; hold MISO idle-high.
    .spi_sclk(), .spi_mosi(), .spi_cs_n(), .spi_miso(1'b1),
    // SDRAM disabled: outputs are unused, inputs held quiescent.
    .sdram_cke(), .sdram_cs_n(), .sdram_ras_n(), .sdram_cas_n(), .sdram_we_n(),
    .sdram_ba(), .sdram_a(), .sdram_dqm(), .sdram_dq_i(16'b0),
    .sdram_dq_o(), .sdram_dq_oe(), .sdram_init_done(),
    .finished(finished), .exit_code(exit_code)
  );
  // verilator lint_on PINMISSING

  // Liveness aid: LED0 shows reset, LED5 a ~0.5 s heartbeat.  The documented
  // UART transcript remains the verdict.  LEDs are active-low.
  logic [24:0] beat;
  always_ff @(posedge clk_27mhz) beat <= beat + 1'b1;
  assign led_n = ~{beat[24], 4'b0, rst};

  // verilator lint_off UNUSED
  wire unused = ^{finished, exit_code, uart_rx_ready};
  // verilator lint_on UNUSED
endmodule
