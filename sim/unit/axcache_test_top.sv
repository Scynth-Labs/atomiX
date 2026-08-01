// Test-only composition: cache backed by the same delayed RAM model used by
// the Phase 6 SoC configuration.  Exposing the lower bus lets the C++ test
// prove hits do not generate extra external-memory transactions.
module axcache_test_top (
  input  logic clk,
  input  logic rst,
  input  logic flush,
  output logic flush_busy,
  input  logic c_valid,
  input  logic [31:0] c_addr,
  input  logic [31:0] c_wdata,
  input  logic [3:0] c_wstrb,
  output logic c_ready,
  output logic [31:0] c_rdata,
  output logic c_err,
  output logic mem_valid,
  output logic mem_ready,
  // A second instance whose backing store is four times its capacity, so two
  // addresses can land on the same line.  The primary instance above covers
  // exactly as many bytes as it can hold and therefore never evicts anything,
  // which leaves the whole conflict-miss path -- refill over a live line --
  // untested.
  input  logic a_valid,
  input  logic [31:0] a_addr,
  input  logic [31:0] a_wdata,
  input  logic [3:0] a_wstrb,
  output logic a_ready,
  output logic [31:0] a_rdata,
  output logic a_err
);
  logic [31:0] mem_addr, mem_wdata, mem_rdata;
  logic [3:0] mem_wstrb;
  logic mem_err;

  axcache #(.CACHE_BASE(32'h8000_0000), .CACHE_BYTES(64), .LINES(4), .WORDS_PER_LINE(4)) u_cache (
    .clk(clk), .rst(rst), .flush(flush), .flush_busy(flush_busy),
    .c_valid(c_valid), .c_addr(c_addr), .c_wdata(c_wdata), .c_wstrb(c_wstrb),
    .c_ready(c_ready), .c_rdata(c_rdata), .c_err(c_err),
    .m_valid(mem_valid), .m_addr(mem_addr), .m_wdata(mem_wdata), .m_wstrb(mem_wstrb),
    .m_ready(mem_ready), .m_rdata(mem_rdata), .m_err(mem_err)
  );

  // verilator lint_off PINCONNECTEMPTY
  axdram_model #(.BASE(32'h8000_0000), .BYTES(256), .LATENCY(2)) u_memory (
    .clk(clk), .rst(rst),
    .i_valid(mem_valid), .i_addr(mem_addr), .i_wdata(mem_wdata), .i_wstrb(mem_wstrb),
    .i_ready(mem_ready), .i_rdata(mem_rdata), .i_err(mem_err),
    .d_valid(1'b0), .d_addr(32'b0), .d_wdata(32'b0), .d_wstrb(4'b0),
    .d_ready(), .d_rdata(), .d_err()
  );
  // verilator lint_on PINCONNECTEMPTY

  logic [31:0] alias_mem_addr, alias_mem_wdata, alias_mem_rdata;
  logic [3:0] alias_mem_wstrb;
  logic alias_mem_valid, alias_mem_ready, alias_mem_err;

  // verilator lint_off PINCONNECTEMPTY
  axcache #(.CACHE_BASE(32'h8000_0000), .CACHE_BYTES(256), .LINES(4), .WORDS_PER_LINE(4)) u_alias_cache (
    .clk(clk), .rst(rst), .flush(1'b0), .flush_busy(),
    .c_valid(a_valid), .c_addr(a_addr), .c_wdata(a_wdata), .c_wstrb(a_wstrb),
    .c_ready(a_ready), .c_rdata(a_rdata), .c_err(a_err),
    .m_valid(alias_mem_valid), .m_addr(alias_mem_addr), .m_wdata(alias_mem_wdata),
    .m_wstrb(alias_mem_wstrb),
    .m_ready(alias_mem_ready), .m_rdata(alias_mem_rdata), .m_err(alias_mem_err)
  );

  axdram_model #(.BASE(32'h8000_0000), .BYTES(256), .LATENCY(2)) u_alias_memory (
    .clk(clk), .rst(rst),
    .i_valid(alias_mem_valid), .i_addr(alias_mem_addr), .i_wdata(alias_mem_wdata),
    .i_wstrb(alias_mem_wstrb),
    .i_ready(alias_mem_ready), .i_rdata(alias_mem_rdata), .i_err(alias_mem_err),
    .d_valid(1'b0), .d_addr(32'b0), .d_wdata(32'b0), .d_wstrb(4'b0),
    .d_ready(), .d_rdata(), .d_err()
  );
  // verilator lint_on PINCONNECTEMPTY
endmodule
