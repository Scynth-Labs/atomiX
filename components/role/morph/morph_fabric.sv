// Morph fabric: a coarse-grained reconfigurable array in the role window.
//
// R2 asks how much scalar, SIMT, and systolic work one resident datapath can
// support before its flexibility costs more than separate hard roles.  This is
// the smallest fabric that can answer that honestly: NPE processing elements
// with a configurable fused operation, configurable local routing between
// them, per-PE accumulator state, and a *bounded* configuration memory.
// Changing personality writes CFG_WORDS words and rings the doorbell.  No FPGA
// configuration bit changes, so a switch costs a descriptor write rather than
// a bitstream load.
//
// Every PE computes the single fused form
//
//     out = (a + b) * c + d
//
// where a, b, c and d each come from an independently configured source mux.
// That one shape covers all three personalities:
//
//   scalar recurrence   acc' = (acc + x[i]) * mult + inc
//   SIMT SAXPY          y[i] = (x[i] +  0 ) * a    + y[i]
//   systolic dot step   acc' = (a[k] +  0 ) * b[k] + acc
//
// so the personality lives entirely in the genome, not in the fabric.  It is
// also the shape a GW5A DSP block implements natively (pre-adder, multiplier,
// post-adder), which is what makes the area comparison against the hard GPU
// and TPU roles meaningful.
//
// The shell owns everything outside this window.  The fabric has no path to
// shell state: it is a slave on its two aXbus ports, its engine addresses are
// truncated into its own buffer, and its sequencer is bounded by the latched
// M/N/K dimensions.  A descriptor that fails any acceptance rule is rejected
// *before* BUSY rises, leaving the configuration, the buffer, and the previous
// result untouched.
//
// Window layout (common role header per DESIGN.md 3.3 first):
//
//   0x0000  ROLE_ID     RO  "MRPH"
//   0x0004  VERSION     RO  fabric programming-model revision
//   0x0008  DOORBELL    WO  any write starts a job when idle
//   0x000c  STATUS      R/W1C  bit0 BUSY, bit1 DONE, bit2 REJECTED
//   0x0010  NITEMS      R/W  advisory work-item count, latched at the doorbell
//   0x0014  NCONFIG     R/W  configuration words the host claims to have
//                       written; must equal CFG_WORDS or the job is rejected
//   0x0018  COUNT       RO   completed-job counter
//   0x001c  CAPS        RO   {8'NPE, 8'CFG_WORDS, 16'DATA_WORDS}
//   0x0020  GENERATION  RO   configuration generation; +1 per accepted job
//   0x0024  REJECTS     RO   descriptor-rejection counter
//   0x0100  configuration memory, CFG_WORDS words (the bounded genome)
//   0x1000  data buffer, DATA_WORDS 32-bit words, word-addressed
//
// Genome:
//
//   cfg[0]  MODE   {28'0, mode[3:0]}   0 scalar, 1 SIMT, 2 systolic
//   cfg[1]  DIMS   {n[15:0], m[15:0]}
//   cfg[2]  KDIM   {16'0, k[15:0]}
//   cfg[3]  A      {a_row_stride[15:0], a_base[15:0]}
//   cfg[4]  AK     {a_col_stride[15:0], a_k_stride[15:0]}
//   cfg[5]  B      {b_col_stride[15:0], b_base[15:0]}
//   cfg[6]  BK     {16'0, b_k_stride[15:0]}
//   cfg[7]  C      {c_row_stride[15:0], c_base[15:0]}
//   cfg[8]  IMM0   32-bit immediate (recurrence multiplier / SAXPY a)
//   cfg[9]  IMM1   32-bit immediate (recurrence increment)
//   cfg[10] PEOPS0 {4'0, pe1[13:0], pe0[13:0]}
//   cfg[11] PEOPS1 {4'0, pe3[13:0], pe2[13:0]}
//   cfg[12] ACCINIT accumulator preload applied at the doorbell
//
// PE descriptor (14 bits): {accrule[1:0], srcd[2:0], srcc[2:0], srcb[2:0],
// srca[2:0]}.  Source mux: 0 STREAM_A, 1 STREAM_B, 2 ACC, 3 IMM0, 4 IMM1,
// 5 ZERO, 6 ONE, 7 CHAIN (the left neighbour's accumulator).  Accumulator
// rule: 0 HOLD, 1 LOAD.
//
// Loop structure, identical in every mode:
//
//   for row in 0..m-1: for col in 0..n-1: for kk in 0..k-1: step; store C
//
// Lanes map onto `col` in SIMT mode and onto `kk` in systolic mode, and the
// scalar personality runs a single lane so its loop-carried dependency is
// respected.
module morph_fabric #(
  parameter logic [31:0] BASE = 32'h4000_0000,
  parameter int unsigned NPE = 4,
  parameter int unsigned DATA_WORDS = 256
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
  ,

  // One-cycle pulse per refused descriptor, for the shell's Live FPGA
  // telemetry (docs/live-fpga.md).  The role already counts these in REJECTS;
  // the shell needs its own view because a role being reconfigured is exactly
  // the component whose self-reported registers may be about to disappear.
  output logic        reject_event
