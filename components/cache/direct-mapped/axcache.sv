// Small direct-mapped, write-through aXbus cache.  It sits between one core
// port and the SoC bus: RAM reads fill a line, writes bypass to memory and
// invalidate their local line, and non-RAM requests (MMIO, ROM, errors) pass
// through unchanged.  `flush` invalidates all lines; soc_top cross-connects
// it to writes from the other core port so I/D views remain coherent.
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
  // Write-through: no dirty data can outlive a write, so a flush completes
  // in the cycle it is requested and this is always low.
  assign flush_busy = 1'b0;
  localparam int unsigned LINE_BYTES = WORDS_PER_LINE * 4;
  localparam int unsigned WORD_BITS = $clog2(WORDS_PER_LINE);
  localparam int unsigned INDEX_BITS = $clog2(LINES);
  localparam int unsigned OFFSET_BITS = $clog2(LINE_BYTES);

  typedef enum logic [2:0] {IDLE, REFILL, WRITE, BYPASS, RESPOND} state_e;
  state_e state;

  logic [LINES-1:0] valid_lines;
  logic [31:0] tags [0:LINES-1];
  logic [31:0] data [0:LINES-1][0:WORDS_PER_LINE-1];

  logic [31:0] req_addr_q, req_wdata_q, line_base_q, response_data_q;
  logic [3:0] req_wstrb_q;
  logic [INDEX_BITS-1:0] req_index_q;
  logic [WORD_BITS-1:0] req_word_q, fill_word_q;
  logic response_err_q, discard_fill_q;

  wire cacheable = c_addr >= CACHE_BASE && c_addr - CACHE_BASE < CACHE_BYTES &&
                   c_addr[1:0] == 2'b00;
  wire [INDEX_BITS-1:0] c_index = c_addr[OFFSET_BITS + INDEX_BITS - 1:OFFSET_BITS];
  wire [WORD_BITS-1:0] c_word = c_addr[OFFSET_BITS-1:2];
  wire [31:0] c_tag = c_addr >> (OFFSET_BITS + INDEX_BITS);
  wire hit = cacheable && valid_lines[c_index] && tags[c_index] == c_tag;
  // Only IDLE may answer from the array.  A refill writes the line's words in
  // place as they arrive and only updates the tag at the end, so while it runs
  // the addressed line still advertises its previous occupant while already
  // holding some of the new one's data.  Answering a lookup then returns a
  // blend of two lines -- and because index bits are all that decide which line
  // a fetch and a page-table read share, that is exactly how a walker reading a
  // PTE collides with an instruction fetch.
  wire read_hit = c_valid && c_wstrb == 4'b0 && hit && !flush && state == IDLE;

  // A master may walk away from a request it has not yet been answered for:
  // the MMU abandons an in-flight page-table read when a trap redirect or an
  // sfence.vma changes the translation context under it, and then drives the
  // next request on the very next cycle.  The buffered response belongs to the
  // request that was latched, so it may only be handed back to that same
  // request -- otherwise the new one silently collects the old one's data,
  // which for a walker means a page-table entry read out of an instruction
  // line and a page fault on a perfectly valid mapping.
  wire respond_hold = state == RESPOND && c_valid &&
                      c_addr == req_addr_q && c_wstrb == req_wstrb_q;

  always_comb begin
    c_ready = read_hit || respond_hold;
    c_rdata = read_hit ? data[c_index][c_word] : response_data_q;
    c_err = respond_hold && response_err_q;

    m_valid = state == REFILL || state == WRITE || state == BYPASS;
    m_addr = req_addr_q;
    m_wdata = req_wdata_q;
    m_wstrb = req_wstrb_q;
    if (state == REFILL) begin
      m_addr = line_base_q + {{(30-WORD_BITS){1'b0}}, fill_word_q, 2'b00};
      m_wdata = 32'b0;
      m_wstrb = 4'b0;
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      state <= IDLE;
      valid_lines <= '0;
      response_data_q <= '0;
      response_err_q <= 1'b0;
      discard_fill_q <= 1'b0;
      req_addr_q <= '0;
      req_wdata_q <= '0;
      req_wstrb_q <= '0;
      req_index_q <= '0;
      req_word_q <= '0;
      line_base_q <= '0;
      fill_word_q <= '0;
    end else begin
      // A line being fetched while the other port writes could contain old
      // data.  Let the outstanding fetch finish (aXbus cannot be cancelled),
      // but do not install it in the cache.
      if (flush) begin
        valid_lines <= '0;
        if (state == REFILL) discard_fill_q <= 1'b1;
      end

      case (state)
        IDLE: begin
          if (c_valid && !read_hit) begin
            req_addr_q <= c_addr;
            req_wdata_q <= c_wdata;
            req_wstrb_q <= c_wstrb;
            req_index_q <= c_index;
            req_word_q <= c_word;
            if (!cacheable) begin
              state <= BYPASS;
            end else if (|c_wstrb) begin
              valid_lines[c_index] <= 1'b0;
              state <= WRITE;
            end else begin
              // Drop the victim up front rather than at the end of the burst:
              // the line stops describing anything the moment its words start
              // being overwritten, and a half-filled line must never look like
              // a hit.
              valid_lines[c_index] <= 1'b0;
              line_base_q <= {c_addr[31:OFFSET_BITS], {OFFSET_BITS{1'b0}}};
              fill_word_q <= '0;
              discard_fill_q <= 1'b0;
              state <= REFILL;
            end
          end
        end

        REFILL: begin
          if (m_ready) begin
            if (m_err) begin
              response_data_q <= 32'b0;
              response_err_q <= 1'b1;
              state <= RESPOND;
            end else begin
              data[req_index_q][fill_word_q] <= m_rdata;
              if (fill_word_q == WORD_BITS'(WORDS_PER_LINE - 1)) begin
                response_data_q <= req_word_q == fill_word_q ?
                                   m_rdata : data[req_index_q][req_word_q];
                response_err_q <= 1'b0;
                if (!flush && !discard_fill_q) begin
                  valid_lines[req_index_q] <= 1'b1;
                  tags[req_index_q] <= req_addr_q >> (OFFSET_BITS + INDEX_BITS);
                end
                state <= RESPOND;
              end else begin
                fill_word_q <= fill_word_q + 1'b1;
              end
            end
          end
        end

        WRITE, BYPASS: begin
          if (m_ready) begin
            response_data_q <= m_rdata;
            response_err_q <= m_err;
            state <= RESPOND;
          end
        end

        RESPOND: begin
          if (c_valid) state <= IDLE;
        end

        default: state <= IDLE;
      endcase
    end
  end
endmodule
