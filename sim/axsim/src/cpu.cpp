#include "cpu.h"

#include <cstdio>
#include <inttypes.h>

// Immediate extractors, RISC-V unprivileged spec §2.3 (all sign-extended
// except U, which is the raw upper 20 bits).
static uint32_t imm_i(uint32_t i) { return (uint32_t)((int32_t)i >> 20); }
static uint32_t imm_s(uint32_t i) {
  return (uint32_t)((int32_t)(i & 0xFE000000) >> 20) | ((i >> 7) & 0x1F);
}
static uint32_t imm_b(uint32_t i) {
  return ((i & 0x80000000u) ? 0xfffff000u : 0u) | ((i & 0x80) << 4) |
         ((i >> 20) & 0x7E0) | ((i >> 7) & 0x1E);
}
static uint32_t imm_u(uint32_t i) { return i & 0xFFFFF000; }
static uint32_t imm_j(uint32_t i) {
  return ((i & 0x80000000u) ? 0xfff00000u : 0u) | (i & 0xFF000) |
         ((i >> 9) & 0x800) | ((i >> 20) & 0x7FE);
}

// Exception causes (privileged spec table 3.6); interrupts join with CLINT.
enum : uint32_t {
  EXC_IADDR_MISALIGNED = 0,
  EXC_IACCESS_FAULT = 1,
  EXC_ILLEGAL = 2,
  EXC_BREAKPOINT = 3,
  EXC_LADDR_MISALIGNED = 4,
  EXC_LACCESS_FAULT = 5,
  EXC_SADDR_MISALIGNED = 6,
  EXC_SACCESS_FAULT = 7,
  EXC_ECALL_U = 8,
  EXC_IPAGE_FAULT = 12,
  EXC_LPAGE_FAULT = 13,
  EXC_SPAGE_FAULT = 15,
};

// mstatus fields (RV32).
enum : uint32_t {
  MST_SIE = 1u << 1,
  MST_MIE = 1u << 3,
  MST_SPIE = 1u << 5,
  MST_MPIE = 1u << 7,
  MST_SPP = 1u << 8,
  MST_MPP = 3u << 11,
  MST_MPRV = 1u << 17,
  MST_SUM = 1u << 18,
  MST_MXR = 1u << 19,
  MST_TVM = 1u << 20,
  MST_TW = 1u << 21,
  MST_TSR = 1u << 22,
};
// Bits M-mode software may write; sstatus is the S-visible subset.
static constexpr uint32_t MSTATUS_WMASK = 0x007E19AA;
static constexpr uint32_t SSTATUS_MASK = 0x000C0122;

// Trap entry with medeleg/mideleg routing: exceptions raised at S/U whose
// delegation bit is set land in S-mode (sepc/scause/stval, SPIE<=SIE,
// SPP<=prv); everything else lands in M. Synchronous traps name the faulting
// instruction; interrupts name the retired instruction's successor.
Stop Cpu::enter_trap(uint32_t cause, uint32_t tval, uint32_t trap_pc,
                     bool is_interrupt) {
  const uint32_t deleg = is_interrupt ? csr.mideleg : csr.medeleg;
  const bool to_s = ext_su && prv <= 1 && ((deleg >> cause) & 1);
  if (trace)
    fprintf(stderr, "          %s cause=%u tval=%08x pc=%08x -> %c\n",
            is_interrupt ? "INTERRUPT" : "TRAP", cause, tval, trap_pc,
            to_s ? 'S' : 'M');
  uint32_t tvec;
  if (to_s) {
    csr.sepc = trap_pc;
    csr.scause = (is_interrupt ? 0x80000000u : 0) | cause;
    csr.stval = tval;
    const uint32_t sie = (csr.mstatus & MST_SIE) ? 1 : 0;
    csr.mstatus = (csr.mstatus & ~(MST_SIE | MST_SPIE | MST_SPP)) |
                  (sie << 5) | ((prv & 1) << 8);
    prv = 1;
    tvec = csr.stvec;
  } else {
    csr.mepc = trap_pc;
    csr.mcause = (is_interrupt ? 0x80000000u : 0) | cause;
    csr.mtval = tval;
    const uint32_t mie = (csr.mstatus & MST_MIE) ? 1 : 0;
    csr.mstatus = (csr.mstatus & ~(MST_MIE | MST_MPIE | MST_MPP)) |
                  (mie << 7) | (prv << 11);
    prv = 3;
    tvec = csr.mtvec;
  }
  pc = tvec & ~3u;  // MODE is WARL-pinned to direct on write, so this is it
  if (tvec == 0) {
    fprintf(stderr,
            "[axsim] trap (cause=%u tval=%08x) at pc=%08x with %ctvec unset — "
            "halting (retired=%" PRIu64 ")\n",
            cause, tval, trap_pc, to_s ? 's' : 'm', ninsn);
    return Stop::Fault;
  }
  return Stop::None;
}

