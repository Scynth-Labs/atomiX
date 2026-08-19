`ifndef COMPOSITE_GPU_LANES
  `define COMPOSITE_GPU_LANES 1
`endif
`ifndef COMPOSITE_GPU_DATA_WORDS
  `define COMPOSITE_GPU_DATA_WORDS 256
`endif

// Composite hard-role experiment: keep gpu-compute and TPU-lite resident and
// select which native programming model owns the fixed role window.  Existing
// GPU and TPU drivers continue to see their original ROLE_ID and offsets.
// Composite-only discovery/control lives at the unused end of the 64 KiB role
// window:
//
//   0xfff0  COMPOSITE_ID       RO  "GTPC"
//   0xfff4  COMPOSITE_VERSION  RO  1
//   0xfff8  COMPOSITE_CAPS     RO  bit0 GPU, bit1 TPU
//   0xfffc  SELECT             RW  0 GPU, 1 TPU
//
// A personality switch is legal only when neither engine is executing and
// neither DONE interrupt remains asserted.  This prevents software from
// hiding an in-flight engine or losing its completion by changing the mux.
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
  , output logic      reject_event
`endif
);
  localparam logic [31:0] COMPOSITE_ID      = 32'h4754_5043; // "GTPC"
  localparam logic [31:0] COMPOSITE_VERSION = 32'h0000_0001;
  localparam logic [31:0] COMPOSITE_CAPS    = 32'h0000_0003;
  localparam logic [15:0] OFF_COMPOSITE_ID  = 16'hfff0;
  localparam logic [15:0] OFF_VERSION       = 16'hfff4;
  localparam logic [15:0] OFF_CAPS          = 16'hfff8;
  localparam logic [15:0] OFF_SELECT        = 16'hfffc;
  localparam logic [15:0] OFF_DOORBELL      = 16'h0008;

  logic select_q;
  logic gpu_executing_q, tpu_executing_q;

  wire [15:0] i_off = i_addr[15:0];
  wire [15:0] d_off = d_addr[15:0];
  wire i_meta = i_valid && (i_off == OFF_COMPOSITE_ID ||
                            i_off == OFF_VERSION || i_off == OFF_CAPS ||
                            i_off == OFF_SELECT) && i_addr[1:0] == 2'b00;
  wire d_meta = d_valid && (d_off == OFF_COMPOSITE_ID ||
                            d_off == OFF_VERSION || d_off == OFF_CAPS ||
                            d_off == OFF_SELECT) && d_addr[1:0] == 2'b00;

  logic gpu_i_ready, gpu_i_err, gpu_d_ready, gpu_d_err, gpu_irq;
  logic tpu_i_ready, tpu_i_err, tpu_d_ready, tpu_d_err, tpu_irq;
  logic [31:0] gpu_i_rdata, gpu_d_rdata, tpu_i_rdata, tpu_d_rdata;
`ifdef AX_LIVE_ROLE_EVENTS
  logic tpu_reject_unused;
