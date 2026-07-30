// Minimal 16550-compatible console subset. THR writes emit one byte on the
// synthesizable tx_* sideband; a one-byte RX holding register feeds RBR reads.
module uart #(
  parameter logic [31:0] BASE = 32'h1000_0000
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
  output logic        d_err,
  output logic        tx_valid,
  output logic [7:0]  tx_data,
  input  logic        tx_ready,
  input  logic        rx_valid,
  input  logic [7:0]  rx_data,
  output logic        rx_ready,
  // Level-sensitive "a received byte is waiting", for an interrupt controller.
  // It follows the same bit the LSR data-ready flag reports, so a handler that
  // drains RBR clears it by the act of reading -- no separate acknowledge.
  output logic        irq_rx
);
  logic rx_full;
  logic [7:0] rx_byte;
  wire i_in_range = i_addr >= BASE && i_addr - BASE < 32'h1000;
  wire d_in_range = d_addr >= BASE && d_addr - BASE < 32'h1000;
  wire [2:0] i_off = i_addr[2:0];
  wire [2:0] d_off = d_addr[2:0];
  // aXbus reads are word-aligned.  Return register bytes in their physical
  // lane so the core's normal load extractor sees UART offsets 2 and 5.
  function automatic logic [31:0] read_offset(input logic [2:0] off);
    unique case (off)
      3'd0:    read_offset = {24'b0, rx_byte};
      3'd4:    read_offset = 32'h0000_6000 | (rx_full ? 32'h0000_0100 : 0);
      default: read_offset = 32'b0;
    endcase
  endfunction

  always_comb begin
    // A transmitter write is an aXbus handshake with the physical UART
    // backend.  Simulation ties tx_ready high; a board serialises bytes and
    // stalls THR stores while the previous frame is on the wire.
    i_ready = i_valid && (!i_wstrb[0] || i_off != 3'd0 || tx_ready);
    d_ready = d_valid && (!d_wstrb[0] || d_off != 3'd0 || tx_ready);
    i_err = i_valid && !i_in_range;
    d_err = d_valid && !d_in_range;
    i_rdata = read_offset(i_off);
    d_rdata = read_offset(d_off);
    tx_valid = (i_valid && i_off == 3'd0 && i_wstrb[0]) ||
               (d_valid && d_off == 3'd0 && d_wstrb[0]);
    tx_data = (d_valid && d_off == 3'd0 && d_wstrb[0]) ? d_wdata[7:0] : i_wdata[7:0];
    rx_ready = !rx_full;
    irq_rx   = rx_full;
  end
  always_ff @(posedge clk) begin
    if (rst) begin
      rx_full  <= 1'b0;
      rx_byte  <= 8'b0;
    end else begin
      if (d_valid && d_off == 3'd0 && d_wstrb == 4'b0) rx_full <= 1'b0;
      if (rx_valid && rx_ready) begin
        rx_full <= 1'b1;
        rx_byte <= rx_data;
      end
    end
  end

  // The 16550 subset intentionally consumes only THR's low byte/lane.
  // verilator lint_off UNUSED
  wire unused_write_lanes = ^{i_wdata[31:8], i_wstrb[3:1],
                               d_wdata[31:8], d_wstrb[3:1]};
  // verilator lint_on UNUSED
endmodule