// Take a synchronous trap at the current (faulting) pc. The caller returns
// immediately, so the instruction writes no register and does not retire;
// the next step() fetches from the handler.
Stop Cpu::trap(uint32_t cause, uint32_t tval) {
  last.trap = true;
  last.cause = cause;
  last.tval = tval;
  return enter_trap(cause, tval, pc, false);
}

Stop Cpu::interrupt(uint32_t cause, uint32_t resume_pc) {
  return enter_trap(cause, 0, resume_pc, true);
}

// mcycle and minstret both count retired instructions in an ISS, but each
// keeps its own write offset so that writing one does not disturb the other.
uint64_t Cpu::counter(uint32_t addr) const {
  return ninsn + ((addr & 2) ? instret_off : cycle_off);
}

// A counter write suppresses the increment contributed by the writing
// instruction itself, so the next instruction reads back exactly what was
// written (riscv-tests instret_overflow checks this, including the RV32
// "write to minstreth is a write to minstret" case). ninsn has not yet been
// incremented for the in-flight instruction here, hence the +1.
void Cpu::write_counter(uint32_t addr, bool high, uint32_t val) {
  const uint64_t cur = counter(addr);
  const uint64_t nv = high ? ((uint64_t)val << 32) | (uint32_t)cur
                           : (cur & 0xFFFFFFFF00000000ull) | val;
  ((addr & 2) ? instret_off : cycle_off) = nv - (ninsn + 1);
}

// Counter visibility from S/U: gated by mcounteren (and scounteren for U).
bool Cpu::ctr_ok(unsigned bit) const {
  if (prv == 3) return true;
  if (!((csr.mcounteren >> bit) & 1)) return false;
  if (prv == 0 && !((csr.scounteren >> bit) & 1)) return false;
  return true;
}

// Sv32 two-level walk with hardware A/D update (the QEMU/Spike default; the
// rv32si dirty test accepts either policy). Inactive when translation is
// architecturally off: M effective privilege or satp.MODE=0.
bool Cpu::translate(uint32_t va, int acc, uint32_t& pa, uint32_t& cause) {
  uint32_t eprv = prv;
  if (acc != 0 && (csr.mstatus & MST_MPRV)) eprv = (csr.mstatus >> 11) & 3;
  if (!ext_su || eprv == 3 || !(csr.satp & 0x80000000u)) {
    pa = va;
    return true;
  }
  const bool sum = csr.mstatus & MST_SUM;
  const bool mxr = csr.mstatus & MST_MXR;
  const uint32_t pf = acc == 0   ? EXC_IPAGE_FAULT
                      : acc == 1 ? EXC_LPAGE_FAULT
                                 : EXC_SPAGE_FAULT;
  const uint32_t af = acc == 0   ? EXC_IACCESS_FAULT
                      : acc == 1 ? EXC_LACCESS_FAULT
                                 : EXC_SACCESS_FAULT;
  uint32_t base = (csr.satp & 0x003FFFFF) << 12;
  for (int level = 1; level >= 0; --level) {
    const uint32_t vpn = (va >> (12 + 10 * level)) & 0x3FF;
    const uint32_t pte_addr = base + vpn * 4;
    uint32_t pte;
    if (!bus.read(pte_addr, 4, pte)) { cause = af; return false; }
    if (!(pte & 1) || (!(pte & 2) && (pte & 4))) {  // !V, or W without R
      cause = pf;
      return false;
    }
    if (!(pte & 0xA)) {                 // pointer to the next level
      if (pte & 0xD0) { cause = pf; return false; }  // D/A/U reserved here
      if (level == 0) { cause = pf; return false; }  // no leaf found
      base = (pte >> 10) << 12;
      continue;
    }
    // Leaf: permission, privilege, and alignment checks.
    const bool r = pte & 2, w = pte & 4, xb = pte & 8, u = pte & 0x10;
    const bool perm = acc == 0   ? xb
                      : acc == 1 ? (r || (mxr && xb))
                                 : w;
    if (!perm) { cause = pf; return false; }
    if (eprv == 0 && !u) { cause = pf; return false; }
    if (eprv == 1 && u && (acc == 0 || !sum)) { cause = pf; return false; }
    if (level == 1 && (pte & 0x000FFC00)) {  // superpage with ppn[0] != 0
      cause = pf;
      return false;
    }
    const uint32_t npte = pte | 0x40 | (acc == 2 ? 0x80 : 0);  // A, D
    if (npte != pte && !bus.write(pte_addr, 4, npte)) {
      cause = af;
      return false;
    }
    pa = level == 1 ? (((pte >> 20) << 22) | (va & 0x003FFFFF))
                    : (((pte >> 10) << 12) | (va & 0xFFF));
    return true;
  }
  cause = pf;  // unreachable
  return false;
}

