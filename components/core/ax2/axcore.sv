// core.ax2: the dual-issue in-order superscalar RV32IM machine-mode CPU.
//
// One component, tuned at build time.  The microarchitecture is fixed -- that
// is what makes it a component -- but every size in it is a knob, because
// issue width, cache capacity, and predictor depth are sizing decisions a user
// makes against their part and their workload, not different machines.  A new
// component is warranted when the architecture changes (a different pipeline, a
// different privilege model); it is not warranted for a different cache size.
//
// Parameters are declared in component.json with the defaults that define the
// baseline, and a profile overrides them by name:
//
//   "parameters": { "core": { "issue_width": 1, "icache_kb": 8 } }
//
// They arrive here as preprocessor defines so that a parameter can cross the
// stock `axcore` boundary, whose port and parameter list is shared with every
// other core and must not grow ax2-specific knobs.
//
// The defaults below are the fallback when the component is compiled outside
// the configuration flow (a bare Verilator lint, say); the manifest is the
// authority for a real build.
`ifndef AX2_ISSUE_WIDTH
  `define AX2_ISSUE_WIDTH 2
`endif
`ifndef AX2_ICACHE_KB
  `define AX2_ICACHE_KB 2
`endif
`ifndef AX2_BTB_ENTRIES
  `define AX2_BTB_ENTRIES 32
`endif

module axcore #(
  parameter logic [31:0] RESET_PC = 32'h8000_0000,
  parameter bit ENABLE_M = 1'b1
) (
  input  logic        clk,
  input  logic        rst,

  output logic        ibus_valid,
  output logic [31:0] ibus_addr,
  output logic [31:0] ibus_wdata,
  output logic [3:0]  ibus_wstrb,
  input  logic        ibus_ready,
  input  logic [31:0] ibus_rdata,
  input  logic        ibus_err,

  output logic        dbus_valid,
  output logic [31:0] dbus_addr,
  output logic [31:0] dbus_wdata,
  output logic [3:0]  dbus_wstrb,
  input  logic        dbus_ready,
  input  logic [31:0] dbus_rdata,
  input  logic        dbus_err,

  input  logic        irq_software,
  input  logic        irq_timer,
  input  logic        irq_external,
  // Part of the shell's core contract, but this is a machine-mode-only core:
  // there is no S-mode to deliver a supervisor external interrupt to, so the
  // line is accepted and ignored.
  /* verilator lint_off UNUSED */
  input  logic        irq_s_external,
  /* verilator lint_on UNUSED */

  output logic        trace_valid,
  output logic        trace_trap,
  output logic [31:0] trace_insn
);
  // RVFI is deliberately not part of the `core` component seam: the reference
  // core retires one instruction per cycle and ax2 retires two, so the two
  // traces have different channel counts and cannot share a port list.  The
  // formal environment instantiates ax2_core directly to reach them.
  /* verilator lint_off PINCONNECTEMPTY */
  ax2_core #(
    .RESET_PC(RESET_PC),
    .ENABLE_M(ENABLE_M),
    .ISSUE_WIDTH(`AX2_ISSUE_WIDTH),
    .ICACHE_KB(`AX2_ICACHE_KB),
    .BTB_ENTRIES(`AX2_BTB_ENTRIES)
  ) u_core (
    .rvfi_valid(), .rvfi_order(), .rvfi_insn(), .rvfi_trap(), .rvfi_halt(),
    .rvfi_intr(), .rvfi_mode(), .rvfi_ixl(),
    .rvfi_rs1_addr(), .rvfi_rs2_addr(), .rvfi_rs1_rdata(), .rvfi_rs2_rdata(),
    .rvfi_rd_addr(), .rvfi_rd_wdata(), .rvfi_pc_rdata(), .rvfi_pc_wdata(),
    .rvfi_mem_addr(), .rvfi_mem_rmask(), .rvfi_mem_wmask(),
    .rvfi_mem_rdata(), .rvfi_mem_wdata(),
    .*
  );
  /* verilator lint_on PINCONNECTEMPTY */
endmodule
