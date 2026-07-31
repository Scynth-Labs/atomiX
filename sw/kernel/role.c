#include "role.h"

/* ISS and QEMU have no device behind the role alias, while RTL always decodes a
 * role.none window.  role_init performs one recoverable load before userspace
 * starts and remembers whether MMIO exists; later discovery can then remain
 * live for role swapping without risking a fault from inside a syscall. */
volatile uint32_t mmio_probe_active;
static int role_window_present;
static uint32_t gpu_loaded_ninsn;

/* The monitor personality is a 32 KiB image whose page pool starts on a
 * 4 KiB boundary, so a hundred bytes of text it never executes costs a whole
 * page of allocatable memory.  It has no interrupt policy -- it never routes a
 * source -- so the interrupt-driven wait is compiled out rather than linked
 * and left unreachable.  Waits there are polled, exactly as they are on any
 * platform without a controller. */
#ifndef AXOS_MONITOR
#define ROLE_IRQ_WAIT 1
#endif

#ifdef ROLE_IRQ_WAIT
/* Wait accounting exists so "waited on the interrupt" is evidence rather than
 * an assumption.  Only a personality that can take the interrupt reports it,
 * so the counters go with the rest of the machinery. */
static uint32_t role_polled_wait_count;

/* Set by the PLIC handler when the role's completion interrupt arrives.  The
 * waiter only ever reads it, so a plain volatile flag is enough: there is one
 * hart, and the handler cannot interleave with itself. */
static volatile uint32_t role_irq_done;
static int role_irq_routed;
static uint32_t role_irq_wait_count;
#endif

extern int mmio_probe_read32(uint32_t addr, uint32_t *value);

void role_init(void) {
  uint32_t ignored;
  role_window_present = mmio_probe_read32(AX_ROLE_ID, &ignored);
  gpu_loaded_ninsn = 0;
#ifdef ROLE_IRQ_WAIT
  role_irq_done = 0;
  role_irq_routed = 0;
#endif
}

/* Whether completion may be waited on rather than polled is the kernel's call,
 * not the driver's: it owns the interrupt controller and decides which source
 * belongs to which context.  Keeping the decision there also keeps this file
 * free of any reference to the PLIC driver, so the monitor links none of it. */
void role_enable_irq(void) {
#ifdef ROLE_IRQ_WAIT
  role_irq_routed = role_window_present;
#endif
}

/* Called from plic_dispatch with the role source claimed.  Clearing DONE is
 * what makes the role drop its level-sensitive line, so it must happen here,
 * before the PLIC COMPLETE that plic_dispatch issues on return. */
void role_irq_complete(void) {
#ifdef ROLE_IRQ_WAIT
  mmio_write32(AX_ROLE_STATUS, AX_ROLE_STATUS_DONE);
  role_irq_done = 1u;
#endif
}

/* Generic role-header operations, shared by every role. */
static void role_ring_doorbell(void) {
#ifdef ROLE_IRQ_WAIT
  role_irq_done = 0;
#endif
  mmio_write32(AX_ROLE_DOORBELL, 1u);
}

/* A malformed userspace request is rejected before the doorbell, but a broken
 * or partially reconfigured device must not wedge the whole kernel forever.
 * Both waits below are bounded for that reason; the interrupt-driven one
 * counts wakeups rather than loads, and every wakeup is a real interrupt (the
 * scheduler tick at minimum), so the bound is much smaller. */
#define AX_ROLE_POLL_LIMIT 10000000u

#ifdef ROLE_IRQ_WAIT
#define AX_ROLE_WAIT_LIMIT 100000u

#define SSTATUS_SIE (1u << 1)

static inline uint32_t csr_read_sstatus(void) {
  uint32_t value;
  __asm__ volatile("csrr %0, sstatus" : "=r"(value));
  return value;
}

/* Waiting on the interrupt is only possible where the interrupt can actually
 * be delivered.  A trap handler runs with sstatus.SIE clear -- so a syscall
 * such as role_submit does -- and there the handler would never run to set the
 * flag.  Rather than re-enable interrupts underneath a half-finished syscall,
 * those callers keep the polled path; only the supervisor contexts that run
 * with interrupts on (the shell, the host-link service) sleep. */
