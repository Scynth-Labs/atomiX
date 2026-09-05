// Board-independent external-memory model for Phase 6.  It keeps the same
// dual-port aXbus shape as a future SDRAM controller, but inserts a fixed
// response delay and uses a simulation-friendly backing array.  The core must
// therefore tolerate real ready/valid stalls before board-specific SDRAM PHY
// work begins.
module axdram_model #(
  parameter logic [31:0] BASE = 32'h8000_0000,
  parameter int unsigned BYTES = 32 * 1024 * 1024,
  parameter int unsigned LATENCY = 3,
  parameter INIT_FILE = ""
) (
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
  output logic d_err
);
  localparam int unsigned WORDS = BYTES / 4;
  localparam int unsigned INDEX_BITS = $clog2(WORDS);
  localparam int unsigned COUNT_BITS = $clog2(LATENCY + 1);
  localparam int unsigned INITIAL_COUNT = LATENCY - 1;

  logic [31:0] mem [0:WORDS-1];
  logic i_busy, d_busy;
  logic [COUNT_BITS-1:0] i_count, d_count;
  logic [31:0] i_addr_q, d_addr_q, i_wdata_q, d_wdata_q;
  logic [3:0] i_wstrb_q, d_wstrb_q;

  wire i_ok_q = i_addr_q >= BASE && i_addr_q - BASE <= BYTES - 4 &&
                i_addr_q[1:0] == 2'b00;
  wire d_ok_q = d_addr_q >= BASE && d_addr_q - BASE <= BYTES - 4 &&
                d_addr_q[1:0] == 2'b00;
  // Only the word-index bits address the backing array.  The range check
  // above consumes the full address before these slices are used.
  /* verilator lint_off UNUSED */
  wire [31:0] i_offset_q = i_addr_q - BASE;
  wire [31:0] d_offset_q = d_addr_q - BASE;
  /* verilator lint_on UNUSED */
  wire [INDEX_BITS-1:0] i_index = i_offset_q[INDEX_BITS+1:2];
  wire [INDEX_BITS-1:0] d_index = d_offset_q[INDEX_BITS+1:2];

  // The untyped path parameter is a packed vector; the synthesis frontend
  // accepts this form while it rejects `parameter string`.
  // verilator lint_off WIDTH
`ifdef AX_RUNTIME_RAM_IMAGE
  // Match BRAM startup so payload selection does not depend on memory timing.
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

  always_comb begin
    i_ready = i_busy && i_count == 0;
    d_ready = d_busy && d_count == 0;
    i_err = i_ready && !i_ok_q;
    d_err = d_ready && !d_ok_q;
    i_rdata = i_ok_q ? mem[i_index] : 32'b0;
    d_rdata = d_ok_q ? mem[d_index] : 32'b0;
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      i_busy <= 1'b0;
      d_busy <= 1'b0;
      i_count <= '0;
      d_count <= '0;
    end else begin
      if (!i_busy && i_valid) begin
        i_busy <= 1'b1;
        i_count <= INITIAL_COUNT[COUNT_BITS-1:0];
        i_addr_q <= i_addr;
        i_wdata_q <= i_wdata;
        i_wstrb_q <= i_wstrb;
      end else if (i_busy && i_count != 0) begin
        i_count <= i_count - 1'b1;
      end else if (i_busy && i_valid) begin
        i_busy <= 1'b0;
        if (i_ok_q && |i_wstrb_q) begin
          if (i_wstrb_q[0]) mem[i_index][7:0] <= i_wdata_q[7:0];
          if (i_wstrb_q[1]) mem[i_index][15:8] <= i_wdata_q[15:8];
          if (i_wstrb_q[2]) mem[i_index][23:16] <= i_wdata_q[23:16];
          if (i_wstrb_q[3]) mem[i_index][31:24] <= i_wdata_q[31:24];
        end
      end

      if (!d_busy && d_valid) begin
        d_busy <= 1'b1;
        d_count <= INITIAL_COUNT[COUNT_BITS-1:0];
        d_addr_q <= d_addr;
        d_wdata_q <= d_wdata;
        d_wstrb_q <= d_wstrb;
      end else if (d_busy && d_count != 0) begin
        d_count <= d_count - 1'b1;
      end else if (d_busy && d_valid) begin
        d_busy <= 1'b0;
        if (d_ok_q && |d_wstrb_q) begin
          if (d_wstrb_q[0]) mem[d_index][7:0] <= d_wdata_q[7:0];
          if (d_wstrb_q[1]) mem[d_index][15:8] <= d_wdata_q[15:8];
          if (d_wstrb_q[2]) mem[d_index][23:16] <= d_wdata_q[23:16];
          if (d_wstrb_q[3]) mem[d_index][31:24] <= d_wdata_q[31:24];
        end
      end
    end
  end
endmodule
