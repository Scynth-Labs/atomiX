/* Bounded host-link request-format regression.
 *
 * The production service is included verbatim.  Its UART byte pipe and the
 * terminal BYE action become a finite in-memory stream so every case can stop;
 * role execution is a narrow contract double because this test owns framing,
 * dispatch, payload draining, and response status translation rather than role
 * MMIO.  The real role dispatcher remains covered by its component checks.
 */
#include <assert.h>
#include <setjmp.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "hostlink.h"
#include "role.h"

/* The service answers every request frame it can parse, so the reply this
 * harness has to capture is bounded by the input, not by any constant the
 * service knows.  Deriving it: consuming one reply costs at least four input
 * bytes (a sync byte, an opcode, and two length bytes -- a shorter tail
 * reaches EOF and answers nothing), and the largest reply any op produces here
 * is INFO's four header bytes plus eight of payload.  Twelve bytes out per
 * four bytes in is three times the input, and the input is bounded below.
 *
 * This was 2048, which no reachable input had exceeded.  Putting the chunked
 * transfer ops into the directed op range grew the corpus enough to find one;
 * the overflow was in this capture buffer, not in the service, which streams
 * its bytes to a UART and has no such limit. */
#define MAX_INPUT 65536u
#define MAX_REPLY (3u * MAX_INPUT)

static jmp_buf stop;
static const uint8_t *stream;
static size_t stream_size;
static size_t stream_at;
static uint8_t reply[MAX_REPLY];
static size_t reply_size;

enum event { EVENT_EOF = 1, EVENT_FINISH = 2 };

uint8_t axhost_format_get(void) {
  if (stream_at == stream_size) longjmp(stop, EVENT_EOF);
  return stream[stream_at++];
}

void axhost_format_put(uint8_t byte) {
  assert(reply_size < sizeof(reply));
  reply[reply_size++] = byte;
}

void axhost_format_finish(void) { longjmp(stop, EVENT_FINISH); }

uint32_t role_discover(void) { return 0x4c4f4f50u; }
uint32_t role_version(void) { return 1u; }

int role_execute(uint32_t op, const uint8_t *request, uint32_t request_len,
                 uint8_t *response, uint32_t response_cap,
                 uint32_t *response_len) {
  /* The chunked transfer ops dispatch through this same path, so the range
   * the double accepts is the range hostlink.c forwards -- otherwise a raw
   * input carrying op 0x15 would trip this assertion instead of exercising
   * the framing it was meant to reach. */
  assert(op >= HOSTLINK_OP_ROLE_RUN && op <= HOSTLINK_OP_GPU_READ);
  assert(request_len <= HOSTLINK_MAX_PAYLOAD);
  assert(response_cap == HOSTLINK_MAX_PAYLOAD);
  *response_len = 0;
  if (request_len == 0) return AX_ROLE_EXEC_OK;
  switch (request[0] & 3u) {
    case 0: return AX_ROLE_EXEC_BAD_LEN;
    case 1: return AX_ROLE_EXEC_NO_ROLE;
    case 2: return AX_ROLE_EXEC_TIMEOUT;
    default:
      response[0] = request[0];
      *response_len = 1;
      return AX_ROLE_EXEC_OK;
  }
}

#define AXHOST_FORMAT_REGRESSION 1
#include "../../sw/kernel/hostlink.c"

static enum event run(const uint8_t *data, size_t size) {
  stream = data;
  stream_size = size;
  stream_at = 0;
  reply_size = 0;
  const int event = setjmp(stop);
  if (event) return (enum event)event;
  host_service();
  assert(0 && "host service returned without a terminal event");
  return EVENT_EOF;
}

static int has_frame(uint8_t status, uint16_t length) {
  for (size_t i = 0; i + 4 <= reply_size; ++i) {
    if (reply[i] == HOSTLINK_RSP_SYNC && reply[i + 1] == status &&
        reply[i + 2] == (uint8_t)length &&
        reply[i + 3] == (uint8_t)(length >> 8)) return 1;
  }
  return 0;
}

static size_t frame(uint8_t *out, uint8_t op, const uint8_t *data,
                    uint16_t length) {
  out[0] = HOSTLINK_REQ_SYNC;
  out[1] = op;
  out[2] = (uint8_t)length;
  out[3] = (uint8_t)(length >> 8);
  if (length && data != &out[4]) memcpy(&out[4], data, length);
  return 4u + length;
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  if (size > MAX_INPUT) return 0;

  /* Raw bytes cover sync recovery and partial headers. */
  (void)run(data, size);

  uint8_t request[4u + HOSTLINK_MAX_PAYLOAD];
  uint16_t length = (uint16_t)(size > HOSTLINK_MAX_PAYLOAD
      ? HOSTLINK_MAX_PAYLOAD : size);
  if (length) memcpy(&request[4], data, length);
  /* 0x10..0x17: every op that reaches role_execute, so the directed case
   * covers the chunked ops as well as the staged ones. */
  const uint8_t op = (uint8_t)(HOSTLINK_OP_ROLE_RUN + (size % 8u));
  const size_t request_size = frame(request, op, &request[4], length);
  assert(run(request, request_size) == EVENT_EOF);
  const uint8_t status = length == 0 ? HOSTLINK_ST_OK :
      (request[4] & 3u) == 0 ? HOSTLINK_ST_BAD_LEN :
      (request[4] & 3u) == 1 ? HOSTLINK_ST_NO_ROLE :
      (request[4] & 3u) == 2 ? HOSTLINK_ST_DEVICE : HOSTLINK_ST_OK;
  assert(has_frame(status, status == HOSTLINK_ST_OK && length ? 1u : 0u));

  /* An oversized declared payload is consumed exactly before BAD_LEN, keeping
   * the next frame boundary available to the same streaming session. */
  uint8_t oversized[4u + HOSTLINK_MAX_PAYLOAD + 1u + 4u];
  memset(&oversized[4], 0, HOSTLINK_MAX_PAYLOAD + 1u);
  size_t oversized_size = frame(oversized, HOSTLINK_OP_PING, &oversized[4],
                                HOSTLINK_MAX_PAYLOAD + 1u);
  oversized_size += frame(&oversized[oversized_size], HOSTLINK_OP_BYE, 0, 0);
  assert(run(oversized, oversized_size) == EVENT_FINISH);
  assert(has_frame(HOSTLINK_ST_BAD_LEN, 0));
  assert(has_frame(HOSTLINK_ST_OK, 0));
  return 0;
}