`endif
);
  localparam logic [31:0] ROLE_ID      = 32'h4D52_5048;  // "MRPH"
  localparam logic [31:0] ROLE_VERSION = 32'h0000_0001;

  localparam logic [15:0] OFF_ID         = 16'h0000;
  localparam logic [15:0] OFF_VERSION    = 16'h0004;
  localparam logic [15:0] OFF_DOORBELL   = 16'h0008;
  localparam logic [15:0] OFF_STATUS     = 16'h000c;
  localparam logic [15:0] OFF_NITEMS     = 16'h0010;
  localparam logic [15:0] OFF_NCONFIG    = 16'h0014;
  localparam logic [15:0] OFF_COUNT      = 16'h0018;
  localparam logic [15:0] OFF_CAPS       = 16'h001c;
  localparam logic [15:0] OFF_GENERATION = 16'h0020;
  localparam logic [15:0] OFF_REJECTS    = 16'h0024;
  localparam logic [15:0] CFG_BASE       = 16'h0100;
  localparam logic [15:0] DATA_BASE      = 16'h1000;

  localparam int unsigned CFG_WORDS = 13;
  localparam int unsigned CFG_BITS  = $clog2(CFG_WORDS);
  localparam int unsigned ADDR_BITS = $clog2(DATA_WORDS);
  localparam int unsigned LANE_BITS = (NPE <= 1) ? 1 : $clog2(NPE);

  localparam logic [3:0] MODE_SCALAR = 4'd0, MODE_SIMT = 4'd1,
                         MODE_SYSTOLIC = 4'd2;
  localparam logic [2:0] SRC_STREAM_A = 3'd0, SRC_STREAM_B = 3'd1,
                         SRC_ACC = 3'd2, SRC_IMM0 = 3'd3, SRC_IMM1 = 3'd4,
                         SRC_ZERO = 3'd5, SRC_ONE = 3'd6, SRC_CHAIN = 3'd7;
  localparam logic [1:0] ACC_LOAD = 2'd1;

  // ----------------------------------------------------------------- state
  logic [31:0] cfg  [0:CFG_WORDS-1];
  logic [31:0] dmem [0:DATA_WORDS-1];
  logic [31:0] acc  [0:NPE-1];
  logic [31:0] str_a [0:NPE-1];
  logic [31:0] str_b [0:NPE-1];

  logic [31:0] nitems_q, nconfig_q, count_q, generation_q, rejects_q;
  logic        busy_q, done_q, rejected_q;

  logic [3:0]  job_mode_q;
  logic [15:0] job_m_q, job_n_q, job_k_q;
  logic [31:0] acc_init_q;
  logic [15:0] row_q, col_q, kk_q;
  logic [LANE_BITS:0] lane_q;

  typedef enum logic [3:0] {
    S_IDLE, S_LOAD_A, S_CAP_A, S_LOAD_B, S_CAP_B, S_EXEC, S_KNEXT, S_REDUCE,
    S_STORE, S_CNEXT, S_DONE
  } state_e;
  state_e state_q;

  // ------------------------------------------------------------- genome
  wire [3:0]  req_mode = cfg[0][3:0];
  wire [15:0] req_m = cfg[1][15:0],  req_n = cfg[1][31:16];
  wire [15:0] req_k = cfg[2][15:0];
  wire [15:0] a_base = cfg[3][15:0], a_row_stride = cfg[3][31:16];
  wire [15:0] a_k_stride = cfg[4][15:0], a_col_stride = cfg[4][31:16];
  wire [15:0] b_base = cfg[5][15:0], b_col_stride = cfg[5][31:16];
  wire [15:0] b_k_stride = cfg[6][15:0];
  wire [15:0] c_base = cfg[7][15:0], c_row_stride = cfg[7][31:16];
  wire [31:0] imm0 = cfg[8];
  wire [31:0] imm1 = cfg[9];
  wire [31:0] acc_init = cfg[12];

  // Two 14-bit PE descriptors per genome word; the top four bits are reserved.
  function automatic logic [13:0] pe_desc(input int index);
    logic [27:0] word;
    word = (index < 2) ? cfg[10][27:0] : cfg[11][27:0];
    pe_desc = ((index % 2) == 0) ? word[13:0] : word[27:14];
  endfunction

  // Lanes per pass: the scalar personality is sequential by construction.
  wire [LANE_BITS:0] job_lanes = (job_mode_q == MODE_SCALAR) ?
      (LANE_BITS+1)'(1) : (LANE_BITS+1)'(NPE);

  // ------------------------------------------------------------- aXbus
  logic [15:0] i_off, d_off;
  logic i_reg_hit;
  logic d_reg_hit, d_cfg_hit, d_data_hit;

  always_comb begin
    i_off = i_addr[15:0];
    d_off = d_addr[15:0];
    // The fetch port sees only the register page.  Keeping it off the genome
    // and the data buffer holds those arrays to two ports each, which is what
    // lets them infer block RAM instead of a LUT-built multi-port file.
    i_reg_hit  = i_valid && (i_addr & 32'hffff_0000) == BASE && i_off < 16'h0100;
    d_reg_hit  = d_valid && (d_addr & 32'hffff_0000) == BASE && d_off < 16'h0100;
    d_cfg_hit  = d_valid && (d_addr & 32'hffff_0000) == BASE &&
                 d_off >= CFG_BASE && d_off < CFG_BASE + 16'(4 * CFG_WORDS);
    d_data_hit = d_valid && (d_addr & 32'hffff_0000) == BASE &&
                 d_off >= DATA_BASE && d_off < DATA_BASE + 16'(4 * DATA_WORDS);
  end

  wire [CFG_BITS-1:0]  d_cfg_idx  = CFG_BITS'((d_off - CFG_BASE) >> 2);
  wire [ADDR_BITS-1:0] d_data_idx = ADDR_BITS'((d_off - DATA_BASE) >> 2);

  function automatic logic [31:0] read_reg(input logic [15:0] off);
    unique case (off)
      OFF_ID:         read_reg = ROLE_ID;
      OFF_VERSION:    read_reg = ROLE_VERSION;
      OFF_STATUS:     read_reg = {29'b0, rejected_q, done_q, busy_q};
      OFF_NITEMS:     read_reg = nitems_q;
      OFF_NCONFIG:    read_reg = nconfig_q;
      OFF_COUNT:      read_reg = count_q;
      OFF_CAPS:       read_reg = {8'(NPE), 8'(CFG_WORDS), 16'(DATA_WORDS)};
      OFF_GENERATION: read_reg = generation_q;
      OFF_REJECTS:    read_reg = rejects_q;
      default:        read_reg = 32'b0;
    endcase
  endfunction

  // Ready is combinational, exactly as in role.gpu-compute.  A registered
  // ready would hold the master's `valid` high for a second cycle and apply
  // every register write twice -- harmless for idempotent fields, but it made
  // one doorbell count two descriptor rejections.  Block-RAM reads still take
  // their single wait state, signalled through buf_pending_q.
  logic buf_pending_q;
  wire  d_hit      = d_reg_hit || d_cfg_hit || d_data_hit;
  wire  d_buf_read = (d_cfg_hit || d_data_hit) && d_wstrb == 4'b0;

  assign d_ready = d_valid && (d_buf_read ? buf_pending_q : 1'b1);
  assign d_err   = d_valid && !d_hit;
  assign d_rdata = d_cfg_hit  ? cfg_mmio_rdata_q
                 : d_data_hit ? data_mmio_rdata_q
                 : read_reg(d_off);
  assign i_ready = i_valid;
  assign i_err   = i_valid && !i_reg_hit;
  assign i_rdata = i_reg_hit ? read_reg(i_off) : 32'b0;
  assign irq     = done_q;

  // Deliberately unread: the fetch port is read-only in this role, the genome
  // packs two 14-bit PE descriptors per word, and engine addresses are
  // truncated into the data buffer on purpose -- that truncation is the
  // structural guarantee that no genome can address outside the role window.
  wire _unused_ok = &{1'b0, i_wdata, i_wstrb, cfg[10][31:28], cfg[11][31:28],
                      addr_a[31:ADDR_BITS], addr_b[31:ADDR_BITS],
                      addr_c[31:ADDR_BITS], 1'b0};

  // Index arithmetic is 16x16 by construction: indices and strides are both
  // 16-bit genome fields.  Writing it as 32x32 would infer full-width soft
  // multipliers and blow the LUT budget for no reachable range.
  function automatic logic [31:0] mul16(input logic [15:0] a,
                                        input logic [15:0] b);
    mul16 = 32'(a * b);
  endfunction

  // ------------------------------------------------- descriptor acceptance
  // Worst-case word index each stream can touch, evaluated in 32 bits so the
  // bound check cannot itself wrap.
  wire [15:0] last_row = (req_m == 16'b0) ? 16'b0 : req_m - 16'b1;
  wire [15:0] last_col = (req_n == 16'b0) ? 16'b0 : req_n - 16'b1;
  wire [15:0] last_k   = (req_k == 16'b0) ? 16'b0 : req_k - 16'b1;

  wire [31:0] a_last = 32'(a_base) + mul16(last_row, a_row_stride) +
                       mul16(last_col, a_col_stride) +
                       mul16(last_k, a_k_stride);
  wire [31:0] b_last = 32'(b_base) + mul16(last_col, b_col_stride) +
                       mul16(last_k, b_k_stride);
  wire [31:0] c_last = 32'(c_base) + mul16(last_row, c_row_stride) +
                       32'(last_col);

  wire mode_ok    = req_mode == MODE_SCALAR || req_mode == MODE_SIMT ||
                    req_mode == MODE_SYSTOLIC;
  wire nconfig_ok = nconfig_q == 32'(CFG_WORDS);
  wire dims_ok    = req_m != 16'b0 && req_n != 16'b0 && req_k != 16'b0 &&
                    mul16(req_m, req_n) <= 32'(DATA_WORDS);
  // A parallel personality must not carry a dependency through the lanes it
  // is about to run concurrently.
  wire lanes_ok   = (req_mode != MODE_SCALAR) ||
                    (32'(req_k) <= 32'(DATA_WORDS));
  wire range_ok   = a_last < 32'(DATA_WORDS) && b_last < 32'(DATA_WORDS) &&
                    c_last < 32'(DATA_WORDS);
  wire accept     = mode_ok && nconfig_ok && dims_ok && lanes_ok && range_ok;

  // The shell-visible rejection: the same condition that increments REJECTS in
  // the sequencer below, exported as a one-cycle pulse.  It is one cycle per
  // refused doorbell because a register write retires in a single cycle here --
  // the same property that stops one doorbell counting twice.
`ifdef AX_LIVE_ROLE_EVENTS
  assign reject_event = d_reg_hit && |d_wstrb && d_off == OFF_DOORBELL &&
                        !busy_q && !accept;
