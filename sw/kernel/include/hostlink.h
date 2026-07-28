#pragma once

/* aX host-link protocol v0 — the shell control-plane wire format.  The
 * authoritative spec is docs/host-protocol.md; keep this and sw/host/axhost.py
 * in step with it.  Transport is a byte pipe (the console UART in this base;
 * a dedicated USB-serial channel later). */
#define HOSTLINK_REQ_SYNC 0xa5u
#define HOSTLINK_RSP_SYNC 0x5au

#define HOSTLINK_OP_PING     0x01u
#define HOSTLINK_OP_INFO     0x02u
#define HOSTLINK_OP_ROLE_RUN 0x10u  /* loopback copy */
#define HOSTLINK_OP_TPU_GEMM 0x11u  /* tpu-lite GEMM */
#define HOSTLINK_OP_GPU_RUN  0x12u  /* gpu-compute kernel */
#define HOSTLINK_OP_BYE      0x7fu

#define HOSTLINK_ST_OK      0x00u
#define HOSTLINK_ST_BAD_OP  0x01u
#define HOSTLINK_ST_BAD_LEN 0x02u
#define HOSTLINK_ST_NO_ROLE 0x03u
#define HOSTLINK_ST_DEVICE  0x04u

/* Frame and per-op payload caps.  MAX_PAYLOAD bounds the staging buffer; the
 * per-role caps bound the job dimensions so a request cannot overrun it. */
#define HOSTLINK_MAX_PAYLOAD  1280u
#define HOSTLINK_MAX_WORDS    62u   /* loopback ROLE_RUN */
#define HOSTLINK_TPU_MAX_M    32u   /* tpu-lite activation rows */
#define HOSTLINK_GPU_MAX_INSN 64u   /* gpu-compute kernel length */
#define HOSTLINK_GPU_MAX_DATA 200u  /* gpu-compute data words */

/* Run the host-link service over the console byte pipe: read framed requests,
 * dispatch them to the in-kernel role driver, and write framed responses.
 * Ends the session (and the program) on a BYE request. */
void host_service(void);
