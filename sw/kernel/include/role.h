#pragma once

#include <stdint.h>

#include "platform.h"

/* aXos in-kernel role driver — the shell + role contract (DESIGN.md §3.3) as
 * the management kernel sees it.  The fixed 64 KiB physical role window is
 * device-mapped at a kernel-only virtual alias by vm_bootstrap_map. This is
 * the first piece of the shell control plane: aXos, not a bare-metal program,
 * discovering and driving the accelerator. The host-link and userspace
 * services call this same driver on behalf of checked requests. */
#define AX_ROLE_ID       (AX_ROLE_KERNEL_BASE + 0x0000u)
#define AX_ROLE_VERSION  (AX_ROLE_KERNEL_BASE + 0x0004u)
#define AX_ROLE_DOORBELL (AX_ROLE_KERNEL_BASE + 0x0008u)
#define AX_ROLE_STATUS   (AX_ROLE_KERNEL_BASE + 0x000cu)

#define AX_ROLE_STATUS_BUSY 0x1u
#define AX_ROLE_STATUS_DONE 0x2u

/* Known role identities (ROLE_ID reads zero when no role is present). */
#define AX_ROLE_ID_LOOPBACK 0x4c4f4f50u /* "LOOP" */
#define AX_ROLE_ID_TPU      0x5450554cu /* "TPUL" */
#define AX_ROLE_ID_GPU      0x47505543u /* "GPUC" */

/* Stable userspace/host job opcodes.  Their byte payloads are documented in
 * docs/abi.md and docs/host-protocol.md; both entry paths reach role_execute,
 * so validation and MMIO marshaling cannot drift apart. */
#define AX_ROLE_OP_LOOPBACK 0x10u
#define AX_ROLE_OP_TPU_GEMM 0x11u
#define AX_ROLE_OP_GPU_RUN  0x12u
#define AX_ROLE_OP_GPU_LOAD 0x13u
#define AX_ROLE_OP_GPU_EXEC 0x14u

/* role_info capability bits.  A role advertises the job encoding it accepts,
 * not raw MMIO access: U-mode never maps the role window. */
#define AX_ROLE_CAP_LOOPBACK (1u << 0)
#define AX_ROLE_CAP_TPU_GEMM (1u << 1)
#define AX_ROLE_CAP_GPU_RUN  (1u << 2)

/* Bounds shared by the userspace and host-link front ends. */
#define AX_ROLE_MAX_REQUEST  1280u
#define AX_ROLE_MAX_RESPONSE 1280u
#define AX_ROLE_LOOP_MAX_WORDS 62u
#define AX_ROLE_TPU_MAX_M      32u
#define AX_ROLE_GPU_MAX_INSN   64u
#define AX_ROLE_GPU_MAX_DATA   200u

enum {
  AX_ROLE_EXEC_OK = 0,
  AX_ROLE_EXEC_BAD_OP = -1,
  AX_ROLE_EXEC_BAD_LEN = -2,
  AX_ROLE_EXEC_NO_ROLE = -3,
  AX_ROLE_EXEC_NO_SPACE = -4,
  AX_ROLE_EXEC_TIMEOUT = -5,
};

/* loopback descriptor registers — the universal contract-proof role, which the
 * kernel can drive end-to-end without any role-specific job encoding. */
#define AX_ROLE_LOOP_SRC (AX_ROLE_KERNEL_BASE + 0x0010u)
#define AX_ROLE_LOOP_DST (AX_ROLE_KERNEL_BASE + 0x0014u)
#define AX_ROLE_LOOP_LEN (AX_ROLE_KERNEL_BASE + 0x0018u)
#define AX_ROLE_LOOP_BUF (AX_ROLE_KERNEL_BASE + 0x1000u)

/* tpu-lite descriptor registers: C[M*8] = A[M*8] x W[8*8], int8 in, int32 out.
 * W and A pack one int8 per byte, little-endian, two words per 8-wide row. */
#define AX_ROLE_TPU_CTRL  (AX_ROLE_KERNEL_BASE + 0x0010u)
#define AX_ROLE_TPU_M     (AX_ROLE_KERNEL_BASE + 0x0014u)
#define AX_ROLE_TPU_W     (AX_ROLE_KERNEL_BASE + 0x0100u)
#define AX_ROLE_TPU_A     (AX_ROLE_KERNEL_BASE + 0x1000u)
#define AX_ROLE_TPU_C     (AX_ROLE_KERNEL_BASE + 0x2000u)
#define AX_ROLE_TPU_CTRL_RELU 0x1u
#define AX_ROLE_TPU_CTRL_ACC  0x2u

/* gpu-compute descriptor registers: an uploaded straight-line kernel over a
 * flat global data buffer, run across NTHREADS lanes. */
#define AX_ROLE_GPU_NTHREADS (AX_ROLE_KERNEL_BASE + 0x0010u)
#define AX_ROLE_GPU_NINSN    (AX_ROLE_KERNEL_BASE + 0x0014u)
#define AX_ROLE_GPU_PROG     (AX_ROLE_KERNEL_BASE + 0x0100u)
#define AX_ROLE_GPU_DATA     (AX_ROLE_KERNEL_BASE + 0x1000u)

uint32_t role_discover(void);       /* ROLE_ID; 0 means no role present */
uint32_t role_version(void);
uint32_t role_capabilities(uint32_t role_id);
const char *role_name(uint32_t role_id);
void role_init(void);               /* safely probe whether the MMIO window exists */
int role_loopback_selftest(void);   /* 0 = copy verified, -1 = mismatch */

/* Drive one loopback copy over caller-supplied data: write `words` inputs into
 * the role buffer, run the copy, and read the results back.  Used by the
 * host-link service to run a job on behalf of a remote request. */
int role_loopback_copy(const uint32_t *in, uint32_t *out, uint32_t words);

/* Per-role job drivers used by the host-link service to run real accelerator
 * work on behalf of a host request.  Each marshals caller data into the role,
 * runs one job through the shared doorbell/status cycle, and reads results. */
int role_tpu_gemm(const int8_t *w, const int8_t *a, uint32_t m,
                  uint32_t ctrl, int32_t *c_out);
int role_gpu_run(const uint32_t *prog, uint32_t ninsn,
                 const uint32_t *data_in, uint32_t ndata,
                 uint32_t nthreads, uint32_t *data_out);
int role_gpu_load(const uint32_t *prog, uint32_t ninsn);
int role_gpu_exec(const uint32_t *data_in, uint32_t ndata,
                  uint32_t nthreads, uint32_t *data_out);

/* Validate and execute one encoded role job.  `request` and `response` may be
 * the same buffer; all inputs are consumed before results are written. */
int role_execute(uint32_t op, const uint8_t *request, uint32_t request_len,
                 uint8_t *response, uint32_t response_cap,
                 uint32_t *response_len);