static int role_wait_irq(void) {
  for (uint32_t waits = 0; waits < AX_ROLE_WAIT_LIMIT; ++waits) {
    /* Close the window between testing the flag and sleeping: with SIE clear
     * the completion cannot be delivered and missed in between, and wfi still
     * wakes on any pending enabled interrupt.  Re-enabling SIE afterwards is
     * what lets the handler actually run. */
    __asm__ volatile("csrci sstatus, %0" :: "i"(SSTATUS_SIE));
    if (role_irq_done) {
      __asm__ volatile("csrsi sstatus, %0" :: "i"(SSTATUS_SIE));
      return 0;
    }
    __asm__ volatile("wfi");
    __asm__ volatile("csrsi sstatus, %0" :: "i"(SSTATUS_SIE));
  }
  return -1;
}
#endif /* ROLE_IRQ_WAIT */

static int role_wait_done(void) {
#ifdef ROLE_IRQ_WAIT
  if (role_irq_routed && (csr_read_sstatus() & SSTATUS_SIE)) {
    const int result = role_wait_irq();
    if (result == 0) role_irq_wait_count++;
    return result;
  }
#endif
  for (uint32_t polls = 0; polls < AX_ROLE_POLL_LIMIT; ++polls)
    if (mmio_read32(AX_ROLE_STATUS) & AX_ROLE_STATUS_DONE) {
#ifdef ROLE_IRQ_WAIT
      role_polled_wait_count++;
#endif
      return 0;
    }
  return -1;
}

#ifdef ROLE_IRQ_WAIT
uint32_t role_irq_waits(void) { return role_irq_wait_count; }
uint32_t role_polled_waits(void) { return role_polled_wait_count; }
#else
uint32_t role_irq_waits(void) { return 0u; }
uint32_t role_polled_waits(void) { return 0u; }
#endif

uint32_t role_discover(void) {
  return role_window_present ? mmio_read32(AX_ROLE_ID) : 0;
}

uint32_t role_version(void) {
  return role_window_present ? mmio_read32(AX_ROLE_VERSION) : 0;
}

uint32_t role_capabilities(uint32_t role_id) {
  switch (role_id) {
    case AX_ROLE_ID_LOOPBACK: return AX_ROLE_CAP_LOOPBACK;
    case AX_ROLE_ID_TPU:      return AX_ROLE_CAP_TPU_GEMM;
    case AX_ROLE_ID_GPU:      return AX_ROLE_CAP_GPU_RUN;
    default:                  return 0;
  }
}

const char *role_name(uint32_t role_id) {
  switch (role_id) {
    case AX_ROLE_ID_LOOPBACK: return "loopback";
    case AX_ROLE_ID_TPU:      return "tpu-lite";
    case AX_ROLE_ID_GPU:      return "gpu-compute";
    default:                  return "unknown";
  }
}

/* Drive one loopback copy through the full descriptor cycle — program the
 * descriptor, ring the doorbell, poll for completion, read the result back —
 * proving aXos owns the whole role protocol, not just discovery.  Loopback is
 * the one role driveable with no role-specific job encoding, so it is the
 * kernel's self-test; per-role job marshaling (GEMM, SIMT kernels) layers on
 * top of this same header driver. */
int role_loopback_selftest(void) {
  const uint32_t words = 4u, dst_word = 16u;
  for (uint32_t i = 0; i < words; ++i)
    mmio_write32(AX_ROLE_LOOP_BUF + 4u * i, 0xa5000000u | i);
  mmio_write32(AX_ROLE_LOOP_SRC, 0u);            /* byte offset of source */
  mmio_write32(AX_ROLE_LOOP_DST, 4u * dst_word); /* byte offset of destination */
  mmio_write32(AX_ROLE_LOOP_LEN, words);
  role_ring_doorbell();
  if (role_wait_done() != 0) return -1;
  for (uint32_t i = 0; i < words; ++i)
    if (mmio_read32(AX_ROLE_LOOP_BUF + 4u * (dst_word + i)) != (0xa5000000u | i))
      return -1;
  return 0;
}

