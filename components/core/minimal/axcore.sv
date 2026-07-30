// axcore (minimal): a small multi-cycle RV32IM machine-mode core.
//
// A drop-in replacement for the 5-stage core on the stock `axcore` boundary,
// built for accelerator hosts: it orchestrates a role (set up buffers, ring the
// doorbell, poll done) while spending as little fabric as possible, so the
// reclaimed LUTs go to the accelerator.  It drops the Sv32 MMU and S/U modes
// entirely (machine mode only, physical addressing) and trades the pipeline for
// a compact FETCH/EXEC/MEM/MULDIV state machine — no forwarding, no hazard
// unit, no pipeline registers.  It reuses the same verified units as the
// reference core: decoder, immdec, branch_cmp, the ALU, mul/div, and register
// file components.
//
// It implements the full RV32IM instruction set and a minimal machine-mode CSR
// set (mstatus, mie, mtvec, mscratch, mepc, mcause, mtval, mip, mcycle,
// minstret, and the read-only id CSRs), with precise traps for illegal
// instructions, ECALL/EBREAK, misaligned/bus-error access, and the three
// machine interrupts.  It drives a one-retire RVFI trace, so it carries its own
// bounded riscv-formal evidence (`make -C formal check-minimal`).  The wider
// architectural-state trace the reference core exports for lock-step cosim is
// still absent: without Sv32 and S/U there is nothing to compare against the
// golden ISS's privileged state.
module axcore #(
  parameter logic [31:0] RESET_PC = 32'h8000_0000,
  parameter bit ENABLE_M = 1'b1
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

  // One-retire RVFI trace.  Same shape as the reference core's, because both
  // retire at most one instruction per cycle -- which is what lets this core
  // reuse that core's formal environment rather than needing its own.
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

  // The instruction fetch port never writes (no page-table walker here).
  assign ibus_wdata = 32'b0;
  assign ibus_wstrb = 4'b0;

  typedef enum logic [2:0] {S_FETCH, S_EXEC, S_MEM, S_MULDIV, S_TRAP} state_e;
  state_e state_q;

  logic [31:0] pc_q, ir_q;

  // Machine-mode CSRs.
  logic [31:0] mstatus_q, mtvec_q, mscratch_q, mepc_q, mcause_q, mtval_q, mie_q;
  logic [63:0] mcycle_q, minstret_q;
  // mstatus fields we model: MIE (bit 3), MPIE (bit 7), MPP (bits 12:11 = 11).
  localparam int MIE_B = 3, MPIE_B = 7;

  // ---- Decode of the latched instruction ------------------------------------
  wire [4:0]  rs1 = ir_q[19:15];
  wire [4:0]  rs2 = ir_q[24:20];
  wire [4:0]  rd  = ir_q[11:7];
  wire [2:0]  f3  = ir_q[14:12];
  wire [11:0] csr_addr = ir_q[31:20];

  logic illegal, dec_rd_we, dec_csr_imm, dec_muldiv, dec_uses_rs1, dec_uses_rs2;
  opa_sel_e dec_opa; opb_sel_e dec_opb; imm_sel_e dec_imm_sel;
  alu_op_t  dec_alu_op; wb_sel_e dec_wb; mem_op_e dec_mem; br_sel_e dec_br;
  csr_op_e  dec_csr_op; sys_e dec_sys;

  decoder #(.ENABLE_M(ENABLE_M)) u_dec (
    .insn(ir_q), .illegal(illegal), .rd_we(dec_rd_we),
    .opa_sel(dec_opa), .opb_sel(dec_opb), .imm_sel(dec_imm_sel),
    .alu_op(dec_alu_op), .wb_sel(dec_wb), .mem_op(dec_mem), .br_sel(dec_br),
    .csr_op(dec_csr_op), .csr_imm(dec_csr_imm), .muldiv(dec_muldiv),
    .sys(dec_sys), .uses_rs1(dec_uses_rs1), .uses_rs2(dec_uses_rs2));

  logic [31:0] imm;
  immdec u_imm (.insn(ir_q), .sel(dec_imm_sel), .imm(imm));

  logic [31:0] rs1_val, rs2_val, rf_wdata;
  logic        rf_we;
  regfile #(.BYPASS(0)) u_rf (
    .clk(clk), .we(rf_we && rd != 5'd0), .waddr(rd), .wdata(rf_wdata),
    .raddr1(rs1), .rdata1(rs1_val), .raddr2(rs2), .rdata2(rs2_val));

  wire [31:0] alu_a = (dec_opa == OPA_PC)   ? pc_q :
                      (dec_opa == OPA_ZERO) ? 32'b0 : rs1_val;
  wire [31:0] alu_b = (dec_opb == OPB_IMM)  ? imm : rs2_val;
  logic [31:0] alu_y;

  alu u_alu (.a(alu_a), .b(alu_b), .op(dec_alu_op), .y(alu_y));

  logic br_taken;
  branch_cmp u_bc (.a(rs1_val), .b(rs2_val), .f3(f3), .taken(br_taken));

  logic        md_start, md_busy, md_done;
  logic [31:0] md_result;
  generate if (ENABLE_M) begin : g_md
    muldiv u_md (.clk(clk), .rst(rst), .start(md_start), .op(f3),
                 .a(rs1_val), .b(rs2_val), .busy(md_busy), .done(md_done),
                 .result(md_result));
  end else begin : g_no_md
    assign md_busy = 1'b0; assign md_done = 1'b1; assign md_result = 32'b0;
  end endgenerate

  // Deliberately unread signals, tied off after everything they name exists: a
  // strict 1800-2017 frontend (Slang, which the formal flow uses) rejects a
  // reference that appears above its declaration, even inside a lint pragma.
  // verilator lint_off UNUSED
  wire _unused = &{1'b0, dec_uses_rs1, dec_uses_rs2, md_busy};
  // verilator lint_on UNUSED

  // ---- Memory access sizing --------------------------------------------------
  wire [31:0] mem_addr = alu_y;               // rs1 + imm (decoder forces ADD)
  wire [1:0]  ma = mem_addr[1:0];
  wire        misaligned = (f3[1:0] == 2'b10 && ma != 2'b00) ||       // word
                           (f3[1:0] == 2'b01 && ma[0] != 1'b0);       // half
  // Store data + strobe positioned by the low address bits.
  logic [31:0] st_wdata; logic [3:0] st_wstrb;
  always_comb begin
    st_wdata = rs2_val; st_wstrb = 4'b0000;
    unique case (f3[1:0])
      2'b00: begin st_wdata = {4{rs2_val[7:0]}};  st_wstrb = 4'b0001 << ma; end
      2'b01: begin st_wdata = {2{rs2_val[15:0]}}; st_wstrb = (ma[1] ? 4'b1100 : 4'b0011); end
      default: begin st_wdata = rs2_val; st_wstrb = 4'b1111; end
    endcase
  end
  // Load data extraction with sign/zero extension.
  logic [31:0] ld_data;
  always_comb begin
    logic [7:0]  b8;  logic [15:0] h16;
    b8  = dbus_rdata[8*ma +: 8];
    h16 = ma[1] ? dbus_rdata[31:16] : dbus_rdata[15:0];
    unique case (f3)
      3'b000:  ld_data = {{24{b8[7]}},  b8};    // LB
      3'b001:  ld_data = {{16{h16[15]}}, h16};  // LH
      3'b100:  ld_data = {24'b0, b8};           // LBU
      3'b101:  ld_data = {16'b0, h16};          // LHU
      default: ld_data = dbus_rdata;            // LW
    endcase
  end

  // ---- Interrupts ------------------------------------------------------------
  wire [31:0] mip_val = {20'b0, irq_external, 3'b0, irq_timer, 3'b0,
                         irq_software, 3'b0};
  wire        int_pending = mstatus_q[MIE_B] && ((mie_q & mip_val) != 32'b0);
  logic [3:0] int_cause;
  always_comb begin
    int_cause = 4'd11;                                   // machine external
    if (mie_q[7] && irq_timer)          int_cause = 4'd7; // machine timer
    else if (mie_q[3] && irq_software)  int_cause = 4'd3; // machine software
    else if (mie_q[11] && irq_external) int_cause = 4'd11;
  end

  // ---- CSR read/write --------------------------------------------------------
  logic [31:0] csr_rdata;
  always_comb begin
    unique case (csr_addr)
      12'h300: csr_rdata = mstatus_q;
      12'h301: csr_rdata = 32'h4000_0100;      // misa: RV32 I+M (MXL=1, bits I,M)
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
      default: csr_rdata = 32'b0;              // mhartid/mvendorid/... read 0
    endcase
  end
  wire [31:0] csr_src = dec_csr_imm ? {27'b0, rs1} : rs1_val;
  logic [31:0] csr_wval;
  always_comb begin
    unique case (dec_csr_op)
      CSR_RS:  csr_wval = csr_rdata | csr_src;
      CSR_RC:  csr_wval = csr_rdata & ~csr_src;
      default: csr_wval = csr_src;             // CSR_RW
    endcase
  end
  // A CSR write actually happens unless it is a set/clear with x0 source.
  wire csr_writes = dec_csr_op != CSR_NONE &&
                    !((dec_csr_op == CSR_RS || dec_csr_op == CSR_RC) && rs1 == 5'd0);

  // ---- Writeback mux ---------------------------------------------------------
  always_comb begin
    unique case (dec_wb)
      WB_MEM:  rf_wdata = ld_data;
      WB_PC4:  rf_wdata = pc_q + 32'd4;
      WB_CSR:  rf_wdata = csr_rdata;
      default: rf_wdata = dec_muldiv ? md_result : alu_y;
    endcase
  end

  // ---- Next PC for control transfers ----------------------------------------
  wire [31:0] pc4 = pc_q + 32'd4;
  logic [31:0] seq_next_pc;
  always_comb begin
    unique case (dec_br)
      BR_JAL:  seq_next_pc = pc_q + imm;
      BR_JALR: seq_next_pc = (rs1_val + imm) & ~32'd1;
      BR_COND: seq_next_pc = br_taken ? (pc_q + imm) : pc4;
      default: seq_next_pc = pc4;
    endcase
  end

  // Instruction-address-misaligned: a taken transfer to a misaligned target
  // traps on the transfer itself (IALIGN=32, no C extension), so rd is not
  // written and mepc names the branch rather than the target -- riscv-tests
  // ma_fetch checks exactly this.  Only bit 1 can ever be set: JALR already
  // masks bit 0 and the JAL/branch immediates have it clear.
  wire fetch_misaligned = dec_br != BR_NONE && seq_next_pc[1:0] != 2'b00;

  // Bus drives are level signals qualified by the state.
  assign ibus_valid = (state_q == S_FETCH) && !int_pending;
  assign ibus_addr  = pc_q;
  assign dbus_valid = (state_q == S_MEM);
  // aXbus data accesses are word-aligned; the byte/half lane is carried by the
  // low address bits and handled by the store-strobe and load-extract logic.
  assign dbus_addr  = {mem_addr[31:2], 2'b00};
  assign dbus_wdata = st_wdata;
  assign dbus_wstrb = (dec_mem == MEM_STORE) ? st_wstrb : 4'b0;

  // Retirement / trap trace for the SoC (I$ coherency hooks, cosim).
  assign trace_insn = ir_q;

  // ---- The machine -----------------------------------------------------------
  logic take_trap; logic [31:0] trap_cause, trap_tval;

  always_comb begin
    rf_we      = 1'b0;
    md_start   = 1'b0;
    take_trap  = 1'b0;
    trap_cause = 32'b0;
    trap_tval  = 32'b0;
    trace_valid = 1'b0;
    trace_trap  = 1'b0;
    unique case (state_q)
      S_EXEC: begin
        if (illegal) begin
          take_trap = 1'b1; trap_cause = 32'd2; trap_tval = ir_q;
        end else if (fetch_misaligned) begin
          take_trap = 1'b1; trap_cause = 32'd0; trap_tval = seq_next_pc;
        end else if (dec_sys == SYS_ECALL) begin
          take_trap = 1'b1; trap_cause = 32'd11;
        end else if (dec_sys == SYS_EBREAK) begin
          take_trap = 1'b1; trap_cause = 32'd3; trap_tval = pc_q;
        end else if (dec_mem != MEM_NONE) begin
          if (misaligned) begin
            take_trap = 1'b1;
            trap_cause = (dec_mem == MEM_STORE) ? 32'd6 : 32'd4;
            trap_tval  = mem_addr;
          end
          // else -> S_MEM (handled in always_ff)
        end else if (dec_muldiv) begin
          md_start = 1'b1;    // -> S_MULDIV
        end else begin
          rf_we = dec_rd_we;  // ALU/LUI/AUIPC/JAL/JALR/CSR/MRET/WFI retire now
          trace_valid = 1'b1;
        end
      end
      S_MEM: if (dbus_ready) begin
        if (dbus_err) begin
          take_trap = 1'b1;
          trap_cause = (dec_mem == MEM_STORE) ? 32'd7 : 32'd5;
          trap_tval  = mem_addr;
        end else begin
          rf_we = (dec_mem == MEM_LOAD) && dec_rd_we;
          trace_valid = 1'b1;
        end
      end
      S_MULDIV: if (md_done) begin
        rf_we = dec_rd_we;
        trace_valid = 1'b1;
      end
      default: ;
    endcase
    if (take_trap) begin trace_valid = 1'b1; trace_trap = 1'b1; end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      state_q    <= S_FETCH;
      pc_q       <= RESET_PC;
      ir_q       <= 32'b0;
      mstatus_q  <= 32'h0000_1800;   // MPP = 11
      mtvec_q    <= 32'b0;
      mscratch_q <= 32'b0;
      mepc_q     <= 32'b0;
      mcause_q   <= 32'b0;
      mtval_q    <= 32'b0;
      mie_q      <= 32'b0;
      mcycle_q   <= 64'b0;
      minstret_q <= 64'b0;
    end else begin
      mcycle_q <= mcycle_q + 64'd1;

      unique case (state_q)
        S_FETCH: begin
          if (int_pending) begin
            // Take the interrupt at this instruction boundary.
            mepc_q          <= pc_q;
            mcause_q        <= {1'b1, 27'b0, int_cause};
            mstatus_q[MPIE_B] <= mstatus_q[MIE_B];
            mstatus_q[MIE_B]  <= 1'b0;
            pc_q            <= mtvec_q;
          end else if (ibus_ready) begin
            ir_q    <= ibus_rdata;
            state_q <= ibus_err ? S_TRAP : S_EXEC;
            if (ibus_err) begin
              mepc_q <= pc_q; mcause_q <= 32'd1; mtval_q <= pc_q;
            end
          end
        end

        S_EXEC: begin
          minstret_q <= minstret_q + 64'd1;   // count the instruction we act on
          if (take_trap) begin
            mepc_q            <= pc_q;
            mcause_q          <= trap_cause;
            mtval_q           <= trap_tval;
            mstatus_q[MPIE_B] <= mstatus_q[MIE_B];
            mstatus_q[MIE_B]  <= 1'b0;
            pc_q              <= mtvec_q;
            state_q           <= S_FETCH;
          end else if (dec_mem != MEM_NONE) begin
            state_q <= S_MEM;                 // load/store address phase
          end else if (dec_muldiv) begin
            state_q <= S_MULDIV;
          end else begin
            if (dec_sys == SYS_MRET) begin
              pc_q              <= mepc_q;
              mstatus_q[MIE_B]  <= mstatus_q[MPIE_B];
              mstatus_q[MPIE_B] <= 1'b1;
            end else begin
              pc_q <= seq_next_pc;
            end
            if (csr_writes) csr_write(csr_addr, csr_wval);
            state_q <= S_FETCH;
          end
        end

        S_MEM: if (dbus_ready) begin
          if (dbus_err) begin
            mepc_q            <= pc_q;
            mcause_q          <= trap_cause;
            mtval_q           <= trap_tval;
            mstatus_q[MPIE_B] <= mstatus_q[MIE_B];
            mstatus_q[MIE_B]  <= 1'b0;
            pc_q              <= mtvec_q;
          end else begin
            pc_q <= pc4;
          end
          state_q <= S_FETCH;
        end

        S_MULDIV: if (md_done) begin
          pc_q    <= pc4;
          state_q <= S_FETCH;
        end

        S_TRAP: begin                          // fetch bus-error landing
          mstatus_q[MPIE_B] <= mstatus_q[MIE_B];
          mstatus_q[MIE_B]  <= 1'b0;
          pc_q              <= mtvec_q;
          state_q           <= S_FETCH;
        end
        default: state_q <= S_FETCH;
      endcase
    end
  end

  // ---- RVFI --------------------------------------------------------------------
  // `trace_valid` is already exactly one architectural event -- a retirement or
  // a precise trap -- so it is the commit point RVFI needs.  Every field below
  // is read at that instant, when `ir_q`, the register reads, and the bus
  // response are all stable for the instruction being retired.
  //
  // The S_TRAP path (a fetch bus error) does not raise `trace_valid` and so does
  // not appear here; the formal environment ties `ibus_err` low, matching the
  // reference core's wrapper, so that path is outside the proof either way.
  wire rvfi_mem_done = (state_q == S_MEM) && dbus_ready && !dbus_err;

  // The first instruction to retire after any trap-vector redirect is the
  // handler's entry, which is what rvfi_intr marks.
  wire rvfi_take_int = (state_q == S_FETCH) && int_pending;
  logic rvfi_intr_q;
  always_ff @(posedge clk) begin
    if (rst)                              rvfi_intr_q <= 1'b0;
    else if (take_trap || rvfi_take_int)  rvfi_intr_q <= 1'b1;
    else if (trace_valid)                 rvfi_intr_q <= 1'b0;
  end

  logic [63:0] rvfi_order_q;
  always_ff @(posedge clk) begin
    if (rst)               rvfi_order_q <= 64'b0;
    else if (trace_valid)  rvfi_order_q <= rvfi_order_q + 64'd1;
  end

  always_comb begin
    rvfi_valid = trace_valid;
    rvfi_order = rvfi_order_q;
    rvfi_insn  = ir_q;
    rvfi_trap  = trace_trap;
    rvfi_halt  = 1'b0;
    rvfi_intr  = rvfi_intr_q;
    rvfi_mode  = 2'b11;                   // machine mode only
    rvfi_ixl   = 2'b01;                   // RV32

    rvfi_rs1_addr = rs1;
    rvfi_rs2_addr = rs2;
    // Reading x0 must report zero; the register file already returns zero, but
    // stating it here keeps the trace correct independently of that.
    rvfi_rs1_rdata = rs1 == 5'd0 ? 32'b0 : rs1_val;
    rvfi_rs2_rdata = rs2 == 5'd0 ? 32'b0 : rs2_val;

    // rd is written only when the register file actually accepts it, which is
    // also the condition under which RVFI may name a destination.
    rvfi_rd_addr  = (rf_we && rd != 5'd0) ? rd : 5'd0;
    rvfi_rd_wdata = (rf_we && rd != 5'd0) ? rf_wdata : 32'b0;

    rvfi_pc_rdata = pc_q;
    rvfi_pc_wdata = take_trap             ? mtvec_q
                  : (dec_sys == SYS_MRET) ? mepc_q
                                          : seq_next_pc;

    // Only a data access that reached the bus and completed without a fault is
    // an architectural memory event.
    rvfi_mem_addr  = rvfi_mem_done ? dbus_addr : 32'b0;
    rvfi_mem_rmask = (rvfi_mem_done && dec_mem == MEM_LOAD)  ? st_wstrb : 4'b0;
    rvfi_mem_wmask = (rvfi_mem_done && dec_mem == MEM_STORE) ? st_wstrb : 4'b0;
    rvfi_mem_rdata = rvfi_mem_done ? dbus_rdata : 32'b0;
    rvfi_mem_wdata = rvfi_mem_done ? st_wdata   : 32'b0;
  end

  // CSR write side effects, kept in one place.
  task automatic csr_write(input logic [11:0] a, input logic [31:0] v);
    unique case (a)
      12'h300: mstatus_q  <= (v & 32'h0000_0088) | 32'h0000_1800; // MIE|MPIE, MPP=11
      12'h304: mie_q      <= v;
      // Both MODE fields are WARL and only direct is implemented, so mtvec
      // reads back mode 0 -- reporting vectored would strand software in the
      // wrong handler, and the raw value is what the trap paths load into pc.
      12'h305: mtvec_q    <= v & ~32'd3;
      12'h340: mscratch_q <= v;
      12'h341: mepc_q     <= v & ~32'd3;     // IALIGN=32: no C extension
      12'h342: mcause_q   <= v;
      12'h343: mtval_q    <= v;
      // Counters are M-mode writable.  Full-width assignments, so they
      // override this cycle's increment above -- which is exactly the rule
      // that a write suppresses the increment from the writing instruction.
      12'hb00: mcycle_q   <= {mcycle_q[63:32], v};
      12'hb80: mcycle_q   <= {v, mcycle_q[31:0]};
      12'hb02: minstret_q <= {minstret_q[63:32], v};
      12'hb82: minstret_q <= {v, minstret_q[31:0]};
      default: ;
    endcase
  endtask
endmodule
