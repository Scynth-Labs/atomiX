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
`ifndef AX_LIVE_MONITOR
  `define AX_LIVE_MONITOR 1
`endif
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
  output logic        role_irq_out,

  // Explicit Live FPGA events. These stay separate from generic bus errors:
  // a malformed MMIO access is not automatically an adaptive rejection, and
  // a real watchdog will remain shell-owned when it is connected here.
  input  logic        role_reject_event,
  input  logic        watchdog_event
);
  localparam logic [31:0] SHELL_ID       = 32'h6158_5348;  // "aXSH"
  localparam logic [31:0] LIVE_ID        = 32'h6158_4c56;  // "aXLV"
  localparam logic [31:0] LIVE_VERSION   = 32'h0001_0000;
  localparam logic [31:0] LIVE_SNAPSHOT  = 32'd1;
  localparam logic [31:0] LIVE_ACTIVATE  = 32'd2;
  localparam logic [15:0] OFF_ID         = 16'h0000;
  localparam logic [15:0] OFF_CTRL       = 16'h0004;
  localparam logic [15:0] OFF_STATUS     = 16'h0008;
  localparam logic [15:0] OFF_LIVE_ID    = 16'h0100;
  localparam logic [15:0] OFF_LIVE_VER   = 16'h0104;
  localparam logic [15:0] OFF_LIVE_CMD   = 16'h0108;
  localparam logic [15:0] OFF_LIVE_SEQ   = 16'h010c;
  localparam logic [15:0] OFF_CYCLES_LO  = 16'h0110;
  localparam logic [15:0] OFF_CYCLES_HI  = 16'h0114;
  localparam logic [15:0] OFF_WORK_LO    = 16'h0118;
  localparam logic [15:0] OFF_WORK_HI    = 16'h011c;
  localparam logic [15:0] OFF_STALL_LO   = 16'h0120;
  localparam logic [15:0] OFF_STALL_HI   = 16'h0124;
  localparam logic [15:0] OFF_REJECT_LO  = 16'h0128;
  localparam logic [15:0] OFF_REJECT_HI  = 16'h012c;
  localparam logic [15:0] OFF_WATCH_LO   = 16'h0130;
  localparam logic [15:0] OFF_WATCH_HI   = 16'h0134;
  localparam logic [15:0] OFF_GEN_LO     = 16'h0138;
  localparam logic [15:0] OFF_GEN_HI     = 16'h013c;

  logic isolate_q, role_rst_q, role_irq_q;
  logic [31:0] live_sequence;
  logic [63:0] live_cycles;
  logic [63:0] live_work_completed;
  logic [63:0] live_memory_stalls;
  logic [63:0] live_descriptor_rejections;
  logic [63:0] live_watchdog_events;
  logic [63:0] live_configuration_generation;

  // ISO_CTRL defines two bits; the rest of a write is discarded rather than
  // reserved for later, so the upper lanes are deliberately unread.
  wire unused_wdata_bits = &{1'b0, i_wdata[31:2], d_wdata[31:2]};

  wire i_in_range = i_addr >= BASE && i_addr - BASE < 32'h0000_1000;
  wire d_in_range = d_addr >= BASE && d_addr - BASE < 32'h0000_1000;
  wire [15:0] i_off = i_addr[15:0];
  wire [15:0] d_off = d_addr[15:0];

  function automatic logic live_command(input logic [31:0] value);
    live_command = value == LIVE_SNAPSHOT || value == LIVE_ACTIVATE;
  endfunction

  // Gating only the axlivemon instance is not enough: the decode below, the
  // command validation, and the 64-bit read muxes are themselves ~550 LUT4 on
  // a GW5A-25, which is what pushed role.tpu-lite past its placement limit.
  // LIVE_MON folds all of it out when the profile opts out.
  localparam bit LIVE_MON = (`AX_LIVE_MONITOR != 0);

  function automatic logic reg_offset(input logic [15:0] off);
    reg_offset = off == OFF_ID || off == OFF_CTRL || off == OFF_STATUS ||
                 LIVE_MON && (off == OFF_LIVE_ID || off == OFF_LIVE_VER ||
                 off == OFF_LIVE_CMD || off == OFF_LIVE_SEQ ||
                 off == OFF_CYCLES_LO || off == OFF_CYCLES_HI ||
                 off == OFF_WORK_LO || off == OFF_WORK_HI ||
                 off == OFF_STALL_LO || off == OFF_STALL_HI ||
                 off == OFF_REJECT_LO || off == OFF_REJECT_HI ||
                 off == OFF_WATCH_LO || off == OFF_WATCH_HI ||
                 off == OFF_GEN_LO || off == OFF_GEN_HI);
  endfunction
  function automatic logic [31:0] read_reg(input logic [15:0] off);
    unique case (off)
      OFF_ID:        read_reg = SHELL_ID;
      OFF_CTRL:      read_reg = {30'b0, role_rst_q, isolate_q};
      OFF_STATUS:    read_reg = {31'b0, isolate_q};
      OFF_LIVE_ID:   read_reg = LIVE_ID;
      OFF_LIVE_VER:  read_reg = LIVE_VERSION;
      OFF_LIVE_SEQ:  read_reg = live_sequence;
      OFF_CYCLES_LO: read_reg = live_cycles[31:0];
      OFF_CYCLES_HI: read_reg = live_cycles[63:32];
      OFF_WORK_LO:   read_reg = live_work_completed[31:0];
      OFF_WORK_HI:   read_reg = live_work_completed[63:32];
      OFF_STALL_LO:  read_reg = live_memory_stalls[31:0];
      OFF_STALL_HI:  read_reg = live_memory_stalls[63:32];
      OFF_REJECT_LO: read_reg = live_descriptor_rejections[31:0];
      OFF_REJECT_HI: read_reg = live_descriptor_rejections[63:32];
      OFF_WATCH_LO:  read_reg = live_watchdog_events[31:0];
      OFF_WATCH_HI:  read_reg = live_watchdog_events[63:32];
      OFF_GEN_LO:    read_reg = live_configuration_generation[31:0];
      OFF_GEN_HI:    read_reg = live_configuration_generation[63:32];
      default:       read_reg = 32'b0;
    endcase
  endfunction

  wire i_bad_live_command = LIVE_MON && i_off == OFF_LIVE_CMD && |i_wstrb &&
                            (i_wstrb != 4'hf || !live_command(i_wdata));
  wire d_bad_live_command = LIVE_MON && d_off == OFF_LIVE_CMD && |d_wstrb &&
                            (d_wstrb != 4'hf || !live_command(d_wdata));

  // Control register access.  Both ports are decoded because every shell slave
  // presents the pair, but fetching from a control register is meaningless, so
  // the I port is served identically rather than specially.
  always_comb begin
    i_ready = i_valid;
    i_err   = i_valid && (!i_in_range || !reg_offset(i_off) ||
                          i_addr[1:0] != 2'b00 || i_bad_live_command);
    i_rdata = read_reg(i_off);
    d_ready = d_valid;
    d_err   = d_valid && (!d_in_range || !reg_offset(d_off) ||
                          d_addr[1:0] != 2'b00 || d_bad_live_command);
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

  wire live_snapshot_event =
      (i_valid && !i_err && i_off == OFF_LIVE_CMD && |i_wstrb &&
       i_wdata == LIVE_SNAPSHOT) ||
      (d_valid && !d_err && d_off == OFF_LIVE_CMD && |d_wstrb &&
       d_wdata == LIVE_SNAPSHOT);
  wire live_activation_event =
      (i_valid && !i_err && i_off == OFF_LIVE_CMD && |i_wstrb &&
       i_wdata == LIVE_ACTIVATE) ||
      (d_valid && !d_err && d_off == OFF_LIVE_CMD && |d_wstrb &&
       d_wdata == LIVE_ACTIVATE);
  wire live_work_event = role_irq_in && !role_irq_q && !isolate_q;
  wire live_stall_event = !isolate_q &&
      ((bus_i_valid && !role_i_ready) || (bus_d_valid && !role_d_ready));

  // The telemetry counters are optional; the fence is not.  A profile that
  // sets AX_LIVE_MONITOR to 0 keeps ISO_CTRL/ISO_STATUS and the monitor's
  // register window — reads simply return zero — so software needs no
  // separate address map and role swapping is unaffected.
generate if (LIVE_MON) begin : g_livemon
  axlivemon u_livemon (
    .clk(clk), .rst(rst),
    .snapshot_event(live_snapshot_event),
    .work_completed_event(live_work_event),
    .memory_stall_event(live_stall_event),
    .descriptor_rejected_event(role_reject_event),
    .watchdog_event(watchdog_event),
    .configuration_activated_event(live_activation_event),
    .snapshot_sequence(live_sequence),
    .snapshot_cycles(live_cycles),
    .snapshot_work_completed(live_work_completed),
    .snapshot_memory_stalls(live_memory_stalls),
    .snapshot_descriptor_rejections(live_descriptor_rejections),
    .snapshot_watchdog_events(live_watchdog_events),
    .snapshot_configuration_generation(live_configuration_generation)
  );
end else begin : g_no_livemon
  // Tie the snapshot outputs off and absorb the event lines so the fence and
  // its register decode are unchanged.
  assign live_sequence                 = 64'b0;
  assign live_cycles                   = 64'b0;
  assign live_work_completed           = 64'b0;
  assign live_memory_stalls            = 64'b0;
  assign live_descriptor_rejections    = 64'b0;
  assign live_watchdog_events          = 64'b0;
  assign live_configuration_generation = 64'b0;
  wire _unused_live = &{1'b0, live_snapshot_event, live_work_event,
                        live_stall_event, role_reject_event, watchdog_event,
                        live_activation_event, 1'b0};
end
endgenerate

  always_ff @(posedge clk) begin
    if (rst) begin
      // Out of reset the shell forwards to whatever role the image contains,
      // so a profile that never touches this register behaves exactly as it
      // did before the fence existed.
      isolate_q  <= 1'b0;
      role_rst_q <= 1'b0;
      role_irq_q <= 1'b0;
    end else begin
      // Track the raw line even while isolated. De-isolating a role that is
      // already asserting DONE must not manufacture a fresh completion edge.
      role_irq_q <= role_irq_in;
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