bool Cpu::csr_read(uint32_t addr, uint32_t& val) {
  switch (addr) {
    case 0x300: val = csr.mstatus; return true;
    case 0x301:  // misa: RV32IM (+SU once the privileged extension is on)
      val = ext_su ? 0x40141100 : 0x40001100;
      return true;
    case 0x304: val = csr.mie; return true;
    case 0x305: val = csr.mtvec; return true;
    case 0x340: val = csr.mscratch; return true;
    case 0x341: val = csr.mepc; return true;
    case 0x342: val = csr.mcause; return true;
    case 0x343: val = csr.mtval; return true;
    case 0x344: val = csr.mip; return true;
    case 0xF11: case 0xF12: case 0xF13: case 0xF14:  // mvendorid..mhartid
      val = 0; return true;
    case 0xB00: case 0xB02:  // mcycle/minstret
      val = (uint32_t)counter(addr); return true;
    case 0xB80: case 0xB82:
      val = (uint32_t)(counter(addr) >> 32); return true;
    case 0xC00: case 0xC02:  // cycle/instret user aliases
      if (!ctr_ok(addr == 0xC00 ? 0 : 2)) return false;
      val = (uint32_t)counter(addr); return true;
    case 0xC80: case 0xC82:
      if (!ctr_ok(addr == 0xC80 ? 0 : 2)) return false;
      val = (uint32_t)(counter(addr) >> 32); return true;
    case 0x7A0: case 0x7A1: case 0x7A2: case 0x7A3: case 0x7A4: case 0x7A5:
      // Sdtrig with no triggers implemented. The architectural way to say so
      // is tselect hardwired to 0 with tdata1.type == 0 ("no trigger here"),
      // which is what riscv-tests breakpoint probes before skipping itself.
      val = 0; return true;
    default:
      break;
  }
  if (!ext_su) return false;
  switch (addr) {
    case 0x100: val = csr.mstatus & SSTATUS_MASK; return true;  // sstatus
    case 0x104: val = csr.mie & csr.mideleg; return true;       // sie
    case 0x105: val = csr.stvec; return true;
    case 0x106: val = csr.scounteren; return true;
    case 0x140: val = csr.sscratch; return true;
    case 0x141: val = csr.sepc; return true;
    case 0x142: val = csr.scause; return true;
    case 0x143: val = csr.stval; return true;
    case 0x144: val = csr.mip & csr.mideleg; return true;       // sip
    case 0x180: val = csr.satp; return true;
    case 0x302: val = csr.medeleg; return true;
    case 0x303: val = csr.mideleg; return true;
    case 0x306: val = csr.mcounteren; return true;
    default:
      return false;
  }
}

