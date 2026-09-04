/* Bounded AXK1 upload-format regression for the immutable UART loader.
 *
 * The production parser streams UART bytes directly into RAM, then transfers
 * control after checking the declared length and CRC-32.  This harness includes
 * that source verbatim and supplies only its hardware seams: a finite byte
 * pipe, a bounded RAM array, and a non-returning stand-in for the handoff.
 * Thus the checked host instrumentation executes the same magic scan, LE header decoding, length
 * check, byte-at-a-time copy, CRC, and retry loop the ROM ships.
 */
#include <assert.h>
#include <setjmp.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

enum { AXBOOT_FORMAT_RAM_BYTES = 32768, AXBOOT_FORMAT_PAYLOAD_MAX = 28672 };

static jmp_buf stop;
static const uint8_t *stream;
static size_t stream_size;
static size_t stream_at;
static uint8_t fuzz_response[64];
static size_t fuzz_response_size;
static uint32_t accepted_length;
static uint8_t ram[AXBOOT_FORMAT_RAM_BYTES];
volatile uint8_t *axboot_format_ram = ram;
const uint32_t axboot_format_ram_bytes = AXBOOT_FORMAT_RAM_BYTES;

enum event { EVENT_EOF = 1, EVENT_ACCEPT = 2 };

uint8_t axboot_format_get(void) {
  if (stream_at == stream_size) longjmp(stop, EVENT_EOF);
  return stream[stream_at++];
}

void axboot_format_put(uint8_t byte) {
  if (fuzz_response_size < sizeof(fuzz_response))
    fuzz_response[fuzz_response_size++] = byte;
}

void axboot_format_accept(uint32_t length) {
  accepted_length = length;
  longjmp(stop, EVENT_ACCEPT);
}

#define AXBOOT_UART 1
#define AXBOOT_FORMAT_REGRESSION 1
#define main axboot_format_source_main
#include "../../sw/bootrom/boot.c"
#undef main

static enum event run(const uint8_t *data, size_t size) {
  stream = data;
  stream_size = size;
  stream_at = 0;
  fuzz_response_size = 0;
  accepted_length = 0;
  memset(ram, 0, sizeof(ram));
  const int event = setjmp(stop);
  if (event) return (enum event)event;
  uart_boot();
}

static int responded(const char tag[4], uint32_t value) {
  for (size_t i = 0; i + 8 <= fuzz_response_size; ++i) {
    if (fuzz_response[i] == (uint8_t)tag[0] &&
        fuzz_response[i + 1] == (uint8_t)tag[1] &&
        fuzz_response[i + 2] == (uint8_t)tag[2] &&
        fuzz_response[i + 3] == (uint8_t)tag[3] &&
        fuzz_response[i + 4] == (uint8_t)value &&
        fuzz_response[i + 5] == (uint8_t)(value >> 8) &&
        fuzz_response[i + 6] == (uint8_t)(value >> 16) &&
        fuzz_response[i + 7] == (uint8_t)(value >> 24)) return 1;
  }
  return 0;
}

static void put_le32(uint8_t *dst, uint32_t value) {
  dst[0] = (uint8_t)value;
  dst[1] = (uint8_t)(value >> 8);
  dst[2] = (uint8_t)(value >> 16);
  dst[3] = (uint8_t)(value >> 24);
}

static uint32_t crc32(const uint8_t *data, size_t size) {
  uint32_t crc = 0xffffffffu;
  for (size_t i = 0; i < size; ++i) crc = crc32_byte(crc, data[i]);
  return ~crc;
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  if (size > 65536u) return 0;

  /* The unmodified input exercises magic resynchronization and arbitrary
   * headers.  EOF is a modeled idle UART, not an accepted partial frame. */
  (void)run(data, size);

  /* A valid-shaped frame makes every payload byte reach the real copy and CRC
   * loop; its payload remains input-controlled. */
  uint8_t frame[12 + AXBOOT_FORMAT_PAYLOAD_MAX];
  size_t payload_size = size;
  if (payload_size > AXBOOT_FORMAT_PAYLOAD_MAX)
    payload_size = AXBOOT_FORMAT_PAYLOAD_MAX;
  if (payload_size == 0) payload_size = 1;
  memcpy(frame, "AXK1", 4);
  put_le32(&frame[4], (uint32_t)payload_size);
  if (size) memcpy(&frame[12], data, payload_size);
  else frame[12] = 0;
  put_le32(&frame[8], crc32(&frame[12], payload_size));
  assert(run(frame, 12 + payload_size) == EVENT_ACCEPT);
  assert(accepted_length == payload_size);
  assert(!memcmp(ram, &frame[12], payload_size));

  /* The same payload with one CRC bit flipped must be rejected and return to
   * magic scanning, never hand off partially validated RAM. */
  frame[8] ^= 1u;
  assert(run(frame, 12 + payload_size) == EVENT_EOF);
  assert(responded("AXER", 2u));
  return 0;
}
