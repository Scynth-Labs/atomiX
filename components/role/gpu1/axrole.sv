// role.gpu1: the scalable SIMT compute engine behind the role window.
//
// One component, tuned at build time.  Lane count, bank count, and which
// optional ISA groups are present are sizing decisions against a part's
// LUT/DSP/BRAM budget, not different accelerators -- the engine, the ISA, the
// window layout, and the driver contract are identical at every setting.  A new
// component would be warranted by a different execution model; it is not
// warranted by a different lane count.
//
// A profile overrides by name:
//
//   "parameters": { "role": { "lanes": 16, "banks": 16 } }
//
// Software does not need to know any of this at compile time: the geometry and
// the feature bits are published in the CAPS register, so one driver and one
// oracle cover every setting.
`ifndef GPU1_LANES
  `define GPU1_LANES 8
`endif
`ifndef GPU1_BANKS
  `define GPU1_BANKS 8
`endif
`ifndef GPU1_ENABLE_DIV
  `define GPU1_ENABLE_DIV 1
`endif
`ifndef GPU1_ENABLE_SHFL
  `define GPU1_ENABLE_SHFL 1
`endif

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

  // Level-sensitive completion line to the PLIC: held while STATUS.DONE is
  // set, so clearing DONE (write 1 to STATUS bit 1) is what deasserts it.
  output logic        irq
`ifdef AX_LIVE_ROLE_EVENTS
  ,

  // One-cycle pulse per descriptor or job this role refused (docs/live-fpga.md).
  // The gpu1 engine validates nothing at the window: lane, bank, and buffer
  // addresses are truncated into its own storage, so a malformed job is a wrong
  // result rather than a refused one, and the shell counts no rejections here.
  output logic        reject_event
`endif
);
`ifdef AX_LIVE_ROLE_EVENTS
  assign reject_event = 1'b0;
`endif

  gpu1_engine #(
    .BASE(BASE),
    .NLANES(`GPU1_LANES),
    .NBANKS(`GPU1_BANKS),
    .ENABLE_DIV(`GPU1_ENABLE_DIV != 0),
    .ENABLE_SHFL(`GPU1_ENABLE_SHFL != 0)
  ) u_engine (.*);
endmodule