bool Cpu::csr_write(uint32_t addr, uint32_t val) {
  if (((addr >> 10) & 3) == 3) return false;  // read-only CSR space
  switch (addr) {
    case 0x300:
      if (ext_su) {
        uint32_t nv = (csr.mstatus & ~MSTATUS_WMASK) | (val & MSTATUS_WMASK);
        if (((nv >> 11) & 3) == 2) nv = (nv & ~MST_MPP) | (csr.mstatus & MST_MPP);
        csr.mstatus = nv;
      } else {
        // Legacy M-only WARL: MPP pinned to 11; writable bits MIE and MPIE.
        csr.mstatus = (val & 0x88u) | 0x1800;
      }
      return true;
    case 0x301: return true;                     // misa: writes ignored
    case 0x304: csr.mie = val; return true;
    // mtvec/stvec MODE is WARL and only direct (0) is implemented, so the
    // mode field reads back 0. Reporting a vectored mode we do not dispatch
    // would strand software in the wrong handler (riscv-tests illegal probes
    // exactly this before deciding to test vectored entry).
    case 0x305: csr.mtvec = val & ~3u; return true;
    case 0x340: csr.mscratch = val; return true;
    case 0x341: csr.mepc = val & ~3u; return true;   // IALIGN=32
    case 0x342: csr.mcause = val; return true;
    case 0x343: csr.mtval = val; return true;
    case 0x344:  // mip: hardware bits read-only; S-level bits software-set
      if (ext_su) soft_ip = val & 0x222;  // legacy: no-op, mirroring RTL
      return true;
    case 0xB00: case 0xB02:  // mcycle/minstret
      write_counter(addr, false, val); return true;
    case 0xB80: case 0xB82:  // mcycleh/minstreth
      write_counter(addr, true, val); return true;
    case 0x7A0: case 0x7A1: case 0x7A2: case 0x7A3: case 0x7A4: case 0x7A5:
      return true;  // trigger CSRs are WARL-hardwired to 0; see csr_read
    default:
      break;
  }
  if (!ext_su) return false;
  switch (addr) {
    case 0x100:
      csr.mstatus =
          (csr.mstatus & ~SSTATUS_MASK) | (val & SSTATUS_MASK);
      return true;
    case 0x104:
      csr.mie = (csr.mie & ~csr.mideleg) | (val & csr.mideleg);
      return true;
    case 0x105: csr.stvec = val & ~3u; return true;  // direct mode only
    case 0x106: csr.scounteren = val; return true;
    case 0x140: csr.sscratch = val; return true;
    case 0x141: csr.sepc = val & ~3u; return true;
    case 0x142: csr.scause = val; return true;
    case 0x143: csr.stval = val; return true;
    case 0x144:  // sip: only SSIP is software-writable, and only if delegated
      soft_ip = (soft_ip & ~(csr.mideleg & 2u)) | (val & csr.mideleg & 2u);
      return true;
    case 0x180: csr.satp = val; return true;
    case 0x302: csr.medeleg = val & 0xF7FF; return true;  // no M-ecall deleg
    case 0x303: csr.mideleg = val & 0x222; return true;   // S-level irqs only
    case 0x306: csr.mcounteren = val; return true;
    default:
      return false;
  }
}

