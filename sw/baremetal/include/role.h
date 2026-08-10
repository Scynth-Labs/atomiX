#pragma once

#include "platform.h"

/* Shell + role contract (DESIGN.md §3.3): one fixed 64 KiB aXbus window.
 * Software discovers the role by reading ROLE_ID; zero means no role is
 * present.  Offsets past AX_ROLE_COUNT are defined by the selected role. */
#define AX_ROLE_BASE 0x40000000u

#define AX_ROLE_ID       (AX_ROLE_BASE + 0x0000u)
#define AX_ROLE_VERSION  (AX_ROLE_BASE + 0x0004u)
#define AX_ROLE_DOORBELL (AX_ROLE_BASE + 0x0008u)
#define AX_ROLE_STATUS   (AX_ROLE_BASE + 0x000cu)

#define AX_ROLE_STATUS_BUSY 0x1u
#define AX_ROLE_STATUS_DONE 0x2u

/* role.loopback programming model. */
#define AX_ROLE_LOOP_ID    0x4c4f4f50u /* "LOOP" */
#define AX_ROLE_LOOP_SRC   (AX_ROLE_BASE + 0x0010u)
#define AX_ROLE_LOOP_DST   (AX_ROLE_BASE + 0x0014u)
#define AX_ROLE_LOOP_LEN   (AX_ROLE_BASE + 0x0018u)
#define AX_ROLE_LOOP_COUNT (AX_ROLE_BASE + 0x001cu)
#define AX_ROLE_LOOP_BUF   (AX_ROLE_BASE + 0x1000u)

/* role.tpu-lite programming model: C = acc_base + A x W, int8 operands,
 * int32 results.  W is the stationary 8x8 tile; A is M x 8 (M <= 256);
 * rows pack one int8 per byte, little-endian, two words per row. */
#define AX_ROLE_TPU_ID    0x5450554cu /* "TPUL" */
#define AX_ROLE_TPU_CTRL  (AX_ROLE_BASE + 0x0010u)
#define AX_ROLE_TPU_M     (AX_ROLE_BASE + 0x0014u)
#define AX_ROLE_TPU_COUNT (AX_ROLE_BASE + 0x0018u)
#define AX_ROLE_TPU_W     (AX_ROLE_BASE + 0x0100u)
#define AX_ROLE_TPU_A     (AX_ROLE_BASE + 0x1000u)
#define AX_ROLE_TPU_C     (AX_ROLE_BASE + 0x2000u)

#define AX_ROLE_TPU_CTRL_RELU 0x1u
#define AX_ROLE_TPU_CTRL_ACC  0x2u

/* role.gpu-compute programming model: an 8-lane SIMT vector engine.  Upload a
 * straight-line kernel to PROG and input data to DATA, set NTHREADS/NINSN, and
 * ring the doorbell; lane L of wave w runs thread tid = 8*w + L, with tid >=
 * NTHREADS predicated off (its stores drop, its loads read zero).  Registers
 * are undefined at kernel entry — a kernel must write a register before it
 * reads it.  DATA is a flat word-addressed global buffer the kernel indexes. */
#define AX_ROLE_GPU_ID       0x47505543u /* "GPUC" */
#define AX_ROLE_GPU_NTHREADS (AX_ROLE_BASE + 0x0010u)
#define AX_ROLE_GPU_NINSN    (AX_ROLE_BASE + 0x0014u)
#define AX_ROLE_GPU_COUNT    (AX_ROLE_BASE + 0x0018u)
#define AX_ROLE_GPU_PROG     (AX_ROLE_BASE + 0x0100u)
#define AX_ROLE_GPU_DATA     (AX_ROLE_BASE + 0x1000u)

