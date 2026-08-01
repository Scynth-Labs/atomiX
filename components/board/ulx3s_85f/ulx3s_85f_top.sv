// ULX3S v2/v3 board shell, fixed to the 85F / CABGA381 configuration.
//
// The on-board 25 MHz oscillator clocks the SoC.  ODDRX1F launches the SDRAM
// clock half a system cycle later, so commands/data registered at the FPGA's
// rising edge have a full 20 ns of board-level setup time before SDRAM samples
// them.  This intentionally conservative first hardware target prioritises
// reliable bring-up over SDRAM bandwidth.
module ulx3s_85f_top (
  input  logic        clk_25mhz,
  output logic        ftdi_rxd,
  input  logic        ftdi_txd,
  output logic [7:0]  led,

  output logic        sd_clk,
  output logic        sd_cmd,
  input  logic        sd_d0,
  output logic        sd_d3,

  output logic        sdram_clk,
  output logic        sdram_cke,
  output logic        sdram_csn,
  output logic        sdram_wen,
  output logic        sdram_rasn,
  output logic        sdram_casn,
  output logic [12:0] sdram_a,
  output logic [1:0]  sdram_ba,
  output logic [1:0]  sdram_dqm,
  inout  wire  [15:0] sdram_d
);
  logic [15:0] reset_count = 16'b0;
  logic rst;
  logic uart_tx_valid, uart_tx_ready, uart_rx_valid, uart_rx_ready;
  logic [7:0] uart_tx_data, uart_rx_data;
  logic sdram_init_done, finished;
  logic [15:0] exit_code;
  logic [15:0] sdram_dq_i, sdram_dq_o;
  logic sdram_dq_oe;

  always_ff @(posedge clk_25mhz) begin
    if (!&reset_count) reset_count <= reset_count + 1'b1;
  end
  assign rst = !&reset_count;

  // A registered output clock is phase-shifted 180 degrees from the logic
  // clock. SDRAM sees a rising edge on the logic clock's falling edge.
  ODDRX1F u_sdram_clock (
    .D0(1'b0), .D1(1'b1), .SCLK(clk_25mhz), .RST(rst), .Q(sdram_clk)
  );

  axuart_phy #(.CLOCK_HZ(25_000_000), .BAUD(115_200)) u_uart_phy (
    .clk(clk_25mhz), .rst(rst), .tx_valid(uart_tx_valid), .tx_data(uart_tx_data),
    .tx_ready(uart_tx_ready), .tx(ftdi_rxd), .rx(ftdi_txd),
    .rx_valid(uart_rx_valid), .rx_data(uart_rx_data), .rx_ready(uart_rx_ready)
  );

  // cpu_idle is deliberately left unconnected: a board may gate a clock on it,
  // and this one does not.  Verilator makes a missing pin fatal under -Wall, so
  // say so here rather than wiring a signal nothing consumes.
  // verilator lint_off PINMISSING
  soc_top #(
    .RAM_BYTES(32 * 1024 * 1024),
    .USE_SDRAM(1),
    .USE_CACHES(1),
    .ROM_INIT_FILE("../../sw/bootrom/build/bootrom.hex")
  ) u_soc (
    .clk(clk_25mhz), .rst(rst), .irq_external(1'b0),
    .uart_tx_valid(uart_tx_valid), .uart_tx_data(uart_tx_data), .uart_tx_ready(uart_tx_ready),
    .uart_rx_valid(uart_rx_valid), .uart_rx_data(uart_rx_data), .uart_rx_ready(uart_rx_ready),
    .spi_sclk(sd_clk), .spi_mosi(sd_cmd), .spi_cs_n(sd_d3), .spi_miso(sd_d0),
    .sdram_cke(sdram_cke), .sdram_cs_n(sdram_csn), .sdram_ras_n(sdram_rasn),
    .sdram_cas_n(sdram_casn), .sdram_we_n(sdram_wen), .sdram_ba(sdram_ba),
    .sdram_a(sdram_a), .sdram_dqm(sdram_dqm), .sdram_dq_i(sdram_dq_i),
    .sdram_dq_o(sdram_dq_o), .sdram_dq_oe(sdram_dq_oe),
    .sdram_init_done(sdram_init_done), .finished(finished), .exit_code(exit_code)
  );
  // verilator lint_on PINMISSING

  genvar dq_bit;
  generate
    for (dq_bit = 0; dq_bit < 16; dq_bit = dq_bit + 1) begin : g_sdram_dq
      // ECP5's explicit bidirectional pad primitive avoids an internal
      // tri-state feedback loop and lets P&R own the physical I/O buffer.
      BB u_dq (
        .I(sdram_dq_o[dq_bit]), .T(!sdram_dq_oe),
        .O(sdram_dq_i[dq_bit]), .B(sdram_d[dq_bit])
      );
    end
  endgenerate

  // Board LEDs are a liveness aid during first bring-up: reset, SDRAM ready,
  // and a heartbeat. The documented console transcript remains the verdict.
  assign led[0] = rst;
  assign led[1] = sdram_init_done;
  assign led[7:2] = {6{reset_count[15]}};

  // verilator lint_off UNUSED
  wire unused_finish = ^{finished, exit_code};
  // verilator lint_on UNUSED
endmodule
