// Formal-only environment for ax2, the dual-issue core.
//
// Structurally this mirrors the reference core's wrapper: both bus responses
// are unconstrained, so riscv-formal quantifies over instruction streams and
// memory contents rather than over a program we chose.  The difference is the
// trace width -- ax2 retires up to two instructions per cycle, so its RVFI
// surface has two channels and `checks-ax2.cfg` sets `nret 2`.
//
// ax2_core is instantiated directly rather than through the `axcore` shim: the
// shim exists to present the interchangeable `core` component interface, which
// deliberately does not carry RVFI (the two cores' traces have different
// channel counts).  Parameters are therefore set here explicitly.
module rvfi_wrapper (
  input  wire        clock,
  input  wire        reset,
  output wire [1:0]   rvfi_valid,
  output wire [127:0] rvfi_order,
  output wire [63:0]  rvfi_insn,
  output wire [1:0]   rvfi_trap,
  output wire [1:0]   rvfi_halt,
  output wire [1:0]   rvfi_intr,
  output wire [3:0]   rvfi_mode,
  output wire [3:0]   rvfi_ixl,
  output wire [9:0]   rvfi_rs1_addr,
  output wire [9:0]   rvfi_rs2_addr,
  output wire [63:0]  rvfi_rs1_rdata,
  output wire [63:0]  rvfi_rs2_rdata,
  output wire [9:0]   rvfi_rd_addr,
  output wire [63:0]  rvfi_rd_wdata,
  output wire [63:0]  rvfi_pc_rdata,
  output wire [63:0]  rvfi_pc_wdata,
  output wire [63:0]  rvfi_mem_addr,
  output wire [7:0]   rvfi_mem_rmask,
  output wire [7:0]   rvfi_mem_wmask,
  output wire [63:0]  rvfi_mem_rdata,
  output wire [63:0]  rvfi_mem_wdata
);
  // ax2 fetches a two-instruction bundle, so the fetch path returns a pair.
  // Holding both words constant keeps the bounded suite tractable while still
  // leaving the instructions themselves arbitrary; each check constrains them
  // to the class it is proving.
  (* anyconst *) reg [31:0] ibus_rdata;
  (* anyseq *)   reg [31:0] dbus_rdata;

  wire        ibus_valid;
  wire [31:0] ibus_addr, ibus_wdata;
  wire [3:0]  ibus_wstrb;
  wire        dbus_valid;
  wire [31:0] dbus_addr, dbus_wdata;
  wire [3:0]  dbus_wstrb;

  // The smallest configuration that still exercises dual issue.  Cache sets and
  // BTB entries multiply the state the solver unrolls once per cycle, and
  // neither affects instruction semantics: what is under proof here is what a
  // bundle of two retires, not how fetch found it.  BTB_ENTRIES(0) removes the
  // predictor entirely, leaving the mispredict path permanently on its
  // not-predicted arm.
  ax2_core #(
    .ENABLE_M(1'b0),          // matches the reference core's RV32I configuration
    .ISSUE_WIDTH(2),          // the property under proof is dual issue itself
    .ICACHE_KB(1),
    .BTB_ENTRIES(0)
  ) uut (
    .clk(clock), .rst(reset),
    .ibus_valid(ibus_valid), .ibus_addr(ibus_addr), .ibus_wdata(ibus_wdata),
    .ibus_wstrb(ibus_wstrb), .ibus_ready(1'b1),
    .ibus_rdata(ibus_rdata), .ibus_err(1'b0),
    .dbus_valid(dbus_valid), .dbus_addr(dbus_addr), .dbus_wdata(dbus_wdata),
    .dbus_wstrb(dbus_wstrb), .dbus_ready(1'b1), .dbus_rdata(dbus_rdata),
    .dbus_err(1'b0),
    .irq_software(1'b0), .irq_timer(1'b0), .irq_external(1'b0),
    .trace_valid(), .trace_trap(), .trace_insn(),
    .rvfi_valid(rvfi_valid), .rvfi_order(rvfi_order), .rvfi_insn(rvfi_insn),
    .rvfi_trap(rvfi_trap), .rvfi_halt(rvfi_halt), .rvfi_intr(rvfi_intr),
    .rvfi_mode(rvfi_mode), .rvfi_ixl(rvfi_ixl),
    .rvfi_rs1_addr(rvfi_rs1_addr), .rvfi_rs2_addr(rvfi_rs2_addr),
    .rvfi_rs1_rdata(rvfi_rs1_rdata), .rvfi_rs2_rdata(rvfi_rs2_rdata),
    .rvfi_rd_addr(rvfi_rd_addr), .rvfi_rd_wdata(rvfi_rd_wdata),
    .rvfi_pc_rdata(rvfi_pc_rdata), .rvfi_pc_wdata(rvfi_pc_wdata),
    .rvfi_mem_addr(rvfi_mem_addr), .rvfi_mem_rmask(rvfi_mem_rmask),
    .rvfi_mem_wmask(rvfi_mem_wmask), .rvfi_mem_rdata(rvfi_mem_rdata),
    .rvfi_mem_wdata(rvfi_mem_wdata)
  );
endmodule
