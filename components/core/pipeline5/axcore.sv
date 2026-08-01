// aXcore — RV32IM + Zicsr, classic 5-stage pipeline (DESIGN.md §4).
//
//   IF: fetch via ibus; wait-state tolerant (holds valid/addr stable; a
//       redirect during an in-flight fetch drains it and discards).
//   ID: decode, regfile read (write-before-read bypass), immediates,
//       load-use stall, serialization detect.
//   EX: ALU, branch resolve (redirect, 2-cycle taken penalty), misaligned
//       load/store detect, operand forwarding (EX/MEM and MEM/WB -> EX).
//   MEM: dbus loads/stores; THE COMMIT POINT — traps, CSR effects, mret,
//       serialize-resume redirects all fire here, so nothing younger can
//       have had architectural effect.
//   WB: register writeback only.
//
// Serialized instructions (CSR ops, mret, fence.i, wfi, ecall, ebreak, and
// RV32M multiply/divide):
// fetch halts while they drain, the effect happens at commit, and fetch
// resumes at the redirect target (pc+4 / mepc / mtvec). No CSR forwarding
// network exists — by design.
module axcore #(
  parameter logic [31:0] RESET_PC = 32'h8000_0000,
  // Set to zero for the RV32I formal configuration. Product builds leave
  // this enabled and implement the complete RV32IM ISA.
  parameter bit ENABLE_M = 1'b1
) (
  input  logic        clk,
  input  logic        rst,       // synchronous, active high

  // instruction fetch master (writes only for Sv32 PTE A-bit updates by
  // the fetch-side page-table walker)
  output logic        ibus_valid,
  output logic [31:0] ibus_addr,
  output logic [31:0] ibus_wdata,
  output logic [3:0]  ibus_wstrb,
  input  logic        ibus_ready,
  input  logic [31:0] ibus_rdata,
  input  logic        ibus_err,

  // data master
  output logic        dbus_valid,
  output logic [31:0] dbus_addr,
  output logic [31:0] dbus_wdata,
  output logic [3:0]  dbus_wstrb,
  input  logic        dbus_ready,
  input  logic [31:0] dbus_rdata,
  input  logic        dbus_err,

  // Machine-level interrupt lines. They are sampled precisely at the MEM
  // commit point, after the current instruction has retired.
  input  logic        irq_software,
  input  logic        irq_timer,
  input  logic        irq_external,
  // Supervisor external interrupt from the PLIC's S-mode context. A core
  // without S-mode ignores it; this one routes it to mip.SEIP.
  input  logic        irq_s_external,

  // High while the hart is parked in WFI with nothing pending.  It carries no
  // architectural meaning -- the machine behaves identically whether anyone
  // looks at it -- but it is the only way for anything outside the core to
  // tell "waiting for an interrupt" apart from "working".  A board can gate a
  // clock on it; a simulator can decline to spend host time clocking a core
  // that is, by construction, going to do nothing until an input changes.
  output logic        cpu_idle,

  // Commit trace for lock-step cosimulation.  `trace_valid` marks exactly
  // one architectural event: either a retirement or a precise trap.
  output logic        trace_valid,
  output logic        trace_trap,
  output logic [31:0] trace_pc,
  output logic [31:0] trace_insn,
  output logic [3:0]  trace_cause,
  output logic [31:0] trace_tval,
  output logic        trace_rd_we,
  output logic [4:0]  trace_rd,
  output logic [31:0] trace_rd_val,
  output logic [31:0] trace_mstatus,
  output logic [31:0] trace_mtvec,
  output logic [31:0] trace_mepc,
  output logic [31:0] trace_mcause,
  output logic [31:0] trace_mtval,
  output logic [31:0] trace_mscratch,
  output logic [31:0] trace_mie,
  output logic [31:0] trace_mip,
  output logic [1:0]  trace_prv,

  // One-retire RVFI trace for riscv-formal.  Like trace_*, these are sampled
  // at the MEM commit point immediately before the active clock edge.
  output logic        rvfi_valid,
  output logic [63:0] rvfi_order,
  output logic [31:0] rvfi_insn,
  output logic        rvfi_trap,
  output logic        rvfi_halt,
  output logic        rvfi_intr,
  output logic [1:0]  rvfi_mode,
  output logic [1:0]  rvfi_ixl,
  output logic [4:0]  rvfi_rs1_addr,
  output logic [4:0]  rvfi_rs2_addr,
  output logic [31:0] rvfi_rs1_rdata,
  output logic [31:0] rvfi_rs2_rdata,
  output logic [4:0]  rvfi_rd_addr,
  output logic [31:0] rvfi_rd_wdata,
  output logic [31:0] rvfi_pc_rdata,
  output logic [31:0] rvfi_pc_wdata,
  output logic [31:0] rvfi_mem_addr,
  output logic [3:0]  rvfi_mem_rmask,
  output logic [3:0]  rvfi_mem_wmask,
  output logic [31:0] rvfi_mem_rdata,
  output logic [31:0] rvfi_mem_wdata
);

  import axcore_pkg::*;

  // Exception causes (privileged spec).
  localparam logic [3:0] EXC_IADDR = 4'd0, EXC_IFAULT = 4'd1,
                         EXC_ILL = 4'd2, EXC_BREAK = 4'd3,
                         EXC_LADDR = 4'd4, EXC_LFAULT = 4'd5,
                         EXC_SADDR = 4'd6, EXC_SFAULT = 4'd7,
                         EXC_IPAGE = 4'd12, EXC_LPAGE = 4'd13,
                         EXC_SPAGE = 4'd15;

  // ---------------------------------------------------------------- control
  logic        stall_ld;                    // load-use: ID holds, EX bubbles
  logic        mem_busy;                    // dbus wait state: global freeze
  logic        wfi_wait;                    // WFI parked: global freeze
  // Both freezes hold the whole pipeline for the same reason -- the commit
  // point cannot proceed -- so they hold it the same way.  Keeping them
  // separate signals rather than one is deliberate: `mem_busy` is a property
  // of the bus and `wfi_wait` of the program, and only the latter means the
  // machine is idle rather than slow.
  wire         core_hold = mem_busy || wfi_wait;
  logic        redirect_ex, redirect_mem;
  logic [31:0] redirect_ex_pc, redirect_mem_pc;
  wire         redirect    = redirect_mem || redirect_ex;
  wire  [31:0] redirect_pc = redirect_mem ? redirect_mem_pc : redirect_ex_pc;

  // ---------------------------------------------------------------- IF
  logic [31:0] pc_q;      // address being fetched (stable while in flight)
  logic [31:0] npc_q;     // deferred redirect target (fetch was in flight)
  logic        kill_q;    // in-flight fetch is wrong-path: drop its result
  logic        halt_q;    // serialized instruction draining
  logic        ser_in_id; // (from ID) serialized instruction sits in ID now

  // Fetch runs through the instruction-side MMU (bare mode passes straight
  // through); the MMU owns the ibus master ports.
  logic        if_valid, if_ready, if_err, if_pgf;
  logic [31:0] if_rdata;

  wire fetch_halt    = halt_q || ser_in_id;
  wire pc_misaligned = pc_q[1:0] != 2'b00;
  wire want_fetch    = !fetch_halt && !pc_misaligned;
  wire pend_hold     = if_valid && !if_ready;       // must keep valid+addr

  logic pend_q;                                     // request crossed an edge
  assign if_valid = !rst && (pend_q || want_fetch);
  wire [31:0] if_addr = pc_q;
  wire fetch_done = if_valid && if_ready;

  always_ff @(posedge clk) begin
    if (rst) begin
      pc_q   <= RESET_PC;
      npc_q  <= 32'b0;
      kill_q <= 1'b0;
      pend_q <= 1'b0;
      halt_q <= 1'b0;
    end else begin
      pend_q <= pend_hold;

      if (redirect) begin
        halt_q <= 1'b0;
        if (pend_hold) begin              // drain in-flight fetch first
          kill_q <= 1'b1;
          npc_q  <= redirect_pc;
        end else begin
          pc_q   <= redirect_pc;
          kill_q <= 1'b0;
        end
      end else begin
        if (ser_in_id && !stall_ld && !core_hold)
          halt_q <= 1'b1;                 // serialized insn advances to EX
        if (fetch_done && kill_q) begin   // stale fetch drained
          pc_q   <= npc_q;
          kill_q <= 1'b0;
        end else if (fetch_done && !core_hold && !stall_ld && !fetch_halt)
          pc_q <= pc_q + 32'd4;           // normal advance
      end
    end
  end

  // ---------------------------------------------------------------- IF/ID
  logic        ifid_v_q;
  logic [31:0] ifid_pc_q, ifid_insn_q;
  logic        ifid_exc_q;
  logic [3:0]  ifid_cause_q;
  logic [31:0] ifid_tval_q;

  wire if_capture = fetch_done && !kill_q && !fetch_halt;

  always_ff @(posedge clk) begin
    if (rst || redirect) begin
      ifid_v_q <= 1'b0;
    end else if (!core_hold && !stall_ld) begin
      if (if_capture) begin
        ifid_v_q     <= 1'b1;
        ifid_pc_q    <= pc_q;
        ifid_insn_q  <= if_rdata;
        ifid_exc_q   <= if_err || if_pgf;
        ifid_cause_q <= if_pgf ? EXC_IPAGE : EXC_IFAULT;
        ifid_tval_q  <= pc_q;
      end else if (pc_misaligned && !fetch_halt) begin
        ifid_v_q     <= 1'b1;             // inject fetch-misalign exception
        ifid_pc_q    <= pc_q;
        ifid_insn_q  <= 32'b0;
        ifid_exc_q   <= 1'b1;
        ifid_cause_q <= EXC_IADDR;
        ifid_tval_q  <= pc_q;
      end else begin
        ifid_v_q <= 1'b0;                 // bubble
      end
    end
    // stall_ld, mem_busy, or wfi_wait: IF/ID holds
  end

  // ---------------------------------------------------------------- ID
  logic     dec_illegal, dec_rd_we, dec_csr_imm, dec_muldiv, dec_rs1, dec_rs2;
  opa_sel_e dec_opa;
  opb_sel_e dec_opb;
  imm_sel_e dec_imm_sel;
  alu_op_t  dec_alu_op;
  wb_sel_e  dec_wb;
  mem_op_e  dec_mem;
  br_sel_e  dec_br;
  csr_op_e  dec_csr;
  sys_e     dec_sys;

  decoder #(.ENABLE_M(ENABLE_M)) u_dec (
    .insn(ifid_insn_q), .illegal(dec_illegal), .rd_we(dec_rd_we),
    .opa_sel(dec_opa), .opb_sel(dec_opb), .imm_sel(dec_imm_sel),
    .alu_op(dec_alu_op), .wb_sel(dec_wb), .mem_op(dec_mem), .br_sel(dec_br),
    .csr_op(dec_csr), .csr_imm(dec_csr_imm), .muldiv(dec_muldiv), .sys(dec_sys),
    .uses_rs1(dec_rs1), .uses_rs2(dec_rs2)
  );

  logic [31:0] dec_imm;
  immdec u_imm (.insn(ifid_insn_q), .sel(dec_imm_sel), .imm(dec_imm));

  wire [4:0] id_rs1 = ifid_insn_q[19:15];
  wire [4:0] id_rs2 = ifid_insn_q[24:20];
  wire [4:0] id_rd  = ifid_insn_q[11:7];

  logic [31:0] rf_rs1, rf_rs2;
  logic        wb_we;
  logic [4:0]  wb_rd;
  logic [31:0] wb_val;
  regfile u_rf (
    .clk(clk), .we(wb_we), .waddr(wb_rd), .wdata(wb_val),
    .raddr1(id_rs1), .rdata1(rf_rs1), .raddr2(id_rs2), .rdata2(rf_rs2)
  );

  assign ser_in_id =
      ifid_v_q && !ifid_exc_q &&
      (dec_csr != CSR_NONE || dec_muldiv || dec_sys != SYS_NONE);

  // ---------------------------------------------------------------- ID/EX
  logic        idex_v_q, idex_exc_q, idex_rd_we_q, idex_csr_imm_q,
               idex_muldiv_q;
  logic        idex_rs1_use_q, idex_rs2_use_q;
  logic [3:0]  idex_cause_q;
  logic [31:0] idex_tval_q, idex_pc_q, idex_insn_q;
  logic [31:0] idex_rs1v_q, idex_rs2v_q, idex_imm_q;
  logic [4:0]  idex_rs1_q, idex_rs2_q, idex_rd_q;
  opa_sel_e    idex_opa_q;
  opb_sel_e    idex_opb_q;
  alu_op_t     idex_alu_q;
  wb_sel_e     idex_wb_q;
  mem_op_e     idex_mem_q;
  br_sel_e     idex_br_q;
  csr_op_e     idex_csr_q;
  sys_e        idex_sys_q;

  // The M unit holds its instruction in ID/EX while older MEM/WB work drains.
  // At done, the result advances exactly once into EX/MEM like an ALU result.
  logic md_busy, md_done, md_start;
  logic [31:0] md_result;
  wire md_wait = idex_v_q && idex_muldiv_q && !md_done;

  // Load-use hazard: consumer in ID, load in EX.
  assign stall_ld =
      idex_v_q && idex_mem_q == MEM_LOAD && idex_rd_q != 5'd0 &&
      ifid_v_q && !ifid_exc_q &&
      ((dec_rs1 && id_rs1 == idex_rd_q) || (dec_rs2 && id_rs2 == idex_rd_q));

  always_ff @(posedge clk) begin
    if (rst || redirect || (!core_hold && !md_wait && (stall_ld || !ifid_v_q))) begin
      idex_v_q <= 1'b0;
    end else if (!core_hold && !md_wait) begin
      idex_v_q       <= 1'b1;
      idex_pc_q      <= ifid_pc_q;
      idex_insn_q    <= ifid_insn_q;
      idex_exc_q     <= ifid_exc_q || dec_illegal;
      idex_cause_q   <= ifid_exc_q ? ifid_cause_q : EXC_ILL;
      idex_tval_q    <= ifid_exc_q ? ifid_tval_q : ifid_insn_q;
      idex_rs1v_q    <= rf_rs1;
      idex_rs2v_q    <= rf_rs2;
      idex_imm_q     <= dec_imm;
      idex_rs1_q     <= id_rs1;
      idex_rs2_q     <= id_rs2;
      idex_rd_q      <= id_rd;
      idex_rd_we_q   <= dec_rd_we;
      idex_rs1_use_q <= dec_rs1;
      idex_rs2_use_q <= dec_rs2;
      idex_opa_q     <= dec_opa;
      idex_opb_q     <= dec_opb;
      idex_alu_q     <= dec_alu_op;
      idex_wb_q      <= dec_wb;
      idex_mem_q     <= dec_mem;
      idex_br_q      <= dec_br;
      idex_csr_q     <= dec_csr;
      idex_sys_q     <= dec_sys;
      idex_csr_imm_q <= dec_csr_imm;
      idex_muldiv_q  <= dec_muldiv;
    end
  end

  // ---------------------------------------------------------------- EX
  // Forwarding: EX/MEM (ALU/link results) and MEM/WB (any result) -> EX.
  logic        exmem_v_q, exmem_rd_we_q;
  logic [4:0]  exmem_rd_q;
  logic [31:0] exmem_y_q, exmem_pc_q, exmem_npc_q;
  logic [31:0] exmem_rs1_rdata_q, exmem_rs2_rdata_q;
  logic [4:0]  exmem_rs1_q, exmem_rs2_q;
  wb_sel_e     exmem_wb_q;

  logic        memwb_v_q, memwb_rd_we_q;
  logic [4:0]  memwb_rd_q;
  logic [31:0] memwb_val_q;

  wire [31:0] exmem_fwd =
      (exmem_wb_q == WB_PC4) ? exmem_pc_q + 32'd4 : exmem_y_q;

  logic [31:0] rs1_fwd, rs2_fwd;
  always_comb begin
    rs1_fwd = idex_rs1v_q;
    if (memwb_v_q && memwb_rd_we_q && memwb_rd_q == idex_rs1_q &&
        idex_rs1_q != 5'd0)
      rs1_fwd = memwb_val_q;
    if (exmem_v_q && exmem_rd_we_q && exmem_rd_q == idex_rs1_q &&
        idex_rs1_q != 5'd0)
      rs1_fwd = exmem_fwd;

    rs2_fwd = idex_rs2v_q;
    if (memwb_v_q && memwb_rd_we_q && memwb_rd_q == idex_rs2_q &&
        idex_rs2_q != 5'd0)
      rs2_fwd = memwb_val_q;
    if (exmem_v_q && exmem_rd_we_q && exmem_rd_q == idex_rs2_q &&
        idex_rs2_q != 5'd0)
      rs2_fwd = exmem_fwd;
  end

  assign md_start = idex_v_q && idex_muldiv_q && !md_busy && !rst;
  generate
    if (ENABLE_M) begin : g_muldiv
      muldiv u_muldiv (
        .clk(clk), .rst(rst), .start(md_start), .op(idex_insn_q[14:12]),
        .a(rs1_fwd), .b(rs2_fwd), .busy(md_busy), .done(md_done),
        .result(md_result)
      );
    end else begin : g_no_muldiv
      assign md_busy = 1'b0;
      assign md_done = 1'b0;
      assign md_result = 32'b0;
    end
  endgenerate

  logic [31:0] alu_a, alu_b, alu_y;
  always_comb begin
    unique case (idex_opa_q)
      OPA_RS1:  alu_a = rs1_fwd;
      OPA_PC:   alu_a = idex_pc_q;
      default:  alu_a = 32'b0;            // OPA_ZERO
    endcase
    alu_b = (idex_opb_q == OPB_IMM) ? idex_imm_q : rs2_fwd;
  end
  alu u_alu (.a(alu_a), .b(alu_b), .op(idex_alu_q), .y(alu_y));

  logic br_taken_cond;
  branch_cmp u_bcmp (
    .a(rs1_fwd), .b(rs2_fwd), .f3(idex_insn_q[14:12]), .taken(br_taken_cond)
  );

  wire [31:0] br_target =
      (idex_br_q == BR_JALR) ? (rs1_fwd + idex_imm_q) & ~32'h1
                             : idex_pc_q + idex_imm_q;

  wire ex_take = idex_v_q && !idex_exc_q &&
                 (idex_br_q == BR_JAL || idex_br_q == BR_JALR ||
                  (idex_br_q == BR_COND && br_taken_cond));

  // A misaligned target traps on the jump/branch itself (IALIGN=32, no C):
  // the redirect is suppressed and the instruction carries EXC_IADDR to
  // commit, so rd is never written and epc names the jump, not the target.
  wire ex_take_misal = ex_take && br_target[1:0] != 2'b00;

  // Branch acts only when this instruction actually advances, and an older
  // committing instruction's redirect wins (it flushes EX anyway).
  assign redirect_ex    = ex_take && !ex_take_misal && !core_hold && !redirect_mem;
  assign redirect_ex_pc = br_target;

  // Misaligned data address detect (address is known here).
  wire [1:0] msize = idex_insn_q[13:12];  // funct3[1:0]: 00 B, 01 H, 10 W
  wire misal = (msize == 2'b01 && alu_y[0]) ||
               (msize == 2'b10 && alu_y[1:0] != 2'b00);
  wire ex_mem_exc = idex_mem_q != MEM_NONE && misal;

  // CSR write operand: rs1 value or zero-extended uimm (rs1 field).
  wire [31:0] csr_wdata =
      idex_csr_imm_q ? {27'b0, idex_rs1_q} : rs1_fwd;
  wire csr_rs1x0 = idex_rs1_q == 5'd0;

  // ---------------------------------------------------------------- EX/MEM
  logic        exmem_exc_q, exmem_csr_rs1x0_q, exmem_muldiv_q;
  logic [3:0]  exmem_cause_q;
  logic [31:0] exmem_tval_q, exmem_insn_q, exmem_data_q;
  mem_op_e     exmem_mem_q;
  csr_op_e     exmem_csr_q;
  sys_e        exmem_sys_q;

  always_ff @(posedge clk) begin
    // A taken branch is NOT flushed here — it advances and commits; only
    // younger stages (IF/ID, ID/EX) are flushed by redirect_ex.
    if (rst || redirect_mem || (!core_hold && (!idex_v_q || md_wait))) begin
      exmem_v_q <= 1'b0;
    end else if (!core_hold && !md_wait) begin
      exmem_v_q         <= 1'b1;
      exmem_pc_q        <= idex_pc_q;
      exmem_npc_q       <= ex_take ? br_target : idex_pc_q + 32'd4;
      exmem_insn_q      <= idex_insn_q;
      exmem_y_q         <= idex_muldiv_q ? md_result : alu_y;
      exmem_data_q      <= (idex_csr_q != CSR_NONE) ? csr_wdata : rs2_fwd;
      exmem_rs1_q       <= idex_rs1_q;
      exmem_rs2_q       <= idex_rs2_q;
      exmem_rs1_rdata_q <= idex_rs1_q == 5'd0 ? 32'b0 : rs1_fwd;
      exmem_rs2_rdata_q <= idex_rs2_q == 5'd0 ? 32'b0 : rs2_fwd;
      exmem_rd_q        <= idex_rd_q;
      exmem_rd_we_q     <= idex_rd_we_q;
      exmem_wb_q        <= idex_wb_q;
      exmem_mem_q       <= idex_mem_q;
      exmem_csr_q       <= idex_csr_q;
      exmem_sys_q       <= idex_sys_q;
      exmem_muldiv_q    <= idex_muldiv_q;
      exmem_csr_rs1x0_q <= csr_rs1x0;
      exmem_exc_q       <= idex_exc_q || ex_mem_exc || ex_take_misal;
      exmem_cause_q     <= idex_exc_q ? idex_cause_q
                         : ex_take_misal ? EXC_IADDR
                         : (idex_mem_q == MEM_LOAD ? EXC_LADDR : EXC_SADDR);
      exmem_tval_q      <= idex_exc_q ? idex_tval_q
                         : ex_take_misal ? br_target : alu_y;
    end
  end

  // ---------------------------------------------------------------- MEM
  // The commit point: memory access, CSR/system effects, traps.
  wire [2:0]  mf3   = exmem_insn_q[14:12];
  wire [1:0]  moff  = exmem_y_q[1:0];
  wire        is_ld = exmem_mem_q == MEM_LOAD;

  // Data access runs through the data-side MMU; it owns the dbus master.
  logic        dm_valid, dm_ready, dm_err, dm_pgf;
  logic [31:0] dm_addr, dm_wdata, dm_rdata;
  logic [3:0]  dm_wstrb;

  assign dm_valid = exmem_v_q && !exmem_exc_q && exmem_mem_q != MEM_NONE;
  assign dm_addr  = {exmem_y_q[31:2], 2'b00};
  assign dm_wdata = exmem_data_q << {moff, 3'b000};
  always_comb begin
    if (exmem_mem_q != MEM_STORE) dm_wstrb = 4'b0000;
    else unique case (mf3[1:0])
      2'b00:   dm_wstrb = 4'b0001 << moff;
      2'b01:   dm_wstrb = moff[1] ? 4'b1100 : 4'b0011;
      default: dm_wstrb = 4'b1111;
    endcase
  end
  assign mem_busy = dm_valid && !dm_ready;

  // Load result extraction.
  wire [31:0] ld_shift = dm_rdata >> {moff, 3'b000};
  logic [31:0] ld_val;
  always_comb begin
    unique case (mf3)
      3'b000:  ld_val = {{24{ld_shift[7]}}, ld_shift[7:0]};    // LB
      3'b001:  ld_val = {{16{ld_shift[15]}}, ld_shift[15:0]};  // LH
      3'b100:  ld_val = {24'b0, ld_shift[7:0]};                // LBU
      3'b101:  ld_val = {16'b0, ld_shift[15:0]};               // LHU
      default: ld_val = ld_shift;                              // LW
    endcase
  end

  // CSR file access (serialized: nothing younger is live).
  logic [31:0] csr_rdata, trap_vector, mepc_out, sepc_out;
  logic [31:0] csr_mstatus, csr_mtvec, csr_mepc, csr_mcause, csr_mtval;
  logic [31:0] csr_mscratch, csr_mie, csr_mip, csr_satp;
  logic [1:0]  csr_prv;
  logic        csr_illegal, csr_irq_take;
  logic [3:0]  csr_irq_cause;
  wire mem_commit = exmem_v_q && !core_hold;
  wire csr_acc_en = mem_commit && !exmem_exc_q && exmem_csr_q != CSR_NONE;

  // Dynamic (privilege-dependent) SYSTEM legality, resolved at commit.
  wire sys_ill =
      (exmem_sys_q == SYS_MRET && csr_prv != 2'b11) ||
      (exmem_sys_q == SYS_SRET &&
       (csr_prv == 2'b00 || (csr_prv == 2'b01 && csr_mstatus[22]))) ||
      (exmem_sys_q == SYS_WFI && csr_prv != 2'b11 && csr_mstatus[21]) ||
      (exmem_sys_q == SYS_SFENCE &&
       (csr_prv == 2'b00 || (csr_prv == 2'b01 && csr_mstatus[20])));

  // WFI parks the hart instead of retiring as a nop.  The spec permits either,
  // and v1 chose the nop; the cost is that a kernel waiting for a keystroke
  // fetches and retires forever, which burns power on a board and host time
  // under simulation for no architectural gain.
  //
  // "Pending", not "enabled", is the wake condition the spec asks for: a
  // pending-but-disabled interrupt resumes execution at the next instruction
  // rather than trapping, so waking on mip alone is correct and is also what
  // makes a masked device able to break the hart out.
  //
  // The illegal case must be excluded or it deadlocks: WFI below M-mode with
  // mstatus.TW set is an illegal instruction, and an instruction that is going
  // to trap has to be allowed to reach the commit point to do it.
  assign wfi_wait = exmem_v_q && !exmem_exc_q && !sys_ill &&
                    exmem_sys_q == SYS_WFI && csr_mip == 32'b0;
  assign cpu_idle = wfi_wait;

  // Final exception resolution at commit.
  wire bus_fault = dm_valid && dm_ready && dm_err;
  wire bus_pgf   = dm_valid && dm_ready && dm_pgf;
  logic       trap_now;
  logic [3:0] trap_cause;
  logic [31:0] trap_tval;
  always_comb begin
    trap_now   = 1'b0;
    trap_cause = 4'd0;
    trap_tval  = 32'b0;
    if (mem_commit) begin
      if (exmem_exc_q) begin
        trap_now = 1'b1; trap_cause = exmem_cause_q; trap_tval = exmem_tval_q;
      end else if (bus_pgf) begin
        trap_now   = 1'b1;
        trap_cause = is_ld ? EXC_LPAGE : EXC_SPAGE;
        trap_tval  = exmem_y_q;
      end else if (bus_fault) begin
        trap_now   = 1'b1;
        trap_cause = is_ld ? EXC_LFAULT : EXC_SFAULT;
        trap_tval  = exmem_y_q;
      end else if (csr_illegal || sys_ill) begin
        trap_now = 1'b1; trap_cause = EXC_ILL; trap_tval = exmem_insn_q;
      end else if (exmem_sys_q == SYS_ECALL) begin
        trap_now = 1'b1; trap_cause = {2'b10, csr_prv};  // 8 + privilege
        trap_tval = 32'b0;
      end else if (exmem_sys_q == SYS_EBREAK) begin
        trap_now = 1'b1; trap_cause = EXC_BREAK; trap_tval = exmem_pc_q;
      end
    end
  end

  // Interrupt take/cause (delegation-aware) live in the CSR file; taken
  // only after a committing instruction, so its side effects are visible
  // and the epc names its successor.
  wire irq_now = mem_commit && !trap_now && csr_irq_take;

  // Not gated on irq_now: an interrupt landing on an xret commit still
  // retires it (epc <= its target, the IE restore folded into the trap
  // entry's PIE save inside csr_file).
  wire mret_now = mem_commit && !trap_now && exmem_sys_q == SYS_MRET;
  wire sret_now = mem_commit && !trap_now && exmem_sys_q == SYS_SRET;
  wire ser_done = mem_commit && !trap_now && !irq_now &&
                  (exmem_csr_q != CSR_NONE || exmem_sys_q == SYS_WFI ||
                   exmem_sys_q == SYS_FENCE_I || exmem_sys_q == SYS_SFENCE ||
                   exmem_muldiv_q);

  // Conservative TLB maintenance: flush on every serialized commit (covers
  // sfence.vma and satp writes; over-flushing is always safe).
  wire tlb_flush = ser_done || mret_now || sret_now || trap_now || irq_now;

  assign redirect_mem    = trap_now || irq_now || mret_now || sret_now ||
                           ser_done;
  assign redirect_mem_pc = (trap_now || irq_now) ? trap_vector
                         : mret_now ? mepc_out
                         : sret_now ? sepc_out
                                    : exmem_pc_q + 32'd4;

  wire retire = mem_commit && !trap_now;

  csr_file u_csr (
    .clk(clk), .rst(rst),
    .acc_en(csr_acc_en), .acc_addr(exmem_insn_q[31:20]),
    .acc_op(exmem_csr_q), .acc_wdata(exmem_data_q),
    .acc_rs1x0(exmem_csr_rs1x0_q), .acc_commit(mem_commit),
    .acc_rdata(csr_rdata), .acc_illegal(csr_illegal),
    .trap_en(trap_now || irq_now), .trap_interrupt(irq_now),
    // An interrupt names the retired instruction's architectural successor:
    // the branch target is already in exmem_npc_q; an xret's successor is
    // its return address, which only the CSR file knows.
    .trap_pc(!irq_now ? exmem_pc_q
             : mret_now ? mepc_out
             : sret_now ? sepc_out : exmem_npc_q),
    .trap_cause(irq_now ? csr_irq_cause : trap_cause),
    .trap_tval(trap_tval), .trap_vector(trap_vector),
    .mret_en(mret_now), .sret_en(sret_now),
    .mepc_out(mepc_out), .sepc_out(sepc_out),
    .retire(retire), .irq_software(irq_software), .irq_timer(irq_timer),
    .irq_external(irq_external), .irq_s_external(irq_s_external),
    .irq_take(csr_irq_take), .irq_cause(csr_irq_cause),
    .prv(csr_prv), .satp_out(csr_satp),
    .state_mstatus(csr_mstatus), .state_mtvec(csr_mtvec),
    .state_mepc(csr_mepc), .state_mcause(csr_mcause),
    .state_mtval(csr_mtval), .state_mscratch(csr_mscratch),
    .state_mie(csr_mie), .state_mip(csr_mip)
  );

  // ------------------------------------------------------------- MMUs
  // Fetch translates at the current privilege; data applies the MPRV
  // substitution (loads/stores as MPP when mstatus.MPRV is set).
  wire [1:0] data_eprv = csr_mstatus[17] ? csr_mstatus[12:11] : csr_prv;

  sv32_mmu #(.IS_FETCH(1'b1)) u_immu (
    .clk(clk), .rst(rst),
    .satp(csr_satp), .eprv(csr_prv),
    .sum(csr_mstatus[18]), .mxr(csr_mstatus[19]), .flush(tlb_flush),
    .c_valid(if_valid), .c_addr(if_addr), .c_wdata(32'b0), .c_wstrb(4'b0),
    .c_ready(if_ready), .c_rdata(if_rdata), .c_err(if_err),
    .c_pgfault(if_pgf),
    .m_valid(ibus_valid), .m_addr(ibus_addr), .m_wdata(ibus_wdata),
    .m_wstrb(ibus_wstrb), .m_ready(ibus_ready), .m_rdata(ibus_rdata),
    .m_err(ibus_err)
  );

  sv32_mmu #(.IS_FETCH(1'b0)) u_dmmu (
    .clk(clk), .rst(rst),
    .satp(csr_satp), .eprv(data_eprv),
    .sum(csr_mstatus[18]), .mxr(csr_mstatus[19]), .flush(tlb_flush),
    .c_valid(dm_valid), .c_addr(dm_addr), .c_wdata(dm_wdata),
    .c_wstrb(dm_wstrb), .c_ready(dm_ready), .c_rdata(dm_rdata),
    .c_err(dm_err), .c_pgfault(dm_pgf),
    .m_valid(dbus_valid), .m_addr(dbus_addr), .m_wdata(dbus_wdata),
    .m_wstrb(dbus_wstrb), .m_ready(dbus_ready), .m_rdata(dbus_rdata),
    .m_err(dbus_err)
  );

  // ---------------------------------------------------------------- MEM/WB
  logic [31:0] wb_mux;
  always_comb begin
    unique case (exmem_wb_q)
      WB_MEM:  wb_mux = ld_val;
      WB_PC4:  wb_mux = exmem_pc_q + 32'd4;
      WB_CSR:  wb_mux = csr_rdata;
      default: wb_mux = exmem_y_q;        // WB_ALU
    endcase
  end

  // MEM/WB must HOLD during a freeze: a consumer frozen in EX still forwards
  // from here, and its ID-time regfile read predates this result.  That is
  // true of a WFI park for exactly the reason it is true of a dbus stall --
  // the younger instruction is still sitting in EX waiting on this value.
  // (The repeated regfile write while held is idempotent.)
  always_ff @(posedge clk) begin
    if (rst) begin
      memwb_v_q <= 1'b0;
    end else if (!core_hold) begin
      memwb_v_q     <= retire;
      memwb_rd_q    <= exmem_rd_q;
      memwb_rd_we_q <= exmem_rd_we_q;
      memwb_val_q   <= wb_mux;
    end
  end

  // ---------------------------------------------------------------- WB
  assign wb_we  = memwb_v_q && memwb_rd_we_q;
  assign wb_rd  = memwb_rd_q;
  assign wb_val = memwb_val_q;

  // These signals are sampled immediately before a rising edge by the
  // cosim harness. CSR state is sampled after that edge, once the event's
  // side effects have taken place.
  assign trace_valid = mem_commit;
  assign trace_trap = trap_now;
  assign trace_pc = exmem_pc_q;
  assign trace_insn = exmem_insn_q;
  assign trace_cause = trap_cause;
  assign trace_tval = trap_tval;
  assign trace_rd_we = retire && exmem_rd_we_q && exmem_rd_q != 5'd0;
  assign trace_rd = exmem_rd_q;
  assign trace_rd_val = wb_mux;
  assign trace_mstatus = csr_mstatus;
  assign trace_mtvec = csr_mtvec;
  assign trace_mepc = csr_mepc;
  assign trace_mcause = csr_mcause;
  assign trace_mtval = csr_mtval;
  assign trace_mscratch = csr_mscratch;
  assign trace_mie = csr_mie;
  assign trace_mip = csr_mip;
  assign trace_prv = csr_prv;

  // ---------------------------------------------------------------- RVFI
  // rvfi_order is the index of this commit; it advances for both retirement
  // and traps, exactly once per mem_commit event.
  logic [63:0] rvfi_order_q;
  always_ff @(posedge clk) begin
    if (rst) rvfi_order_q <= 64'b0;
    else if (mem_commit) rvfi_order_q <= rvfi_order_q + 64'd1;
  end

  always_comb begin
    rvfi_valid     = mem_commit;
    rvfi_order     = rvfi_order_q;
    rvfi_insn      = exmem_insn_q;
    rvfi_trap      = trap_now;
    rvfi_halt      = 1'b0;
    rvfi_intr      = irq_now;
    rvfi_mode      = csr_prv;
    rvfi_ixl       = 2'b01;               // RV32
    rvfi_rs1_addr  = exmem_rs1_q;
    rvfi_rs2_addr  = exmem_rs2_q;
    rvfi_rs1_rdata = exmem_rs1_rdata_q;
    rvfi_rs2_rdata = exmem_rs2_rdata_q;
    rvfi_rd_addr   = retire && exmem_rd_we_q ? exmem_rd_q : 5'b0;
    rvfi_rd_wdata  = rvfi_rd_addr == 5'd0 ? 32'b0 : wb_mux;
    rvfi_pc_rdata  = exmem_pc_q;
    rvfi_pc_wdata  = (trap_now || irq_now) ? trap_vector
                   : mret_now ? mepc_out
                   : sret_now ? sepc_out
                              : exmem_npc_q;
    rvfi_mem_addr  = dm_addr;
    rvfi_mem_rmask = is_ld && !trap_now ?
                     (mf3[1:0] == 2'b00 ? (4'b0001 << moff) :
                      mf3[1:0] == 2'b01 ? (moff[1] ? 4'b1100 : 4'b0011) :
                                          4'b1111) : 4'b0000;
    rvfi_mem_wmask = exmem_mem_q == MEM_STORE && !trap_now ? dm_wstrb
                                                              : 4'b0000;
    rvfi_mem_rdata = dm_rdata;
    rvfi_mem_wdata = dm_wdata;
  end

  // Unused instruction bits (decoder/slices take what they need).
  // verilator lint_off UNUSED
  logic unused_bits;
  assign unused_bits = ^{idex_rs2_use_q, idex_rs1_use_q, exmem_insn_q[19:15],
                         exmem_insn_q[11:0], ld_shift[31:16]};
  // verilator lint_on UNUSED

endmodule
