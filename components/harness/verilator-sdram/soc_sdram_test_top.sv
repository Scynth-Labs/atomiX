// Full SoC integration wrapper: cached aXcore traffic uses the physical SDRAM
// controller while the testbench supplies an SDRAM behavioural model.
module soc_sdram_test_top #(
  parameter ROM_INIT_FILE = ""
) (
  input  logic clk,
  input  logic rst,
  input  logic irq_external,
  output logic uart_tx_valid,
  output logic [7:0] uart_tx_data,
  input  logic uart_tx_ready,
  input  logic uart_rx_valid,
  input  logic [7:0] uart_rx_data,
  output logic uart_rx_ready,
  output logic spi_sclk,
  output logic spi_mosi,
  output logic spi_cs_n,
  input  logic spi_miso,
  output logic finished,
  output logic [15:0] exit_code,
  // Carried through for the same reason the BRAM harness top exposes it: the
  // shared runner reads it on whichever top it was built against, so the two
  // simulation environments have to present the same machine.
  output logic cpu_idle
);
  logic sdram_cke, sdram_cs_n, sdram_ras_n, sdram_cas_n, sdram_we_n;
  logic [1:0] sdram_ba, sdram_dqm;
  logic [12:0] sdram_a;
  logic [15:0] sdram_dq_i, sdram_dq_o;
  // verilator lint_off UNUSED
  logic sdram_dq_oe, sdram_init_done;
  // verilator lint_on UNUSED
  tri [15:0] sdram_dq;

  soc_top #(
    .RAM_BYTES(32 * 1024 * 1024), .USE_SDRAM(1), .USE_CACHES(1),
    .ROM_INIT_FILE(ROM_INIT_FILE)
  ) u_soc (
    .clk(clk), .rst(rst), .irq_external(irq_external),
    .uart_tx_valid(uart_tx_valid), .uart_tx_data(uart_tx_data), .uart_tx_ready(uart_tx_ready),
    .uart_rx_valid(uart_rx_valid), .uart_rx_data(uart_rx_data), .uart_rx_ready(uart_rx_ready),
    .spi_sclk(spi_sclk), .spi_mosi(spi_mosi), .spi_cs_n(spi_cs_n), .spi_miso(spi_miso),
    .sdram_cke(sdram_cke), .sdram_cs_n(sdram_cs_n), .sdram_ras_n(sdram_ras_n),
    .sdram_cas_n(sdram_cas_n), .sdram_we_n(sdram_we_n), .sdram_ba(sdram_ba),
    .sdram_a(sdram_a), .sdram_dqm(sdram_dqm), .sdram_dq_i(sdram_dq_i),
    .sdram_dq_o(sdram_dq_o), .sdram_dq_oe(sdram_dq_oe), .sdram_init_done(sdram_init_done),
    .finished(finished), .exit_code(exit_code), .cpu_idle(cpu_idle)
  );
  assign sdram_dq = sdram_dq_oe ? sdram_dq_o : 16'hzzzz;
  assign sdram_dq_i = sdram_dq;

  ax_sdram_sim u_sdram (
    .clk(clk), .rst(rst), .cke(sdram_cke), .cs_n(sdram_cs_n), .ras_n(sdram_ras_n),
    .cas_n(sdram_cas_n), .we_n(sdram_we_n), .ba(sdram_ba), .a(sdram_a),
    .dqm(sdram_dqm), .dq(sdram_dq)
  );
endmodule
