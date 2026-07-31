// QEMU-virt-compatible PLIC subset: a small, parameterised number of
// level-sensitive sources delivered to a small number of targets ("contexts").
// Two aXbus ports, as every shell device has, so an instruction fetch can
// proceed while the data port services the programming model.
//
//   0x00_0000 + 4*s      PRIORITY[s]  RW  3-bit priority; 0 means "never deliver"
//   0x00_1000            PENDING      RO  bit s set while source s is asserted
//                                         and has not been claimed
//   0x00_2000 + 0x80*c   ENABLE[c]    RW  bit s permits delivery to context c
//   0x20_0000 + 0x1000*c THRESHOLD[c] RW  3-bit; deliver only priority > threshold
//   0x20_0004 + 0x1000*c CLAIM[c]     RO  read returns the winning source id, or 0
//                        COMPLETE[c]  WO  write a source id to end its service
//
// The context stride and the enable stride are the QEMU-virt ones, so the
// reference shell's context 0 (hart 0 M-mode) and context 1 (hart 0 S-mode)
// land on exactly the addresses a driver written for `-machine virt` uses.
// That is what lets aXos claim and complete from S-mode with the same code on
// QEMU and on the RTL.
//
// Source 0 does not exist (the spec reserves id 0 to mean "no interrupt"), so
// bit 0 of PENDING and ENABLE reads as zero and PRIORITY[0] is not writable.
//
// Sources are level-sensitive, which is what the shell's devices actually
// provide: a role holds its completion line while STATUS.DONE is set, and the
// UART holds its line while a byte is waiting.  The gateway therefore masks a
// source between claim and complete rather than latching an edge -- a device
// that is still asserted when its handler completes simply becomes pending
// again, which is the behaviour a level-driven handler expects.
//
// "In service" is tracked per source rather than per context: a claim by any
// context takes the source out of the pending set, and a complete from any
// context returns it.  Enabling one source in two contexts is a configuration
// error rather than a supported mode, and this is the lenient reading of it.
module plic #(
  parameter logic [31:0] BASE    = 32'h0c00_0000,
  // Sources are numbered 1..SOURCES.  The reference shell wires two.
  parameter int unsigned SOURCES = 2,
  // Targets.  The reference shell has two: context 0 is hart 0 M-mode and
  // context 1 is hart 0 S-mode, matching QEMU-virt's single-hart layout.
  parameter int unsigned CONTEXTS = 2
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

  // Level-sensitive device lines, index 0 == source 1.
  input  logic [SOURCES-1:0] sources,
  // One delivery line per context, to the matching core interrupt input.
  output logic [CONTEXTS-1:0] irq_external
);
  localparam int unsigned NBITS = SOURCES + 1;   // index 0 reserved
  // Width of a source index into the NBITS-deep arrays.  $clog2(2) is 1, which
  // is already the minimum a two-entry array needs.
  localparam int unsigned IDXW = (NBITS <= 2) ? 1 : $clog2(NBITS);
  localparam int unsigned CTXW = (CONTEXTS <= 2) ? 1 : $clog2(CONTEXTS);

  localparam logic [21:0] PENDING_OFF    = 22'h00_1000;
  localparam logic [21:0] ENABLE_BASE    = 22'h00_2000;
  localparam logic [21:0] ENABLE_STRIDE  = 22'h00_0080;
  localparam logic [21:0] CONTEXT_BASE   = 22'h20_0000;
  localparam logic [21:0] CONTEXT_STRIDE = 22'h00_1000;

  logic [2:0]       priority_q [NBITS];
  logic [NBITS-1:0] enable_q   [CONTEXTS];
  logic [NBITS-1:0] claimed_q;              // in service: masked until complete
  logic [2:0]       threshold_q [CONTEXTS];

  wire [21:0] i_off = i_addr[21:0];
  wire [21:0] d_off = d_addr[21:0];
  wire i_in_range = i_addr >= BASE && i_addr - BASE < 32'h0040_0000;
  wire d_in_range = d_addr >= BASE && d_addr - BASE < 32'h0040_0000;

  // A priority register offset is 4*s for a source this instance implements.
  function automatic logic is_priority(input logic [21:0] off);
    is_priority = off < 22'(NBITS * 4) && off[1:0] == 2'b00 && off != 22'd0;
  endfunction

  function automatic logic is_enable(input logic [21:0] off);
    is_enable = 1'b0;
    for (int c = 0; c < CONTEXTS; c++)
      if (off == ENABLE_BASE + 22'(c) * ENABLE_STRIDE) is_enable = 1'b1;
  endfunction

  function automatic logic is_threshold(input logic [21:0] off);
    is_threshold = 1'b0;
    for (int c = 0; c < CONTEXTS; c++)
      if (off == CONTEXT_BASE + 22'(c) * CONTEXT_STRIDE) is_threshold = 1'b1;
  endfunction

  function automatic logic is_claim(input logic [21:0] off);
    is_claim = 1'b0;
    for (int c = 0; c < CONTEXTS; c++)
      if (off == CONTEXT_BASE + 22'(c) * CONTEXT_STRIDE + 22'd4) is_claim = 1'b1;
  endfunction

  // Which context an ENABLE offset names.  Zero for anything else, which is
  // harmless because every use is guarded by the matching is_* predicate.
  function automatic logic [CTXW-1:0] enable_ctx(input logic [21:0] off);
    enable_ctx = '0;
    for (int c = 0; c < CONTEXTS; c++)
      if (off == ENABLE_BASE + 22'(c) * ENABLE_STRIDE) enable_ctx = CTXW'(c);
  endfunction

  // Which context a THRESHOLD or CLAIM offset names.
  function automatic logic [CTXW-1:0] context_ctx(input logic [21:0] off);
    context_ctx = '0;
    for (int c = 0; c < CONTEXTS; c++)
      if (off == CONTEXT_BASE + 22'(c) * CONTEXT_STRIDE ||
          off == CONTEXT_BASE + 22'(c) * CONTEXT_STRIDE + 22'd4)
        context_ctx = CTXW'(c);
  endfunction

  function automatic logic known_offset(input logic [21:0] off);
    known_offset = is_priority(off) || off == PENDING_OFF || is_enable(off) ||
                   is_threshold(off) || is_claim(off);
  endfunction

  // ---- Gateway ---------------------------------------------------------------
  logic [NBITS-1:0] pending;
  always_comb begin
    pending = '0;
    for (int s = 1; s < NBITS; s++)
      pending[s] = sources[s-1] && !claimed_q[s];
  end

  // Eligible = pending, enabled for this context, and strictly above that
  // context's threshold.  The winner is the highest priority, and among equals
  // the lowest id -- the tie-break the spec requires, and the reason this loop
  // counts downwards.
  logic [NBITS-1:0] eligible  [CONTEXTS];
  logic [31:0]      claim_id  [CONTEXTS];
  always_comb begin
    for (int c = 0; c < CONTEXTS; c++) begin
      eligible[c] = '0;
      for (int s = 1; s < NBITS; s++)
        eligible[c][s] = pending[s] && enable_q[c][s] &&
                         priority_q[s] > threshold_q[c];
      claim_id[c] = 32'b0;
      for (int s = NBITS - 1; s >= 1; s--)
        if (eligible[c][s] &&
            (claim_id[c] == 32'b0 ||
             priority_q[s] >= priority_q[claim_id[c][IDXW-1:0]]))
          claim_id[c] = 32'(s);
    end
  end

  always_comb
    for (int c = 0; c < CONTEXTS; c++) irq_external[c] = |eligible[c];

  // ---- Register reads --------------------------------------------------------
  function automatic logic [31:0] read_offset(input logic [21:0] off);
    read_offset = 32'b0;
    if (is_priority(off))          read_offset = {29'b0, priority_q[off[IDXW+1:2]]};
    else if (off == PENDING_OFF)   read_offset = 32'(pending);
    else if (is_enable(off))       read_offset = 32'(enable_q[enable_ctx(off)]);
    else if (is_threshold(off))    read_offset = {29'b0, threshold_q[context_ctx(off)]};
    else if (is_claim(off))        read_offset = claim_id[context_ctx(off)];
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

  always_comb begin
    i_ready = i_valid;
    i_err   = i_valid && (!i_in_range || !known_offset(i_off) ||
                          i_addr[1:0] != 2'b00);
    i_rdata = read_offset(i_off);
    d_ready = d_valid;
    d_err   = d_valid && (!d_in_range || !known_offset(d_off) ||
                          d_addr[1:0] != 2'b00);
    d_rdata = read_offset(d_off);
  end

  // A claim is a side-effecting read: it takes the winning source into service
  // for the context whose CLAIM register was read.
  wire i_claims = i_valid && !i_err && !(|i_wstrb) && is_claim(i_off);
  wire d_claims = d_valid && !d_err && !(|d_wstrb) && is_claim(d_off);
  wire [CTXW-1:0] i_claim_ctx = context_ctx(i_off);
  wire [CTXW-1:0] d_claim_ctx = context_ctx(d_off);

  // ---- Register writes -------------------------------------------------------
  // The D port wins a same-cycle conflict, as it does in the CLINT: the CPU
  // programs devices through it and an I-port write here would be unusual.
  wire        wr_valid = (d_valid && !d_err && |d_wstrb) ||
                         (i_valid && !i_err && |i_wstrb);
  wire        wr_from_d = d_valid && !d_err && |d_wstrb;
  wire [21:0] wr_off   = wr_from_d ? d_off : i_off;
  wire [31:0] wr_data  = wr_from_d ? d_wdata : i_wdata;
  wire [3:0]  wr_strb  = wr_from_d ? d_wstrb : i_wstrb;

  always_ff @(posedge clk) begin
    if (rst) begin
      for (int s = 0; s < NBITS; s++) priority_q[s] <= 3'b0;
      for (int c = 0; c < CONTEXTS; c++) begin
        enable_q[c]    <= '0;
        threshold_q[c] <= 3'b0;
      end
      claimed_q <= '0;
    end else begin
      if (wr_valid) begin
        // Bit-selecting a function result directly is not accepted by every
        // frontend, so the merge lands in a local first.  Only the low bits of
        // each register are implemented, so the rest of the merge is discarded.
        /* verilator lint_off UNUSED */
        logic [31:0] merged;
        /* verilator lint_on UNUSED */
        if (is_priority(wr_off)) begin
          merged = merge_bytes({29'b0, priority_q[wr_off[IDXW+1:2]]}, wr_data, wr_strb);
          priority_q[wr_off[IDXW+1:2]] <= merged[2:0];
        end else if (is_enable(wr_off)) begin
          merged = merge_bytes(32'(enable_q[enable_ctx(wr_off)]), wr_data, wr_strb);
          // Bit 0 is reserved and stays clear whatever is written.
          enable_q[enable_ctx(wr_off)] <=
              merged[NBITS-1:0] & ~{{(NBITS-1){1'b0}}, 1'b1};
        end else if (is_threshold(wr_off)) begin
          merged = merge_bytes({29'b0, threshold_q[context_ctx(wr_off)]}, wr_data, wr_strb);
          threshold_q[context_ctx(wr_off)] <= merged[2:0];
        end else if (is_claim(wr_off)) begin
          // Complete: end service for the written id.  An id this instance does
          // not implement is ignored rather than faulted, which is what a
          // handler writing back a stale claim should get.
          if (wr_data != 32'b0 && wr_data < 32'(NBITS))
            claimed_q[wr_data[IDXW-1:0]] <= 1'b0;
        end
      end
      // A claim read takes effect even in the same cycle as a write elsewhere.
      // The D port wins if both ports claim at once, matching the write rule.
      if (d_claims && claim_id[d_claim_ctx] != 32'b0)
        claimed_q[claim_id[d_claim_ctx][IDXW-1:0]] <= 1'b1;
      else if (i_claims && claim_id[i_claim_ctx] != 32'b0)
        claimed_q[claim_id[i_claim_ctx][IDXW-1:0]] <= 1'b1;
    end
  end

  // verilator lint_off UNUSED
  wire unused = &{1'b0, i_addr[31:22], d_addr[31:22]};
  // verilator lint_on UNUSED
endmodule
