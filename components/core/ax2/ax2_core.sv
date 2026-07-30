// ax2_core: a dual-issue, in-order superscalar RV32IM machine-mode CPU.
//
// This is the engine behind `core.ax2`; the sizes below are set by that
// component's build-time parameters.  Where core.minimal trades
// throughput for area and core.pipeline5 is the scalar reference, ax2 exists to
// go faster than one instruction per cycle: it fetches a bundle of two through
// a block-RAM instruction cache, decodes and issues both in the same cycle when
// they are independent, and retires both through a 4-read / 2-write register
// file.
//
// Pipeline: F -> D -> X, three stages.
//
//   F  ax2_icache: a line lookup and the branch-target-buffer prediction that
//      chooses the next fetch address.  Owns the fetch pointer.
//   D  decode both slots, read four operands, decide whether the second slot
//      may issue alongside the first.
//   X  execute both, drive the data bus, resolve the branch, take traps, write
//      back.  Multi-cycle work (loads/stores, mul/div) holds the bundle here.
//
// Three stages rather than five is deliberate.  A shorter pipeline means the
// only forwarding path is X's result back into D's operand capture, and a
// branch mispredict costs two cycles instead of four.  The cost is a longer X
// stage; at the clock rates these FPGA targets run, that is the right trade.
//
// Dual-issue rules (all checked in D; violating any of them issues slot 0
// alone and re-presents slot 1 as the next bundle's slot 0):
//
//   - slot 1 may not read a register slot 0 writes (no intra-bundle forwarding)
//   - at most one memory operation per bundle (one data port)
//   - at most one mul/div per bundle (one iterative unit)
//   - a control transfer in slot 0 squashes slot 1: it is not on the taken path
//   - CSR, system, and illegal instructions issue alone, which keeps every trap
//     and every CSR side effect precise without a second commit path
//
// Scope: machine mode only, physical addressing, RV32IM + Zicsr, precise traps.
// The Sv32 MMU and S/U modes of core.pipeline5 are not implemented here, so ax2
// does not carry the reference core's lock-step cosim evidence.  It does carry
// its own bounded riscv-formal evidence through the two-channel RVFI trace
// below (`make -C formal check-ax2`).  See docs/design-checklist.md.
module ax2_core #(
  parameter logic [31:0] RESET_PC    = 32'h8000_0000,
  parameter bit          ENABLE_M    = 1'b1,
  // 2 is the superscalar configuration; 1 keeps the cache and the predictor but
  // retires one instruction per cycle, for a part that cannot afford slot 1.
  parameter int unsigned ISSUE_WIDTH = 2,
  parameter int unsigned ICACHE_KB   = 2,
  parameter int unsigned BTB_ENTRIES = 32
) (
  input  logic        clk,
  input  logic        rst,

  output logic        ibus_valid,
  output logic [31:0] ibus_addr,
  output logic [31:0] ibus_wdata,
  output logic [3:0]  ibus_wstrb,
  input  logic        ibus_ready,
  input  logic [31:0] ibus_rdata,
  input  logic        ibus_err,

  output logic        dbus_valid,
  output logic [31:0] dbus_addr,
  output logic [31:0] dbus_wdata,
  output logic [3:0]  dbus_wstrb,
  input  logic        dbus_ready,
  input  logic [31:0] dbus_rdata,
  input  logic        dbus_err,

  input  logic        irq_software,
  input  logic        irq_timer,
  input  logic        irq_external,

  output logic        trace_valid,
  output logic        trace_trap,
  output logic [31:0] trace_insn,

  // Two-channel RVFI.  Channel 0 is slot 0 and channel 1 is slot 1, which is
  // also program order: slot 1 is never valid without slot 0, and a slot-0 trap
  // squashes slot 1 entirely, so `rvfi_order` stays gapless without a reorder
  // network.  Fields are packed low-channel-first, as riscv-formal expects.
  output logic [1:0]   rvfi_valid,
  output logic [127:0] rvfi_order,
  output logic [63:0]  rvfi_insn,
  output logic [1:0]   rvfi_trap,
  output logic [1:0]   rvfi_halt,
  output logic [1:0]   rvfi_intr,
  output logic [3:0]   rvfi_mode,
  output logic [3:0]   rvfi_ixl,
  output logic [9:0]   rvfi_rs1_addr,
  output logic [9:0]   rvfi_rs2_addr,
  output logic [63:0]  rvfi_rs1_rdata,
  output logic [63:0]  rvfi_rs2_rdata,
  output logic [9:0]   rvfi_rd_addr,
  output logic [63:0]  rvfi_rd_wdata,
  output logic [63:0]  rvfi_pc_rdata,
  output logic [63:0]  rvfi_pc_wdata,
  output logic [63:0]  rvfi_mem_addr,
  output logic [7:0]   rvfi_mem_rmask,
  output logic [7:0]   rvfi_mem_wmask,
  output logic [63:0]  rvfi_mem_rdata,
  output logic [63:0]  rvfi_mem_wdata
);
  import axcore_pkg::*;
  import ax2_pkg::*;

  // The fetch port only ever reads: there is no page-table walker here.
  assign ibus_wdata = 32'b0;
  assign ibus_wstrb = 4'b0;

  // ---- Machine-mode CSR state -------------------------------------------------
  logic [31:0] mstatus_q, mtvec_q, mscratch_q, mepc_q, mcause_q, mtval_q, mie_q;
  logic [63:0] mcycle_q, minstret_q;
  localparam int MIE_B = 3, MPIE_B = 7;

  // ---- Front end --------------------------------------------------------------
  logic        redirect;
  logic [31:0] redirect_pc;
  logic        fe_flush;
  logic        d_stall;

  logic        ic_valid, ic_insn1_valid, ic_err;
  logic [31:0] ic_pc, ic_insn0, ic_insn1;
  logic        ic_pred_taken, ic_pred_slot;
  logic [31:0] ic_pred_target;

  logic        take_two;
  logic        btb_upd_valid, btb_upd_slot, btb_upd_taken;
  logic [31:0] btb_upd_pc, btb_upd_target;

  ax2_icache #(
    .SIZE_KB(ICACHE_KB), .BTB_ENTRIES(BTB_ENTRIES), .RESET_PC(RESET_PC)
  ) u_ic (
    .clk(clk), .rst(rst),
    .redirect(redirect), .redirect_pc(redirect_pc),
    .stall(d_stall), .take_two(take_two), .flush(fe_flush),
    .out_valid(ic_valid), .out_pc(ic_pc),
    .out_insn0(ic_insn0), .out_insn1_valid(ic_insn1_valid), .out_insn1(ic_insn1),
    .out_err(ic_err),
    .out_pred_taken(ic_pred_taken), .out_pred_slot(ic_pred_slot),
    .out_pred_target(ic_pred_target),
    .upd_valid(btb_upd_valid), .upd_pc(btb_upd_pc), .upd_slot(btb_upd_slot),
    .upd_taken(btb_upd_taken), .upd_target(btb_upd_target),
    .bus_valid(ibus_valid), .bus_addr(ibus_addr), .bus_ready(ibus_ready),
    .bus_rdata(ibus_rdata), .bus_err(ibus_err)
  );

  // ---- Decode stage -----------------------------------------------------------
  dec_t d0, d1;
  logic [31:0] imm0, imm1;
  // Only slot 1's operand-use flags feed a decision (the RAW check against
  // slot 0); slot 0's are decoded but have no consumer in an in-order machine.
  /* verilator lint_off UNUSED */
  logic d0_uses_rs1, d0_uses_rs2;
  /* verilator lint_on UNUSED */
  logic d1_uses_rs1, d1_uses_rs2;

  // Both slots decode through the same verified decoder the reference core
  // uses; dual issue duplicates the instance, never the decode rules.
  decoder #(.ENABLE_M(ENABLE_M)) u_dec0 (
    .insn(ic_insn0), .illegal(d0.illegal), .rd_we(d0.rd_we),
    .opa_sel(d0.opa_sel), .opb_sel(d0.opb_sel), .imm_sel(d0.imm_sel),
    .alu_op(d0.alu_op), .wb_sel(d0.wb_sel), .mem_op(d0.mem_op),
    .br_sel(d0.br_sel), .csr_op(d0.csr_op), .csr_imm(d0.csr_imm),
    .muldiv(d0.muldiv), .sys(d0.sys),
    .uses_rs1(d0_uses_rs1), .uses_rs2(d0_uses_rs2));
  decoder #(.ENABLE_M(ENABLE_M)) u_dec1 (
    .insn(ic_insn1), .illegal(d1.illegal), .rd_we(d1.rd_we),
    .opa_sel(d1.opa_sel), .opb_sel(d1.opb_sel), .imm_sel(d1.imm_sel),
    .alu_op(d1.alu_op), .wb_sel(d1.wb_sel), .mem_op(d1.mem_op),
    .br_sel(d1.br_sel), .csr_op(d1.csr_op), .csr_imm(d1.csr_imm),
    .muldiv(d1.muldiv), .sys(d1.sys),
    .uses_rs1(d1_uses_rs1), .uses_rs2(d1_uses_rs2));

  immdec u_imm0 (.insn(ic_insn0), .sel(d0.imm_sel), .imm(imm0));
  immdec u_imm1 (.insn(ic_insn1), .sel(d1.imm_sel), .imm(imm1));

  wire [4:0] rs1_0 = ic_insn0[19:15], rs2_0 = ic_insn0[24:20], rd_0 = ic_insn0[11:7];
  wire [4:0] rs1_1 = ic_insn1[19:15], rs2_1 = ic_insn1[24:20], rd_1 = ic_insn1[11:7];

  logic [31:0] rf_a0, rf_b0, rf_a1, rf_b1;
  logic        wb_we0, wb_we1;
  logic [4:0]  wb_rd0, wb_rd1;
  logic [31:0] wb_data0, wb_data1;

  ax2_regfile u_rf (
    .clk(clk),
    .we0(wb_we0), .waddr0(wb_rd0), .wdata0(wb_data0),
    .we1(wb_we1), .waddr1(wb_rd1), .wdata1(wb_data1),
    .raddr0(rs1_0), .rdata0(rf_a0), .raddr1(rs2_0), .rdata1(rf_b0),
    .raddr2(rs1_1), .rdata2(rf_a1), .raddr3(rs2_1), .rdata3(rf_b1));

  // ---- Issue decision ---------------------------------------------------------
  wire d0_ctrl   = d0.br_sel != BR_NONE;
  wire d0_simple = d0.sys == SYS_NONE && d0.csr_op == CSR_NONE && !d0.illegal;
  wire d1_simple = d1.sys == SYS_NONE && d1.csr_op == CSR_NONE && !d1.illegal;
  wire raw_1on0  = d0.rd_we && rd_0 != 5'd0 &&
                   ((d1_uses_rs1 && rs1_1 == rd_0) ||
                    (d1_uses_rs2 && rs2_1 == rd_0));
  wire both_mem  = d0.mem_op != MEM_NONE && d1.mem_op != MEM_NONE;
  wire both_md   = d0.muldiv && d1.muldiv;

  wire dual_ok = (ISSUE_WIDTH >= 2) && ic_insn1_valid && !ic_err &&
                 d0_simple && d1_simple && !d0_ctrl &&
                 !raw_1on0 && !both_mem && !both_md;

  // The fetch pointer advances by two only when slot 1 really issues.
  assign take_two = dual_ok;

  // ---- D/X pipeline register ---------------------------------------------------
  logic        x_v0_q, x_v1_q;
  logic [31:0] x_pc0_q, x_insn0_q, x_insn1_q;
  dec_t        x_d0_q, x_d1_q;
  logic [31:0] x_a0_q, x_b0_q, x_imm0_q, x_a1_q, x_b1_q, x_imm1_q;
  // rs1 of slot 0 is execute state (CSR immediates read it).  The other three
  // source addresses are carried only so the RVFI trace can name them; nothing
  // in the datapath reads them.
  logic [4:0]  x_rd0_q, x_rd1_q, x_rs1_0_q;
  logic [4:0]  x_rs2_0_q, x_rs1_1_q, x_rs2_1_q;
  logic        x_ic_err_q;
  logic        x_pred_taken_q, x_pred_slot_q;
  logic [31:0] x_pred_target_q;

  wire [31:0] x_pc1_q = x_pc0_q + 32'd4;
  wire x_busy = x_v0_q || x_v1_q;

  // ---- Execute ----------------------------------------------------------------
  logic [31:0] alu_y0, alu_y1;
  wire [31:0] opa0 = (x_d0_q.opa_sel == OPA_PC)   ? x_pc0_q :
                     (x_d0_q.opa_sel == OPA_ZERO) ? 32'b0 : x_a0_q;
  wire [31:0] opb0 = (x_d0_q.opb_sel == OPB_IMM)  ? x_imm0_q : x_b0_q;
  wire [31:0] opa1 = (x_d1_q.opa_sel == OPA_PC)   ? x_pc1_q :
                     (x_d1_q.opa_sel == OPA_ZERO) ? 32'b0 : x_a1_q;
  wire [31:0] opb1 = (x_d1_q.opb_sel == OPB_IMM)  ? x_imm1_q : x_b1_q;

  alu u_alu0 (.a(opa0), .b(opb0), .op(x_d0_q.alu_op), .y(alu_y0));
  alu u_alu1 (.a(opa1), .b(opb1), .op(x_d1_q.alu_op), .y(alu_y1));

  logic br_taken0, br_taken1;
  branch_cmp u_bc0 (.a(x_a0_q), .b(x_b0_q), .f3(x_insn0_q[14:12]), .taken(br_taken0));
  branch_cmp u_bc1 (.a(x_a1_q), .b(x_b1_q), .f3(x_insn1_q[14:12]), .taken(br_taken1));

  // ---- Multi-cycle units -------------------------------------------------------
  // Which slot owns the bundle's single memory op / single mul-div.
  wire mem_slot = (x_v0_q && x_d0_q.mem_op != MEM_NONE) ? 1'b0 : 1'b1;
  wire has_mem  = (x_v0_q && x_d0_q.mem_op != MEM_NONE) ||
                  (x_v1_q && x_d1_q.mem_op != MEM_NONE);
  wire md_slot  = (x_v0_q && x_d0_q.muldiv) ? 1'b0 : 1'b1;
  wire has_md   = ENABLE_M && ((x_v0_q && x_d0_q.muldiv) ||
                               (x_v1_q && x_d1_q.muldiv));

  wire [31:0] mem_base = mem_slot ? alu_y1 : alu_y0;      // decoder forces ADD
  wire [31:0] mem_st   = mem_slot ? x_b1_q : x_b0_q;
  wire [2:0]  mem_f3   = mem_slot ? x_insn1_q[14:12] : x_insn0_q[14:12];
  wire        mem_is_store = mem_slot ? (x_d1_q.mem_op == MEM_STORE)
                                      : (x_d0_q.mem_op == MEM_STORE);
  wire [1:0]  ma = mem_base[1:0];
  wire mem_misaligned = (mem_f3[1:0] == 2'b10 && ma != 2'b00) ||
                        (mem_f3[1:0] == 2'b01 && ma[0] != 1'b0);

  logic [31:0] st_wdata; logic [3:0] st_wstrb;
  always_comb begin
    st_wdata = mem_st; st_wstrb = 4'b0000;
    unique case (mem_f3[1:0])
      2'b00: begin st_wdata = {4{mem_st[7:0]}};  st_wstrb = 4'b0001 << ma; end
      2'b01: begin st_wdata = {2{mem_st[15:0]}}; st_wstrb = ma[1] ? 4'b1100 : 4'b0011; end
      default: begin st_wdata = mem_st; st_wstrb = 4'b1111; end
    endcase
  end

  logic        mem_done_q, mem_err_q;
  logic [31:0] mem_rdata_q;
  wire mem_pending = has_mem && !mem_misaligned && !mem_done_q;

  assign dbus_valid = mem_pending;
  assign dbus_addr  = {mem_base[31:2], 2'b00};
  assign dbus_wdata = st_wdata;
  assign dbus_wstrb = mem_is_store ? st_wstrb : 4'b0;

  wire [31:0] mem_raw = mem_done_q ? mem_rdata_q : dbus_rdata;
  logic [31:0] ld_data;
  always_comb begin
    logic [7:0] b8; logic [15:0] h16;
    b8  = mem_raw[8*ma +: 8];
    h16 = ma[1] ? mem_raw[31:16] : mem_raw[15:0];
    unique case (mem_f3)
      3'b000:  ld_data = {{24{b8[7]}}, b8};
      3'b001:  ld_data = {{16{h16[15]}}, h16};
      3'b100:  ld_data = {24'b0, b8};
      3'b101:  ld_data = {16'b0, h16};
      default: ld_data = mem_raw;
    endcase
  end

  // `busy` is unused: completion is tracked by `done` plus a latch, so the
  // bundle can wait on the data bus and the divider at the same time.
  /* verilator lint_off UNUSED */
  logic        md_busy;
  /* verilator lint_on UNUSED */
  logic        md_start, md_done, md_done_q;
  logic [31:0] md_result, md_res_q;
  logic        md_started_q;
  wire [31:0]  md_a = md_slot ? x_a1_q : x_a0_q;
  wire [31:0]  md_b = md_slot ? x_b1_q : x_b0_q;
  wire [2:0]   md_f3 = md_slot ? x_insn1_q[14:12] : x_insn0_q[14:12];

  generate if (ENABLE_M) begin : g_md
    muldiv u_md (.clk(clk), .rst(rst), .start(md_start), .op(md_f3),
                 .a(md_a), .b(md_b), .busy(md_busy), .done(md_done),
                 .result(md_result));
  end else begin : g_no_md
    assign md_busy = 1'b0; assign md_done = 1'b0; assign md_result = 32'b0;
  end endgenerate

  assign md_start = has_md && !md_started_q && !md_done_q;
  wire [31:0] md_value = md_done_q ? md_res_q : md_result;
  wire md_pending = has_md && !md_done_q && !(md_started_q && md_done);

  // ---- CSR --------------------------------------------------------------------
  // CSR and system instructions issue alone, so slot 0 is always the one that
  // carries them and there is never a second CSR write in the same cycle.
  wire [11:0] csr_addr = x_insn0_q[31:20];
  wire [31:0] mip_val = {20'b0, irq_external, 3'b0, irq_timer, 3'b0,
                         irq_software, 3'b0};
  logic [31:0] csr_rdata;
  always_comb begin
    unique case (csr_addr)
      12'h300: csr_rdata = mstatus_q;
      12'h301: csr_rdata = 32'h4000_0100;      // misa: RV32 I+M
      12'h304: csr_rdata = mie_q;
      12'h305: csr_rdata = mtvec_q;
      12'h340: csr_rdata = mscratch_q;
      12'h341: csr_rdata = mepc_q;
      12'h342: csr_rdata = mcause_q;
      12'h343: csr_rdata = mtval_q;
      12'h344: csr_rdata = mip_val;
      12'hb00, 12'hc00: csr_rdata = mcycle_q[31:0];
      12'hb80, 12'hc80: csr_rdata = mcycle_q[63:32];
      12'hb02, 12'hc02: csr_rdata = minstret_q[31:0];
      12'hb82, 12'hc82: csr_rdata = minstret_q[63:32];
      default: csr_rdata = 32'b0;
    endcase
  end
  wire [31:0] csr_src = x_d0_q.csr_imm ? {27'b0, x_rs1_0_q} : x_a0_q;
  logic [31:0] csr_wval;
  always_comb begin
    unique case (x_d0_q.csr_op)
      CSR_RS:  csr_wval = csr_rdata | csr_src;
      CSR_RC:  csr_wval = csr_rdata & ~csr_src;
      default: csr_wval = csr_src;
    endcase
  end
  wire csr_writes = x_v0_q && x_d0_q.csr_op != CSR_NONE &&
                    !((x_d0_q.csr_op == CSR_RS || x_d0_q.csr_op == CSR_RC) &&
                      x_rs1_0_q == 5'd0);

  // ---- Branch resolution -------------------------------------------------------
  // Resolved here, ahead of the trap logic, because a taken transfer to a
  // misaligned target is itself a trap (IALIGN=32, no C extension).
  wire br_slot   = (x_v0_q && x_d0_q.br_sel != BR_NONE) ? 1'b0 : 1'b1;
  wire has_br    = (x_v0_q && x_d0_q.br_sel != BR_NONE) ||
                   (x_v1_q && x_d1_q.br_sel != BR_NONE);
  br_sel_e br_kind;
  assign br_kind = br_slot ? x_d1_q.br_sel : x_d0_q.br_sel;
  wire [31:0] br_pc  = br_slot ? x_pc1_q : x_pc0_q;
  wire [31:0] br_imm = br_slot ? x_imm1_q : x_imm0_q;
  wire [31:0] br_a   = br_slot ? x_a1_q : x_a0_q;
  wire        br_cmp = br_slot ? br_taken1 : br_taken0;

  logic        br_actual_taken;
  logic [31:0] br_actual_target;
  always_comb begin
    br_actual_taken  = 1'b0;
    br_actual_target = br_pc + 32'd4;
    unique case (br_kind)
      BR_JAL:  begin br_actual_taken = 1'b1; br_actual_target = br_pc + br_imm; end
      BR_JALR: begin br_actual_taken = 1'b1;
                     br_actual_target = (br_a + br_imm) & ~32'd1; end
      BR_COND: begin br_actual_taken = br_cmp;
                     if (br_cmp) br_actual_target = br_pc + br_imm; end
      default: ;
    endcase
  end

  // Instruction-address-misaligned: the transfer traps on the branch itself,
  // so rd is not written and mepc names the branch rather than the target
  // (riscv-tests ma_fetch checks exactly this).  Only bit 1 can ever be set:
  // JALR already masks bit 0 and the JAL/branch immediates have it clear.
  wire br_ma = has_br && br_actual_taken && br_actual_target[1:0] != 2'b00;

  // ---- Traps ------------------------------------------------------------------
  wire mem_fault_now = has_mem && !mem_misaligned && dbus_valid && dbus_ready && dbus_err;
  wire mem_fault     = mem_err_q || mem_fault_now;

  logic trap0, trap1;
  logic [31:0] cause0, tval0, cause1, tval1;
  always_comb begin
    trap0 = 1'b0; cause0 = 32'b0; tval0 = 32'b0;
    trap1 = 1'b0; cause1 = 32'b0; tval1 = 32'b0;
    if (x_v0_q) begin
      if (x_ic_err_q)          begin trap0 = 1'b1; cause0 = 32'd1;  tval0 = x_pc0_q; end
      else if (x_d0_q.illegal) begin trap0 = 1'b1; cause0 = 32'd2;  tval0 = x_insn0_q; end
      else if (x_d0_q.sys == SYS_ECALL)  begin trap0 = 1'b1; cause0 = 32'd11; end
      else if (x_d0_q.sys == SYS_EBREAK) begin trap0 = 1'b1; cause0 = 32'd3; tval0 = x_pc0_q; end
      else if (br_ma && br_slot == 1'b0) begin
        trap0 = 1'b1; cause0 = 32'd0; tval0 = br_actual_target;
      end
      else if (x_d0_q.mem_op != MEM_NONE && mem_slot == 1'b0) begin
        if (mem_misaligned) begin
          trap0 = 1'b1; tval0 = mem_base;
          cause0 = (x_d0_q.mem_op == MEM_STORE) ? 32'd6 : 32'd4;
        end else if (mem_fault) begin
          trap0 = 1'b1; tval0 = mem_base;
          cause0 = (x_d0_q.mem_op == MEM_STORE) ? 32'd7 : 32'd5;
        end
      end
    end
    if (x_v1_q && br_ma && br_slot == 1'b1) begin
      trap1 = 1'b1; cause1 = 32'd0; tval1 = br_actual_target;
    end else if (x_v1_q && x_d1_q.mem_op != MEM_NONE && mem_slot == 1'b1) begin
      if (mem_misaligned) begin
        trap1 = 1'b1; tval1 = mem_base;
        cause1 = (x_d1_q.mem_op == MEM_STORE) ? 32'd6 : 32'd4;
      end else if (mem_fault) begin
        trap1 = 1'b1; tval1 = mem_base;
        cause1 = (x_d1_q.mem_op == MEM_STORE) ? 32'd7 : 32'd5;
      end
    end
  end

  // Interrupts are taken at a bundle boundary, when nothing is in flight.
  wire        int_pending = mstatus_q[MIE_B] && ((mie_q & mip_val) != 32'b0);
  logic [3:0] int_cause;
  always_comb begin
    int_cause = 4'd11;
    if (mie_q[7] && irq_timer)         int_cause = 4'd7;
    else if (mie_q[3] && irq_software) int_cause = 4'd3;
    else if (mie_q[11] && irq_external) int_cause = 4'd11;
  end

  // ---- Bundle completion -------------------------------------------------------
  // The bundle stays in X until every multi-cycle unit it owns has answered.
  // A misaligned access never reaches the bus, so it does not hold anything.
  wire x_stall = x_busy && ((mem_pending && !dbus_ready) || md_pending);
  wire x_retire = x_busy && !x_stall;

  wire take_trap = x_retire && (trap0 || trap1);
  wire commit0   = x_retire && x_v0_q && !trap0;
  wire commit1   = x_retire && x_v1_q && !trap0 && !trap1;

  // ---- Writeback ---------------------------------------------------------------
  /* verilator lint_off UNUSED */
  function automatic logic [31:0] wb_value(
      input dec_t d, input logic [31:0] alu_y, input logic [31:0] pc,
      input logic is_md_slot);
    unique case (d.wb_sel)
      WB_MEM:  wb_value = ld_data;
      WB_PC4:  wb_value = pc + 32'd4;
      WB_CSR:  wb_value = csr_rdata;
      default: wb_value = (d.muldiv && is_md_slot) ? md_value : alu_y;
    endcase
  endfunction
  /* verilator lint_on UNUSED */

  assign wb_we0   = commit0 && x_d0_q.rd_we;
  assign wb_rd0   = x_rd0_q;
  assign wb_data0 = wb_value(x_d0_q, alu_y0, x_pc0_q, md_slot == 1'b0);
  assign wb_we1   = commit1 && x_d1_q.rd_we;
  assign wb_rd1   = x_rd1_q;
  assign wb_data1 = wb_value(x_d1_q, alu_y1, x_pc1_q, md_slot == 1'b1);

  // The sequential address after everything this bundle delivered.
  wire [31:0] bundle_next_pc = (x_v1_q ? x_pc1_q : x_pc0_q) + 32'd4;

  // Fetch assumed a branch at pred_slot.  It was right only if there really is
  // a branch there, it really was taken, and it went where fetch guessed.
  wire pred_applies = x_pred_taken_q && has_br && x_pred_slot_q == br_slot;
  wire mispredict =
      x_retire && !take_trap &&
      ( (has_br && br_actual_taken &&
         (!pred_applies || x_pred_target_q != br_actual_target))
      || (has_br && !br_actual_taken && pred_applies)
      || (x_pred_taken_q && !pred_applies) );

  wire [31:0] resolved_pc = (has_br && br_actual_taken) ? br_actual_target
                                                        : bundle_next_pc;

  wire is_mret    = x_retire && x_v0_q && !take_trap && x_d0_q.sys == SYS_MRET;
  wire is_fencei  = x_retire && x_v0_q && !take_trap && x_d0_q.sys == SYS_FENCE_I;
  // Taken at the point D would have accepted the next bundle: the bundle in X
  // has retired, the bundle in D has not started, so mepc is the address of a
  // real instruction that has not executed and nothing needs squashing beyond
  // the accept itself.  Any redirect already in flight wins -- its own
  // instruction owns mepc, and the interrupt is still pending next cycle.
  wire take_int = int_pending && ic_valid && !d_stall &&
                  !take_trap && !is_mret && !is_fencei && !mispredict;

  assign fe_flush = is_fencei;
  always_comb begin
    redirect    = 1'b0;
    redirect_pc = 32'b0;
    if (take_trap)      begin redirect = 1'b1; redirect_pc = mtvec_q; end
    else if (take_int)  begin redirect = 1'b1; redirect_pc = mtvec_q; end
    else if (is_mret)   begin redirect = 1'b1; redirect_pc = mepc_q; end
    else if (is_fencei) begin redirect = 1'b1; redirect_pc = bundle_next_pc; end
    else if (mispredict) begin redirect = 1'b1; redirect_pc = resolved_pc; end
  end

  // Train the predictor on every resolved branch, taken or not.
  assign btb_upd_valid  = x_retire && !take_trap && has_br;
  assign btb_upd_pc     = x_pc0_q;
  assign btb_upd_slot   = br_slot;
  assign btb_upd_taken  = br_actual_taken;
  assign btb_upd_target = br_actual_target;

  // ---- Stall / advance ---------------------------------------------------------
  // D holds when X cannot take a new bundle.  A redirect is not a stall: the
  // bundle in D is squashed anyway.
  assign d_stall = x_busy && !x_retire;

  wire [1:0] retire_count = {1'b0, commit0} + {1'b0, commit1};

  assign trace_valid = x_retire && (commit0 || commit1 || take_trap);
  assign trace_trap  = take_trap;
  assign trace_insn  = commit1 ? x_insn1_q : x_insn0_q;

  // ---- RVFI --------------------------------------------------------------------
  // Slot 0 either retires or traps; slot 1 only ever appears when slot 0 did not
  // trap, because a slot-0 trap redirects before slot 1 has any architectural
  // effect and mepc names slot 0, so slot 1 is re-fetched rather than retired.
  wire rvfi_v0 = x_retire && x_v0_q;
  wire rvfi_v1 = x_retire && x_v1_q && !trap0;

  // Where the bundle actually goes, ignoring an interrupt taken at the bundle
  // boundary: that redirect belongs to the *next* instruction (which reports
  // rvfi_intr), not to the one retiring here, whose successor is still its own.
  wire [31:0] arch_next_pc =
      take_trap                     ? mtvec_q
    : is_mret                       ? mepc_q
    : (has_br && br_actual_taken)   ? br_actual_target
                                    : bundle_next_pc;

  // A dual bundle never puts a control transfer, CSR, or system instruction in
  // slot 0 (`dual_ok` forbids it), so whenever slot 1 is live the architectural
  // successor of slot 0 is simply slot 1.
  wire [31:0] rvfi_pc_wdata0 = rvfi_v1 ? x_pc1_q : arch_next_pc;

  // The single memory port belongs to one slot per bundle, and only reports a
  // transaction that actually reached the bus.
  wire mem_ok      = has_mem && !mem_misaligned && !mem_fault;
  wire mem_is_load = mem_ok && !mem_is_store;
  wire mem_on0     = mem_ok && mem_slot == 1'b0 && commit0;
  wire mem_on1     = mem_ok && mem_slot == 1'b1 && commit1;

  // The first instruction to retire after any trap-vector redirect is the
  // handler's entry, which is what rvfi_intr marks.  A bundle always starts at
  // slot 0, so slot 1 is never a handler entry.
  logic rvfi_intr_q;
  always_ff @(posedge clk) begin
    if (rst)                        rvfi_intr_q <= 1'b0;
    else if (take_trap || take_int) rvfi_intr_q <= 1'b1;
    else if (rvfi_v0)               rvfi_intr_q <= 1'b0;
  end

  logic [63:0] rvfi_order_q;
  always_ff @(posedge clk) begin
    if (rst) rvfi_order_q <= 64'b0;
    else     rvfi_order_q <= rvfi_order_q + 64'(rvfi_v0) + 64'(rvfi_v1);
  end

  always_comb begin
    rvfi_valid = {rvfi_v1, rvfi_v0};
    rvfi_order = {rvfi_order_q + 64'd1, rvfi_order_q};
    rvfi_insn  = {x_insn1_q, x_insn0_q};
    rvfi_trap  = {rvfi_v1 && trap1, rvfi_v0 && trap0};
    rvfi_halt  = 2'b00;
    rvfi_intr  = {1'b0, rvfi_intr_q};
    rvfi_mode  = {2'b11, 2'b11};            // machine mode only
    rvfi_ixl   = {2'b01, 2'b01};            // RV32

    rvfi_rs1_addr  = {x_rs1_1_q, x_rs1_0_q};
    rvfi_rs2_addr  = {x_rs2_1_q, x_rs2_0_q};
    // Reading x0 must report zero, which is the one place the raw operand
    // capture and the architectural value can differ.
    rvfi_rs1_rdata = {x_rs1_1_q == 5'd0 ? 32'b0 : x_a1_q,
                      x_rs1_0_q == 5'd0 ? 32'b0 : x_a0_q};
    rvfi_rs2_rdata = {x_rs2_1_q == 5'd0 ? 32'b0 : x_b1_q,
                      x_rs2_0_q == 5'd0 ? 32'b0 : x_b0_q};

    rvfi_rd_addr  = {wb_we1 ? x_rd1_q : 5'd0, wb_we0 ? x_rd0_q : 5'd0};
    rvfi_rd_wdata = {(wb_we1 && x_rd1_q != 5'd0) ? wb_data1 : 32'b0,
                     (wb_we0 && x_rd0_q != 5'd0) ? wb_data0 : 32'b0};

    rvfi_pc_rdata = {x_pc1_q, x_pc0_q};
    rvfi_pc_wdata = {arch_next_pc, rvfi_pc_wdata0};

    rvfi_mem_addr  = {mem_on1 ? dbus_addr : 32'b0, mem_on0 ? dbus_addr : 32'b0};
    rvfi_mem_rmask = {(mem_on1 && mem_is_load) ? st_wstrb : 4'b0,
                      (mem_on0 && mem_is_load) ? st_wstrb : 4'b0};
    rvfi_mem_wmask = {(mem_on1 && mem_is_store) ? st_wstrb : 4'b0,
                      (mem_on0 && mem_is_store) ? st_wstrb : 4'b0};
    rvfi_mem_rdata = {mem_on1 ? mem_raw : 32'b0, mem_on0 ? mem_raw : 32'b0};
    rvfi_mem_wdata = {mem_on1 ? st_wdata : 32'b0, mem_on0 ? st_wdata : 32'b0};
  end

  // ---- Sequential --------------------------------------------------------------
  task automatic csr_write(input logic [11:0] a, input logic [31:0] v);
    unique case (a)
      12'h300: mstatus_q  <= (v & 32'h0000_0088) | 32'h0000_1800;
      12'h304: mie_q      <= v;
      // Both MODE fields are WARL and only direct is implemented, so mtvec
      // reads back mode 0 -- reporting vectored would strand software in the
      // wrong handler, and the raw value is what redirect_pc loads.
      12'h305: mtvec_q    <= v & ~32'd3;
      12'h340: mscratch_q <= v;
      12'h341: mepc_q     <= v & ~32'd3;     // IALIGN=32: no C extension
      12'h342: mcause_q   <= v;
      12'h343: mtval_q    <= v;
      // Counters are M-mode writable.  Full-width assignments, so they
      // override this bundle's increment above -- which is exactly the rule
      // that a write suppresses the increment from the writing instruction.
      12'hb00: mcycle_q   <= {mcycle_q[63:32], v};
      12'hb80: mcycle_q   <= {v, mcycle_q[31:0]};
      12'hb02: minstret_q <= {minstret_q[63:32], v};
      12'hb82: minstret_q <= {v, minstret_q[31:0]};
      default: ;
    endcase
  endtask

  always_ff @(posedge clk) begin
    if (rst) begin
      x_v0_q <= 1'b0; x_v1_q <= 1'b0;
      mem_done_q <= 1'b0; mem_err_q <= 1'b0; mem_rdata_q <= 32'b0;
      md_started_q <= 1'b0; md_done_q <= 1'b0; md_res_q <= 32'b0;
      mstatus_q <= 32'h0000_1800;
      mtvec_q <= 32'b0; mscratch_q <= 32'b0; mepc_q <= 32'b0;
      mcause_q <= 32'b0; mtval_q <= 32'b0; mie_q <= 32'b0;
      mcycle_q <= 64'b0; minstret_q <= 64'b0;
      x_pc0_q <= 32'b0; x_insn0_q <= 32'b0; x_insn1_q <= 32'b0;
      x_ic_err_q <= 1'b0; x_pred_taken_q <= 1'b0; x_pred_slot_q <= 1'b0;
      x_pred_target_q <= 32'b0;
    end else begin
      mcycle_q <= mcycle_q + 64'd1;

      // Latch multi-cycle answers so the bundle can wait on the other unit.
      if (md_start)                    md_started_q <= 1'b1;
      if (has_md && md_started_q && md_done) begin
        md_done_q <= 1'b1; md_res_q <= md_result;
      end
      if (mem_pending && dbus_ready) begin
        mem_done_q  <= 1'b1;
        mem_rdata_q <= dbus_rdata;
        if (dbus_err) mem_err_q <= 1'b1;
      end

      if (x_retire) begin
        minstret_q <= minstret_q + 64'(retire_count);
        // Clear the per-bundle multi-cycle state for the next occupant.
        mem_done_q <= 1'b0; mem_err_q <= 1'b0;
        md_started_q <= 1'b0; md_done_q <= 1'b0;

        if (take_trap) begin
          mepc_q            <= trap0 ? x_pc0_q : x_pc1_q;
          mcause_q          <= trap0 ? cause0 : cause1;
          mtval_q           <= trap0 ? tval0 : tval1;
          mstatus_q[MPIE_B] <= mstatus_q[MIE_B];
          mstatus_q[MIE_B]  <= 1'b0;
        end else begin
          if (is_mret) begin
            mstatus_q[MIE_B]  <= mstatus_q[MPIE_B];
            mstatus_q[MPIE_B] <= 1'b1;
          end
          if (csr_writes) csr_write(csr_addr, csr_wval);
        end
      end

      // An interrupt is taken between bundles, so nothing needs squashing.
      if (take_int) begin
        mepc_q            <= ic_pc;
        mcause_q          <= {1'b1, 27'b0, int_cause};
        mstatus_q[MPIE_B] <= mstatus_q[MIE_B];
        mstatus_q[MIE_B]  <= 1'b0;
      end

      // ---- D -> X --------------------------------------------------------------
      if (!d_stall) begin
        logic accept;
        accept = ic_valid && !redirect;
        x_v0_q          <= accept;
        x_v1_q          <= accept && dual_ok;
        x_pc0_q         <= ic_pc;
        x_insn0_q       <= ic_insn0;
        x_insn1_q       <= ic_insn1;
        x_d0_q          <= d0;
        x_d1_q          <= d1;
        x_a0_q          <= rf_a0;
        x_b0_q          <= rf_b0;
        x_imm0_q        <= imm0;
        x_a1_q          <= rf_a1;
        x_b1_q          <= rf_b1;
        x_imm1_q        <= imm1;
        x_rd0_q         <= rd_0;
        x_rd1_q         <= rd_1;
        x_rs1_0_q       <= rs1_0;
        x_rs2_0_q       <= rs2_0;
        x_rs1_1_q       <= rs1_1;
        x_rs2_1_q       <= rs2_1;
        x_ic_err_q      <= ic_err;
        x_pred_taken_q  <= ic_pred_taken;
        x_pred_slot_q   <= ic_pred_slot;
        x_pred_target_q <= ic_pred_target;
      end
    end
  end
endmodule