`endif

  wire gpu_i_valid = i_valid && !i_meta && !select_q;
  wire gpu_d_valid = d_valid && !d_meta && !select_q;
  wire tpu_i_valid = i_valid && !i_meta && select_q;
  wire tpu_d_valid = d_valid && !d_meta && select_q;

  gpu_engine #(
    .BASE(BASE),
    .NLANES(`COMPOSITE_GPU_LANES),
    .DATA_WORDS(`COMPOSITE_GPU_DATA_WORDS)
  ) u_gpu (
    .clk, .rst,
    .i_valid(gpu_i_valid), .i_addr, .i_wdata, .i_wstrb,
    .i_ready(gpu_i_ready), .i_rdata(gpu_i_rdata), .i_err(gpu_i_err),
    .d_valid(gpu_d_valid), .d_addr, .d_wdata, .d_wstrb,
    .d_ready(gpu_d_ready), .d_rdata(gpu_d_rdata), .d_err(gpu_d_err),
    .irq(gpu_irq)
  );

  tpu_lite_engine #(.BASE(BASE)) u_tpu (
    .clk, .rst,
    .i_valid(tpu_i_valid), .i_addr, .i_wdata, .i_wstrb,
    .i_ready(tpu_i_ready), .i_rdata(tpu_i_rdata), .i_err(tpu_i_err),
    .d_valid(tpu_d_valid), .d_addr, .d_wdata, .d_wstrb,
    .d_ready(tpu_d_ready), .d_rdata(tpu_d_rdata), .d_err(tpu_d_err),
    .irq(tpu_irq)
`ifdef AX_LIVE_ROLE_EVENTS
    , .reject_event(tpu_reject_unused)
`endif
  );

  function automatic logic [31:0] metadata(input logic [15:0] off);
    unique case (off)
      OFF_COMPOSITE_ID: metadata = COMPOSITE_ID;
      OFF_VERSION:      metadata = COMPOSITE_VERSION;
      OFF_CAPS:         metadata = COMPOSITE_CAPS;
      OFF_SELECT:       metadata = {31'b0, select_q};
      default:          metadata = 32'b0;
    endcase
  endfunction

  wire switch_blocked = gpu_executing_q || tpu_executing_q || gpu_irq || tpu_irq;
  wire i_select_write = i_meta && i_off == OFF_SELECT && |i_wstrb;
  wire d_select_write = d_meta && d_off == OFF_SELECT && |d_wstrb;
  wire i_select_bad = i_select_write &&
      (i_wstrb != 4'hf || i_wdata > 32'd1 ||
       (i_wdata[0] != select_q && switch_blocked));
  wire d_select_bad = d_select_write &&
      (d_wstrb != 4'hf || d_wdata > 32'd1 ||
       (d_wdata[0] != select_q && switch_blocked));

  always_comb begin
    if (i_meta) begin
      i_ready = 1'b1;
      i_rdata = metadata(i_off);
      i_err   = i_select_bad ||
                (|i_wstrb && i_off != OFF_SELECT);
    end else if (select_q) begin
      i_ready = tpu_i_ready;
      i_rdata = tpu_i_rdata;
      i_err   = tpu_i_err;
    end else begin
      i_ready = gpu_i_ready;
      i_rdata = gpu_i_rdata;
      i_err   = gpu_i_err;
    end

    if (d_meta) begin
      d_ready = 1'b1;
      d_rdata = metadata(d_off);
      d_err   = d_select_bad ||
                (|d_wstrb && d_off != OFF_SELECT);
    end else if (select_q) begin
      d_ready = tpu_d_ready;
      d_rdata = tpu_d_rdata;
      d_err   = tpu_d_err;
    end else begin
      d_ready = gpu_d_ready;
      d_rdata = gpu_d_rdata;
      d_err   = gpu_d_err;
    end
  end

  assign irq = select_q ? tpu_irq : gpu_irq;

  always_ff @(posedge clk) begin
    if (rst) begin
      select_q        <= 1'b0;
      gpu_executing_q <= 1'b0;
      tpu_executing_q <= 1'b0;
`ifdef AX_LIVE_ROLE_EVENTS
      reject_event    <= 1'b0;
`endif
    end else begin
`ifdef AX_LIVE_ROLE_EVENTS
      reject_event <= i_select_bad || d_select_bad;
`endif
      if (gpu_irq) gpu_executing_q <= 1'b0;
      if (tpu_irq) tpu_executing_q <= 1'b0;

      if (!select_q && ((gpu_i_valid && i_off == OFF_DOORBELL &&
                         |i_wstrb && gpu_i_ready && !gpu_i_err) ||
                        (gpu_d_valid && d_off == OFF_DOORBELL &&
                         |d_wstrb && gpu_d_ready && !gpu_d_err)))
        gpu_executing_q <= 1'b1;
      if (select_q && ((tpu_i_valid && i_off == OFF_DOORBELL &&
                        |i_wstrb && tpu_i_ready && !tpu_i_err) ||
                       (tpu_d_valid && d_off == OFF_DOORBELL &&
                        |d_wstrb && tpu_d_ready && !tpu_d_err)))
        tpu_executing_q <= 1'b1;

      if (i_select_write && !i_select_bad) select_q <= i_wdata[0];
      if (d_select_write && !d_select_bad) select_q <= d_wdata[0];
    end
  end
endmodule
