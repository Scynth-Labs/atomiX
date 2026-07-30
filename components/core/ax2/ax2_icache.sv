// ax2_icache: the instruction front end -- a direct-mapped instruction cache
// that owns the fetch PC and delivers up to two instructions per cycle.
//
// This module is what makes dual issue worth having.  The aXbus fetch port is
// 32 bits wide, so a core fetching straight off the bus can never sustain more
// than one instruction per cycle no matter how wide its back end is.  A cache
// line here is four words; a hit reads the whole line out of one block RAM and
// hands the pipeline the two words at the fetch PC, so a 2-wide back end can
// actually be fed.
//
// Storage is synchronous-read so the arrays map to FPGA block RAM rather than
// fabric flops (an asynchronous-read array cannot be packed into block RAM --
// the same constraint the main memory hit).  A lookup is therefore pipelined:
// the index goes to the arrays in one cycle and the tag comparison happens in
// the next, against the registered address.  That one cycle is the fetch stage.
//
// The module owns the fetch pointer rather than taking it from the core: the
// sequencing of accept / hold / refill / redirect is all interlocked with the
// array pipeline, and keeping it in one place is what stops those interactions
// from leaking into the core's hazard logic.
// Branch prediction lives here too, for the same reason: a predictor that is
// consulted anywhere but the fetch stage cannot steer the next fetch without
// costing a bubble, which is most of what it was meant to save.  The BTB is a
// direct-mapped bundle predictor -- one entry per fetch bundle, recording which
// slot held the branch, where it went, and a 2-bit hysteresis counter -- so a
// single combinational lookup covers both issue slots.
module ax2_icache #(
  parameter int unsigned SIZE_KB     = 2,
  // 0 disables prediction entirely (every taken branch costs a redirect).
  parameter int unsigned BTB_ENTRIES = 32,
  parameter logic [31:0] RESET_PC    = 32'h8000_0000
) (
  input  logic        clk,
  input  logic        rst,

  // Redirect the fetch stream (branch mispredict, trap, MRET, fence.i restart).
  input  logic        redirect,
  input  logic [31:0] redirect_pc,
  // The consumer cannot take a delivery this cycle; hold the current output.
  input  logic        stall,
  // The consumer is taking BOTH delivered instructions.  When it takes only the
  // first -- because the pair could not dual-issue -- the fetch pointer must
  // advance by one instruction, not two, or the second word is silently lost.
  input  logic        take_two,
  // Invalidate everything (fence.i).
  input  logic        flush,

  output logic        out_valid,
  output logic [31:0] out_pc,
  output logic [31:0] out_insn0,
  output logic        out_insn1_valid,
  output logic [31:0] out_insn1,
  output logic        out_err,          // fetch bus error at out_pc

  // Prediction carried alongside the delivered bundle, so the core can tell
  // whether the branch it resolves went the way fetch already assumed.
  output logic        out_pred_taken,
  output logic        out_pred_slot,    // which slot the predicted branch is in
  output logic [31:0] out_pred_target,

  // Resolution feedback from the execute stage.
  input  logic        upd_valid,
  input  logic [31:0] upd_pc,           // bundle base PC the branch was fetched with
  input  logic        upd_slot,
  input  logic        upd_taken,
  input  logic [31:0] upd_target,

  output logic        bus_valid,
  output logic [31:0] bus_addr,
  input  logic        bus_ready,
  input  logic [31:0] bus_rdata,
  input  logic        bus_err
);
  localparam int unsigned LINE_WORDS = 4;
  localparam int unsigned SETS     = (SIZE_KB * 1024) / (LINE_WORDS * 4);
  localparam int unsigned IDX_BITS = $clog2(SETS);
  localparam int unsigned TAG_BITS = 32 - 4 - IDX_BITS;

  // Address-slicing helpers deliberately use only part of the address; the
  // discarded bits are the offset and tag halves the other helper takes.
  /* verilator lint_off UNUSED */
  function automatic logic [IDX_BITS-1:0] idx_of(input logic [31:0] a);
    idx_of = a[4 + IDX_BITS - 1 : 4];
  endfunction
  function automatic logic [TAG_BITS-1:0] tag_of(input logic [31:0] a);
    tag_of = a[31 : 4 + IDX_BITS];
  endfunction
  /* verilator lint_on UNUSED */

  logic [127:0]         data_ram [0:SETS-1];
  logic [TAG_BITS-1:0]  tag_ram  [0:SETS-1];
  logic [SETS-1:0]      valid_q;

  logic [127:0]         data_out_q;
  logic [TAG_BITS-1:0]  tag_out_q;

  logic [31:0] pc_q;          // address to look up next
  logic [31:0] acc_pc_q;      // address whose lookup lands this cycle
  logic        acc_valid_q;
  logic        acc_err_q;     // the lookup resolved to a fetch bus error

  // ---- Branch target buffer --------------------------------------------------
  localparam int unsigned BTB_ON   = (BTB_ENTRIES > 0) ? 1 : 0;
  localparam int unsigned BTB_SETS = (BTB_ENTRIES > 0) ? BTB_ENTRIES : 1;
  // BTB_ENTRIES = 0 disables prediction, but the arrays are still elaborated
  // (BTB_ON gates their use), so the index must stay at least one bit wide --
  // a zero-width range is an elaboration error, not an empty array.
  localparam int unsigned BTB_IDX  = (BTB_SETS <= 1) ? 1 : $clog2(BTB_SETS);
  localparam int unsigned BTB_TAG  = 32 - 2 - BTB_IDX;

  logic [BTB_SETS-1:0]     btb_valid_q;
  logic [BTB_TAG-1:0]      btb_tag_q    [0:BTB_SETS-1];
  logic [31:0]             btb_target_q [0:BTB_SETS-1];
  logic                    btb_slot_q   [0:BTB_SETS-1];
  logic [1:0]              btb_ctr_q    [0:BTB_SETS-1];

  /* verilator lint_off UNUSED */
  function automatic logic [BTB_IDX-1:0] btb_idx(input logic [31:0] a);
    btb_idx = a[2 + BTB_IDX - 1 : 2];
  endfunction
  function automatic logic [BTB_TAG-1:0] btb_tag(input logic [31:0] a);
    btb_tag = a[31 : 2 + BTB_IDX];
  endfunction
  /* verilator lint_on UNUSED */

  // Declared before the lookup below uses it: the assignment is further down
  // with the fetch pointer it belongs to, but a strict 1800-2017 frontend
  // (Slang, which the formal flow uses) requires the net to exist first.
  wire [31:0] fetch_addr;     // address the lookup launched this cycle uses

  // The lookup is combinational on the address being launched this cycle, so
  // the prediction is available in time to choose the *next* fetch address --
  // that is what makes a correct prediction cost zero cycles.
  wire [BTB_IDX-1:0] look_idx = btb_idx(fetch_addr);
  wire btb_hit = (BTB_ON != 0) && btb_valid_q[look_idx] &&
                 btb_tag_q[look_idx] == btb_tag(fetch_addr);
  // Keep update lookup values at module scope.  Some synthesis frontends do
  // not accept initialized automatic variables declared inside always_ff.
  wire [BTB_IDX-1:0] upd_idx = btb_idx(upd_pc);
  wire upd_hit = btb_valid_q[upd_idx] &&
                 btb_tag_q[upd_idx] == btb_tag(upd_pc);
  // 2-bit hysteresis: predict taken only in the two "taken" states, so a single
  // contrary outcome does not flip a well-established branch.
  wire btb_predict = btb_hit && btb_ctr_q[look_idx][1];

  logic        pred_taken_q;
  logic        pred_slot_q;
  logic [31:0] pred_target_q;

  typedef enum logic [1:0] { S_LOOK, S_REFILL, S_INSTALL } state_e;
  state_e state_q;
  logic [$clog2(LINE_WORDS)-1:0] fill_cnt_q;
  logic [127:0] fill_buf_q;
  logic         fill_err_q;
  logic [31:0]  fill_addr_q;

  // ---- Hit determination -----------------------------------------------------
  wire [IDX_BITS-1:0] acc_idx = idx_of(acc_pc_q);
  wire hit = acc_valid_q && valid_q[acc_idx] && tag_out_q == tag_of(acc_pc_q);

  wire [1:0]   word_sel = acc_pc_q[3:2];
  wire [31:0]  line_word [0:3];
  assign line_word[0] = data_out_q[31:0];
  assign line_word[1] = data_out_q[63:32];
  assign line_word[2] = data_out_q[95:64];
  assign line_word[3] = data_out_q[127:96];

  always_comb begin
    out_valid       = acc_valid_q && (hit || acc_err_q);
    out_pc          = acc_pc_q;
    out_err         = acc_err_q;
    out_insn0       = line_word[word_sel];
    // The second instruction is only free when it shares the line; a fetch PC
    // in the last word of a line delivers one instruction and the next lookup
    // picks up the following line.
    out_insn1_valid = hit && !acc_err_q && word_sel != 2'd3;
    out_insn1       = line_word[word_sel + 2'd1];
    // A branch predicted taken in slot 0 means slot 1 was never on the
    // predicted path: do not deliver it.
    if (pred_taken_q && pred_slot_q == 1'b0) out_insn1_valid = 1'b0;
    if (acc_err_q) begin
      out_insn0       = 32'b0;
      out_insn1_valid = 1'b0;
    end
    // A prediction on slot 1 only applies if slot 1 is actually being issued.
    out_pred_taken  = pred_taken_q && !acc_err_q &&
                      (pred_slot_q == 1'b0 || take_two);
    out_pred_slot   = pred_slot_q;
    out_pred_target = pred_target_q;
  end

  // The consumer took this delivery: advance past what was delivered, or to the
  // predicted target when fetch has assumed a branch is taken.
  wire accepted = out_valid && !stall;
  wire pred_applies = pred_taken_q && !acc_err_q &&
                      (pred_slot_q == 1'b0 || take_two);
  wire [31:0] next_seq_pc =
      pred_applies ? pred_target_q
                   : acc_pc_q + ((out_insn1_valid && take_two) ? 32'd8 : 32'd4);

  // A lookup is launched whenever the array pipeline is free to move.  The
  // address it launches with is the one *after* whatever is being accepted this
  // cycle, so a bundle that delivered two instructions advances by two -- the
  // second word must not be re-fetched as the next bundle's first.
  wire lookup_en = (state_q == S_LOOK) && (!out_valid || accepted);
  assign fetch_addr = accepted ? next_seq_pc : pc_q;

  // ---- Refill bus ------------------------------------------------------------
  always_comb begin
    bus_valid = (state_q == S_REFILL);
    bus_addr  = {fill_addr_q[31:4], fill_cnt_q, 2'b00};
  end

  always_ff @(posedge clk) begin
    if (lookup_en) begin
      data_out_q <= data_ram[idx_of(fetch_addr)];
      tag_out_q  <= tag_ram[idx_of(fetch_addr)];
    end
    if (state_q == S_INSTALL && !fill_err_q) begin
      data_ram[idx_of(fill_addr_q)] <= fill_buf_q;
      tag_ram[idx_of(fill_addr_q)]  <= tag_of(fill_addr_q);
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      pc_q        <= RESET_PC;
      acc_pc_q    <= 32'b0;
      acc_valid_q <= 1'b0;
      acc_err_q   <= 1'b0;
      valid_q     <= '0;
      state_q     <= S_LOOK;
      fill_cnt_q  <= '0;
      fill_buf_q  <= '0;
      fill_err_q  <= 1'b0;
      fill_addr_q <= 32'b0;
    end else begin
      if (flush) valid_q <= '0;

      unique case (state_q)
        S_LOOK: begin
          if (lookup_en) begin
            acc_pc_q      <= fetch_addr;
            acc_valid_q   <= 1'b1;
            acc_err_q     <= 1'b0;
            pc_q          <= fetch_addr;
            pred_taken_q  <= btb_predict;
            pred_slot_q   <= btb_slot_q[look_idx];
            pred_target_q <= btb_target_q[look_idx];
          end
          // A resolved miss starts a refill of the line the access needs.
          if (acc_valid_q && !hit && !acc_err_q) begin
            fill_addr_q  <= {acc_pc_q[31:4], 4'b0};
            fill_cnt_q   <= '0;
            fill_err_q   <= 1'b0;
            acc_valid_q  <= 1'b0;
            pred_taken_q <= 1'b0;
            pc_q         <= acc_pc_q;      // re-look-up this address after fill
            state_q      <= S_REFILL;
          end
        end

        S_REFILL: begin
          if (bus_ready) begin
            unique case (fill_cnt_q)
              2'd0: fill_buf_q[31:0]   <= bus_rdata;
              2'd1: fill_buf_q[63:32]  <= bus_rdata;
              2'd2: fill_buf_q[95:64]  <= bus_rdata;
              2'd3: fill_buf_q[127:96] <= bus_rdata;
            endcase
            if (bus_err) fill_err_q <= 1'b1;
            if (fill_cnt_q == 2'(LINE_WORDS - 1)) state_q <= S_INSTALL;
            else fill_cnt_q <= fill_cnt_q + 1'b1;
          end
        end

        S_INSTALL: begin
          if (fill_err_q) begin
            // Do not cache a line that faulted.  Present the fault to the core
            // as a delivery at the faulting PC so it takes a precise trap.
            acc_pc_q    <= pc_q;
            acc_valid_q <= 1'b1;
            acc_err_q   <= 1'b1;
          end else begin
            valid_q[idx_of(fill_addr_q)] <= 1'b1;
          end
          state_q <= S_LOOK;
        end

        default: state_q <= S_LOOK;
      endcase

      // A redirect outranks everything above: drop the in-flight lookup and
      // restart.  An in-progress refill is allowed to finish -- the line is
      // still worth having, and aborting mid-burst would desynchronise the bus.
      if (redirect) begin
        pc_q         <= redirect_pc;
        acc_valid_q  <= 1'b0;
        acc_err_q    <= 1'b0;
        pred_taken_q <= 1'b0;
      end

      // ---- BTB update from resolution -----------------------------------------
      // Allocate on a taken branch, and train the counter either way.  A
      // not-taken branch that saturates down stops predicting but keeps its
      // target, so a loop that is re-entered warms up again in one iteration.
      if (BTB_ON != 0 && upd_valid) begin
        if (upd_hit) begin
          if (upd_taken) begin
            btb_target_q[upd_idx] <= upd_target;
            btb_slot_q[upd_idx]   <= upd_slot;
            if (btb_ctr_q[upd_idx] != 2'b11)
              btb_ctr_q[upd_idx] <= btb_ctr_q[upd_idx] + 2'd1;
          end else if (btb_ctr_q[upd_idx] != 2'b00) begin
            btb_ctr_q[upd_idx] <= btb_ctr_q[upd_idx] - 2'd1;
          end
        end else if (upd_taken) begin
          btb_valid_q[upd_idx]  <= 1'b1;
          btb_tag_q[upd_idx]    <= btb_tag(upd_pc);
          btb_target_q[upd_idx] <= upd_target;
          btb_slot_q[upd_idx]   <= upd_slot;
          btb_ctr_q[upd_idx]    <= 2'b10;   // weakly taken on allocation
        end
      end
      if (flush) btb_valid_q <= '0;
    end
  end
endmodule
