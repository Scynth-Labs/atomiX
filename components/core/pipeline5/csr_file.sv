// aXcore privileged CSR file — DESIGN.md §4.2: its own module, accessed at
// the commit point under the serialization rule, so no CSR forwarding exists.
// Phase 4: M/S/U privilege levels, medeleg/mideleg trap routing, and the
// Sv32 translation context (satp/SUM/MXR/MPRV) consumed by the MMUs.
// Semantics mirror sim/axsim/src/cpu.cpp exactly; any intentional divergence
// must change both and be called out.
module csr_file (
  input  logic        clk,
  input  logic        rst,

  // Access port: driven at commit for a serialized Zicsr instruction.
  input  logic                 acc_en,     // check + read
  input  logic [11:0]          acc_addr,
  input  axcore_pkg::csr_op_e  acc_op,
  input  logic [31:0]          acc_wdata,  // rs1 value or zero-extended uimm
  input  logic                 acc_rs1x0,  // rs1/uimm is zero: RS/RC skip write
  input  logic                 acc_commit, // perform the side effect
  output logic [31:0]          acc_rdata,
  output logic                 acc_illegal,

  // Trap entry (routed to S when the cause is delegated and the effective
  // privilege after the committing instruction is <= S, else to M).
  input  logic        trap_en,
  input  logic        trap_interrupt,
  input  logic [31:0] trap_pc,
  input  logic [3:0]  trap_cause,
  input  logic [31:0] trap_tval,
  output logic [31:0] trap_vector,   // resolved handler base (mtvec or stvec)

  input  logic        mret_en,
  input  logic        sret_en,
  output logic [31:0] mepc_out,
  output logic [31:0] sepc_out,

  input  logic        retire,     // minstret increment

  // Machine-level interrupt lines. Hardware-driven mip bits; the S-level
  // bits (SSIP/STIP/SEIP) additionally have software-writable sources.
  input  logic        irq_software,
  input  logic        irq_timer,
  input  logic        irq_external,

  // Interrupt delivery decision against current (pre-commit) state.
  output logic        irq_take,
  output logic [3:0]  irq_cause,

  // Privilege + translation context for the pipeline and the MMUs.
  output logic [1:0]  prv,
  output logic [31:0] satp_out,

  // Architectural-state observation port for the lock-step checker.  These
  // are outputs only; they do not participate in core control.
  output logic [31:0] state_mstatus,
  output logic [31:0] state_mtvec,
  output logic [31:0] state_mepc,
  output logic [31:0] state_mcause,
  output logic [31:0] state_mtval,
  output logic [31:0] state_mscratch,
  output logic [31:0] state_mie,
  output logic [31:0] state_mip
);

  import axcore_pkg::*;

  // mstatus fields, stored individually.
  logic        sie_q, mie_q, spie_q, mpie_q, spp_q;
  logic [1:0]  mpp_q;
  logic        mprv_q, sum_q, mxr_q, tvm_q, tw_q, tsr_q;
  logic [1:0]  prv_q;

  logic [31:0] mtvec_q, mepc_q, mcause_q, mtval_q, mscratch_q, mie_reg_q;
  logic [31:0] medeleg_q, mideleg_q, mcounteren_q;
  logic [31:0] stvec_q, sepc_q, scause_q, stval_q, sscratch_q, scounteren_q;
  logic [31:0] satp_q;
  logic [31:0] soft_ip_q;   // software-writable SSIP/STIP/SEIP sources
  logic [63:0] cycle_q, instret_q;

  wire [31:0] mstatus = {9'b0, tsr_q, tw_q, tvm_q, mxr_q, sum_q, mprv_q,
                         4'b0, mpp_q, 2'b0, spp_q, mpie_q, 1'b0, spie_q,
                         1'b0, mie_q, 1'b0, sie_q, 1'b0};
  localparam logic [31:0] SSTATUS_MASK = 32'h000C_0122;
  wire [31:0] mip = {20'b0, irq_external, 3'b0, irq_timer, 3'b0,
                     irq_software, 3'b0} | (soft_ip_q & 32'h222);

  assign mepc_out = mepc_q;
  assign sepc_out = sepc_q;
  assign prv      = prv_q;
  assign satp_out = satp_q;
  assign state_mstatus = mstatus;
  assign state_mtvec = mtvec_q;
  assign state_mepc = mepc_q;
  assign state_mcause = mcause_q;
  assign state_mtval = mtval_q;
  assign state_mscratch = mscratch_q;
  assign state_mie = mie_reg_q;
  assign state_mip = mip;

  // Counter visibility from S/U: mcounteren gates S and U, scounteren
  // additionally gates U. Bit 0 = CY, bit 2 = IR.
  function automatic logic ctr_ok(input logic [1:0] p,
                                  input logic [4:0] bit_idx);
    ctr_ok = p == 2'b11 ||
             (mcounteren_q[bit_idx] && (p == 2'b01 || scounteren_q[bit_idx]));
  endfunction

  // Read + legality (combinational).
  logic known;
  always_comb begin
    known     = 1'b1;
    acc_rdata = 32'b0;
    unique case (acc_addr)
      12'h100: acc_rdata = mstatus & SSTATUS_MASK;      // sstatus
      12'h104: acc_rdata = mie_reg_q & mideleg_q;       // sie
      12'h105: acc_rdata = stvec_q;
      12'h106: acc_rdata = scounteren_q;
      12'h140: acc_rdata = sscratch_q;
      12'h141: acc_rdata = sepc_q;
      12'h142: acc_rdata = scause_q;
      12'h143: acc_rdata = stval_q;
      12'h144: acc_rdata = mip & mideleg_q;             // sip
      12'h180: acc_rdata = satp_q;
      12'h300: acc_rdata = mstatus;
      12'h301: acc_rdata = 32'h4014_1100;  // misa: RV32IMSU
      12'h302: acc_rdata = medeleg_q;
      12'h303: acc_rdata = mideleg_q;
      12'h304: acc_rdata = mie_reg_q;
      12'h305: acc_rdata = mtvec_q;
      12'h306: acc_rdata = mcounteren_q;
      12'h340: acc_rdata = mscratch_q;
      12'h341: acc_rdata = mepc_q;
      12'h342: acc_rdata = mcause_q;
      12'h343: acc_rdata = mtval_q;
      12'h344: acc_rdata = mip;
      12'hF11, 12'hF12, 12'hF13, 12'hF14: acc_rdata = 32'b0;
      12'hB00: acc_rdata = cycle_q[31:0];
      12'hB80: acc_rdata = cycle_q[63:32];
      12'hB02: acc_rdata = instret_q[31:0];
      12'hB82: acc_rdata = instret_q[63:32];
      12'hC00: begin
        acc_rdata = cycle_q[31:0];
        known = ctr_ok(prv_q, 5'd0);
      end
      12'hC80: begin
        acc_rdata = cycle_q[63:32];
        known = ctr_ok(prv_q, 5'd0);
      end
      12'hC02: begin
        acc_rdata = instret_q[31:0];
        known = ctr_ok(prv_q, 5'd2);
      end
      12'hC82: begin
        acc_rdata = instret_q[63:32];
        known = ctr_ok(prv_q, 5'd2);
      end
      // Sdtrig with no triggers implemented: tselect hardwired to 0 and
      // tdata1.type == 0 ("no trigger here") is the architectural way to
      // report that, and is what riscv-tests breakpoint probes for.
      12'h7A0, 12'h7A1, 12'h7A2, 12'h7A3, 12'h7A4, 12'h7A5: acc_rdata = 32'b0;
      default: known = 1'b0;
    endcase
  end

  wire wen = acc_en && (acc_op == CSR_RW || !acc_rs1x0);
  // Access legality: unknown CSR, write to read-only space, privilege
  // below the CSR's required level, or satp touched from S under TVM.
  assign acc_illegal =
      acc_en && (!known || (wen && acc_addr[11:10] == 2'b11) ||
                 (acc_addr[9:8] > prv_q) ||
                 (acc_addr == 12'h180 && prv_q == 2'b01 && tvm_q));

  wire [31:0] wval = (acc_op == CSR_RW) ? acc_wdata
                   : (acc_op == CSR_RS) ? (acc_rdata | acc_wdata)
                                        : (acc_rdata & ~acc_wdata);

  wire do_write   = wen && acc_commit && !acc_illegal;
  wire wr_mstatus = do_write && acc_addr == 12'h300;
  wire wr_sstatus = do_write && acc_addr == 12'h100;

  // State as left by the committing instruction's own effect. When an
  // interrupt lands on the same commit, trap entry must observe these —
  // not the pre-instruction values (mirrors the ISS's "effect, then trap"
  // ordering).
  wire       eff_mie = mret_en ? mpie_q : wr_mstatus ? wval[3] : mie_q;
  wire       eff_sie = sret_en ? spie_q
                     : (wr_mstatus || wr_sstatus) ? wval[1] : sie_q;
  wire [1:0] eff_prv = mret_en ? mpp_q : sret_en ? {1'b0, spp_q} : prv_q;
  wire [31:0] eff_mideleg =
      (do_write && acc_addr == 12'h303) ? (wval & 32'h222) : mideleg_q;
  wire [31:0] eff_medeleg =
      (do_write && acc_addr == 12'h302) ? (wval & 32'hF7FF) : medeleg_q;

  // Trap routing: delegated causes raised at S/U land in S.
  wire [31:0] eff_deleg = trap_interrupt ? eff_mideleg : eff_medeleg;
  wire trap_to_s = trap_en && eff_prv != 2'b11 &&
                   eff_deleg[{1'b0, trap_cause}];
  assign trap_vector = trap_to_s ? {stvec_q[31:2], 2'b00}
                                 : {mtvec_q[31:2], 2'b00};

  // Interrupt delivery against current (pre-commit) state: M-level (not
  // delegated) interrupts are taken below M, or in M with MIE; S-level
  // (delegated) below S, or in S with SIE — never in M. Priority MEI, MSI,
  // MTI, then SEI, SSI, STI.
  wire [31:0] pend  = mie_reg_q & mip;
  wire [31:0] mpend = pend & ~mideleg_q;
  wire [31:0] spend = pend & mideleg_q;
  wire take_m = |mpend && (prv_q != 2'b11 || mie_q);
  wire take_s = |spend && (prv_q == 2'b00 || (prv_q == 2'b01 && sie_q));
  assign irq_take = take_m || take_s;
  always_comb begin
    if (take_m) begin
      if (mpend[11])      irq_cause = 4'd11;
      else if (mpend[3])  irq_cause = 4'd3;
      else if (mpend[7])  irq_cause = 4'd7;
      else if (mpend[9])  irq_cause = 4'd9;
      else if (mpend[1])  irq_cause = 4'd1;
      else                irq_cause = 4'd5;
    end else begin
      if (spend[9])       irq_cause = 4'd9;
      else if (spend[1])  irq_cause = 4'd1;
      else                irq_cause = 4'd5;
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      sie_q <= 1'b0; mie_q <= 1'b0; spie_q <= 1'b0; mpie_q <= 1'b0;
      spp_q <= 1'b0; mpp_q <= 2'b11;
      mprv_q <= 1'b0; sum_q <= 1'b0; mxr_q <= 1'b0;
      tvm_q <= 1'b0; tw_q <= 1'b0; tsr_q <= 1'b0;
      prv_q      <= 2'b11;
      mtvec_q    <= 32'b0;
      mepc_q     <= 32'b0;
      mcause_q   <= 32'b0;
      mtval_q    <= 32'b0;
      mscratch_q <= 32'b0;
      mie_reg_q  <= 32'b0;
      medeleg_q  <= 32'b0;
      mideleg_q  <= 32'b0;
      mcounteren_q <= 32'b0;
      stvec_q    <= 32'b0;
      sepc_q     <= 32'b0;
      scause_q   <= 32'b0;
      stval_q    <= 32'b0;
      sscratch_q <= 32'b0;
      scounteren_q <= 32'b0;
      satp_q     <= 32'b0;
      soft_ip_q  <= 32'b0;
      cycle_q    <= 64'b0;
      instret_q  <= 64'b0;
    end else begin
      cycle_q <= cycle_q + 64'd1;
      if (retire) instret_q <= instret_q + 64'd1;

      // The committing instruction's own effect applies first: an
      // interrupt (trap_en && trap_interrupt) still retires it, so its CSR
      // write or xret restore must land. A synchronous trap never
      // coincides with either — acc_illegal and the axcore commit chain
      // exclude that by construction.
      if (do_write) begin
        unique case (acc_addr)
          12'h100: begin  // sstatus: the S-visible mstatus subset
            sie_q <= wval[1]; spie_q <= wval[5]; spp_q <= wval[8];
            sum_q <= wval[18]; mxr_q <= wval[19];
          end
          12'h104: mie_reg_q <= (mie_reg_q & ~mideleg_q) |
                                (wval & mideleg_q);
          12'h105: stvec_q <= wval & ~32'h3;      // MODE: direct only
          12'h106: scounteren_q <= wval;
          12'h140: sscratch_q <= wval;
          12'h141: sepc_q <= wval & ~32'h3;
          12'h142: scause_q <= wval;
          12'h143: stval_q <= wval;
          12'h144: soft_ip_q <= (soft_ip_q & ~(mideleg_q & 32'h2)) |
                                (wval & mideleg_q & 32'h2);
          12'h180: satp_q <= wval;
          12'h300: begin
            sie_q <= wval[1]; mie_q <= wval[3];
            spie_q <= wval[5]; mpie_q <= wval[7];
            spp_q <= wval[8];
            if (wval[12:11] != 2'b10) mpp_q <= wval[12:11];  // WARL clamp
            mprv_q <= wval[17]; sum_q <= wval[18]; mxr_q <= wval[19];
            tvm_q <= wval[20]; tw_q <= wval[21]; tsr_q <= wval[22];
          end
          12'h301: ;                             // misa: writes ignored
          12'h302: medeleg_q <= wval & 32'hF7FF; // ecall-from-M not delegable
          12'h303: mideleg_q <= wval & 32'h222;  // S-level interrupts only
          12'h304: mie_reg_q <= wval;
          // MODE is WARL and only direct is implemented, so it reads back 0.
          // Reporting a vectored mode we do not dispatch would strand
          // software in the wrong handler.
          12'h305: mtvec_q <= wval & ~32'h3;
          12'h306: mcounteren_q <= wval;
          12'h340: mscratch_q <= wval;
          12'h341: mepc_q <= wval & ~32'h3;      // IALIGN=32
          12'h342: mcause_q <= wval;
          12'h343: mtval_q <= wval;
          12'h344: soft_ip_q <= wval & 32'h222;  // hardware bits read-only
          // Counters are M-mode writable. These are full-width assignments so
          // they override this cycle's increment above, which is exactly the
          // architectural rule that a write suppresses the increment from the
          // writing instruction itself.
          12'hB00: cycle_q   <= {cycle_q[63:32], wval};
          12'hB80: cycle_q   <= {wval, cycle_q[31:0]};
          12'hB02: instret_q <= {instret_q[63:32], wval};
          12'hB82: instret_q <= {wval, instret_q[31:0]};
          12'h7A0, 12'h7A1, 12'h7A2,             // trigger CSRs are WARL-
          12'h7A3, 12'h7A4, 12'h7A5: ;           // hardwired to 0; see above
          default: ;
        endcase
      end
      if (mret_en) begin
        mie_q  <= mpie_q;
        mpie_q <= 1'b1;
        mpp_q  <= 2'b00;
        prv_q  <= mpp_q;
        if (mpp_q != 2'b11) mprv_q <= 1'b0;
      end
      if (sret_en) begin
        sie_q  <= spie_q;
        spie_q <= 1'b1;
        spp_q  <= 1'b0;
        prv_q  <= {1'b0, spp_q};
        mprv_q <= 1'b0;  // the new privilege is never M
      end
      // Trap entry last: its assignments override any same-cycle write to
      // the same register, and it observes the eff_* values the committing
      // instruction left behind — the ISS's exact ordering.
      if (trap_en) begin
        if (trap_to_s) begin
          sepc_q   <= trap_pc;
          scause_q <= {trap_interrupt, 27'b0, trap_cause};
          stval_q  <= trap_tval;
          spie_q   <= eff_sie;
          sie_q    <= 1'b0;
          spp_q    <= eff_prv[0];
          prv_q    <= 2'b01;
        end else begin
          mepc_q   <= trap_pc;
          mcause_q <= {trap_interrupt, 27'b0, trap_cause};
          mtval_q  <= trap_tval;
          mpie_q   <= eff_mie;
          mie_q    <= 1'b0;
          mpp_q    <= eff_prv;
          prv_q    <= 2'b11;
        end
      end
    end
  end

endmodule
