// Shell-owned CPU progress monitor.  Entirely optional: the whole file is
// empty unless AX_PROGRESS_MONITOR is defined, so a profile that declines it
// compiles the identical text it compiled before, and no board build changes.
//
// It exists for one question a cycle limit cannot answer.  When a workload
// stops finishing -- as ELF exec does on the SDRAM pin model -- "it needed
// more cycles" is not a diagnosis, and the three plausible causes want three
// different fixes:
//
//   interrupt starvation   the hart keeps entering the trap handler and never
//                          gets back to user code: user retirements stall
//                          while handler entries climb.
//   a stalled transaction  the hart is waiting on the bus: stall cycles
//                          dominate and retirements of every mode stop.
//   slow useful work       everything advances, just far more slowly per
//                          cycle than on-chip RAM.
//
// Each leaves a different signature here, and each counter is a count of an
// event the machine already produced -- nothing is inferred from a name.
`ifdef AX_PROGRESS_MONITOR
module axprogmon (
  input  logic        clk,
  input  logic        rst,

  // The core's commit trace.  `trace_valid` marks a commit; `trace_trap` says
  // that commit took an exception rather than retiring.  `trace_prv` is the
  // privilege the committed instruction ran in, which is what makes user
  // progress separable from handler progress.
  input  logic        trace_valid,
  input  logic        trace_trap,
  input  logic [31:0] trace_pc,
  input  logic [1:0]  trace_prv,
  input  logic [31:0] trace_mip,
  input  logic [31:0] trace_mie,

  // The timer line as the core sees it, and the two bus ports as the core
  // drives them: a cycle with valid high and ready low is a cycle the hart
  // spent waiting for memory.
  input  logic        irq_timer,
  input  logic        ibus_valid,
  input  logic        ibus_ready,
  input  logic        dbus_valid,
  input  logic        dbus_ready,
  input  logic        cpu_idle,

  output logic [63:0] cycles,
  output logic [63:0] retired,
  output logic [63:0] retired_user,
  output logic [63:0] retired_supervisor,
  output logic [63:0] retired_machine,
  output logic [63:0] exceptions,
  // A commit in a more privileged mode than the previous commit, and the
  // reverse.  Together they are handler entry and exit as the architecture
  // defines it, without the monitor needing to know a single kernel address.
  output logic [63:0] user_exits,
  output logic [63:0] user_entries,
  output logic [63:0] timer_arrivals,
  output logic [63:0] timer_pending_cycles,
  output logic [63:0] idle_cycles,
  output logic [63:0] ifetch_stall_cycles,
  output logic [63:0] dmem_stall_cycles,
  output logic [31:0] last_pc,
  output logic [31:0] last_mip,
  output logic [31:0] last_mie,
  output logic [1:0]  last_prv
);
  localparam logic [1:0] PRV_U = 2'b00;

  logic irq_timer_q;
  logic [1:0] prv_q;
  logic prv_valid_q;

  wire commit  = trace_valid;
  wire retires = trace_valid && !trace_trap;
  // Only across two observed commits: the mode before the first commit of a
  // run is not a transition anyone made.
  wire changed = commit && prv_valid_q && trace_prv != prv_q;

  always_ff @(posedge clk) begin
    if (rst) begin
      cycles               <= 64'b0;
      retired              <= 64'b0;
      retired_user         <= 64'b0;
      retired_supervisor   <= 64'b0;
      retired_machine      <= 64'b0;
      exceptions           <= 64'b0;
      user_exits           <= 64'b0;
      user_entries         <= 64'b0;
      timer_arrivals       <= 64'b0;
      timer_pending_cycles <= 64'b0;
      idle_cycles          <= 64'b0;
      ifetch_stall_cycles  <= 64'b0;
      dmem_stall_cycles    <= 64'b0;
      last_pc              <= 32'b0;
      last_mip             <= 32'b0;
      last_mie             <= 32'b0;
      last_prv             <= PRV_U;
      irq_timer_q          <= 1'b0;
      prv_q                <= PRV_U;
      prv_valid_q          <= 1'b0;
    end else begin
      cycles <= cycles + 64'd1;
      if (retires) begin
        retired <= retired + 64'd1;
        case (trace_prv)
          2'b00: retired_user       <= retired_user + 64'd1;
          2'b01: retired_supervisor <= retired_supervisor + 64'd1;
          default: retired_machine  <= retired_machine + 64'd1;
        endcase
      end
      if (commit && trace_trap) exceptions <= exceptions + 64'd1;
      if (changed && prv_q == PRV_U)     user_exits   <= user_exits + 64'd1;
      if (changed && trace_prv == PRV_U) user_entries <= user_entries + 64'd1;
      if (commit) begin
        prv_q       <= trace_prv;
        prv_valid_q <= 1'b1;
        last_pc     <= trace_pc;
        last_prv    <= trace_prv;
      end
      last_mip <= trace_mip;
      last_mie <= trace_mie;

      if (irq_timer && !irq_timer_q) timer_arrivals <= timer_arrivals + 64'd1;
      if (irq_timer) timer_pending_cycles <= timer_pending_cycles + 64'd1;
      irq_timer_q <= irq_timer;

      if (cpu_idle) idle_cycles <= idle_cycles + 64'd1;
      if (ibus_valid && !ibus_ready)
        ifetch_stall_cycles <= ifetch_stall_cycles + 64'd1;
      if (dbus_valid && !dbus_ready)
        dmem_stall_cycles <= dmem_stall_cycles + 64'd1;
    end
  end
endmodule
`endif