Stop Cpu::step() {
  last = {};
  last.valid = true;
  last.pc = pc;
  // Match aXcore's commit-point sampling: this instruction sees the
  // privilege, interrupt enables, and pending lines from before its own
  // architectural side effects. A CSR write that enables MIE therefore takes
  // effect for the following instruction, not retroactively for itself.
  csr.mip = bus.mip() | (soft_ip & 0x222);
  const uint32_t irq_prv = prv;
  const uint32_t irq_mstatus = csr.mstatus;
  const uint32_t irq_mideleg = csr.mideleg;
  const uint32_t irq_pend = csr.mie & csr.mip;
  if (pc & 3) return trap(EXC_IADDR_MISALIGNED, pc);
  uint32_t insn, fetch_pa, fetch_cause;
  if (!translate(pc, 0, fetch_pa, fetch_cause))
    return trap(fetch_cause, pc);
  if (!bus.read(fetch_pa, 4, insn)) return trap(EXC_IACCESS_FAULT, pc);
  last.insn = insn;

  const uint32_t op = insn & 0x7F;
  const uint32_t rd = (insn >> 7) & 0x1F;
  const uint32_t f3 = (insn >> 12) & 0x7;
  const uint32_t rs1 = (insn >> 15) & 0x1F;
  const uint32_t rs2 = (insn >> 20) & 0x1F;
  const uint32_t f7 = insn >> 25;
  const uint32_t a = x[rs1];
  const uint32_t b = x[rs2];

  uint32_t next_pc = pc + 4;
  bool wr = false;
  uint32_t wval = 0;

  switch (op) {
    case 0x37:  // LUI
      wr = true; wval = imm_u(insn); break;
    case 0x17:  // AUIPC
      wr = true; wval = pc + imm_u(insn); break;
    case 0x6F:  // JAL
      wr = true; wval = pc + 4; next_pc = pc + imm_j(insn);
      // A misaligned target traps on the jump itself (IALIGN=32, no C):
      // rd is not written and epc names the jump, not the target
      // (riscv-tests ma_fetch checks exactly this).
      if (next_pc & 3) return trap(EXC_IADDR_MISALIGNED, next_pc);
      break;
    case 0x67:  // JALR
      if (f3 != 0) return trap(EXC_ILLEGAL, insn);
      wr = true; wval = pc + 4;
      next_pc = (a + imm_i(insn)) & ~1u;
      if (next_pc & 3) return trap(EXC_IADDR_MISALIGNED, next_pc);
      break;
    case 0x63: {  // branches
      bool taken;
      switch (f3) {
        case 0: taken = a == b; break;                        // BEQ
        case 1: taken = a != b; break;                        // BNE
        case 4: taken = (int32_t)a < (int32_t)b; break;       // BLT
        case 5: taken = (int32_t)a >= (int32_t)b; break;      // BGE
        case 6: taken = a < b; break;                         // BLTU
        case 7: taken = a >= b; break;                        // BGEU
        default: return trap(EXC_ILLEGAL, insn);
      }
      if (taken) {
        next_pc = pc + imm_b(insn);
        if (next_pc & 3) return trap(EXC_IADDR_MISALIGNED, next_pc);
      }
      break;
    }
    case 0x03: {  // loads
      unsigned size;
      switch (f3) {
        case 0: case 4: size = 1; break;  // LB, LBU
        case 1: case 5: size = 2; break;  // LH, LHU
        case 2: size = 4; break;          // LW
        default: return trap(EXC_ILLEGAL, insn);
      }
      const uint32_t addr = a + imm_i(insn);
      if (addr % size) return trap(EXC_LADDR_MISALIGNED, addr);
      uint32_t pa, cz, v;
      if (!translate(addr, 1, pa, cz)) return trap(cz, addr);
      if (!bus.read(pa, size, v)) return trap(EXC_LACCESS_FAULT, addr);
      if (f3 == 0) v = (uint32_t)(int32_t)(int8_t)v;
      if (f3 == 1) v = (uint32_t)(int32_t)(int16_t)v;
      wr = true; wval = v;
      break;
    }
    case 0x23: {  // stores
      unsigned size;
      switch (f3) {
        case 0: size = 1; break;  // SB
        case 1: size = 2; break;  // SH
        case 2: size = 4; break;  // SW
        default: return trap(EXC_ILLEGAL, insn);
      }
      const uint32_t addr = a + imm_s(insn);
      if (addr % size) return trap(EXC_SADDR_MISALIGNED, addr);
      uint32_t pa, cz;
      if (!translate(addr, 2, pa, cz)) return trap(cz, addr);
      if (!bus.write(pa, size, b)) return trap(EXC_SACCESS_FAULT, addr);
      break;
    }
    case 0x13: {  // OP-IMM
      const uint32_t imm = imm_i(insn);
      switch (f3) {
        case 0: wval = a + imm; break;                         // ADDI
        case 2: wval = (int32_t)a < (int32_t)imm; break;       // SLTI
        case 3: wval = a < imm; break;                         // SLTIU
        case 4: wval = a ^ imm; break;                         // XORI
        case 6: wval = a | imm; break;                         // ORI
        case 7: wval = a & imm; break;                         // ANDI
        case 1:                                                // SLLI
          if (f7 != 0) return trap(EXC_ILLEGAL, insn);
          wval = a << (imm & 31);
          break;
        case 5:                                                // SRLI/SRAI
          if (f7 == 0x00) wval = a >> (imm & 31);
          else if (f7 == 0x20) wval = (uint32_t)((int32_t)a >> (imm & 31));
          else return trap(EXC_ILLEGAL, insn);
          break;
      }
      wr = true;
      break;
    }
    case 0x33: {  // OP + RV32M
      if (f7 == 0x00) {
        switch (f3) {
          case 0: wval = a + b; break;                         // ADD
          case 1: wval = a << (b & 31); break;                 // SLL
          case 2: wval = (int32_t)a < (int32_t)b; break;       // SLT
          case 3: wval = a < b; break;                         // SLTU
          case 4: wval = a ^ b; break;                         // XOR
          case 5: wval = a >> (b & 31); break;                 // SRL
          case 6: wval = a | b; break;                         // OR
          case 7: wval = a & b; break;                         // AND
        }
      } else if (f7 == 0x20 && f3 == 0) {
        wval = a - b;                                          // SUB
      } else if (f7 == 0x20 && f3 == 5) {
        wval = (uint32_t)((int32_t)a >> (b & 31));             // SRA
      } else if (f7 == 0x01) {
        const int64_t sa = (int32_t)a, sb = (int32_t)b;
        switch (f3) {
          case 0: wval = (uint32_t)((uint64_t)a * b); break;   // MUL
          case 1: wval = (uint32_t)((uint64_t)(sa * sb) >> 32); break;
          case 2: wval = (uint32_t)((uint64_t)(sa * (int64_t)(uint64_t)b) >> 32); break;
          case 3: wval = (uint32_t)(((uint64_t)a * b) >> 32); break;
          case 4:  // DIV
            wval = b == 0 ? 0xFFFFFFFFu : (uint32_t)(sa / sb); break;
          case 5:  // DIVU
            wval = b == 0 ? 0xFFFFFFFFu : a / b; break;
          case 6:  // REM
            wval = b == 0 ? a : (uint32_t)(sa % sb); break;
          case 7:  // REMU
            wval = b == 0 ? a : a % b; break;
        }
      } else {
        return trap(EXC_ILLEGAL, insn);
      }
      wr = true;
      break;
    }
    case 0x0F:  // MISC-MEM: FENCE/FENCE.I are no-ops — single hart, no
                // caches (DESIGN §4.2); reserved funct3 is illegal (must
                // match the RTL decoder exactly or cosim diverges).
      if (f3 > 1) return trap(EXC_ILLEGAL, insn);
      break;
    case 0x73: {  // SYSTEM
      if (insn == 0x00000073) return trap(EXC_ECALL_U + prv, 0);  // ECALL
      if (insn == 0x00100073) return trap(EXC_BREAKPOINT, pc);    // EBREAK
      if (insn == 0x30200073) {                                   // MRET
        if (prv != 3) return trap(EXC_ILLEGAL, insn);
        const uint32_t mpie = (csr.mstatus & MST_MPIE) ? 1 : 0;
        const uint32_t mpp = (csr.mstatus >> 11) & 3;
        csr.mstatus = (csr.mstatus & ~(MST_MIE | MST_MPIE | MST_MPP)) |
                      (mpie << 3) | MST_MPIE;
        if (!ext_su) csr.mstatus |= 0x1800;  // legacy: MPP pinned to M
        prv = ext_su ? mpp : 3;
        if (prv != 3) csr.mstatus &= ~MST_MPRV;
        next_pc = csr.mepc;
        break;
      }
      if (insn == 0x10200073) {                                   // SRET
        if (!ext_su || prv == 0 ||
            (prv == 1 && (csr.mstatus & MST_TSR)))
          return trap(EXC_ILLEGAL, insn);
        const uint32_t spie = (csr.mstatus & MST_SPIE) ? 1 : 0;
        const uint32_t spp = (csr.mstatus & MST_SPP) ? 1 : 0;
        csr.mstatus = (csr.mstatus & ~(MST_SIE | MST_SPIE | MST_SPP)) |
                      (spie << 1) | MST_SPIE;
        prv = spp;
        csr.mstatus &= ~MST_MPRV;  // new privilege is never M
        next_pc = csr.sepc;
        break;
      }
      if (insn == 0x10500073) {                                   // WFI
        if (ext_su && prv < 3 && (csr.mstatus & MST_TW))
          return trap(EXC_ILLEGAL, insn);
        break;  // executes as a nop
      }
      if ((insn & 0xFE007FFF) == 0x12000073) {                    // SFENCE.VMA
        if (!ext_su || prv == 0 ||
            (prv == 1 && (csr.mstatus & MST_TVM)))
          return trap(EXC_ILLEGAL, insn);
        break;  // no TLB to flush in the ISS
      }
      if (f3 == 0 || f3 == 4) return trap(EXC_ILLEGAL, insn);
      // Zicsr: f3 = 1/2/3 register forms, 5/6/7 immediate forms (uimm = rs1
      // field, zero-extended).
      const uint32_t csr_addr = insn >> 20;
      if (((csr_addr >> 8) & 3) > prv) return trap(EXC_ILLEGAL, insn);
      if (ext_su && csr_addr == 0x180 && prv == 1 &&
          (csr.mstatus & MST_TVM))
        return trap(EXC_ILLEGAL, insn);
      const uint32_t src = (f3 & 4) ? rs1 : a;
      const uint32_t kind = f3 & 3;  // 1=RW 2=RS 3=RC
      uint32_t old;
      if (!csr_read(csr_addr, old)) return trap(EXC_ILLEGAL, insn);
      // RS/RC with rs1=x0 (or uimm=0) are pure reads: no write occurs, so
      // they don't trap on a read-only CSR.
      const bool write = (kind == 1) || rs1 != 0;
      const uint32_t nv =
          kind == 1 ? src : kind == 2 ? (old | src) : (old & ~src);
      if (write && !csr_write(csr_addr, nv)) return trap(EXC_ILLEGAL, insn);
      wr = true;
      wval = old;
      break;
    }
    default:
      return trap(EXC_ILLEGAL, insn);
  }

  if (wr && rd != 0) x[rd] = wval;
  last.rd_we = wr && rd != 0;
  last.rd = rd;
  last.rd_val = wval;

  if (trace) {
    fprintf(stderr, "%8" PRIu64 "  %08x: %08x", ninsn, pc, insn);
    if (wr && rd != 0) fprintf(stderr, "  x%-2u <= %08x", rd, x[rd]);
    fputc('\n', stderr);
  }

  last.retired = true;
  ninsn++;
  pc = next_pc;
  bus.tick();
  csr.mip = bus.mip() | (soft_ip & 0x222);

  // Interrupt delivery against the pre-instruction sample. M-level (not
  // delegated) interrupts are taken below M, or in M with MIE; S-level
  // (delegated) below S, or in S with SIE — and never in M. Priority order
  // MEI, MSI, MTI then SEI, SSI, STI.
  const uint32_t mpend = irq_pend & ~irq_mideleg;
  const uint32_t spend = irq_pend & irq_mideleg;
  static const uint32_t m_order[] = {11, 3, 7, 9, 1, 5};
  static const uint32_t s_order[] = {9, 1, 5};
  if (mpend && (irq_prv < 3 || (irq_mstatus & MST_MIE))) {
    for (uint32_t c : m_order)
      if (mpend & (1u << c)) return interrupt(c, next_pc);
  } else if (ext_su && spend &&
             (irq_prv == 0 || (irq_prv == 1 && (irq_mstatus & MST_SIE)))) {
    for (uint32_t c : s_order)
      if (spend & (1u << c)) return interrupt(c, next_pc);
  }
  return Stop::None;
}