/* Same descriptor cycle as the self-test, but the payload comes from the
 * caller (the host-link service) instead of a fixed pattern.  Source words
 * occupy [0, words); the destination is placed immediately after them. */
int role_loopback_copy(const uint32_t *in, uint32_t *out, uint32_t words) {
  for (uint32_t i = 0; i < words; ++i)
    mmio_write32(AX_ROLE_LOOP_BUF + 4u * i, in[i]);
  mmio_write32(AX_ROLE_LOOP_SRC, 0u);
  mmio_write32(AX_ROLE_LOOP_DST, 4u * words);
  mmio_write32(AX_ROLE_LOOP_LEN, words);
  role_ring_doorbell();
  if (role_wait_done() != 0) return -1;
  for (uint32_t i = 0; i < words; ++i)
    out[i] = mmio_read32(AX_ROLE_LOOP_BUF + 4u * (words + i));
  return 0;
}

/* Pack four consecutive int8 operands into one little-endian word, the layout
 * both the TPU weight tile and activation buffer expect. */
static uint32_t pack4(const int8_t *p) {
  return (uint32_t)(uint8_t)p[0] | ((uint32_t)(uint8_t)p[1] << 8) |
         ((uint32_t)(uint8_t)p[2] << 16) | ((uint32_t)(uint8_t)p[3] << 24);
}

/* Run one TPU-lite GEMM: load the 8x8 weight tile and M activation rows, latch
 * CTRL/M, ring the doorbell, and read back the M x 8 int32 result tile. */
int role_tpu_gemm(const int8_t *w, const int8_t *a, uint32_t m,
                  uint32_t ctrl, int32_t *c_out) {
  for (uint32_t r = 0; r < 8u; ++r) {
    mmio_write32(AX_ROLE_TPU_W + 8u * r, pack4(&w[8u * r]));
    mmio_write32(AX_ROLE_TPU_W + 8u * r + 4u, pack4(&w[8u * r + 4u]));
  }
  for (uint32_t i = 0; i < m; ++i) {
    mmio_write32(AX_ROLE_TPU_A + 8u * i, pack4(&a[8u * i]));
    mmio_write32(AX_ROLE_TPU_A + 8u * i + 4u, pack4(&a[8u * i + 4u]));
  }
  mmio_write32(AX_ROLE_TPU_CTRL, ctrl);
  mmio_write32(AX_ROLE_TPU_M, m);
  role_ring_doorbell();
  if (role_wait_done() != 0) return -1;
  for (uint32_t i = 0; i < m * 8u; ++i)
    c_out[i] = (int32_t)mmio_read32(AX_ROLE_TPU_C + 4u * i);
  return 0;
}

/* Run one GPU-compute job: upload the kernel and the flat data buffer, launch
 * NTHREADS lanes over the program, and read the data buffer back. */
int role_gpu_load(const uint32_t *prog, uint32_t ninsn) {
  for (uint32_t i = 0; i < ninsn; ++i)
    mmio_write32(AX_ROLE_GPU_PROG + 4u * i, prog[i]);
  mmio_write32(AX_ROLE_GPU_NINSN, ninsn);
  gpu_loaded_ninsn = ninsn;
  return 0;
}

int role_gpu_exec(const uint32_t *data_in, uint32_t ndata,
                  uint32_t nthreads, uint32_t *data_out) {
  if (gpu_loaded_ninsn == 0u) return -1;
  for (uint32_t i = 0; i < ndata; ++i)
    mmio_write32(AX_ROLE_GPU_DATA + 4u * i, data_in[i]);
  mmio_write32(AX_ROLE_GPU_NTHREADS, nthreads);
  role_ring_doorbell();
  if (role_wait_done() != 0) return -1;
  for (uint32_t i = 0; i < ndata; ++i)
    data_out[i] = mmio_read32(AX_ROLE_GPU_DATA + 4u * i);
  return 0;
}

