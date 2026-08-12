// Dual-port read-only boot ROM. Writes complete with an access error.
//
// SYNC_READ selects the read timing, and mirrors the same parameter on
// `axram`.  With SYNC_READ=0 (the default) both ports complete combinationally:
// reads return in the same cycle.  That async read suits the ISS/cosim but
// cannot map to FPGA block RAM, which is synchronous-read only, so the whole
// array is forced into LUTs -- on the Tang Primer the 4 KiB loader ROM alone
// costs about 1,534 LUT4 that way, which is a reason for a tight profile to
// refuse the loader and bake its payload instead.  With SYNC_READ=1 the read
// and its completion are registered, producing the canonical block-RAM (BSRAM)
// template: read data appears the cycle after the request, and one wait state
// is inserted per access via `ready`.  Only the loader pays that wait state,
// and only while it is executing out of the ROM.
//
// There is no write port in either mode, so the synchronous form is a 0W2R
// memory: the synthesiser duplicates it into one initialised BSRAM bank per
// read port rather than a LUT mux tree.
module axrom #(
  parameter logic [31:0] BASE = 32'h0000_1000,
  parameter int unsigned BYTES = 4096,
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
  initial if (INIT_FILE) $readmemh(INIT_FILE, mem);
  // verilator lint_on WIDTH

  generate
    if (SYNC_READ == 0) begin : g_async
      // Same-cycle combinational completion (simulation-friendly).
      always_comb begin
        i_ready = i_valid;
        i_err   = i_valid && (!i_ok || |i_wstrb);
        i_rdata = i_ok ? mem[i_index] : 32'b0;
        d_ready = d_valid;
        d_err   = d_valid && (!d_ok || |d_wstrb);
        d_rdata = d_ok ? mem[d_index] : 32'b0;
      end
      // verilator lint_off UNUSED
      wire unused_async = ^{clk, rst, i_wdata, d_wdata};
      // verilator lint_on UNUSED
    end else begin : g_sync
      // Block-RAM template.  `ready` toggles low then high and the aXbus master
      // holds the request until `ready`, so the address is stable across the
      // extra cycle and exactly one wait state is inserted per access.  A write
      // still reports an access error rather than modifying the array.
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
        i_err   <= i_valid && (!i_ok || |i_wstrb);
        d_err   <= d_valid && (!d_ok || |d_wstrb);
      end
      assign i_ready = i_ready_r;
      assign d_ready = d_ready_r;
      // verilator lint_off UNUSED
      wire unused_sync = ^{i_wdata, d_wdata};
      // verilator lint_on UNUSED
    end
  endgenerate
endmodule
