// Shell-side isolation for the role window: the decoupling boundary a role
// swap needs (docs/partial-reconfig.md).
//
// Rewriting the fabric under a running shell means the role's aXbus slave
// stops being a slave for a while.  A role region that is half-configured can
// hold `ready` low forever, or drive it high with garbage, and either wedges
// the master that happens to be talking to it -- with no recovery except a
// full reprogram, which is exactly what live reconfiguration is meant to
// avoid.  This module is the fence: shell logic, placed with the shell and
// outside any reconfigurable region, that can be told to stop forwarding to
// the role and answer the bus itself.
//
// The register lives in *shell* address space rather than in the role window,
// because the role window is the thing being rewritten.  A control register
// inside the region it controls is unreachable at exactly the moment it is
// needed.
//
//   0x0000  SHELL_ID    RO     "aXSH"; reads as 0 on a shell without this device
//   0x0004  ISO_CTRL    R/W    bit0 ISOLATE, bit1 ROLE_RESET
//   0x0008  ISO_STATUS  RO     bit0 ISOLATED (the fence is in effect)
//
// While ISOLATE is set:
//
//   - the role sees no transactions at all (`valid` is held low into it), so a
//     partially configured region is never asked to respond;
//   - the bus sees `ready` asserted with zero data and no error, so no master
//     can stall on the window;
//   - the role's completion line is masked, so fabric coming up in an unknown
//     state cannot storm the PLIC with a level-sensitive interrupt nothing
//     will ever clear.
//
// Reads returning zero is deliberate rather than convenient: `ROLE_ID` reading
// zero already means "no role present" in the discovery contract every driver
// implements, so an isolated role is indistinguishable from `role.none` to
// software that follows it.  Isolation therefore needs no new software path --
// re-running discovery after a swap is the path.
//
// ISOLATE takes effect immediately and unconditionally.  Waiting for an
// in-flight transaction to retire first would be the polite thing to do and is
// precisely wrong here: the role this protects against is the one that has
// stopped answering, so a fence that waits for it deadlocks on the failure it
// exists to contain.  Quiescing is the driver's job and happens one level up,
// by polling the role's own STATUS.BUSY before isolating; the fence is the
// backstop for when that does not work.
module axroleiso #(
  parameter logic [31:0] BASE = 32'h1002_0000
) (
  input  logic        clk,
  input  logic        rst,

  // Control register: an ordinary aXbus slave in shell space.
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

  // Bus side of the role window, from the address decoders.
  input  logic        bus_i_valid,
  output logic        bus_i_ready,
  output logic [31:0] bus_i_rdata,
  output logic        bus_i_err,
  input  logic        bus_d_valid,
  output logic        bus_d_ready,
  output logic [31:0] bus_d_rdata,
  output logic        bus_d_err,

  // Role side of the role window.
  output logic        role_i_valid,
  input  logic        role_i_ready,
  input  logic [31:0] role_i_rdata,
  input  logic        role_i_err,
  output logic        role_d_valid,
  input  logic        role_d_ready,
  input  logic [31:0] role_d_rdata,
  input  logic        role_d_err,

  // Synchronous reset for the role region, so fabric that has just been
  // rewritten starts from a defined state rather than whatever the
  // configuration left in its flops.
  output logic        role_rst,

  // Completion line, masked while isolated.
  input  logic        role_irq_in,
  output logic        role_irq_out
);
  localparam logic [31:0] SHELL_ID   = 32'h6158_5348;  // "aXSH"
  localparam logic [15:0] OFF_ID     = 16'h0000;
  localparam logic [15:0] OFF_CTRL   = 16'h0004;
  localparam logic [15:0] OFF_STATUS = 16'h0008;

  logic isolate_q, role_rst_q;

  // ISO_CTRL defines two bits; the rest of a write is discarded rather than
  // reserved for later, so the upper lanes are deliberately unread.
  wire unused_wdata_bits = &{1'b0, i_wdata[31:2], d_wdata[31:2]};

  wire i_in_range = i_addr >= BASE && i_addr - BASE < 32'h0000_1000;
  wire d_in_range = d_addr >= BASE && d_addr - BASE < 32'h0000_1000;
  wire [15:0] i_off = i_addr[15:0];
  wire [15:0] d_off = d_addr[15:0];

  function automatic logic reg_offset(input logic [15:0] off);
    reg_offset = off == OFF_ID || off == OFF_CTRL || off == OFF_STATUS;
  endfunction
  function automatic logic [31:0] read_reg(input logic [15:0] off);
    unique case (off)
      OFF_ID:     read_reg = SHELL_ID;
      OFF_CTRL:   read_reg = {30'b0, role_rst_q, isolate_q};
      OFF_STATUS: read_reg = {31'b0, isolate_q};
      default:    read_reg = 32'b0;
    endcase
  endfunction

  // Control register access.  Both ports are decoded because every shell slave
  // presents the pair, but fetching from a control register is meaningless, so
  // the I port is served identically rather than specially.
  always_comb begin
    i_ready = i_valid;
    i_err   = i_valid && (!i_in_range || !reg_offset(i_off) || i_addr[1:0] != 2'b00);
    i_rdata = read_reg(i_off);
    d_ready = d_valid;
    d_err   = d_valid && (!d_in_range || !reg_offset(d_off) || d_addr[1:0] != 2'b00);
    d_rdata = read_reg(d_off);
  end

  // The fence itself.  Note there is no registered stage between bus and role:
  // adding one would change the role window's timing depending on whether the
  // shell was built with isolation, and the whole point is that a role sees an
  // identical window either way.
  always_comb begin
    role_i_valid = bus_i_valid && !isolate_q;
    role_d_valid = bus_d_valid && !isolate_q;
    if (isolate_q) begin
      bus_i_ready = bus_i_valid;
      bus_i_rdata = 32'b0;
      bus_i_err   = 1'b0;
      bus_d_ready = bus_d_valid;
      bus_d_rdata = 32'b0;
      bus_d_err   = 1'b0;
    end else begin
      bus_i_ready = role_i_ready;
      bus_i_rdata = role_i_rdata;
      bus_i_err   = role_i_err;
      bus_d_ready = role_d_ready;
      bus_d_rdata = role_d_rdata;
      bus_d_err   = role_d_err;
    end
  end

  assign role_irq_out = role_irq_in && !isolate_q;
  assign role_rst     = rst || role_rst_q;

  always_ff @(posedge clk) begin
    if (rst) begin
      // Out of reset the shell forwards to whatever role the image contains,
      // so a profile that never touches this register behaves exactly as it
      // did before the fence existed.
      isolate_q  <= 1'b0;
      role_rst_q <= 1'b0;
    end else begin
      // Same both-ports-in-one-cycle rule as the other shell devices: the D
      // port wins, because that is the port software writes from.
      if (i_valid && !i_err && |i_wstrb && i_off == OFF_CTRL && i_wstrb[0]) begin
        isolate_q  <= i_wdata[0];
        role_rst_q <= i_wdata[1];
      end
      if (d_valid && !d_err && |d_wstrb && d_off == OFF_CTRL && d_wstrb[0]) begin
        isolate_q  <= d_wdata[0];
        role_rst_q <= d_wdata[1];
      end
    end
  end
endmodule