`endif

  // ---------------------------------------------------------- PE datapath
  // Each PE computes (a + b) * c + d.  The multiply goes through the shared
  // GW5A wrapper so a board build lands on DSP slices instead of soft LUT
  // multipliers -- the same treatment the hard GPU role gets, which is what
  // makes their area numbers comparable.
  logic [31:0] pe_out   [0:NPE-1];
  logic [31:0] pe_pre   [0:NPE-1];
  logic [31:0] pe_scale [0:NPE-1];
  logic [31:0] pe_post  [0:NPE-1];
  logic [31:0] pe_prod  [0:NPE-1];
  logic        pe_load  [0:NPE-1];

  always_comb begin
    for (int p = 0; p < NPE; p++) begin
      logic [13:0] desc;
      logic [31:0] sel [0:3];
      desc = pe_desc(p);
      for (int s = 0; s < 4; s++) begin
        unique case (desc[3*s +: 3])
          SRC_STREAM_A: sel[s] = str_a[p];
          SRC_STREAM_B: sel[s] = str_b[p];
          SRC_ACC:      sel[s] = acc[p];
          SRC_IMM0:     sel[s] = imm0;
          SRC_IMM1:     sel[s] = imm1;
          SRC_ONE:      sel[s] = 32'd1;
          SRC_CHAIN:    sel[s] = (p == 0) ? 32'b0 : acc[p - 1];
          default:      sel[s] = 32'b0;
        endcase
      end
      pe_pre[p]   = sel[0] + sel[1];
      pe_scale[p] = sel[2];
      pe_post[p]  = sel[3];
      pe_load[p]  = desc[13:12] == ACC_LOAD;
    end
  end

  // Separate block so the multiplier output does not appear to feed back into
  // the source muxes that drive its inputs.
  always_comb begin
    for (int p = 0; p < NPE; p++) pe_out[p] = pe_prod[p] + pe_post[p];
  end

  generate
    for (genvar g = 0; g < NPE; g++) begin : g_pe_mul
      ax_mul32_low u_mul (
        .a(pe_pre[g]),
        .b(pe_scale[g]),
        .y(pe_prod[g])
      );
    end
  endgenerate

  // ------------------------------------------------- engine address streams
  // SIMT spreads lanes over columns; systolic spreads them over the reduction.
  wire [15:0] lane_col = (job_mode_q == MODE_SIMT)
      ? col_q + 16'(lane_q) : col_q;
  wire [15:0] lane_k = (job_mode_q == MODE_SIMT)
      ? kk_q : kk_q + 16'(lane_q);

  wire [31:0] addr_a = 32'(a_base) + mul16(row_q, a_row_stride) +
                       mul16(lane_col, a_col_stride) +
                       mul16(lane_k, a_k_stride);
  wire [31:0] addr_b = 32'(b_base) + mul16(lane_col, b_col_stride) +
                       mul16(lane_k, b_k_stride);
  wire [31:0] addr_c = 32'(c_base) + mul16(row_q, c_row_stride) +
                       32'(lane_col);

  // Acceptance already proved these are in range; truncation is the
  // structural guarantee that no genome can reach outside the role window.
  wire [ADDR_BITS-1:0] eng_a = addr_a[ADDR_BITS-1:0];
  wire [ADDR_BITS-1:0] eng_b = addr_b[ADDR_BITS-1:0];
  wire [ADDR_BITS-1:0] eng_c = addr_c[ADDR_BITS-1:0];

  wire lane_last  = lane_q + (LANE_BITS+1)'(1) >= job_lanes;
  wire col_in_use = lane_col < job_n_q;
  wire k_in_use   = lane_k < job_k_q;
  wire k_done     = 32'(kk_q) + 32'(job_lanes) >= 32'(job_k_q) ||
                    job_mode_q == MODE_SIMT;
  wire col_done   = 32'(col_q) + ((job_mode_q == MODE_SIMT)
                    ? 32'(job_lanes) : 32'd1) >= 32'(job_n_q);
  wire row_done   = 32'(row_q) + 32'd1 >= 32'(job_m_q);

  // ------------------------------------------------------------ sequencer
  always_ff @(posedge clk) begin
    if (rst) begin
      nitems_q     <= 32'b0;
      nconfig_q    <= 32'b0;
      count_q      <= 32'b0;
      generation_q <= 32'b0;
      rejects_q    <= 32'b0;
      busy_q       <= 1'b0;
      done_q       <= 1'b0;
      rejected_q   <= 1'b0;
      state_q      <= S_IDLE;
      row_q        <= 16'b0;
      col_q        <= 16'b0;
      kk_q         <= 16'b0;
      lane_q       <= '0;
      job_mode_q   <= 4'b0;
      job_m_q      <= 16'b0;
      job_n_q      <= 16'b0;
      job_k_q      <= 16'b0;
      acc_init_q   <= 32'b0;
      buf_pending_q <= 1'b0;
    end else begin
      buf_pending_q <= d_buf_read && !buf_pending_q;

      // ---- host writes; genome and buffer are writable only while idle
      if (d_reg_hit && |d_wstrb) begin
        unique case (d_off)
          OFF_NITEMS:  if (!busy_q) nitems_q  <= d_wdata;
          OFF_NCONFIG: if (!busy_q) nconfig_q <= d_wdata;
          OFF_STATUS: begin
            if (d_wdata[1]) done_q     <= 1'b0;
            if (d_wdata[2]) rejected_q <= 1'b0;
          end
          OFF_DOORBELL: if (!busy_q) begin
            if (!accept) begin
              // Rejected before any state changes: no BUSY, no generation
              // bump, no engine write.
              rejected_q <= 1'b1;
              rejects_q  <= rejects_q + 32'd1;
            end else begin
              busy_q       <= 1'b1;
              done_q       <= 1'b0;
              rejected_q   <= 1'b0;
              generation_q <= generation_q + 32'd1;
              job_mode_q   <= req_mode;
              job_m_q      <= req_m;
              job_n_q      <= req_n;
              job_k_q      <= req_k;
              acc_init_q   <= acc_init;
              row_q        <= 16'b0;
              col_q        <= 16'b0;
              kk_q         <= 16'b0;
              lane_q       <= '0;
              state_q      <= S_LOAD_A;
              for (int p = 0; p < NPE; p++) begin
                acc[p]   <= acc_init;
                str_a[p] <= 32'b0;
                str_b[p] <= 32'b0;
              end
            end
          end
          default: ;
        endcase
      end

      // ---- bounded sequencer
      if (busy_q) begin
        unique case (state_q)
          // The buffer read is registered, so each lane presents its address
          // in one cycle and captures the data in the next.
          S_LOAD_A: state_q <= S_CAP_A;
          S_CAP_A: begin
            // Out-of-range lanes read zero, which is the identity for the
            // fused form's pre-adder and keeps tail lanes harmless.
            str_a[lane_q[LANE_BITS-1:0]] <=
                (col_in_use && k_in_use) ? data_eng_rdata_q : 32'b0;
            if (lane_last) begin
              lane_q  <= '0;
              state_q <= S_LOAD_B;
            end else begin
              lane_q  <= lane_q + 1'b1;
              state_q <= S_LOAD_A;
            end
          end
          S_LOAD_B: state_q <= S_CAP_B;
          S_CAP_B: begin
            str_b[lane_q[LANE_BITS-1:0]] <=
                (col_in_use && k_in_use) ? data_eng_rdata_q : 32'b0;
            if (lane_last) begin
              lane_q  <= '0;
              state_q <= S_EXEC;
            end else begin
              lane_q  <= lane_q + 1'b1;
              state_q <= S_LOAD_B;
            end
          end
          S_EXEC: begin
            for (int p = 0; p < NPE; p++)
              if (pe_load[p]) acc[p] <= pe_out[p];
            state_q <= S_KNEXT;
          end
          S_KNEXT: begin
            if (k_done) begin
              state_q <= (job_mode_q == MODE_SYSTOLIC) ? S_REDUCE : S_STORE;
            end else begin
              kk_q    <= kk_q + 16'(job_lanes);
              state_q <= S_LOAD_A;
            end
          end
          S_REDUCE: begin
            // Collapse the per-lane partial sums of the reduction into PE 0.
            for (int p = 1; p < NPE; p++) acc[p] <= 32'b0;
            acc[0] <= acc_sum;
            state_q <= S_STORE;
          end
          S_STORE: begin
            if (job_mode_q == MODE_SIMT && !lane_last) begin
              lane_q <= lane_q + 1'b1;
            end else begin
              lane_q  <= '0;
              state_q <= S_CNEXT;
            end
          end
          S_CNEXT: begin
            kk_q <= 16'b0;
            for (int p = 0; p < NPE; p++)
              if (job_mode_q != MODE_SCALAR) acc[p] <= acc_init_q;
            if (!col_done) begin
              col_q   <= col_q + ((job_mode_q == MODE_SIMT)
                         ? 16'(job_lanes) : 16'd1);
              state_q <= S_LOAD_A;
            end else if (!row_done) begin
              col_q   <= 16'b0;
              row_q   <= row_q + 16'd1;
              state_q <= S_LOAD_A;
            end else begin
              state_q <= S_DONE;
            end
          end
          S_DONE: begin
            busy_q  <= 1'b0;
            done_q  <= 1'b1;
            count_q <= count_q + 32'd1;
            state_q <= S_IDLE;
          end
          default: state_q <= S_IDLE;
        endcase
      end

    end
  end


  // ------------------------------------------------------- memory ports
  // Two ports each, exactly like the other roles: port A is data-port MMIO,
  // port B is the engine.  The engine multiplexes its single address across
  // the load-A, load-B and store phases, which is why those are separate
  // sequencer states rather than one wide step.
  logic [31:0] cfg_mmio_rdata_q, data_mmio_rdata_q, data_eng_rdata_q;

  always_ff @(posedge clk) begin
    if (d_cfg_hit && d_wstrb == 4'hf && !busy_q) cfg[d_cfg_idx] <= d_wdata;
    else if (d_cfg_hit && d_wstrb == 4'b0)       cfg_mmio_rdata_q <= cfg[d_cfg_idx];
  end

  always_ff @(posedge clk) begin
    if (d_data_hit && d_wstrb == 4'hf && !busy_q) dmem[d_data_idx] <= d_wdata;
    else if (d_data_hit && d_wstrb == 4'b0)       data_mmio_rdata_q <= dmem[d_data_idx];
  end

  // Engine port: one address, phase-selected.
  wire eng_store = busy_q && state_q == S_STORE &&
                   ((job_mode_q == MODE_SIMT) ? col_in_use
                    : (job_mode_q == MODE_SYSTOLIC) ? 1'b1
                    : (row_done && col_done));
  wire [31:0] eng_wdata = (job_mode_q == MODE_SIMT)
      ? acc[lane_q[LANE_BITS-1:0]] : acc[0];
  wire [ADDR_BITS-1:0] eng_addr =
      (state_q == S_LOAD_B || state_q == S_CAP_B) ? eng_b :
      (state_q == S_STORE)                        ? eng_c : eng_a;

  always_ff @(posedge clk) begin
    if (eng_store) dmem[eng_addr] <= eng_wdata;
    else           data_eng_rdata_q <= dmem[eng_addr];
  end

  // Reduction adder over the per-lane accumulators.
  logic [31:0] acc_sum;
  always_comb begin
    acc_sum = 32'b0;
    for (int p = 0; p < NPE; p++) acc_sum = acc_sum + acc[p];
  end
endmodule