/* Instruction opcodes (see components/role/gpu-compute/axrole.sv). */
#define AX_GPU_HALT 0u
#define AX_GPU_TID  1u
#define AX_GPU_LI   2u
#define AX_GPU_MOV  3u
#define AX_GPU_LDX  4u
#define AX_GPU_STX  5u
#define AX_GPU_ADD  6u
#define AX_GPU_SUB  7u
#define AX_GPU_MUL  8u
#define AX_GPU_AND  9u
#define AX_GPU_OR   10u
#define AX_GPU_XOR  11u
#define AX_GPU_SLL  12u
#define AX_GPU_SRL  13u
#define AX_GPU_SRA  14u
#define AX_GPU_MIN  15u
#define AX_GPU_MAX  16u
#define AX_GPU_ADDI 17u
#define AX_GPU_MULI 18u

/* Encode one 32-bit kernel instruction: op[31:26] rd[25:23] ra[22:20]
 * rb[19:17] imm[16:0]. */
static inline uint32_t gpu_insn(uint32_t op, uint32_t rd, uint32_t ra,
                                uint32_t rb, int32_t imm) {
  return (op << 26) | (rd << 23) | (ra << 20) | (rb << 17) |
         ((uint32_t)imm & 0x1ffffu);
}

/* role.gpu1 programming model: the scalable SIMT engine.
 *
 * Same doorbell/descriptor shape as role.gpu-compute, with two differences that
 * matter to software:
 *
 *   - CAPS publishes the geometry (lane count, bank count, optional ISA
 *     groups).  A driver reads it instead of being compiled against a lane
 *     count, so one binary runs on every tier.
 *   - Registers are zeroed at the start of each wave, and a lane that is not
 *     executing -- masked off by the thread tail or by divergence -- writes no
 *     register and performs no store.
 *
 * Opcodes 0..18 are encoding-compatible with role.gpu-compute, so a
 * straight-line kernel written for that engine assembles unchanged. */
#define AX_ROLE_GPU1_ID       0x47505531u /* "GPU1" */
#define AX_ROLE_GPU1_NTHREADS (AX_ROLE_BASE + 0x0010u)
#define AX_ROLE_GPU1_NINSN    (AX_ROLE_BASE + 0x0014u)
#define AX_ROLE_GPU1_COUNT    (AX_ROLE_BASE + 0x0018u)
#define AX_ROLE_GPU1_CAPS     (AX_ROLE_BASE + 0x001cu)
#define AX_ROLE_GPU1_PROG     (AX_ROLE_BASE + 0x0100u)
#define AX_ROLE_GPU1_DATA     (AX_ROLE_BASE + 0x1000u)

#define AX_GPU1_CAPS_LANES(c) (((c) >> 24) & 0xffu)
#define AX_GPU1_CAPS_BANKS(c) (((c) >> 16) & 0xffu)
#define AX_GPU1_CAPS_DIV      0x1u
#define AX_GPU1_CAPS_SHFL     0x2u

/* Instructions added over the gpu-compute set. */
#define AX_GPU_SEQ   19u
#define AX_GPU_SNE   20u
#define AX_GPU_SLT   21u
#define AX_GPU_SLTU  22u
#define AX_GPU_SGE   23u
#define AX_GPU_IF    24u   /* push mask; keep lanes whose ra != 0 */
#define AX_GPU_ELSE  25u   /* complement within the enclosing mask */
#define AX_GPU_ENDIF 26u   /* pop mask */
#define AX_GPU_BRA   27u   /* pc += imm, unconditional */
#define AX_GPU_BRANY 28u   /* pc += imm if any executing lane has ra != 0 */
#define AX_GPU_DIV   29u
#define AX_GPU_REM   30u
#define AX_GPU_DIVU  31u
#define AX_GPU_REMU  32u
#define AX_GPU_SHFL  33u   /* rd = lane (rb mod NLANES)'s ra */
#define AX_GPU_LDXI  34u   /* rd = mem[ra + imm] */
#define AX_GPU_STXI  35u   /* mem[ra + imm] = rb */

/* mmio_read32 lives in platform.h with the rest of the accessors. */

static inline uint32_t role_id(void) { return mmio_read32(AX_ROLE_ID); }

static inline void role_ring_doorbell(void) {
  mmio_write32(AX_ROLE_DOORBELL, 1u);
}

