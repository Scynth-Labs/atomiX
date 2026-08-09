// Loopback role: the composition proof for the shell + role contract.
//
// Every role is an aXbus slave in the fixed 64 KiB window at ROLE_BASE and
// implements the common header below; everything past the header is
// role-defined.  This role's "accelerator" copies words inside its private
// buffer, which exercises the whole driver model — discovery, descriptor
// programming, doorbell, busy polling, completion — with trivially
// checkable results.
//
//   0x0000  ROLE_ID   RO  nonzero role identity ("LOOP" here; 0 = no role)
//   0x0004  VERSION   RO  role programming-model revision
//   0x0008  DOORBELL  WO  any write starts a job when idle; reads as zero
//   0x000c  STATUS    R/W1C  bit0 BUSY, bit1 DONE; writing bit1 clears DONE
//   0x0010+           role-defined registers
//
// Every role also drives one level-sensitive `irq` line, asserted for exactly
// as long as STATUS.DONE stands.  It reaches the core as an external interrupt
// through the shell's PLIC, so completion can be waited on instead of polled;
// clearing DONE is what deasserts it, which is the write a polling driver
// already performs.  A role with no engine (role.none) ties it low.
//
// Loopback-defined layout:
//
//   0x0010  SRC    byte offset of the source region inside the buffer
//   0x0014  DST    byte offset of the destination region
//   0x0018  LEN    number of words to copy
//   0x001c  COUNT  RO  completed-job counter
//   0x1000  4 KiB word-addressed data buffer
//
// SRC/DST/LEN are latched at the doorbell; reprogramming them while BUSY has
// no effect on the running job.  The buffer is deliberately block-RAM shaped
// so every future role can reuse the pattern: it has exactly two synchronous
// ports (data-port MMIO and the copy engine), buffer reads complete with one
// aXbus wait state, buffer writes are full-word only (partial strobes error),
// and the engine copies one word per two cycles.  The buffer is reachable
// through the data port only; the fetch port sees just the register page.
// Software must not touch the buffer while BUSY.  Offsets wrap inside the
// 4 KiB buffer.
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
  localparam logic [31:0] ROLE_ID      = 32'h4c4f_4f50;  // "LOOP"
  localparam logic [31:0] ROLE_VERSION = 32'h0000_0001;
  localparam logic [15:0] OFF_ID       = 16'h0000;
  localparam logic [15:0] OFF_VERSION  = 16'h0004;
  localparam logic [15:0] OFF_DOORBELL = 16'h0008;
  localparam logic [15:0] OFF_STATUS   = 16'h000c;
  localparam logic [15:0] OFF_SRC      = 16'h0010;
  localparam logic [15:0] OFF_DST      = 16'h0014;
  localparam logic [15:0] OFF_LEN      = 16'h0018;
  localparam logic [15:0] OFF_COUNT    = 16'h001c;
  localparam logic [15:0] BUF_BASE     = 16'h1000;
  localparam int unsigned BUF_WORDS    = 1024;

  logic [31:0] buffer [0:BUF_WORDS-1];
  logic [31:0] src_q, dst_q, len_q, count_q;
  logic        busy_q, done_q;
  logic [9:0]  job_src_q, job_dst_q;
  logic [31:0] job_left_q;
  logic [31:0] buf_rdata_q, eng_data_q;
  logic        buf_pending_q, eng_write_q;

  wire i_in_range = i_addr >= BASE && i_addr - BASE < 32'h0001_0000;
  wire d_in_range = d_addr >= BASE && d_addr - BASE < 32'h0001_0000;
  wire [15:0] i_off = i_addr[15:0];
  wire [15:0] d_off = d_addr[15:0];

  function automatic logic buf_offset(input logic [15:0] off);
    buf_offset = off >= BUF_BASE && off < BUF_BASE + 16'(4 * BUF_WORDS);
  endfunction
  function automatic logic reg_offset(input logic [15:0] off);
    reg_offset = off == OFF_ID || off == OFF_VERSION ||
                 off == OFF_DOORBELL || off == OFF_STATUS ||
                 off == OFF_SRC || off == OFF_DST || off == OFF_LEN ||
                 off == OFF_COUNT;
  endfunction
  function automatic logic [31:0] read_reg(input logic [15:0] off);
    unique case (off)
      OFF_ID:      read_reg = ROLE_ID;
      OFF_VERSION: read_reg = ROLE_VERSION;
      OFF_STATUS:  read_reg = {30'b0, done_q, busy_q};
      OFF_SRC:     read_reg = src_q;
      OFF_DST:     read_reg = dst_q;
      OFF_LEN:     read_reg = len_q;
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

  // The fetch port sees only the register page: executing from a role window
  // is meaningless, and keeping the buffer off the I port leaves it a clean
  // two-port block RAM.
  wire d_buf_hit   = d_valid && d_in_range && buf_offset(d_off) &&
                     d_addr[1:0] == 2'b00;
  // The programming contract already forbids software from touching the
  // buffer while the copy engine owns it.  Enforce that rule in hardware as
  // well: besides making an accidental access fail cleanly, the mutually
  // exclusive enables let FPGA synthesis combine the two logical ports into
  // a block-RAM implementation instead of expanding 4 KiB into flip-flops.
  wire d_buf_write = d_buf_hit && !busy_q && d_wstrb == 4'hf;
  wire d_buf_read  = d_buf_hit && !busy_q && d_wstrb == 4'b0;

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
        d_rdata = buf_rdata_q;
      end else if (d_in_range && reg_offset(d_off) && d_addr[1:0] == 2'b00) begin
        d_ready = 1'b1;
        d_rdata = read_reg(d_off);
      end else begin
        d_ready = 1'b1;
        d_err   = 1'b1;
      end
    end
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
          if (len_q == 32'b0) begin
            done_q  <= 1'b1;
            count_q <= count_q + 32'd1;
          end else begin
            busy_q     <= 1'b1;
            job_src_q  <= src_q[11:2];
            job_dst_q  <= dst_q[11:2];
            job_left_q <= len_q;
          end
        end
      end
      OFF_STATUS: begin
        // DONE is write-1-to-clear; its bit lives in byte lane 0.
        if (strb[0] && wdata[1]) done_q <= 1'b0;
      end
      OFF_SRC: if (!busy_q) src_q <= merge_bytes(src_q, wdata, strb);
      OFF_DST: if (!busy_q) dst_q <= merge_bytes(dst_q, wdata, strb);
      OFF_LEN: if (!busy_q) len_q <= merge_bytes(len_q, wdata, strb);
      default: ;
    endcase
  endtask

  // Buffer port A: data-port MMIO.  A separate always_ff per port is the
  // true-dual-port block-RAM inference pattern.
  always_ff @(posedge clk) begin
    if (d_buf_write) buffer[d_off[11:2]] <= d_wdata;
    else if (d_buf_read) buf_rdata_q <= buffer[d_off[11:2]];
  end

  // Buffer port B: the copy engine, alternating read and write phases.
  always_ff @(posedge clk) begin
    if (busy_q) begin
      if (!eng_write_q) eng_data_q <= buffer[job_src_q];
      else buffer[job_dst_q] <= eng_data_q;
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      src_q         <= 32'b0;
      dst_q         <= 32'b0;
      len_q         <= 32'b0;
      count_q       <= 32'b0;
      busy_q        <= 1'b0;
      done_q        <= 1'b0;
      job_src_q     <= 10'b0;
      job_dst_q     <= 10'b0;
      job_left_q    <= 32'b0;
      buf_pending_q <= 1'b0;
      eng_write_q   <= 1'b0;
    end else begin
      buf_pending_q <= d_buf_read && !buf_pending_q;
      // Same conflict rule as the reference CLINT: if both ports write a
      // register in one cycle the D port wins.
      if (i_valid && !i_err && |i_wstrb) apply_reg_write(i_off, i_wdata, i_wstrb);
      if (d_valid && !d_err && |d_wstrb && !d_buf_hit)
        apply_reg_write(d_off, d_wdata, d_wstrb);
      if (busy_q) begin
        eng_write_q <= !eng_write_q;
        if (eng_write_q) begin
          job_src_q  <= job_src_q + 10'd1;
          job_dst_q  <= job_dst_q + 10'd1;
          job_left_q <= job_left_q - 32'd1;
          if (job_left_q == 32'd1) begin
            busy_q  <= 1'b0;
            done_q  <= 1'b1;
            count_q <= count_q + 32'd1;
          end
        end
      end
    end
  end
  /* verilator lint_on MULTIDRIVEN */
endmodule
