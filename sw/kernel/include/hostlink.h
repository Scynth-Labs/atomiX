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
#define HOSTLINK_OP_GPU_LOAD 0x13u  /* replace resident GPU microcode */
#define HOSTLINK_OP_GPU_EXEC 0x14u  /* run resident GPU microcode */
#define HOSTLINK_OP_GPU_WRITE  0x15u /* chunk into role memory at an offset */
#define HOSTLINK_OP_GPU_LAUNCH 0x16u /* run resident, transfer nothing */
#define HOSTLINK_OP_GPU_READ   0x17u /* chunk out of role memory at an offset */
#define HOSTLINK_OP_BYE      0x7fu

#define HOSTLINK_ST_OK      0x00u
#define HOSTLINK_ST_BAD_OP  0x01u
#define HOSTLINK_ST_BAD_LEN 0x02u
#define HOSTLINK_ST_NO_ROLE 0x03u
#define HOSTLINK_ST_DEVICE  0x04u

/* The staging buffer's size.  This header owns no cap of its own: the local
 * role_submit ABI and the host link stage the same encoded jobs through the
 * same role_execute, so they take the same number from the one place that
 * declares it.  Two buffers sized by two independent literals could disagree
 * about what "an encoded job" may be.
 *
 * The per-role job dimensions used to be repeated here as well.  They were
 * copies of the AX_ROLE_* bounds in role.h that role_execute actually enforces,
 * and nothing ever read them; a second spelling of a limit is a limit that can
 * drift silently, so they are gone.  role.h is where a job bound lives. */
#include "role.h"
#define HOSTLINK_MAX_PAYLOAD AX_ROLE_MAX_PAYLOAD

/* Run the host-link service over the console byte pipe: read framed requests,
 * dispatch them to the in-kernel role driver, and write framed responses.
 * Ends the session (and the program) on a BYE request. */
void host_service(void);
