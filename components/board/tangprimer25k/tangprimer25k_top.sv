// Sipeed Tang Primer 25K + Dock board shell
// (Gowin GW5A-LV25MG121NC1/I0, open-flow device GW5A-LV25MG121NES).
//
// This first target is deliberately BRAM-only: the bare-metal image is baked
// into on-chip block RAM and starts at 0x8000_0000. A dedicated GW5A CLKDIV
// divides the Dock's 50 MHz crystal to a conservative 25 MHz first-hardware
// clock, and the onboard debugger supplies the 115200 8-N-1 UART. S1 is an
// active-high manual reset in addition to power-on reset.
`ifndef AX_TANG_PRIMER_UART_BAUD
`define AX_TANG_PRIMER_UART_BAUD 115200
`endif

module tangprimer25k_top #(
  parameter RAM_INIT_FILE = "../../sw/baremetal/build/hello.hex",
  parameter ROM_INIT_FILE = "",
  parameter logic [31:0] RESET_PC = 32'h8000_0000,
  parameter int unsigned RAM_BYTES = 32 * 1024,
  parameter int unsigned UART_BAUD = `AX_TANG_PRIMER_UART_BAUD
) (
  input  logic clk_50mhz,
  input  logic button_s1,
  output logic uart_tx,
  input  logic uart_rx
);
  logic clk_25mhz;
  logic [15:0] reset_count = 16'b0;
  logic rst;
  logic uart_tx_valid, uart_tx_ready, uart_rx_valid, uart_rx_ready;
  logic [7:0] uart_tx_data, uart_rx_data;
  logic finished;
  logic [15:0] exit_code;

  // Keep the SoC on a dedicated clock path rather than fabric-dividing the
  // oscillator. The baseline pipeline closes comfortably at 25 MHz but its
  // first physical P&R reaches only 34.29 MHz, so driving it directly at
  // 50 MHz would be unsafe even if nextpnr's timing failure were waived.
  CLKDIV #(.DIV_MODE("2")) u_clkdiv (
    .HCLKIN(clk_50mhz),
    .RESETN(1'b1),
    .CALIB(1'b0),
    .CLKOUT(clk_25mhz)
  );

  always_ff @(posedge clk_25mhz) begin
    if (!&reset_count) reset_count <= reset_count + 1'b1;
  end
  assign rst = !&reset_count || button_s1;

  axuart_phy #(.CLOCK_HZ(25_000_000), .BAUD(UART_BAUD)) u_uart_phy (
    .clk(clk_25mhz), .rst(rst),
    .tx_valid(uart_tx_valid), .tx_data(uart_tx_data), .tx_ready(uart_tx_ready),
    .tx(uart_tx), .rx(uart_rx),
    .rx_valid(uart_rx_valid), .rx_data(uart_rx_data), .rx_ready(uart_rx_ready)
  );

  soc_top #(
    .RESET_PC(RESET_PC),
    .RAM_BYTES(RAM_BYTES),
    .USE_DRAM_MODEL(0),
    .USE_SDRAM(0),
    .USE_CACHES(0),
    .SYNC_READ(1),
    .RAM_INIT_FILE(RAM_INIT_FILE),
    .ROM_INIT_FILE(ROM_INIT_FILE)
  ) u_soc (
    .clk(clk_25mhz), .rst(rst), .irq_external(1'b0),
    .uart_tx_valid(uart_tx_valid), .uart_tx_data(uart_tx_data), .uart_tx_ready(uart_tx_ready),
    .uart_rx_valid(uart_rx_valid), .uart_rx_data(uart_rx_data), .uart_rx_ready(uart_rx_ready),
    .spi_sclk(), .spi_mosi(), .spi_cs_n(), .spi_miso(1'b1),
    .sdram_cke(), .sdram_cs_n(), .sdram_ras_n(), .sdram_cas_n(), .sdram_we_n(),
    .sdram_ba(), .sdram_a(), .sdram_dqm(), .sdram_dq_i(16'b0),
    .sdram_dq_o(), .sdram_dq_oe(), .sdram_init_done(),
    .finished(finished), .exit_code(exit_code)
  );

  // The Dock has no ordinary FPGA-driven user LED; UART is the bring-up
  // verdict. READY/DONE remain dedicated configuration pins.
  // verilator lint_off UNUSED
  wire unused = ^{finished, exit_code, uart_rx_ready};
  // verilator lint_on UNUSED
endmodule
