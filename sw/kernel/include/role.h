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
/* Chunked transfer: move the data buffer in frame-sized pieces straight
 * through the role window, so a job's size stops being bounded by what one
 * request frame and one kernel stack frame can hold.  WRITE and READ address
 * the role's own global memory by word offset; LAUNCH runs what is already
 * resident.  The staged ops above are unchanged and remain the whole-job
 * encoding for anything that fits one frame. */
#define AX_ROLE_OP_GPU_WRITE  0x15u
#define AX_ROLE_OP_GPU_LAUNCH 0x16u
#define AX_ROLE_OP_GPU_READ   0x17u

/* role_info capability bits.  A role advertises the job encoding it accepts,
 * not raw MMIO access: U-mode never maps the role window. */
#define AX_ROLE_CAP_LOOPBACK (1u << 0)
#define AX_ROLE_CAP_TPU_GEMM (1u << 1)
#define AX_ROLE_CAP_GPU_RUN  (1u << 2)

/* The encoded-job staging cap, shared by every front end.  A kernel profile
 * setting rather than any component's parameter: the syscall component's
 * role_submit, the role dispatcher, and the host-link service all stage the
 * same encoded job through the same role_execute, and the host-link
 * personality contains no syscalls at all -- so no one component owns it.
 * sw/kernel/Makefile passes the resolved value; this is the profile-less
 * default, and it is spelled here once for all three paths. */
#ifndef ROLE_MAX_PAYLOAD
#define ROLE_MAX_PAYLOAD 1280
#endif
#define AX_ROLE_MAX_PAYLOAD ((uint32_t)ROLE_MAX_PAYLOAD)

/* Bounds on the staged path: one whole job in one request frame, marshalled
 * through arrays on role_execute's stack.  AX_ROLE_GPU_MAX_DATA is what those
 * arrays cost, so it bounds the kernel stack rather than the accelerator, and
 * it is a profile setting for the same reason the payload cap is -- a 32 KiB
 * profile and a 128 KiB one do not want the same number.  It is deliberately
 * distinct from the role's addressable capacity below: the two answer
 * different questions and a profile may move one without the other. */
#define AX_ROLE_LOOP_MAX_WORDS 62u
#define AX_ROLE_TPU_MAX_M      32u
#define AX_ROLE_GPU_MAX_INSN   64u
#ifndef ROLE_STAGED_WORDS
#define ROLE_STAGED_WORDS 200
#endif
#define AX_ROLE_GPU_MAX_DATA ((uint32_t)ROLE_STAGED_WORDS)

/* How many words of role global memory the streaming ops may address.
 *
 * This is the role's property, but it cannot be the role component's
 * parameter, because the kernel is not built against a role: KERNEL_CONFIG
 * selects software components only and the role is chosen by the hardware
 * profile the *simulator* or the bitstream is built from.  One kernel image
 * discovers whatever role is present at runtime, which is what keeps a new
 * accelerator from meaning a new kernel binary.  So this is the task_slots
 * case named in SETTINGS in tools/configure.py -- a capacity that belongs to
 * no single component from the kernel's side, because the kernel only indexes
 * what it is handed -- and it is a profile setting, with its default here
 * exactly as the task_slots default lives in task.h.
 *
 * The default is the smallest role memory in the tree rather than the largest.
 * role.gpu-tpu presents the gpu-compute engine at ROLE_ID "GPUC" and the
 * original offsets, so this driver runs against it, and its memory is 256
 * words; role.morph is 256 as well; role.gpu-compute defaults to 4096.  A
 * profile that has more than 256 words says so and gets them.  Guessing the
 * largest instead would fault this kernel against the smallest: the role
 * bounds its own decode and answers anything past its memory with a bus
 * error, so an over-long write is a store access fault in supervisor mode --
 * a host request that takes the kernel down.  Guessing low costs a profile
 * some capacity it has to ask for; guessing high costs a crash. */
#ifdef ROLE_DATA_WORDS
#define AX_ROLE_GPU_DATA_WORDS ((uint32_t)ROLE_DATA_WORDS)

/* The staged path writes AX_ROLE_GPU_MAX_DATA words into the role's memory
 * without consulting its size, and nothing checked that relationship before.
 * Only this header knows both numbers, so the check belongs here -- the same
 * reason KERNEL_PROCESS_ARG_MAX <= LOADER_ARG_MAX sits beside its two
 * definitions rather than in a test. */
_Static_assert(AX_ROLE_GPU_MAX_DATA <= AX_ROLE_GPU_DATA_WORDS,
               "staged GPU job bound exceeds this profile's role memory");

/* A chunk still has to arrive in one frame, so the transfer ops are bounded
 * from both ends: by the role's memory above, and by the staging buffer that
 * carried the request.  Four bytes of that frame are the chunk header. */
#define AX_ROLE_GPU_CHUNK_WORDS ((AX_ROLE_MAX_PAYLOAD - 4u) / 4u)
#endif

/* The staged encoding has to fit the buffer that stages it.  Two settings, one
 * relation, and only this header sees both -- a profile that shrank the
 * payload cap without shrinking the staged job would otherwise have built
 * fine and returned NO_SPACE for every job at runtime. */
_Static_assert(4u + 4u * AX_ROLE_GPU_MAX_DATA <= AX_ROLE_MAX_PAYLOAD,
               "staged GPU job cannot fit the payload buffer this profile declares");

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

/* How completions were actually observed, so "waited on the interrupt" is
 * evidence rather than an assumption.  role_irq_waits counts jobs finished by
 * the PLIC handler; role_polled_waits counts those that fell back to reading
 * STATUS, which is what a profile without a controller (or a caller running
 * with interrupts masked) still does. */
uint32_t role_irq_waits(void);
uint32_t role_polled_waits(void);

/* Called from plic_dispatch with the role source claimed. */
void role_irq_complete(void);

/* Let completions be waited on instead of polled.  The kernel calls this once
 * it has routed the role's source to its own interrupt context; until then,
 * and in any personality that never calls it, waits stay polled. */
void role_enable_irq(void);

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

#ifdef AX_ROLE_GPU_DATA_WORDS
/* The same job, split at the two places role_gpu_exec already had a seam: move
 * `words` words to or from the role's global memory at `offset`, and launch
 * what is resident.  The caller streams as many chunks as it likes, so neither
 * the request frame nor this kernel's stack bounds the job any more -- only
 * the role's own memory does.  Bounds are checked in role_execute, which is
 * the one place both the local and the remote front end pass through. */
void role_gpu_write(uint32_t offset, const uint8_t *words_le, uint32_t nwords);
void role_gpu_read(uint32_t offset, uint8_t *words_le, uint32_t nwords);
int role_gpu_launch(uint32_t nthreads);
#endif

/* Validate and execute one encoded role job.  `request` and `response` may be
 * the same buffer; all inputs are consumed before results are written. */
int role_execute(uint32_t op, const uint8_t *request, uint32_t request_len,
                 uint8_t *response, uint32_t response_cap,
                 uint32_t *response_len);
