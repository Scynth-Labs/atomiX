// Direct-mapped, write-back, write-allocate aXbus cache.
//
// The reference `cache.direct-mapped` is write-through with no write-allocate,
// and it *invalidates* a line on every write to it.  For read-mostly code that
// is fine and cheap.  For anything that writes a buffer -- a framebuffer, an
// image, a decoder output -- it is actively harmful: a sequential
// read-modify-write invalidates the line it just filled, so every byte pays a
// full miss and a full memory write, and longer lines make it worse rather than
// better.  Measured on the render workload, the write-through cache sat at 25
// cycles per pixel for a sequential byte increment no matter how large it was.
//
// This component keeps writes in the cache instead.  A write hit merges into
// the line and marks it dirty, costing one cycle; a miss allocates the line
// first, so a subsequent write to the same line is also a hit.  A dirty line is
// written back to memory only when it is evicted or when the cache is flushed.
//
// ## Flush is a drain, and it takes time
//
// Because dirty data can outlive the write that produced it, `flush` cannot be
// a single-cycle invalidate any more: it must walk every line and write the
// dirty ones back before invalidating.  `flush_busy` is high for the duration,
// and the SoC must keep the *other* port off the bus while it is high -- if an
// instruction fetch refilled from memory mid-drain it could read a stale word
// that is still sitting dirty in this cache.  `soc_top` does this by gating the
// instruction port's `valid` and `ready` on `flush_busy`.
//
// ## Constraint: no hardware page-table walker
//
// This cache must not be paired with a core whose instruction port *writes* to
// memory.  `core.pipeline5` has a hardware Sv32 walker that updates PTE A/D
// bits through the fetch port; if a page-table entry were also sitting dirty in
// this cache, the drain triggered by that walker write would write the stale
// line back over the walker's update.  The write-through cache has no such
// hazard because it never holds dirty data.
//
// Profiles using `core.ax2-*` or `core.minimal` are safe: both are machine-mode
// with physical addressing and never write through the fetch port.  Profiles
// using `core.pipeline5` with Sv32 must keep `cache.direct-mapped`.
module axcache #(
  parameter logic [31:0] CACHE_BASE = 32'h8000_0000,
  parameter int unsigned CACHE_BYTES = 32 * 1024 * 1024,
  parameter int unsigned LINES = 16,
  parameter int unsigned WORDS_PER_LINE = 4
) (
  input  logic        clk,
  input  logic        rst,
  input  logic        flush,
  output logic        flush_busy,

  input  logic        c_valid,
  input  logic [31:0] c_addr,
  input  logic [31:0] c_wdata,
  input  logic [3:0]  c_wstrb,
  output logic        c_ready,
  output logic [31:0] c_rdata,
  output logic        c_err,

  output logic        m_valid,
  output logic [31:0] m_addr,
  output logic [31:0] m_wdata,
  output logic [3:0]  m_wstrb,
  input  logic        m_ready,
  input  logic [31:0] m_rdata,
  input  logic        m_err
);
  localparam int unsigned LINE_BYTES  = WORDS_PER_LINE * 4;
  localparam int unsigned WORD_BITS   = $clog2(WORDS_PER_LINE);
  localparam int unsigned INDEX_BITS  = $clog2(LINES);
  localparam int unsigned OFFSET_BITS = $clog2(LINE_BYTES);
  localparam int unsigned TAG_BITS    = 32 - OFFSET_BITS - INDEX_BITS;

  typedef enum logic [2:0] {
    IDLE, WRITEBACK, REFILL, BYPASS, RESPOND, DRAIN
  } state_e;
  state_e state;

  logic [LINES-1:0]     valid_lines, dirty_lines;
  logic [TAG_BITS-1:0]  tags [0:LINES-1];
  logic [31:0]          data [0:LINES-1][0:WORDS_PER_LINE-1];

  logic [31:0] req_addr_q, req_wdata_q, response_data_q;
  logic [3:0]  req_wstrb_q;
  logic [INDEX_BITS-1:0] req_index_q;
  logic [WORD_BITS-1:0]  req_word_q, burst_word_q;
  logic [TAG_BITS-1:0]   wb_tag_q;
  logic response_err_q, req_is_write_q, flush_pending_q;
  logic [INDEX_BITS:0] drain_index_q;

  wire cacheable = c_addr >= CACHE_BASE && c_addr - CACHE_BASE < CACHE_BYTES &&
                   c_addr[1:0] == 2'b00;
  wire [INDEX_BITS-1:0] c_index = c_addr[OFFSET_BITS + INDEX_BITS - 1:OFFSET_BITS];
  wire [WORD_BITS-1:0]  c_word  = c_addr[OFFSET_BITS-1:2];
  wire [TAG_BITS-1:0]   c_tag   = c_addr[31:OFFSET_BITS + INDEX_BITS];
  wire hit = cacheable && valid_lines[c_index] && tags[c_index] == c_tag;

  wire idle_ok   = state == IDLE && !flush && !flush_pending_q;
  wire read_hit  = c_valid && c_wstrb == 4'b0 && hit && idle_ok;
  wire write_hit = c_valid && |c_wstrb && hit && idle_ok;

  function automatic logic [31:0] merge_bytes(
      input logic [31:0] old_value, input logic [31:0] new_value,
      input logic [3:0] strb);
    merge_bytes = old_value;
    if (strb[0]) merge_bytes[7:0]   = new_value[7:0];
    if (strb[1]) merge_bytes[15:8]  = new_value[15:8];
    if (strb[2]) merge_bytes[23:16] = new_value[23:16];
    if (strb[3]) merge_bytes[31:24] = new_value[31:24];
  endfunction

  // The victim currently occupying the requested line.
  wire victim_dirty = valid_lines[c_index] && dirty_lines[c_index];
  wire [INDEX_BITS-1:0] drain_idx = drain_index_q[INDEX_BITS-1:0];

  assign flush_busy = state == DRAIN || flush_pending_q;

  // See the direct-mapped component: a master (the MMU, on a trap redirect or
  // an sfence.vma under an in-flight walk) may abandon a request and present a
  // different one immediately.  The buffered response is only valid for the
  // request that was latched.
  wire respond_hold = state == RESPOND && c_valid &&
                      c_addr == req_addr_q && c_wstrb == req_wstrb_q;

  always_comb begin
    // Both hit kinds complete in the cycle they are presented; that single-cycle
    // write hit is the whole point of the component.
    c_ready = read_hit || write_hit || respond_hold;
    c_rdata = read_hit ? data[c_index][c_word] : response_data_q;
    c_err   = respond_hold && response_err_q;

    m_valid = state == WRITEBACK || state == REFILL || state == BYPASS ||
              (state == DRAIN && drain_index_q < LINES[INDEX_BITS:0] &&
               valid_lines[drain_idx] && dirty_lines[drain_idx]);
    m_addr  = req_addr_q;
    m_wdata = req_wdata_q;
    m_wstrb = req_wstrb_q;
    if (state == REFILL) begin
      m_addr  = {req_addr_q[31:OFFSET_BITS], {OFFSET_BITS{1'b0}}} |
                {{(32-OFFSET_BITS){1'b0}}, burst_word_q, 2'b00};
      m_wdata = 32'b0;
      m_wstrb = 4'b0;
    end else if (state == WRITEBACK) begin
      m_addr  = {wb_tag_q, req_index_q, {OFFSET_BITS{1'b0}}} |
                {{(32-OFFSET_BITS){1'b0}}, burst_word_q, 2'b00};
      m_wdata = data[req_index_q][burst_word_q];
      m_wstrb = 4'hf;
    end else if (state == DRAIN) begin
      m_addr  = {tags[drain_idx], drain_idx, {OFFSET_BITS{1'b0}}} |
                {{(32-OFFSET_BITS){1'b0}}, burst_word_q, 2'b00};
      m_wdata = data[drain_idx][burst_word_q];
      m_wstrb = 4'hf;
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      state <= IDLE;
      valid_lines <= '0;
      dirty_lines <= '0;
      response_data_q <= '0;
      response_err_q  <= 1'b0;
      req_addr_q <= '0; req_wdata_q <= '0; req_wstrb_q <= '0;
      req_index_q <= '0; req_word_q <= '0; burst_word_q <= '0;
      wb_tag_q <= '0; req_is_write_q <= 1'b0;
      flush_pending_q <= 1'b0; drain_index_q <= '0;
    end else begin
      // A flush request is latched: it cannot preempt an in-flight aXbus burst,
      // which has no cancel.
      if (flush) flush_pending_q <= 1'b1;

      unique case (state)
        IDLE: begin
          if (flush || flush_pending_q) begin
            drain_index_q <= '0;
            burst_word_q  <= '0;
            state         <= DRAIN;
          end else if (write_hit) begin
            data[c_index][c_word] <= merge_bytes(data[c_index][c_word],
                                                 c_wdata, c_wstrb);
            dirty_lines[c_index]  <= 1'b1;
          end else if (c_valid && !read_hit) begin
            req_addr_q  <= c_addr;
            req_wdata_q <= c_wdata;
            req_wstrb_q <= c_wstrb;
            req_index_q <= c_index;
            req_word_q  <= c_word;
            req_is_write_q <= |c_wstrb;
            burst_word_q   <= '0;
            if (!cacheable) begin
              state <= BYPASS;
            end else if (victim_dirty) begin
              // Evict before allocating: the victim's own tag addresses it.
              wb_tag_q <= tags[c_index];
              state    <= WRITEBACK;
            end else begin
              state <= REFILL;
            end
          end
        end

        WRITEBACK: begin
          if (m_ready) begin
            if (burst_word_q == WORD_BITS'(WORDS_PER_LINE - 1)) begin
              dirty_lines[req_index_q] <= 1'b0;
              burst_word_q <= '0;
              state <= REFILL;
            end else begin
              burst_word_q <= burst_word_q + 1'b1;
            end
          end
        end

        REFILL: begin
          if (m_ready) begin
            if (m_err) begin
              response_data_q <= 32'b0;
              response_err_q  <= 1'b1;
              valid_lines[req_index_q] <= 1'b0;
              state <= RESPOND;
            end else begin
              data[req_index_q][burst_word_q] <= m_rdata;
              if (burst_word_q == WORD_BITS'(WORDS_PER_LINE - 1)) begin
                valid_lines[req_index_q] <= 1'b1;
                tags[req_index_q] <= req_addr_q[31:OFFSET_BITS + INDEX_BITS];
                response_err_q <= 1'b0;
                // Write-allocate: fold the store that missed into the freshly
                // filled line rather than sending it to memory separately.
                if (req_is_write_q) begin
                  data[req_index_q][req_word_q] <=
                      merge_bytes(req_word_q == burst_word_q ? m_rdata
                                                : data[req_index_q][req_word_q],
                                  req_wdata_q, req_wstrb_q);
                  dirty_lines[req_index_q] <= 1'b1;
                  response_data_q <= 32'b0;
                end else begin
                  response_data_q <= req_word_q == burst_word_q ?
                                     m_rdata : data[req_index_q][req_word_q];
                end
                state <= RESPOND;
              end else begin
                burst_word_q <= burst_word_q + 1'b1;
              end
            end
          end
        end

        BYPASS: begin
          if (m_ready) begin
            response_data_q <= m_rdata;
            response_err_q  <= m_err;
            state <= RESPOND;
          end
        end

        RESPOND: if (c_valid) state <= IDLE;

        // Walk every line, writing back the dirty ones, then invalidate.
        DRAIN: begin
          if (drain_index_q >= LINES[INDEX_BITS:0]) begin
            valid_lines <= '0;
            dirty_lines <= '0;
            flush_pending_q <= 1'b0;
            state <= IDLE;
          end else if (!valid_lines[drain_idx] || !dirty_lines[drain_idx]) begin
            drain_index_q <= drain_index_q + 1'b1;
            burst_word_q  <= '0;
          end else if (m_ready) begin
            if (burst_word_q == WORD_BITS'(WORDS_PER_LINE - 1)) begin
              dirty_lines[drain_idx] <= 1'b0;
              drain_index_q <= drain_index_q + 1'b1;
              burst_word_q  <= '0;
            end else begin
              burst_word_q <= burst_word_q + 1'b1;
            end
          end
        end

        default: state <= IDLE;
      endcase
    end
  end
endmodule