int role_gpu_run(const uint32_t *prog, uint32_t ninsn,
                 const uint32_t *data_in, uint32_t ndata,
                 uint32_t nthreads, uint32_t *data_out) {
  if (role_gpu_load(prog, ninsn) != 0) return -1;
  return role_gpu_exec(data_in, ndata, nthreads, data_out);
}

static uint16_t get_u16(const uint8_t *p) {
  return (uint16_t)(p[0] | ((uint16_t)p[1] << 8));
}

static uint32_t get_u32(const uint8_t *p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
         ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void put_u32(uint8_t *p, uint32_t value) {
  p[0] = (uint8_t)value;
  p[1] = (uint8_t)(value >> 8);
  p[2] = (uint8_t)(value >> 16);
  p[3] = (uint8_t)(value >> 24);
}

static uint32_t cycle_now(void) {
  return mmio_read32(AX_CLINT_BASE + 0xbff8u);
}

/* The single checked dispatch shared by U-mode and the host-link service.
 * Payloads deliberately retain the host protocol's little-endian wire shape,
 * which makes requests portable across the local and remote control paths. */
int role_execute(uint32_t op, const uint8_t *request, uint32_t request_len,
                 uint8_t *response, uint32_t response_cap,
                 uint32_t *response_len) {
  *response_len = 0;

  if (op == AX_ROLE_OP_LOOPBACK) {
    if (role_discover() != AX_ROLE_ID_LOOPBACK) return AX_ROLE_EXEC_NO_ROLE;
    if (request_len < 2u) return AX_ROLE_EXEC_BAD_LEN;
    const uint32_t words = get_u16(request);
    const uint32_t out_len = 4u * words;
    if (words > AX_ROLE_LOOP_MAX_WORDS ||
        request_len != 2u + out_len) return AX_ROLE_EXEC_BAD_LEN;
    if (out_len > response_cap) return AX_ROLE_EXEC_NO_SPACE;

    uint32_t in[AX_ROLE_LOOP_MAX_WORDS];
    uint32_t out[AX_ROLE_LOOP_MAX_WORDS];
    for (uint32_t i = 0; i < words; ++i)
      in[i] = get_u32(&request[2u + 4u * i]);
    if (role_loopback_copy(in, out, words) != 0)
      return AX_ROLE_EXEC_TIMEOUT;
    for (uint32_t i = 0; i < words; ++i)
      put_u32(&response[4u * i], out[i]);
    *response_len = out_len;
    return AX_ROLE_EXEC_OK;
  }

  if (op == AX_ROLE_OP_TPU_GEMM) {
    if (role_discover() != AX_ROLE_ID_TPU) return AX_ROLE_EXEC_NO_ROLE;
    if (request_len < 66u) return AX_ROLE_EXEC_BAD_LEN;
    const uint32_t m = request[0];
    const uint32_t out_len = 4u * m * 8u;
    if (m == 0u || m > AX_ROLE_TPU_MAX_M ||
        request_len != 66u + 8u * m) return AX_ROLE_EXEC_BAD_LEN;
    if (out_len > response_cap) return AX_ROLE_EXEC_NO_SPACE;

    int32_t out[AX_ROLE_TPU_MAX_M * 8u];
    if (role_tpu_gemm((const int8_t *)&request[2],
                      (const int8_t *)&request[66], m, request[1], out) != 0)
      return AX_ROLE_EXEC_TIMEOUT;
    for (uint32_t i = 0; i < m * 8u; ++i)
      put_u32(&response[4u * i], (uint32_t)out[i]);
    *response_len = out_len;
    return AX_ROLE_EXEC_OK;
  }

  if (op == AX_ROLE_OP_GPU_RUN) {
    if (role_discover() != AX_ROLE_ID_GPU) return AX_ROLE_EXEC_NO_ROLE;
    if (request_len < 6u) return AX_ROLE_EXEC_BAD_LEN;
    const uint32_t nthreads = get_u16(&request[0]);
    const uint32_t ninsn = get_u16(&request[2]);
    const uint32_t ndata = get_u16(&request[4]);
    const uint32_t out_len = 4u * ndata;
    if (ninsn > AX_ROLE_GPU_MAX_INSN || ndata > AX_ROLE_GPU_MAX_DATA ||
        request_len != 6u + 4u * ninsn + out_len)
      return AX_ROLE_EXEC_BAD_LEN;
    if (out_len > response_cap) return AX_ROLE_EXEC_NO_SPACE;

    uint32_t prog[AX_ROLE_GPU_MAX_INSN];
    uint32_t data[AX_ROLE_GPU_MAX_DATA];
    uint32_t out[AX_ROLE_GPU_MAX_DATA];
    for (uint32_t i = 0; i < ninsn; ++i)
      prog[i] = get_u32(&request[6u + 4u * i]);
    for (uint32_t i = 0; i < ndata; ++i)
      data[i] = get_u32(&request[6u + 4u * ninsn + 4u * i]);
    if (role_gpu_run(prog, ninsn, data, ndata, nthreads, out) != 0)
      return AX_ROLE_EXEC_TIMEOUT;
    for (uint32_t i = 0; i < ndata; ++i)
      put_u32(&response[4u * i], out[i]);
    *response_len = out_len;
    return AX_ROLE_EXEC_OK;
  }

  if (op == AX_ROLE_OP_GPU_LOAD) {
    if (role_discover() != AX_ROLE_ID_GPU) return AX_ROLE_EXEC_NO_ROLE;
    if (request_len < 2u) return AX_ROLE_EXEC_BAD_LEN;
    const uint32_t ninsn = get_u16(request);
    if (ninsn == 0u || ninsn > AX_ROLE_GPU_MAX_INSN ||
        request_len != 2u + 4u * ninsn)
      return AX_ROLE_EXEC_BAD_LEN;
    if (response_cap < 4u) return AX_ROLE_EXEC_NO_SPACE;

    uint32_t prog[AX_ROLE_GPU_MAX_INSN];
    for (uint32_t i = 0; i < ninsn; ++i)
      prog[i] = get_u32(&request[2u + 4u * i]);
    const uint32_t start = cycle_now();
    if (role_gpu_load(prog, ninsn) != 0) return AX_ROLE_EXEC_TIMEOUT;
    put_u32(response, cycle_now() - start);
    *response_len = 4u;
    return AX_ROLE_EXEC_OK;
  }

  if (op == AX_ROLE_OP_GPU_EXEC) {
    if (role_discover() != AX_ROLE_ID_GPU) return AX_ROLE_EXEC_NO_ROLE;
    if (request_len < 4u) return AX_ROLE_EXEC_BAD_LEN;
    const uint32_t nthreads = get_u16(&request[0]);
    const uint32_t ndata = get_u16(&request[2]);
    const uint32_t data_len = 4u * ndata;
    if (ndata > AX_ROLE_GPU_MAX_DATA ||
        request_len != 4u + data_len)
      return AX_ROLE_EXEC_BAD_LEN;
    if (4u + data_len > response_cap) return AX_ROLE_EXEC_NO_SPACE;

    uint32_t data[AX_ROLE_GPU_MAX_DATA];
    uint32_t out[AX_ROLE_GPU_MAX_DATA];
    for (uint32_t i = 0; i < ndata; ++i)
      data[i] = get_u32(&request[4u + 4u * i]);
    const uint32_t start = cycle_now();
    if (role_gpu_exec(data, ndata, nthreads, out) != 0)
      return AX_ROLE_EXEC_TIMEOUT;
    put_u32(response, cycle_now() - start);
    for (uint32_t i = 0; i < ndata; ++i)
      put_u32(&response[4u + 4u * i], out[i]);
    *response_len = 4u + data_len;
    return AX_ROLE_EXEC_OK;
  }

  return AX_ROLE_EXEC_BAD_OP;
}
