// Small dual-port byte-write RAM.
//
// SYNC_READ selects the read timing.  With SYNC_READ=0 (the default) both ports
// implement the aXbus completion rule combinationally: reads return in the same
// cycle.  That async read is convenient for simulation but cannot map to FPGA
// block RAM, which is synchronous-read only, so a large SYNC_READ=0 array is
// forced into LUTs.  With SYNC_READ=1 the read and its completion are
// registered, producing the canonical block-RAM (BSRAM) template: read data
// appears the cycle after the request, and one wait state is inserted per
// access via `ready`.  Writes take effect on the following rising edge in both
// modes.
module axram #(
  parameter logic [31:0] BASE = 32'h8000_0000,
  parameter int unsigned BYTES = 128 * 1024,
  parameter int unsigned SYNC_READ = 0,
  parameter INIT_FILE = ""
) (
  input  logic        clk,
  input  logic        rst,
  input  logic        i_valid,
  input  logic [31:0] i_addr,
  input  logic [31:0] i_wdata,
  input  logic [3:0]  i_wstrb,
  output logic        i_ready,
  output logic [31:0] i_rdata,
  output logic        i_err,
  input  logic        d_valid,
  input  logic [31:0] d_addr,
  input  logic [31:0] d_wdata,
  input  logic [3:0]  d_wstrb,
  output logic        d_ready,
  output logic [31:0] d_rdata,
  output logic        d_err
);
  localparam int unsigned WORDS = BYTES / 4;
  localparam int unsigned INDEX_BITS = $clog2(WORDS);
  logic [31:0] mem [0:WORDS-1];
  wire [31:0] i_offset = i_addr - BASE;
  wire [31:0] d_offset = d_addr - BASE;
  wire i_ok = i_addr >= BASE && i_offset <= BYTES - 4 && i_addr[1:0] == 2'b00;
  wire d_ok = d_addr >= BASE && d_offset <= BYTES - 4 && d_addr[1:0] == 2'b00;
  wire [INDEX_BITS-1:0] i_index = i_offset[INDEX_BITS+1:2];
  wire [INDEX_BITS-1:0] d_index = d_offset[INDEX_BITS+1:2];

  // verilator lint_off WIDTH
`ifdef AX_RUNTIME_RAM_IMAGE
  // Simulator startup only. Synthesis keeps the original initialization path.
  string runtime_image;
  initial begin
    if ($value$plusargs("atomix_ram_image=%s", runtime_image))
      $readmemh(runtime_image, mem);
    else if (INIT_FILE) $readmemh(INIT_FILE, mem);
  end
`else
  initial if (INIT_FILE) $readmemh(INIT_FILE, mem);
`endif
  // verilator lint_on WIDTH

  generate
    if (SYNC_READ == 0) begin : g_async
      // Same-cycle combinational completion (simulation-friendly).  Both ports
      // may write, which is a true dual-port model that suits the ISS/cosim but
      // does not map to hardware block RAM.
      always_comb begin
        i_ready = i_valid;
        i_err   = i_valid && !i_ok;
        i_rdata = i_ok ? mem[i_index] : 32'b0;
        d_ready = d_valid;
        d_err   = d_valid && !d_ok;
        d_rdata = d_ok ? mem[d_index] : 32'b0;
      end
      always_ff @(posedge clk) begin
        if (i_valid && i_ok && |i_wstrb) begin
          if (i_wstrb[0]) mem[i_index][7:0]   <= i_wdata[7:0];
          if (i_wstrb[1]) mem[i_index][15:8]  <= i_wdata[15:8];
          if (i_wstrb[2]) mem[i_index][23:16] <= i_wdata[23:16];
          if (i_wstrb[3]) mem[i_index][31:24] <= i_wdata[31:24];
        end
        if (d_valid && d_ok && |d_wstrb) begin
          if (d_wstrb[0]) mem[d_index][7:0]   <= d_wdata[7:0];
          if (d_wstrb[1]) mem[d_index][15:8]  <= d_wdata[15:8];
          if (d_wstrb[2]) mem[d_index][23:16] <= d_wdata[23:16];
          if (d_wstrb[3]) mem[d_index][31:24] <= d_wdata[31:24];
        end
      end
      // verilator lint_off UNUSED
      wire unused_rst = rst;
      // verilator lint_on UNUSED
    end else begin : g_sync
      // Block-RAM template.  Two properties make it infer synchronous BSRAM
      // (rather than flattening to flip-flops):
      //   * Registered read: read data appears the cycle after the request.
      //     `ready` toggles low then high, and the aXbus master holds the
      //     request until `ready`, so the address is stable and one wait state
      //     is inserted per access.
      //   * A single write port.  Only the data port (`d`) writes; instruction
      //     fetch is read-only (the fetch MMU drives wstrb=0).  One write port
      //     plus the two registered read ports is a 1W2R memory, which the
      //     synthesiser duplicates into per-read-port BSRAM banks.  A second
      //     write port would force the flip-flop fallback.
      logic i_ready_r, d_ready_r;
      always_ff @(posedge clk) begin
        if (rst) begin
          i_ready_r <= 1'b0;
          d_ready_r <= 1'b0;
        end else begin
          i_ready_r <= i_valid && !i_ready_r;
          d_ready_r <= d_valid && !d_ready_r;
        end
        i_rdata <= i_ok ? mem[i_index] : 32'b0;
        d_rdata <= d_ok ? mem[d_index] : 32'b0;
        i_err   <= i_valid && !i_ok;
        d_err   <= d_valid && !d_ok;
        if (d_valid && d_ok && |d_wstrb) begin
          if (d_wstrb[0]) mem[d_index][7:0]   <= d_wdata[7:0];
          if (d_wstrb[1]) mem[d_index][15:8]  <= d_wdata[15:8];
          if (d_wstrb[2]) mem[d_index][23:16] <= d_wdata[23:16];
          if (d_wstrb[3]) mem[d_index][31:24] <= d_wdata[31:24];
        end
      end
      assign i_ready = i_ready_r;
      assign d_ready = d_ready_r;
      // The instruction port is read-only in this mode.
      // verilator lint_off UNUSED
      wire unused_iwrite = |{i_wstrb, i_wdata};
      // verilator lint_on UNUSED
    end
  endgenerate
endmodule