static inline void role_wait_done(void) {
  while (!(mmio_read32(AX_ROLE_STATUS) & AX_ROLE_STATUS_DONE)) {}
}

/* role.morph programming model: a coarse-grained reconfigurable PE array.
 * The personality lives entirely in a 13-word genome at CFG; the fabric is
 * identical for every mode.  See components/role/morph/morph_fabric.sv. */
#define AX_ROLE_MORPH_ID    0x4d525048u /* "MRPH" */
#define AX_ROLE_MORPH_NITEMS     (AX_ROLE_BASE + 0x0010u)
#define AX_ROLE_MORPH_NCONFIG    (AX_ROLE_BASE + 0x0014u)
#define AX_ROLE_MORPH_COUNT      (AX_ROLE_BASE + 0x0018u)
#define AX_ROLE_MORPH_CAPS       (AX_ROLE_BASE + 0x001cu)
#define AX_ROLE_MORPH_GENERATION (AX_ROLE_BASE + 0x0020u)
#define AX_ROLE_MORPH_REJECTS    (AX_ROLE_BASE + 0x0024u)
#define AX_ROLE_MORPH_CFG        (AX_ROLE_BASE + 0x0100u)
#define AX_ROLE_MORPH_DATA       (AX_ROLE_BASE + 0x1000u)

#define AX_ROLE_MORPH_CFG_WORDS 13u
#define AX_ROLE_STATUS_REJECTED 0x4u

#define AX_MORPH_MODE_SCALAR   0u
#define AX_MORPH_MODE_SIMT     1u
#define AX_MORPH_MODE_SYSTOLIC 2u

/* PE source muxes and the accumulator rule. */
#define AX_MORPH_SRC_A     0u
#define AX_MORPH_SRC_B     1u
#define AX_MORPH_SRC_ACC   2u
#define AX_MORPH_SRC_IMM0  3u
#define AX_MORPH_SRC_IMM1  4u
#define AX_MORPH_SRC_ZERO  5u
#define AX_MORPH_ACC_LOAD  1u

/* Shell-owned role-window fence and Live FPGA monitor (axroleiso).  These live
 * in *shell* space, not the role window: inside the window they would be
 * unreachable at exactly the moment they are needed. */
#define AX_SHELL_BASE       0x10020000u
#define AX_SHELL_ID         (AX_SHELL_BASE + 0x0000u)
#define AX_SHELL_ISO_CTRL   (AX_SHELL_BASE + 0x0004u)
#define AX_SHELL_ISO_STATUS (AX_SHELL_BASE + 0x0008u)
#define AX_SHELL_ID_MAGIC   0x61585348u /* "aXSH" */
#define AX_ISO_ISOLATE      0x1u
#define AX_ISO_ROLE_RESET   0x2u
#define AX_ISO_STATUS_ISOLATED 0x1u

#define AX_LIVE_ID          (AX_SHELL_BASE + 0x0100u)
#define AX_LIVE_VERSION     (AX_SHELL_BASE + 0x0104u)
#define AX_LIVE_COMMAND     (AX_SHELL_BASE + 0x0108u)
#define AX_LIVE_SEQUENCE    (AX_SHELL_BASE + 0x010cu)
#define AX_LIVE_CYCLES_LO   (AX_SHELL_BASE + 0x0110u)
#define AX_LIVE_WORK_LO     (AX_SHELL_BASE + 0x0118u)
#define AX_LIVE_STALL_LO    (AX_SHELL_BASE + 0x0120u)
#define AX_LIVE_REJECT_LO   (AX_SHELL_BASE + 0x0128u)
#define AX_LIVE_WATCH_LO    (AX_SHELL_BASE + 0x0130u)
#define AX_LIVE_GEN_LO      (AX_SHELL_BASE + 0x0138u)
#define AX_LIVE_ID_MAGIC    0x61584c56u /* "aXLV" */
#define AX_LIVE_CMD_SNAPSHOT 1u
#define AX_LIVE_CMD_ACTIVATE 2u
