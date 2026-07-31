// gpu1_engine: the scalable SIMT compute engine behind `role.gpu1`.
//
// It is the successor to `gpu_engine` (role.gpu-compute).  Two things change,
// and both exist to let the lane count actually scale:
//
//   1. Banked global memory.  `gmem` is split into NBANKS interleaved block
//      RAMs (bank = addr[log2B-1:0], index = addr >> log2B) behind a lane->bank
//      crossbar.  A coalesced access -- the common SIMT pattern where lane L
//      touches base+L -- hits NBANKS distinct banks and retires in one round
//      instead of one round per lane.  The old engine serviced exactly one lane
//      per cycle, which is why lanes past ~8 bought almost nothing.
//
//   2. A real control ISA.  Per-lane divergence via a structured IF/ELSE/ENDIF
//      mask stack, uniform and any-lane branches (so kernels can loop), compare
//      -set, integer divide, and a cross-lane shuffle.  The old ISA was
//      straight-line only.
//
// Execution model.  A job runs waves 0..ceil(NTHREADS/NLANES)-1.  In wave w,
// lane L runs thread tid = NLANES*w + L.  A lane is *executing* when it is both
// within the thread count (tid < NTHREADS -- the SIMT tail) and enabled by the
// divergence mask.  Non-executing lanes write no registers and perform no
// stores, and their loads are skipped.  All lanes share one PC.
//
// Registers are zeroed at the start of every wave.  This is stronger than the
// old engine's "undefined at entry" and it is deliberate: SHFL lets one lane
// read another lane's register file, including a lane that is masked off, so
// the architectural state has to be defined everywhere for the software oracle
// to be exact.
//
// Store ordering.  For a single STX the per-instruction global-store order is
// (wave, instruction, ascending lane): if two lanes write the same address the
// higher lane wins.  Same address implies same bank, and each bank serialises
// the lanes targeting it lowest-lane-first across rounds, so the highest lane
// is the last writer.  This invariant is what the on-core oracle checks and it
// must hold exactly for any NBANKS.
//
// Window layout, ISA encoding, and the driver contract are documented on the
// role wrapper and in sw/baremetal/include/role.h.
module gpu1_engine #(
  parameter logic [31:0] BASE = 32'h4000_0000,
  // Lane count.  Any value in [1, 64]; tid is formed by addition, so a
  // non-power-of-two lane count works.
  parameter int unsigned NLANES = 8,
  // Global-memory banks.  Must be a power of two and <= DATA_WORDS.  Peak
  // memory throughput is NBANKS words/round, so NBANKS == NLANES is the
  // configuration that makes a coalesced access single-round.
  parameter int unsigned NBANKS = 8,
  // Optional ISA groups, so a small part can drop what it cannot afford.
  parameter bit ENABLE_DIV  = 1'b1,   // DIV/REM/DIVU/REMU (iterative, 32 steps)
  parameter bit ENABLE_SHFL = 1'b1    // cross-lane shuffle (NLANES x NLANES mux)
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
  localparam int unsigned LANE_BITS = (NLANES <= 1) ? 1 : $clog2(NLANES);
  localparam int unsigned BANK_BITS = (NBANKS <= 1) ? 1 : $clog2(NBANKS);

  localparam logic [31:0] ROLE_ID      = 32'h4750_5531;  // "GPU1"
  localparam logic [31:0] ROLE_VERSION = 32'h0000_0001;

  localparam logic [15:0] OFF_ID       = 16'h0000;
  localparam logic [15:0] OFF_VERSION  = 16'h0004;
  localparam logic [15:0] OFF_DOORBELL = 16'h0008;
  localparam logic [15:0] OFF_STATUS   = 16'h000c;
  localparam logic [15:0] OFF_NTHREADS = 16'h0010;
  localparam logic [15:0] OFF_NINSN    = 16'h0014;
  localparam logic [15:0] OFF_COUNT    = 16'h0018;
  // Geometry/feature discovery: software reads this instead of being compiled
  // against a lane count, which is what lets one driver and one oracle serve
  // every tier.
  localparam logic [15:0] OFF_CAPS     = 16'h001c;
  localparam logic [15:0] PROG_BASE    = 16'h0100;
  localparam logic [15:0] DATA_BASE    = 16'h1000;

  localparam int unsigned PROG_WORDS = 128;   // branches need more room
  localparam int unsigned DATA_WORDS = 4096;
  localparam int unsigned BANK_WORDS = DATA_WORDS / NBANKS;
  localparam int unsigned IDX_BITS   = $clog2(BANK_WORDS);
  localparam int unsigned PC_BITS    = $clog2(PROG_WORDS);
  localparam int unsigned MAX_THREADS = NLANES * DATA_WORDS;
  localparam int unsigned MASK_DEPTH  = 8;    // IF/ELSE/ENDIF nesting depth

  localparam logic [31:0] CAPS = {8'(NLANES), 8'(NBANKS),
                                  14'b0, ENABLE_SHFL, ENABLE_DIV};

  // ---- Instruction set -------------------------------------------------------
  // Opcodes 0..18 are encoding-compatible with role.gpu-compute so an existing
  // straight-line kernel assembles unchanged.
  localparam logic [5:0]
    OP_HALT = 6'd0,  OP_TID  = 6'd1,  OP_LI   = 6'd2,  OP_MOV  = 6'd3,
    OP_LDX  = 6'd4,  OP_STX  = 6'd5,  OP_ADD  = 6'd6,  OP_SUB  = 6'd7,
    OP_MUL  = 6'd8,  OP_AND  = 6'd9,  OP_OR   = 6'd10, OP_XOR  = 6'd11,
    OP_SLL  = 6'd12, OP_SRL  = 6'd13, OP_SRA  = 6'd14, OP_MIN  = 6'd15,
    OP_MAX  = 6'd16, OP_ADDI = 6'd17, OP_MULI = 6'd18,
    // compare-set (rd = 0 or 1)
    OP_SEQ  = 6'd19, OP_SNE  = 6'd20, OP_SLT  = 6'd21, OP_SLTU = 6'd22,
    OP_SGE  = 6'd23,
    // structured per-lane divergence
    OP_IF   = 6'd24, OP_ELSE = 6'd25, OP_ENDIF= 6'd26,
    // control transfer (PC-relative, imm is signed and relative to this insn)
    OP_BRA  = 6'd27, OP_BRANY= 6'd28,
    // integer divide
    OP_DIV  = 6'd29, OP_REM  = 6'd30, OP_DIVU = 6'd31, OP_REMU = 6'd32,
    // cross-lane shuffle: lane L gets regs[ regs[L][rb] ][ra]
    OP_SHFL = 6'd33,
    // displaced addressing
    OP_LDXI = 6'd34, OP_STXI = 6'd35;

  localparam logic [2:0] E_IDLE = 3'd0, E_FETCH = 3'd1, E_DECODE = 3'd2,
                         E_EXEC = 3'd3, E_LDX   = 3'd4, E_STX    = 3'd5,
                         E_WAVE = 3'd6, E_DIV   = 3'd7;

  // ---- State -----------------------------------------------------------------
  logic [31:0] prog [0:PROG_WORDS-1];
  logic [31:0] regs [0:NLANES-1][0:7];

  logic [31:0] nthreads_q, ninsn_q, count_q;
  logic        busy_q, done_q;
  logic [2:0]  state_q;
  logic [15:0] job_threads_q;
  logic [PC_BITS:0] job_ninsn_q;
  logic [15:0] tid_base_q;
  logic [PC_BITS-1:0] pc_q;

  logic [31:0] prog_mmio_rdata_q;
  logic [31:0] data_mmio_rdata;    // combinational select over the bank outputs
  logic [31:0] prog_eng_rdata_q;
  logic        buf_pending_q;
  logic [31:0] insn_q;
  logic        wave_start_q;   // zero the register file on the first wave cycle

  // Divergence mask and its stack.
  logic [NLANES-1:0] div_mask_q;
  logic [NLANES-1:0] mask_stack_q [0:MASK_DEPTH-1];
  logic [$clog2(MASK_DEPTH):0] mask_sp_q;

  // Memory-round state.
  logic [NLANES-1:0] pend_q;                        // lanes still to service
  logic [NBANKS-1:0] cap_valid_q;                   // captures landing next cycle
  logic [LANE_BITS-1:0] cap_lane_q [0:NBANKS-1];

  // ---- MMIO decode -----------------------------------------------------------
  wire i_in_range = i_addr >= BASE && i_addr - BASE < 32'h0001_0000;
  wire d_in_range = d_addr >= BASE && d_addr - BASE < 32'h0001_0000;
  wire [15:0] i_off = i_addr[15:0];
  wire [15:0] d_off = d_addr[15:0];

  function automatic logic reg_offset(input logic [15:0] off);
    reg_offset = off == OFF_ID || off == OFF_VERSION || off == OFF_DOORBELL ||
                 off == OFF_STATUS || off == OFF_NTHREADS || off == OFF_NINSN ||
                 off == OFF_COUNT || off == OFF_CAPS;
  endfunction
  function automatic logic prog_offset(input logic [15:0] off);
    prog_offset = off >= PROG_BASE && off < PROG_BASE + 16'(4 * PROG_WORDS);
  endfunction
  function automatic logic data_offset(input logic [15:0] off);
    data_offset = off >= DATA_BASE && off < DATA_BASE + 16'(4 * DATA_WORDS);
  endfunction
  function automatic logic [31:0] read_reg(input logic [15:0] off);
    unique case (off)
      OFF_ID:       read_reg = ROLE_ID;
      OFF_VERSION:  read_reg = ROLE_VERSION;
      OFF_STATUS:   read_reg = {30'b0, done_q, busy_q};
      OFF_NTHREADS: read_reg = nthreads_q;
      OFF_NINSN:    read_reg = ninsn_q;
      OFF_COUNT:    read_reg = count_q;
      OFF_CAPS:     read_reg = CAPS;
      default:      read_reg = 32'b0;
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

  wire d_prog_hit = d_valid && d_in_range && prog_offset(d_off) &&
                    d_addr[1:0] == 2'b00;
  wire d_data_hit = d_valid && d_in_range && data_offset(d_off) &&
                    d_addr[1:0] == 2'b00;
  wire d_buf_hit  = d_prog_hit || d_data_hit;
  // Buffer reads wait while the engine owns the single physical RAM ports.
  // Writes retain the documented "accepted but dropped while busy" behavior.
  wire d_buf_read = d_buf_hit && d_wstrb == 4'b0 && !busy_q;

  wire [PC_BITS-1:0] d_prog_idx = PC_BITS'((d_off - PROG_BASE) >> 2);
  wire [11:0] d_data_word = 12'((d_off - DATA_BASE) >> 2);
  wire [BANK_BITS-1:0] d_data_bank = d_data_word[BANK_BITS-1:0];
  wire [IDX_BITS-1:0]  d_data_idx  = IDX_BITS'(d_data_word >> BANK_BITS);
  logic [BANK_BITS-1:0] d_data_bank_q;   // which bank the registered read came from

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
      if (d_buf_hit && busy_q && d_wstrb == 4'b0) begin
        d_ready = 1'b0;
      end else if (d_buf_hit && d_wstrb == 4'hf) begin
        d_ready = 1'b1;              // writes while BUSY are dropped, not faulted
      end else if (d_buf_read) begin
        d_ready = buf_pending_q;     // registered block-RAM read: one wait state
        d_rdata = d_prog_hit ? prog_mmio_rdata_q : data_mmio_rdata;
      end else if (d_in_range && reg_offset(d_off) && d_addr[1:0] == 2'b00) begin
        d_ready = 1'b1;
        d_rdata = read_reg(d_off);
      end else begin
        d_ready = 1'b1;
        d_err   = 1'b1;
      end
    end
  end

  // ---- Instruction decode ----------------------------------------------------
  wire [5:0]  op  = insn_q[31:26];
  wire [2:0]  rd  = insn_q[25:23];
  wire [2:0]  ra  = insn_q[22:20];
  wire [2:0]  rb  = insn_q[19:17];
  wire [31:0] imm = {{15{insn_q[16]}}, insn_q[16:0]};

  wire op_is_store = (op == OP_STX) || (op == OP_STXI);
  // PC-relative branch target.  Truncating two's-complement addition is exactly
  // right here: a negative imm wraps to the correct backward target.
  wire [PC_BITS-1:0] br_target = pc_q + PC_BITS'(imm);
  // Everything except stores, control, and the mask ops writes rd.
  wire op_writes_rd = !(op == OP_HALT || op_is_store || op == OP_IF ||
                        op == OP_ELSE || op == OP_ENDIF || op == OP_BRA ||
                        op == OP_BRANY);

  // ---- Execution mask --------------------------------------------------------
  logic [NLANES-1:0] tail_active;
  always_comb
    for (int l = 0; l < NLANES; l++)
      tail_active[l] = (tid_base_q + 16'(l)) < job_threads_q;
  wire [NLANES-1:0] exec_mask = tail_active & div_mask_q;

  // ---- Per-lane ALU ----------------------------------------------------------
  wire [31:0] mul_result [0:NLANES-1];
  generate
    for (genvar l = 0; l < NLANES; l++) begin : g_mul
      wire [31:0] mul_rhs = (op == OP_MULI) ? imm : regs[l][rb];
      ax_mul32_low u_mul (
        .a(regs[l][ra]),
        .b(mul_rhs),
        .y(mul_result[l])
      );
    end
  endgenerate

  function automatic logic [31:0] alu_result(
      input logic [5:0] o, input logic [31:0] a, input logic [31:0] b,
      input logic [31:0] im, input logic [15:0] tid,
      input logic [31:0] mul);
    unique case (o)
      OP_TID:  alu_result = {16'b0, tid};
      OP_LI:   alu_result = im;
      OP_MOV:  alu_result = a;
      OP_ADD:  alu_result = a + b;
      OP_SUB:  alu_result = a - b;
      OP_MUL:  alu_result = mul;
      OP_AND:  alu_result = a & b;
      OP_OR:   alu_result = a | b;
      OP_XOR:  alu_result = a ^ b;
      OP_SLL:  alu_result = a << b[4:0];
      OP_SRL:  alu_result = a >> b[4:0];
      OP_SRA:  alu_result = $signed(a) >>> b[4:0];
      OP_MIN:  alu_result = ($signed(a) < $signed(b)) ? a : b;
      OP_MAX:  alu_result = ($signed(a) > $signed(b)) ? a : b;
      OP_ADDI: alu_result = a + im;
      OP_MULI: alu_result = mul;
      OP_SEQ:  alu_result = {31'b0, a == b};
      OP_SNE:  alu_result = {31'b0, a != b};
      OP_SLT:  alu_result = {31'b0, $signed(a) < $signed(b)};
      OP_SLTU: alu_result = {31'b0, a < b};
      OP_SGE:  alu_result = {31'b0, $signed(a) >= $signed(b)};
      default: alu_result = 32'b0;
    endcase
  endfunction

  // Cross-lane shuffle: lane L reads lane (regs[L][rb] mod NLANES)'s ra.
  // Gated by ENABLE_SHFL because it is a full NLANES x NLANES mux.
  function automatic logic [31:0] shfl_result(input logic [LANE_BITS-1:0] l);
    logic [31:0] sel;
    sel = regs[l][rb];
    shfl_result = regs[sel % NLANES][ra];
  endfunction

  // ---- Global memory: NBANKS independent block RAMs ---------------------------
  // Host and engine accesses share one physical port per bank. The engine owns
  // the ports while BUSY; host reads wait and host writes are accepted/dropped.
  // Describing both modes in one always_ff is essential for GW5A BSRAM
  // inference--two separate processes flatten the banks into flip-flops.
  logic [IDX_BITS-1:0] bank_addr  [0:NBANKS-1];
  logic                bank_we    [0:NBANKS-1];
  logic [31:0]         bank_wdata [0:NBANKS-1];
  logic [31:0]         bank_rdata [0:NBANKS-1];

  generate
    for (genvar b = 0; b < NBANKS; b++) begin : g_bank
      logic [31:0] mem [0:BANK_WORDS-1];
      wire mmio_sel = d_data_hit && (d_data_bank == BANK_BITS'(b));
      wire host_port = !busy_q && mmio_sel;
      wire port_we = host_port ? (d_wstrb == 4'hf) : bank_we[b];
      wire [IDX_BITS-1:0] port_addr = host_port ? d_data_idx : bank_addr[b];
      wire [31:0] port_wdata = host_port ? d_wdata : bank_wdata[b];
      always_ff @(posedge clk) begin
        if (port_we) mem[port_addr] <= port_wdata;
        else         bank_rdata[b] <= mem[port_addr];
      end
    end
  endgenerate
  // The MMIO read data lands in the bank the *previous* cycle's address chose.
  assign data_mmio_rdata = bank_rdata[d_data_bank_q];

  // Program memory: port A MMIO, port B engine fetch.
  always_ff @(posedge clk) begin
    if (d_prog_hit && d_wstrb == 4'hf && !busy_q) prog[d_prog_idx] <= d_wdata;
    else if (d_prog_hit && d_wstrb == 4'b0)       prog_mmio_rdata_q <= prog[d_prog_idx];
  end
  always_ff @(posedge clk) prog_eng_rdata_q <= prog[pc_q];

  // ---- Lane -> bank crossbar --------------------------------------------------
  // Each lane's effective word address for this memory instruction.
  logic [11:0] lane_word [0:NLANES-1];
  always_comb
    for (int l = 0; l < NLANES; l++)
      lane_word[l] = 12'((op == OP_LDXI || op == OP_STXI)
                         ? (regs[l][ra] + imm) : regs[l][ra]);

  // Round arbitration: every bank picks the lowest-index lane still pending that
  // targets it.  Walking lanes downward makes the lowest index the last write,
  // hence the winner -- this is exactly the lowest-lane-first rule the store
  // ordering invariant depends on.
  logic [NBANKS-1:0]    sel_valid;
  logic [LANE_BITS-1:0] sel_lane [0:NBANKS-1];
  always_comb begin
    for (int b = 0; b < NBANKS; b++) begin
      sel_valid[b] = 1'b0;
      sel_lane[b]  = '0;
      for (int l = NLANES - 1; l >= 0; l--)
        if (pend_q[l] && lane_word[l][BANK_BITS-1:0] == BANK_BITS'(b)) begin
          sel_valid[b] = 1'b1;
          sel_lane[b]  = LANE_BITS'(l);
        end
    end
  end
  // Lanes serviced this round.
  logic [NLANES-1:0] issue_mask;
  always_comb begin
    issue_mask = '0;
    for (int b = 0; b < NBANKS; b++)
      if (sel_valid[b]) issue_mask[sel_lane[b]] = 1'b1;
  end

  wire mem_round_active = (state_q == E_LDX) || (state_q == E_STX);
  always_comb begin
    for (int b = 0; b < NBANKS; b++) begin
      bank_we[b]    = 1'b0;
      bank_wdata[b] = 32'b0;
      bank_addr[b]  = IDX_BITS'(d_data_idx);   // idle: follow the MMIO index
      if (mem_round_active && sel_valid[b]) begin
        bank_addr[b]  = IDX_BITS'(lane_word[sel_lane[b]] >> BANK_BITS);
        bank_we[b]    = (state_q == E_STX);
        bank_wdata[b] = regs[sel_lane[b]][rb];
      end
    end
  end

  // ---- Iterative divider (all lanes step together) ----------------------------
  // Restoring division, 32 iterations, one shared step counter.  Lanes iterate
  // in parallel, so a divide costs 32 cycles regardless of lane count.
  logic [5:0]  div_step_q;
  logic [31:0] div_quo_q [0:NLANES-1];
  logic [31:0] div_rem_q [0:NLANES-1];
  logic [31:0] div_den_q [0:NLANES-1];
  logic        div_qneg_q [0:NLANES-1];
  logic        div_rneg_q [0:NLANES-1];
  logic        div_dz_q [0:NLANES-1];      // divide-by-zero: RISC-V-style result
  logic [31:0] div_num_abs [0:NLANES-1];

  wire div_signed = (op == OP_DIV) || (op == OP_REM);

  // ---- Job control -----------------------------------------------------------
  task automatic apply_reg_write(input logic [15:0] off,
                                 input logic [31:0] wdata,
                                 input logic [3:0] strb);
    unique case (off)
      OFF_DOORBELL: begin
        if (!busy_q) begin
          done_q <= 1'b0;
          if (nthreads_q == 32'b0) begin
            done_q  <= 1'b1;
            count_q <= count_q + 32'd1;
          end else begin
            busy_q        <= 1'b1;
            job_threads_q <= (nthreads_q > 32'(MAX_THREADS)) ?
                             16'(MAX_THREADS) : nthreads_q[15:0];
            job_ninsn_q   <= (ninsn_q > PROG_WORDS) ? (PC_BITS+1)'(PROG_WORDS)
                                                    : (PC_BITS+1)'(ninsn_q);
          end
        end
      end
      OFF_STATUS: if (strb[0] && wdata[1]) done_q <= 1'b0;
      OFF_NTHREADS: if (!busy_q) nthreads_q <= merge_bytes(nthreads_q, wdata, strb);
      OFF_NINSN:    if (!busy_q) ninsn_q    <= merge_bytes(ninsn_q, wdata, strb);
      default: ;
    endcase
  endtask

  // Start a wave: zero the register file, reset PC and divergence state.
  task automatic start_wave();
    pc_q       <= '0;
    div_mask_q <= '1;
    mask_sp_q  <= '0;
    wave_start_q <= 1'b1;
  endtask

  always_ff @(posedge clk) begin
    if (rst) begin
      nthreads_q    <= 32'b0;
      ninsn_q       <= 32'b0;
      count_q       <= 32'b0;
      busy_q        <= 1'b0;
      done_q        <= 1'b0;
      state_q       <= E_IDLE;
      job_threads_q <= 16'b0;
      job_ninsn_q   <= '0;
      tid_base_q    <= 16'b0;
      pc_q          <= '0;
      buf_pending_q <= 1'b0;
      insn_q        <= 32'b0;
      div_mask_q    <= '1;
      mask_sp_q     <= '0;
      pend_q        <= '0;
      cap_valid_q   <= '0;
      div_step_q    <= 6'b0;
      wave_start_q  <= 1'b0;
      d_data_bank_q <= '0;
    end else begin
      buf_pending_q <= d_buf_read && !buf_pending_q;
      if (d_data_hit && d_wstrb == 4'b0) d_data_bank_q <= d_data_bank;

      // Same conflict rule as the reference CLINT: if both ports write a
      // register in one cycle the D port wins.
      if (i_valid && !i_err && |i_wstrb) apply_reg_write(i_off, i_wdata, i_wstrb);
      if (d_valid && !d_err && |d_wstrb && !d_buf_hit)
        apply_reg_write(d_off, d_wdata, d_wstrb);

      // Register-file zeroing happens on the cycle after a wave launch, in
      // parallel with the first instruction fetch.
      if (wave_start_q) begin
        for (int l = 0; l < NLANES; l++)
          for (int r = 0; r < 8; r++) regs[l][r] <= 32'b0;
        wave_start_q <= 1'b0;
      end

      if (busy_q) begin
        unique case (state_q)
          E_IDLE: begin
            tid_base_q <= 16'b0;
            start_wave();
            state_q <= E_FETCH;
          end

          E_FETCH: state_q <= E_DECODE;   // prog_eng_rdata_q settles this cycle

          E_DECODE: begin
            insn_q <= prog_eng_rdata_q;
            // The `op` wires still show the *previous* instruction, so route
            // from the word being latched.
            if (prog_eng_rdata_q[31:26] == OP_HALT ||
                (PC_BITS+1)'(pc_q) >= job_ninsn_q)
              state_q <= E_WAVE;
            else if (prog_eng_rdata_q[31:26] == OP_LDX ||
                     prog_eng_rdata_q[31:26] == OP_LDXI) begin
              pend_q      <= exec_mask;
              cap_valid_q <= '0;
              state_q     <= E_LDX;
            end else if (prog_eng_rdata_q[31:26] == OP_STX ||
                         prog_eng_rdata_q[31:26] == OP_STXI) begin
              pend_q  <= exec_mask;
              state_q <= E_STX;
            end else if (ENABLE_DIV &&
                         (prog_eng_rdata_q[31:26] == OP_DIV ||
                          prog_eng_rdata_q[31:26] == OP_REM ||
                          prog_eng_rdata_q[31:26] == OP_DIVU ||
                          prog_eng_rdata_q[31:26] == OP_REMU)) begin
              div_step_q <= 6'd0;
              state_q    <= E_DIV;
            end else
              state_q <= E_EXEC;
          end

          E_EXEC: begin
            unique case (op)
              OP_IF: begin
                // Push the current mask, then narrow it to lanes whose ra != 0.
                if (mask_sp_q < MASK_DEPTH[$clog2(MASK_DEPTH):0]) begin
                  mask_stack_q[mask_sp_q[$clog2(MASK_DEPTH)-1:0]] <= div_mask_q;
                  mask_sp_q <= mask_sp_q + 1'b1;
                  for (int l = 0; l < NLANES; l++)
                    div_mask_q[l] <= div_mask_q[l] && (regs[l][ra] != 32'b0);
                end
              end
              OP_ELSE: begin
                // Complement within the enclosing mask.
                if (mask_sp_q != '0)
                  for (int l = 0; l < NLANES; l++)
                    div_mask_q[l] <=
                      mask_stack_q[mask_sp_q[$clog2(MASK_DEPTH)-1:0] - 1][l] &&
                      !div_mask_q[l];
              end
              OP_ENDIF: begin
                if (mask_sp_q != '0) begin
                  div_mask_q <= mask_stack_q[mask_sp_q[$clog2(MASK_DEPTH)-1:0] - 1];
                  mask_sp_q  <= mask_sp_q - 1'b1;
                end
              end
              OP_SHFL: begin
                if (ENABLE_SHFL)
                  for (int l = 0; l < NLANES; l++)
                    if (exec_mask[l]) regs[l][rd] <= shfl_result(LANE_BITS'(l));
              end
              default: begin
                for (int l = 0; l < NLANES; l++)
                  if (op_writes_rd && exec_mask[l])
                    regs[l][rd] <= alu_result(op, regs[l][ra], regs[l][rb], imm,
                                              tid_base_q + 16'(l), mul_result[l]);
              end
            endcase

            // Control transfer.  BRA is uniform; BRANY jumps when any executing
            // lane's ra is non-zero, which is how a data-dependent loop stays
            // alive until every lane has exited.
            if (op == OP_BRA)
              pc_q <= br_target;
            else if (op == OP_BRANY) begin
              logic any_lane;
              any_lane = 1'b0;
              for (int l = 0; l < NLANES; l++)
                if (exec_mask[l] && regs[l][ra] != 32'b0) any_lane = 1'b1;
              pc_q <= any_lane ? br_target : pc_q + 1'b1;
            end else
              pc_q <= pc_q + 1'b1;
            state_q <= E_FETCH;
          end

          // Load: one round per cycle, capture pipelined one cycle behind the
          // presented addresses, so R rounds cost R+1 cycles.
          E_LDX: begin
            for (int b = 0; b < NBANKS; b++)
              if (cap_valid_q[b]) regs[cap_lane_q[b]][rd] <= bank_rdata[b];
            cap_valid_q <= sel_valid;
            for (int b = 0; b < NBANKS; b++) cap_lane_q[b] <= sel_lane[b];
            pend_q <= pend_q & ~issue_mask;
            if (pend_q == '0 && cap_valid_q == '0) begin
              pc_q    <= pc_q + 1'b1;
              state_q <= E_FETCH;
            end
          end

          // Store: writes land in the bank always_ff this cycle, so a round is
          // a single cycle and no capture pipeline is needed.
          E_STX: begin
            pend_q <= pend_q & ~issue_mask;
            if (pend_q == '0) begin
              pc_q    <= pc_q + 1'b1;
              state_q <= E_FETCH;
            end
          end

          E_DIV: begin
            // Keep the entire divider cone behind the feature parameter. The
            // decoder cannot enter this state when division is disabled, but
            // the explicit constant guard also lets synthesis delete its
            // per-lane variable selects, subtractors, and state registers.
            if (!ENABLE_DIV) begin
              state_q <= E_IDLE;
            end else if (div_step_q == 6'd0) begin
              // Seed: take magnitudes, remember result signs.
              for (int l = 0; l < NLANES; l++) begin
                logic neg_n, neg_d;
                neg_n = div_signed && regs[l][ra][31];
                neg_d = div_signed && regs[l][rb][31];
                div_num_abs[l] <= neg_n ? (~regs[l][ra] + 32'd1) : regs[l][ra];
                div_den_q[l]   <= neg_d ? (~regs[l][rb] + 32'd1) : regs[l][rb];
                div_quo_q[l]   <= 32'b0;
                div_rem_q[l]   <= 32'b0;
                div_qneg_q[l]  <= neg_n ^ neg_d;
                div_rneg_q[l]  <= neg_n;
                div_dz_q[l]    <= regs[l][rb] == 32'b0;
              end
              div_step_q <= 6'd1;
            end else if (div_step_q <= 6'd32) begin
              for (int l = 0; l < NLANES; l++) begin
                logic [31:0] r_next;
                r_next = {div_rem_q[l][30:0],
                          div_num_abs[l][32 - int'(div_step_q)]};
                if (r_next >= div_den_q[l] && div_den_q[l] != 32'b0) begin
                  div_rem_q[l] <= r_next - div_den_q[l];
                  div_quo_q[l] <= {div_quo_q[l][30:0], 1'b1};
                end else begin
                  div_rem_q[l] <= r_next;
                  div_quo_q[l] <= {div_quo_q[l][30:0], 1'b0};
                end
              end
              div_step_q <= div_step_q + 6'd1;
            end else begin
              // Writeback with RISC-V divide-by-zero semantics: quotient all
              // ones (or -1 signed), remainder the dividend.
              for (int l = 0; l < NLANES; l++)
                if (exec_mask[l]) begin
                  logic [31:0] q, r;
                  q = div_qneg_q[l] ? (~div_quo_q[l] + 32'd1) : div_quo_q[l];
                  r = div_rneg_q[l] ? (~div_rem_q[l] + 32'd1) : div_rem_q[l];
                  if (div_dz_q[l]) begin
                    q = 32'hffff_ffff;
                    r = regs[l][ra];
                  end
                  regs[l][rd] <= (op == OP_DIV || op == OP_DIVU) ? q : r;
                end
              pc_q    <= pc_q + 1'b1;
              state_q <= E_FETCH;
            end
          end

          E_WAVE: begin
            if (16'(tid_base_q) + 16'(NLANES) >= job_threads_q) begin
              busy_q  <= 1'b0;
              done_q  <= 1'b1;
              count_q <= count_q + 32'd1;
              state_q <= E_IDLE;
            end else begin
              tid_base_q <= tid_base_q + 16'(NLANES);
              start_wave();
              state_q <= E_FETCH;
            end
          end

          default: state_q <= E_IDLE;
        endcase
      end
    end
  end
endmodule
