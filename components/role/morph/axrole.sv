`ifndef MORPH_PES
  `define MORPH_PES 4
`endif
`ifndef MORPH_DATA_WORDS
  `define MORPH_DATA_WORDS 256
`endif
// Morph-fabric role: the R2 coarse-grained reconfigurable array.
//
// A thin wrapper that sizes the fabric, exactly like role.gpu-compute wraps
// gpu_engine.  The fabric implementation, window layout, genome format, and
// acceptance rules live in morph_fabric.sv.
module axrole #(
  parameter logic [31:0] BASE = 32'h4000_0000
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
  output logic        irq
`ifdef AX_LIVE_ROLE_EVENTS
  ,
  output logic        reject_event
`endif
);
  morph_fabric #(
    .BASE(BASE),
    .NPE(`MORPH_PES),
    .DATA_WORDS(`MORPH_DATA_WORDS)
  ) u_fabric (.*);
endmodule
