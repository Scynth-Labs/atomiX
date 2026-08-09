// TPU-lite role: an int8 folded 24-MAC GEMM accelerator.
//
// The role computes C = acc_base + A x W over signed 8-bit operands with
// 32-bit accumulation:
//
//   A is M x 8 activations (M up to 256 rows), W is the stationary 8 x 8
//   weight tile, C is M x 8 signed 32-bit results.  CTRL.ACC accumulates
//   into the existing C contents (how software tiles GEMMs with K > 8:
//   load the next weight/activation slice and ring the doorbell again);
//   CTRL.RELU clamps negative results to zero on the way out.
//
// All eight output columns are computed together with three K terms per
// phase. Three phases cover K=8 (3 + 3 + 2), so the datapath instantiates
// 24 signed int8 multipliers--the widest arrangement below the GW5A-25's
// 28-DSP limit. Rows are processed in sequence and the drain stage writes
// (or read-modify-writes, under ACC) the eight completed accumulators to C.
//
// Window layout (common role header per DESIGN.md section 3.3 first):
//
//   0x0000  ROLE_ID   RO  "TPUL"
//   0x0004  VERSION   RO  role programming-model revision
//   0x0008  DOORBELL  WO  any write starts a job when idle
//   0x000c  STATUS    R/W1C  bit0 BUSY, bit1 DONE (write 1 to clear DONE)
//   0x0010  CTRL      bit0 RELU, bit1 ACC; latched at the doorbell
//   0x0014  M         activation rows for the next job; 0 completes
//                     immediately, values above 256 clamp to 256
//   0x0018  COUNT     RO  completed-job counter
//   0x0100  weight tile, 16 words: row r in words 2r and 2r+1, one int8
//           per byte, w[r][c] in byte c%4 of word 2r + c/4 (little-endian)
//   0x1000  A buffer, 512 words: row m in words 2m and 2m+1, packed like W
//   0x2000  C buffer, 2048 words: c[m][col] at word 8m + col
//
// The buffers use synchronous block RAM. Host and engine accesses are
// mutually exclusive on one physical port so the large C buffer infers
// BSRAM on GW5A instead of flip-flops. MMIO reads complete with one aXbus
// wait state and writes are full-word only (partial strobes error). The
// fetch port sees just the header registers; software must not touch the
// buffers while BUSY.
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

  // Level-sensitive completion line to the PLIC: held while STATUS.DONE is
  // set, so clearing DONE (write 1 to STATUS bit 1) is what deasserts it.
  output logic        irq
);
  localparam logic [31:0] ROLE_ID      = 32'h5450_554c;  // "TPUL"
  localparam logic [31:0] ROLE_VERSION = 32'h0000_0001;
  localparam logic [15:0] OFF_ID       = 16'h0000;
  localparam logic [15:0] OFF_VERSION  = 16'h0004;
  localparam logic [15:0] OFF_DOORBELL = 16'h0008;
  localparam logic [15:0] OFF_STATUS   = 16'h000c;
  localparam logic [15:0] OFF_CTRL     = 16'h0010;
  localparam logic [15:0] OFF_M        = 16'h0014;
  localparam logic [15:0] OFF_COUNT    = 16'h0018;
  localparam logic [15:0] W_BASE       = 16'h0100;
  localparam logic [15:0] A_BASE       = 16'h1000;
  localparam logic [15:0] C_BASE       = 16'h2000;

  // Three K phases service all eight result columns with 24 physical MACs.
  // This is the widest datapath that stays below the GW5A-25's 28-DSP budget.
  localparam logic [3:0] E_IDLE     = 4'd0;
  localparam logic [3:0] E_LOAD0    = 4'd1;
  localparam logic [3:0] E_LOAD1    = 4'd2;
  localparam logic [3:0] E_LOAD2    = 4'd3;
  localparam logic [3:0] E_MAC0     = 4'd4;
  localparam logic [3:0] E_MAC1     = 4'd5;
  localparam logic [3:0] E_MAC2     = 4'd6;
  localparam logic [3:0] E_DRAIN_RD = 4'd7;
  localparam logic [3:0] E_DRAIN_WR = 4'd8;

  logic [31:0] wreg_q [0:15];
  logic [31:0] abuf [0:511];
  logic [31:0] cbuf [0:2047];

  logic [31:0] ctrl_q, m_q, count_q;
  logic        busy_q, done_q;
  logic [8:0]  job_m_q;
  logic        job_relu_q, job_acc_q;
  logic [3:0]  state_q;
  logic [7:0]  row_q;
  logic [2:0]  col_q;
  logic [63:0] row_in_q;
  logic [31:0] acc_q [0:7];
  logic [31:0] a_mmio_rdata_q, c_mmio_rdata_q;
  logic [31:0] a_eng_rdata_q, c_eng_rdata_q;
  logic        buf_pending_q;

  wire i_in_range = i_addr >= BASE && i_addr - BASE < 32'h0001_0000;
  wire d_in_range = d_addr >= BASE && d_addr - BASE < 32'h0001_0000;
  wire [15:0] i_off = i_addr[15:0];
  wire [15:0] d_off = d_addr[15:0];

  function automatic logic reg_offset(input logic [15:0] off);
    reg_offset = off == OFF_ID || off == OFF_VERSION ||
                 off == OFF_DOORBELL || off == OFF_STATUS ||
                 off == OFF_CTRL || off == OFF_M || off == OFF_COUNT;
  endfunction
  function automatic logic wreg_offset(input logic [15:0] off);
    wreg_offset = off >= W_BASE && off < W_BASE + 16'h0040;
  endfunction
  function automatic logic abuf_offset(input logic [15:0] off);
    abuf_offset = off >= A_BASE && off < A_BASE + 16'h0800;
  endfunction
  function automatic logic cbuf_offset(input logic [15:0] off);
    cbuf_offset = off >= C_BASE && off < C_BASE + 16'h2000;
  endfunction
  function automatic logic [31:0] read_reg(input logic [15:0] off);
    unique case (off)
      OFF_ID:      read_reg = ROLE_ID;
      OFF_VERSION: read_reg = ROLE_VERSION;
      OFF_STATUS:  read_reg = {30'b0, done_q, busy_q};
      OFF_CTRL:    read_reg = ctrl_q;
      OFF_M:       read_reg = m_q;
      OFF_COUNT:   read_reg = count_q;
      default:     read_reg = 32'b0;
    endcase
  endfunction
  function automatic logic [31:0] merge_bytes(
      input logic [31:0] old_value, input logic [31:0] new_value,
      input logic [3:0] strb);
    merge_bytes = old_value;
    if (strb[0]) merge_bytes[7:0]   = new_value[7:0];
    if (strb[1]) merge_bytes[15:8]  = new_value[15:8];
    if (strb[2]) merge_bytes[23:16] = new_value[23:16];
    if (strb[3]) merge_bytes[31:24] = new_value[31:24];
  endfunction

  wire d_abuf_hit  = d_valid && d_in_range && abuf_offset(d_off) &&
                     d_addr[1:0] == 2'b00;
  wire d_cbuf_hit  = d_valid && d_in_range && cbuf_offset(d_off) &&
                     d_addr[1:0] == 2'b00;
  wire d_buf_hit   = d_abuf_hit || d_cbuf_hit;
  wire d_buf_write = d_buf_hit && d_wstrb == 4'hf;
  wire d_buf_read  = d_buf_hit && d_wstrb == 4'b0;
  wire d_wreg_hit  = d_valid && d_in_range && wreg_offset(d_off) &&
                     d_addr[1:0] == 2'b00;

  // Completion interrupt: level-sensitive, held while DONE stands.  The
  // handler deasserts it by clearing DONE, which is the same write the
  // polling driver already does.
  assign irq = done_q;

  always_comb begin
    i_ready = i_valid;
    i_err   = i_valid && (!i_in_range || !reg_offset(i_off) || i_addr[1:0] != 2'b00);
    i_rdata = read_reg(i_off);
    d_ready = 1'b0;
    d_rdata = 32'b0;
    d_err   = 1'b0;
    if (d_valid) begin
      if (d_buf_write) begin
        d_ready = 1'b1;
      end else if (d_buf_read) begin
        // Synchronous block-RAM read: one wait state, data registered.
        d_ready = buf_pending_q;
        d_rdata = d_abuf_hit ? a_mmio_rdata_q : c_mmio_rdata_q;
      end else if (d_wreg_hit) begin
        d_ready = 1'b1;
        d_rdata = wreg_q[d_off[5:2]];
      end else if (d_in_range && reg_offset(d_off) && d_addr[1:0] == 2'b00) begin
        d_ready = 1'b1;
        d_rdata = read_reg(d_off);
      end else begin
        d_ready = 1'b1;
        d_err   = 1'b1;
      end
    end
  end

  // Fold K=8 over three phases (3 + 3 + 2) while keeping all eight output
  // columns live. Each column owns three signed int8 multipliers.
  wire [3:0] k_base = (state_q == E_MAC0) ? 4'd0 :
                      (state_q == E_MAC1) ? 4'd3 : 4'd6;
  wire signed [15:0] prod [0:7][0:2];
  wire signed [31:0] mac_sum [0:7];
  generate
    for (genvar c = 0; c < 8; c++) begin : g_col
      for (genvar j = 0; j < 3; j++) begin : g_k
        wire [3:0] k = k_base + 4'(j);
        wire signed [7:0] a_val =
            (k < 4'd8) ? $signed(row_in_q[8*k +: 8]) : 8'sd0;
        wire signed [7:0] w_val =
            (k < 4'd8) ? $signed(wreg_q[2*k + c/4][8*(c%4) +: 8]) : 8'sd0;
        ax_mul_s8 u_mul (.a(a_val), .b(w_val), .y(prod[c][j]));
      end
      assign mac_sum[c] =
          {{16{prod[c][0][15]}}, prod[c][0]} +
          {{16{prod[c][1][15]}}, prod[c][1]} +
          {{16{prod[c][2][15]}}, prod[c][2]};
    end
  endgenerate

  wire [10:0] c_eng_idx = {row_q, col_q};
  wire        c_eng_write = busy_q && state_q == E_DRAIN_WR;
  wire [31:0] drain_sum = (job_acc_q ? c_eng_rdata_q : 32'b0) + acc_q[col_q];
  wire [31:0] c_eng_wdata = (job_relu_q && drain_sum[31]) ? 32'b0 : drain_sum;
  wire [8:0]  a_eng_addr = {row_q, state_q == E_LOAD1};

  // A buffer port A: data-port MMIO.  A separate always_ff per port is the
  // true-dual-port block-RAM inference pattern.
  always_ff @(posedge clk) begin
    if (d_abuf_hit && d_wstrb == 4'hf) abuf[d_off[10:2]] <= d_wdata;
    else if (d_abuf_hit && d_wstrb == 4'b0) a_mmio_rdata_q <= abuf[d_off[10:2]];
  end
  // A buffer port B: the engine's activation-row reads.
  always_ff @(posedge clk) begin
    a_eng_rdata_q <= abuf[a_eng_addr];
  end

  // C is physically single-port. Host access is legal only while idle, so
  // arbitration preserves the programming contract and lets cbuf infer BSRAM
  // instead of 65,536 flip-flops.
  always_ff @(posedge clk) begin
    if (busy_q) begin
      if (c_eng_write) cbuf[c_eng_idx] <= c_eng_wdata;
      else if (state_q == E_DRAIN_RD) c_eng_rdata_q <= cbuf[c_eng_idx];
    end else if (d_cbuf_hit && d_wstrb == 4'hf) begin
      cbuf[d_off[12:2]] <= d_wdata;
    end else if (d_cbuf_hit && d_wstrb == 4'b0) begin
      c_mmio_rdata_q <= cbuf[d_off[12:2]];
    end
  end

  // The weight tile is a small register file with one write port; writes
  // while BUSY are ignored so the stationary operand cannot change mid-job.
  always_ff @(posedge clk) begin
    if (d_wreg_hit && |d_wstrb && !busy_q)
      wreg_q[d_off[5:2]] <= merge_bytes(wreg_q[d_off[5:2]], d_wdata, d_wstrb);
  end

  // The 5.051 linter treats nonblocking assignments inside this task as a
  // separate process even though it is called only from the sequential block
  // below. Synthesis and IEEE scheduling both see one writer.
  /* verilator lint_off MULTIDRIVEN */
  task automatic apply_reg_write(input logic [15:0] off,
                                 input logic [31:0] wdata,
                                 input logic [3:0] strb);
    unique case (off)
      OFF_DOORBELL: begin
        if (!busy_q) begin
          done_q <= 1'b0;
          if (m_q == 32'b0) begin
            done_q  <= 1'b1;
            count_q <= count_q + 32'd1;
          end else begin
            busy_q     <= 1'b1;
            job_m_q    <= (m_q > 32'd256) ? 9'd256 : m_q[8:0];
            job_relu_q <= ctrl_q[0];
            job_acc_q  <= ctrl_q[1];
          end
        end
      end
      OFF_STATUS: begin
        // DONE is write-1-to-clear; its bit lives in byte lane 0.
        if (strb[0] && wdata[1]) done_q <= 1'b0;
      end
      OFF_CTRL: if (!busy_q) ctrl_q <= merge_bytes(ctrl_q, wdata, strb);
      OFF_M:    if (!busy_q) m_q <= merge_bytes(m_q, wdata, strb);
      default: ;
    endcase
  endtask

  always_ff @(posedge clk) begin
    if (rst) begin
      ctrl_q        <= 32'b0;
      m_q           <= 32'b0;
      count_q       <= 32'b0;
      busy_q        <= 1'b0;
      done_q        <= 1'b0;
      job_m_q       <= 9'b0;
      job_relu_q    <= 1'b0;
      job_acc_q     <= 1'b0;
      state_q       <= E_IDLE;
      row_q         <= 8'b0;
      col_q         <= 3'b0;
      row_in_q      <= 64'b0;
      buf_pending_q <= 1'b0;
      for (int c = 0; c < 8; c++) acc_q[c] <= 32'b0;
    end else begin
      buf_pending_q <= d_buf_read && !buf_pending_q;
      // Same conflict rule as the reference CLINT: if both ports write a
      // register in one cycle the D port wins.
      if (i_valid && !i_err && |i_wstrb) apply_reg_write(i_off, i_wdata, i_wstrb);
      if (d_valid && !d_err && |d_wstrb && !d_buf_hit && !d_wreg_hit)
        apply_reg_write(d_off, d_wdata, d_wstrb);
      if (busy_q) begin
        unique case (state_q)
          E_IDLE: begin
            row_q   <= 8'd0;
            state_q <= E_LOAD0;
          end
          E_LOAD0: state_q <= E_LOAD1;
          E_LOAD1: begin
            row_in_q[31:0] <= a_eng_rdata_q;
            state_q        <= E_LOAD2;
          end
          E_LOAD2: begin
            row_in_q[63:32] <= a_eng_rdata_q;
            for (int c = 0; c < 8; c++) acc_q[c] <= 32'b0;
            state_q <= E_MAC0;
          end
          E_MAC0: begin
            for (int c = 0; c < 8; c++) acc_q[c] <= mac_sum[c];
            state_q <= E_MAC1;
          end
          E_MAC1: begin
            for (int c = 0; c < 8; c++) acc_q[c] <= acc_q[c] + mac_sum[c];
            state_q <= E_MAC2;
          end
          E_MAC2: begin
            for (int c = 0; c < 8; c++) acc_q[c] <= acc_q[c] + mac_sum[c];
            col_q   <= 3'd0;
            state_q <= job_acc_q ? E_DRAIN_RD : E_DRAIN_WR;
          end
          E_DRAIN_RD: state_q <= E_DRAIN_WR;
          E_DRAIN_WR: begin
            if (col_q != 3'd7) begin
              col_q   <= col_q + 3'd1;
              state_q <= job_acc_q ? E_DRAIN_RD : E_DRAIN_WR;
            end else if ({1'b0, row_q} + 9'd1 == job_m_q) begin
              busy_q  <= 1'b0;
              done_q  <= 1'b1;
              count_q <= count_q + 32'd1;
              state_q <= E_IDLE;
            end else begin
              row_q   <= row_q + 8'd1;
              state_q <= E_LOAD0;
            end
          end
          default: state_q <= E_IDLE;
        endcase
      end
    end
  end
  /* verilator lint_on MULTIDRIVEN */
endmodule
