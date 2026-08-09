/* aXos host-link service — the shell control plane's host-facing side
 * (DESIGN.md §3.3).  It reads framed requests from the console byte pipe,
 * dispatches them to the in-kernel role driver (role.c), and writes framed
 * responses, so a host PC running axhost can discover and drive the
 * accelerator over the link.  The protocol is docs/host-protocol.md.
 *
 * This is the base: one request/response exchange at a time over the console
 * UART, including the loopback, TPU, and GPU job opcodes. A dedicated
 * USB-serial channel can carry the same frame codec without changing it. */
#include <stdint.h>

#include "hostlink.h"
#include "platform.h"
#include "role.h"

/* Frame payload staging.  role_execute accepts an overlapping request/result
 * buffer, so this is the only host-link job buffer. */
static uint8_t payload[HOSTLINK_MAX_PAYLOAD];

/* Polled, deliberately.  The interrupt-driven console exists to stop the hart
 * spinning while a *human* thinks; a host-link session streams framed binary
 * back-to-back and never idles, so buffering it would add a queue to overrun
 * and win nothing.  This is the case polling is right for. */
static uint8_t get_byte(void) { return (uint8_t)uart_getchar(); }
static void put_byte(uint8_t b) { uart_putchar((char)b); }

static uint16_t get_u16(void) {
  const uint8_t lo = get_byte();
  const uint8_t hi = get_byte();
  return (uint16_t)(lo | ((uint16_t)hi << 8));
}

static void put_u32(uint8_t *p, uint32_t v) {
  p[0] = (uint8_t)v;
  p[1] = (uint8_t)(v >> 8);
  p[2] = (uint8_t)(v >> 16);
  p[3] = (uint8_t)(v >> 24);
}

static void put_frame(uint8_t status, const uint8_t *data, uint16_t len) {
  put_byte(HOSTLINK_RSP_SYNC);
  put_byte(status);
  put_byte((uint8_t)len);
  put_byte((uint8_t)(len >> 8));
  for (uint16_t i = 0; i < len; ++i) put_byte(data[i]);
}

void host_service(void) {
  /* AXOK is the ROM's integrity acknowledgement, not proof that aXos has
   * finished page-table, allocator, and role initialization.  Announce the
   * actual request-ready boundary so a physical host cannot overrun the tiny
   * UART receive path by transmitting immediately after the ROM jumps. */
  put_byte('A');
  put_byte('X');
  put_byte('R');
  put_byte('D');
  for (;;) {
    /* Resynchronize to the next request frame. */
    while (get_byte() != HOSTLINK_REQ_SYNC) {}
    const uint8_t op = get_byte();
    const uint16_t len = get_u16();

    /* Read the payload, draining (and rejecting) anything oversized. */
    if (len > sizeof(payload)) {
      for (uint16_t i = 0; i < len; ++i) (void)get_byte();
      put_frame(HOSTLINK_ST_BAD_LEN, 0, 0);
      continue;
    }
    for (uint16_t i = 0; i < len; ++i) payload[i] = get_byte();

    switch (op) {
      case HOSTLINK_OP_PING: {
        const uint8_t pong[4] = {'a', 'X', 'H', 'L'};
        put_frame(HOSTLINK_ST_OK, pong, 4);
        break;
      }
      case HOSTLINK_OP_INFO: {
        uint8_t info[8];
        put_u32(&info[0], role_discover());
        put_u32(&info[4], role_version());
        put_frame(HOSTLINK_ST_OK, info, 8);
        break;
      }
      case HOSTLINK_OP_ROLE_RUN:
      case HOSTLINK_OP_TPU_GEMM:
      case HOSTLINK_OP_GPU_RUN:
      case HOSTLINK_OP_GPU_LOAD:
      case HOSTLINK_OP_GPU_EXEC: {
        uint32_t out_len = 0;
        const int rc = role_execute(op, payload, len, payload,
                                    sizeof(payload), &out_len);
        uint8_t status = HOSTLINK_ST_OK;
        if (rc == AX_ROLE_EXEC_BAD_OP) status = HOSTLINK_ST_BAD_OP;
        else if (rc == AX_ROLE_EXEC_BAD_LEN ||
                 rc == AX_ROLE_EXEC_NO_SPACE) status = HOSTLINK_ST_BAD_LEN;
        else if (rc == AX_ROLE_EXEC_NO_ROLE) status = HOSTLINK_ST_NO_ROLE;
        else if (rc != AX_ROLE_EXEC_OK) status = HOSTLINK_ST_DEVICE;
        put_frame(status, payload,
                  status == HOSTLINK_ST_OK ? (uint16_t)out_len : 0u);
        break;
      }
      case HOSTLINK_OP_BYE:
        put_frame(HOSTLINK_ST_OK, 0, 0);
        test_finish(0);   /* acknowledged; end the session and the program */
        break;
      default:
        put_frame(HOSTLINK_ST_BAD_OP, 0, 0);
        break;
    }
  }
}
