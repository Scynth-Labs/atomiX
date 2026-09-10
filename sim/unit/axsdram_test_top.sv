// Unit-test wrapper: the behavioural SDRAM samples commands on the falling
// edge, matching the ULX3S board top's half-cycle shifted SDRAM clock.
module axsdram_test_top (
  input  logic clk,
  input  logic rst,
  input  logic i_valid,
  input  logic [31:0] i_addr,
  input  logic [31:0] i_wdata,
  input  logic [3:0] i_wstrb,
  output logic i_ready,
  output logic [31:0] i_rdata,
  output logic i_err,
  input  logic d_valid,
  input  logic [31:0] d_addr,
  input  logic [31:0] d_wdata,
  input  logic [3:0] d_wstrb,
  output logic d_ready,
  output logic [31:0] d_rdata,
  output logic d_err,
  output logic init_done,
  // The model's pin-command counters, published so this test can say how much
  // traffic it actually put on the pins rather than only that it agreed.
  output logic [31:0] cmd_activate,
  output logic [31:0] cmd_read,
  output logic [31:0] cmd_write,
  output logic [31:0] cmd_precharge,
  output logic [31:0] cmd_refresh
);
  logic sdram_cke, sdram_cs_n, sdram_ras_n, sdram_cas_n, sdram_we_n;
  logic [1:0] sdram_ba, sdram_dqm;
  logic [12:0] sdram_a;
  logic [15:0] sdram_dq_o;
  logic sdram_dq_oe;
  // verilator lint_off UNUSED
  tri [15:0] sdram_dq;
  // verilator lint_on UNUSED

  axsdram #(.POWERUP_CYCLES(4), .REFRESH_CYCLES(11)) dut (
    .clk(clk), .rst(rst),
    .i_valid(i_valid), .i_addr(i_addr), .i_wdata(i_wdata), .i_wstrb(i_wstrb),
    .i_ready(i_ready), .i_rdata(i_rdata), .i_err(i_err),
    .d_valid(d_valid), .d_addr(d_addr), .d_wdata(d_wdata), .d_wstrb(d_wstrb),
    .d_ready(d_ready), .d_rdata(d_rdata), .d_err(d_err),
    .sdram_cke(sdram_cke), .sdram_cs_n(sdram_cs_n), .sdram_ras_n(sdram_ras_n),
    .sdram_cas_n(sdram_cas_n), .sdram_we_n(sdram_we_n), .sdram_ba(sdram_ba),
    .sdram_a(sdram_a), .sdram_dqm(sdram_dqm), .sdram_dq_i(sdram_dq),
    .sdram_dq_o(sdram_dq_o), .sdram_dq_oe(sdram_dq_oe),
    .init_done(init_done)
  );

  assign sdram_dq = sdram_dq_oe ? sdram_dq_o : 16'hzzzz;

  ax_sdram_sim u_mem (
    .clk(clk), .rst(rst), .cke(sdram_cke), .cs_n(sdram_cs_n),
    .ras_n(sdram_ras_n), .cas_n(sdram_cas_n), .we_n(sdram_we_n),
    .ba(sdram_ba), .a(sdram_a), .dqm(sdram_dqm), .dq(sdram_dq),
    .cmd_activate(cmd_activate), .cmd_read(cmd_read), .cmd_write(cmd_write),
    .cmd_precharge(cmd_precharge), .cmd_refresh(cmd_refresh)
  );
endmodule
