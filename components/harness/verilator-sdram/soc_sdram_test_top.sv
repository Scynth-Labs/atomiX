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
  output logic cpu_idle,
  // Pin-level command activity, published so the runner can state which memory
  // actually carried the run rather than trusting the target's name.
  output logic [31:0] sdram_cmd_activate,
  output logic [31:0] sdram_cmd_read,
  output logic [31:0] sdram_cmd_write,
  output logic [31:0] sdram_cmd_precharge,
  output logic [31:0] sdram_cmd_refresh
`ifdef AX_PROGRESS_MONITOR
  ,
  output logic [63:0] progress_cycles,
  output logic [63:0] progress_retired,
  output logic [63:0] progress_retired_user,
  output logic [63:0] progress_retired_supervisor,
  output logic [63:0] progress_retired_machine,
  output logic [63:0] progress_exceptions,
  output logic [63:0] progress_user_exits,
  output logic [63:0] progress_user_entries,
  output logic [63:0] progress_timer_arrivals,
  output logic [63:0] progress_timer_pending_cycles,
  output logic [63:0] progress_idle_cycles,
  output logic [63:0] progress_ifetch_stall_cycles,
  output logic [63:0] progress_dmem_stall_cycles,
  output logic [31:0] progress_last_pc,
  output logic [31:0] progress_last_mip,
  output logic [31:0] progress_last_mie,
  output logic [1:0]  progress_last_prv
`endif
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
`ifdef AX_PROGRESS_MONITOR
    , .progress_cycles(progress_cycles), .progress_retired(progress_retired),
    .progress_retired_user(progress_retired_user),
    .progress_retired_supervisor(progress_retired_supervisor),
    .progress_retired_machine(progress_retired_machine),
    .progress_exceptions(progress_exceptions),
    .progress_user_exits(progress_user_exits),
    .progress_user_entries(progress_user_entries),
    .progress_timer_arrivals(progress_timer_arrivals),
    .progress_timer_pending_cycles(progress_timer_pending_cycles),
    .progress_idle_cycles(progress_idle_cycles),
    .progress_ifetch_stall_cycles(progress_ifetch_stall_cycles),
    .progress_dmem_stall_cycles(progress_dmem_stall_cycles),
    .progress_last_pc(progress_last_pc),
    .progress_last_mip(progress_last_mip),
    .progress_last_mie(progress_last_mie),
    .progress_last_prv(progress_last_prv)
`endif
  );
  assign sdram_dq = sdram_dq_oe ? sdram_dq_o : 16'hzzzz;
  assign sdram_dq_i = sdram_dq;

  ax_sdram_sim u_sdram (
    .clk(clk), .rst(rst), .cke(sdram_cke), .cs_n(sdram_cs_n), .ras_n(sdram_ras_n),
    .cas_n(sdram_cas_n), .we_n(sdram_we_n), .ba(sdram_ba), .a(sdram_a),
    .dqm(sdram_dqm), .dq(sdram_dq),
    .cmd_activate(sdram_cmd_activate), .cmd_read(sdram_cmd_read),
    .cmd_write(sdram_cmd_write), .cmd_precharge(sdram_cmd_precharge),
    .cmd_refresh(sdram_cmd_refresh)
  );
endmodule
