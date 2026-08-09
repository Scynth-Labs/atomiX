// Shell-owned telemetry counter bank for the Live FPGA research track.
//
// This module deliberately knows nothing about an accelerator ISA, FPGA
// configuration format, or bus protocol.  The immutable shell converts its
// observations into one-cycle events and maps the coherent snapshot outputs
// into its control page.  Keeping the counters outside the changing role means
// their history survives a personality replacement.
//
// Snapshot semantics are precise: a snapshot taken on a rising edge includes
// that edge's cycle and all event inputs asserted for that edge.  Live counters
// continue after the copy.  All counters wrap modulo 2^64; snapshot_sequence
// wraps modulo 2^32.  Consumers compute deltas with the same modular arithmetic.
module axlivemon (
  input  logic        clk,
  input  logic        rst,

  input  logic        snapshot_event,
  input  logic        work_completed_event,
  input  logic        memory_stall_event,
  input  logic        descriptor_rejected_event,
  input  logic        watchdog_event,
  input  logic        configuration_activated_event,

  output logic [31:0] snapshot_sequence,
  output logic [63:0] snapshot_cycles,
  output logic [63:0] snapshot_work_completed,
  output logic [63:0] snapshot_memory_stalls,
  output logic [63:0] snapshot_descriptor_rejections,
  output logic [63:0] snapshot_watchdog_events,
  output logic [63:0] snapshot_configuration_generation
);
  logic [63:0] cycles_q;
  logic [63:0] work_completed_q;
  logic [63:0] memory_stalls_q;
  logic [63:0] descriptor_rejections_q;
  logic [63:0] watchdog_events_q;
  logic [63:0] configuration_generation_q;

  wire [63:0] cycles_next = cycles_q + 64'd1;
  wire [63:0] work_completed_next =
      work_completed_q + {63'b0, work_completed_event};
  wire [63:0] memory_stalls_next =
      memory_stalls_q + {63'b0, memory_stall_event};
  wire [63:0] descriptor_rejections_next =
      descriptor_rejections_q + {63'b0, descriptor_rejected_event};
  wire [63:0] watchdog_events_next =
      watchdog_events_q + {63'b0, watchdog_event};
  wire [63:0] configuration_generation_next =
      configuration_generation_q + {63'b0, configuration_activated_event};

  always_ff @(posedge clk) begin
    if (rst) begin
      cycles_q                         <= 64'b0;
      work_completed_q                 <= 64'b0;
      memory_stalls_q                  <= 64'b0;
      descriptor_rejections_q          <= 64'b0;
      watchdog_events_q                <= 64'b0;
      configuration_generation_q       <= 64'b0;
      snapshot_sequence                <= 32'b0;
      snapshot_cycles                  <= 64'b0;
      snapshot_work_completed          <= 64'b0;
      snapshot_memory_stalls            <= 64'b0;
      snapshot_descriptor_rejections    <= 64'b0;
      snapshot_watchdog_events          <= 64'b0;
      snapshot_configuration_generation <= 64'b0;
    end else begin
      cycles_q                   <= cycles_next;
      work_completed_q           <= work_completed_next;
      memory_stalls_q            <= memory_stalls_next;
      descriptor_rejections_q    <= descriptor_rejections_next;
      watchdog_events_q          <= watchdog_events_next;
      configuration_generation_q <= configuration_generation_next;

      if (snapshot_event) begin
        snapshot_sequence                 <= snapshot_sequence + 32'd1;
        snapshot_cycles                   <= cycles_next;
        snapshot_work_completed           <= work_completed_next;
        snapshot_memory_stalls             <= memory_stalls_next;
        snapshot_descriptor_rejections     <= descriptor_rejections_next;
        snapshot_watchdog_events           <= watchdog_events_next;
        snapshot_configuration_generation <= configuration_generation_next;
      end
    end
  end
endmodule
