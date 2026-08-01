// Intentionally tiny non-RISC-V component used to prove the CPU replacement
// seam.  It performs exactly one aXbus store to the simulation finisher and
// never fetches or decodes an instruction.  It is a composition/bring-up
// example, not an alternative aXcore implementation or a supported software
// target.
module axcore #(
  parameter logic [31:0] RESET_PC = 32'h0000_1000
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
  input  logic        irq_s_external,

  // Part of the core contract.  This core retires WFI as a nop rather than
  // parking on it, which the spec permits, so it is never idle in the sense
  // the signal reports and says so honestly rather than guessing.
  output logic        cpu_idle,
  output logic        trace_valid,
  output logic        trace_trap,
  output logic [31:0] trace_insn
);
  logic done;

  always_comb begin
    ibus_valid = 1'b0;
    ibus_addr = RESET_PC;
    ibus_wdata = 32'b0;
    ibus_wstrb = 4'b0;
    dbus_valid = !done;
    dbus_addr = 32'h0010_0000;
    dbus_wdata = 32'h0000_5555;
    dbus_wstrb = 4'b1111;
    trace_valid = !done && dbus_ready;
    trace_trap = 1'b0;
    trace_insn = 32'h0000_0013;  // addi x0, x0, 0: harmless trace marker
  end

  always_ff @(posedge clk) begin
    if (rst) done <= 1'b0;
    else if (!done && dbus_ready) done <= 1'b1;
  end

  // Intentional no-op inputs make this small module useful as a template.
  // verilator lint_off UNUSED
  wire unused_inputs = ^{ibus_ready, ibus_rdata, ibus_err, dbus_rdata,
                         dbus_err, irq_software, irq_timer, irq_external,
                         irq_s_external};
  // verilator lint_on UNUSED
  assign cpu_idle = 1'b0;

endmodule
