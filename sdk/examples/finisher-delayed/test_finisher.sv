// SPDX-License-Identifier: MIT
// A deliberately small external component used by the atomiX SDK example.
`ifndef AX_SDK_FINISHER_ACK_DELAY
`define AX_SDK_FINISHER_ACK_DELAY 1
`endif

module test_finisher (
  input  logic        clk,
  input  logic        rst,
  input  logic        i_valid,
  input  logic [31:0] i_wdata,
  input  logic [3:0]  i_wstrb,
  output logic        i_ready,
  output logic [31:0] i_rdata,
  output logic        i_err,
  input  logic        d_valid,
  input  logic [31:0] d_wdata,
  input  logic [3:0]  d_wstrb,
  output logic        d_ready,
  output logic [31:0] d_rdata,
  output logic        d_err,
  output logic        finished,
  output logic [15:0] exit_code
);
  localparam integer ACK_DELAY_CYCLES = `AX_SDK_FINISHER_ACK_DELAY;

  logic        i_pending;
  logic        d_pending;
  logic [31:0] i_wait_cycles;
  logic [31:0] d_wait_cycles;

  initial begin
    if (ACK_DELAY_CYCLES < 0 || ACK_DELAY_CYCLES > 16)
      $error("AX_SDK_FINISHER_ACK_DELAY must be in 0..16");
  end

  always_comb begin
    i_rdata = 32'b0;
    i_err = 1'b0;
    d_rdata = 32'b0;
    d_err = 1'b0;
    if (ACK_DELAY_CYCLES == 0) begin
      i_ready = i_valid;
      d_ready = d_valid;
    end else begin
      i_ready = i_pending && (i_wait_cycles == ACK_DELAY_CYCLES - 1);
      d_ready = d_pending && (d_wait_cycles == ACK_DELAY_CYCLES - 1);
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      i_pending <= 1'b0;
      d_pending <= 1'b0;
      i_wait_cycles <= 32'b0;
      d_wait_cycles <= 32'b0;
      finished <= 1'b0;
      exit_code <= 16'b0;
    end else begin
      if (ACK_DELAY_CYCLES != 0) begin
        if (!i_pending && i_valid) begin
          i_pending <= 1'b1;
          i_wait_cycles <= 32'b0;
        end else if (i_pending && i_ready) begin
          i_pending <= 1'b0;
        end else if (i_pending) begin
          i_wait_cycles <= i_wait_cycles + 1'b1;
        end

        if (!d_pending && d_valid) begin
          d_pending <= 1'b1;
          d_wait_cycles <= 32'b0;
        end else if (d_pending && d_ready) begin
          d_pending <= 1'b0;
        end else if (d_pending) begin
          d_wait_cycles <= d_wait_cycles + 1'b1;
        end
      end

      if (i_ready && |i_wstrb &&
          (i_wdata[15:0] == 16'h5555 || i_wdata[15:0] == 16'h7777 ||
           i_wdata[15:0] == 16'h3333)) begin
        finished <= 1'b1;
        exit_code <= i_wdata[15:0] == 16'h3333 ? i_wdata[31:16] : 16'b0;
      end
      if (d_ready && |d_wstrb &&
          (d_wdata[15:0] == 16'h5555 || d_wdata[15:0] == 16'h7777 ||
           d_wdata[15:0] == 16'h3333)) begin
        finished <= 1'b1;
        exit_code <= d_wdata[15:0] == 16'h3333 ? d_wdata[31:16] : 16'b0;
      end
    end
  end
endmodule
