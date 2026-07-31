// Formal-only environment for core.minimal.
//
// Identical in structure to the reference core's wrapper, and deliberately so:
// both cores retire at most one instruction per cycle, so the same environment
// and the same `nret 1` properties apply unchanged.  What differs is the machine
// underneath -- a multi-cycle FETCH/EXEC/MEM/MULDIV state machine rather than a
// five-stage pipeline -- which is the point of proving it separately.  A core
// selectable at the `axcore` seam should earn its own evidence rather than
// inherit the reference core's.
module rvfi_wrapper (
  input  wire        clock,
  input  wire        reset,
  output wire        rvfi_valid,
  output wire [63:0] rvfi_order,
  output wire [31:0] rvfi_insn,
  output wire        rvfi_trap,
  output wire        rvfi_halt,
  output wire        rvfi_intr,
  output wire [1:0]  rvfi_mode,
  output wire [1:0]  rvfi_ixl,
  output wire [4:0]  rvfi_rs1_addr,
  output wire [4:0]  rvfi_rs2_addr,
  output wire [31:0] rvfi_rs1_rdata,
  output wire [31:0] rvfi_rs2_rdata,
  output wire [4:0]  rvfi_rd_addr,
  output wire [31:0] rvfi_rd_wdata,
  output wire [31:0] rvfi_pc_rdata,
  output wire [31:0] rvfi_pc_wdata,
  output wire [31:0] rvfi_mem_addr,
  output wire [3:0]  rvfi_mem_rmask,
  output wire [3:0]  rvfi_mem_wmask,
  output wire [31:0] rvfi_mem_rdata,
  output wire [31:0] rvfi_mem_wdata
);
  // A stable but otherwise arbitrary instruction keeps the bounded suite
  // tractable; each RVFI check constrains this word to the class it proves.
  (* anyconst *) reg [31:0] ibus_rdata;
  (* anyseq *) reg [31:0] dbus_rdata;

  wire        ibus_valid;
  wire [31:0] ibus_addr, ibus_wdata;
  wire [3:0]  ibus_wstrb;
  wire        dbus_valid;
  wire [31:0] dbus_addr, dbus_wdata;
  wire [3:0]  dbus_wstrb;

  axcore #(.ENABLE_M(1'b0)) uut (
    .clk(clock), .rst(reset),
    .ibus_valid(ibus_valid), .ibus_addr(ibus_addr), .ibus_wdata(ibus_wdata),
    .ibus_wstrb(ibus_wstrb), .ibus_ready(1'b1),
    .ibus_rdata(ibus_rdata), .ibus_err(1'b0),
    .dbus_valid(dbus_valid), .dbus_addr(dbus_addr), .dbus_wdata(dbus_wdata),
    .dbus_wstrb(dbus_wstrb), .dbus_ready(1'b1), .dbus_rdata(dbus_rdata),
    .dbus_err(1'b0),
    .irq_software(1'b0), .irq_timer(1'b0), .irq_external(1'b0),
    .irq_s_external(1'b0),
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
