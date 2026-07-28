#include <stdint.h>

enum { SPI = 0x10010000u, GO = 1u, CS_N = 2u, BUSY = 1u };
static uint8_t sector[512];

static inline void write32(uint32_t address, uint32_t value) {
  *(volatile uint32_t *)(uintptr_t)address = value;
}
static inline uint32_t read32(uint32_t address) {
  return *(volatile const uint32_t *)(uintptr_t)address;
}
static void boot_banner(void) {
  static const char text[] = "aXboot\n";
  for (uint32_t i = 0; text[i]; ++i) write32(0x10000000u, text[i]);
}
static void select_card(int selected) { write32(SPI + 4u, selected ? 0 : CS_N); }
static uint8_t transfer(uint8_t value) {
  write32(SPI, value); write32(SPI + 4u, GO);
  while (read32(SPI + 8u) & BUSY) { }
  return (uint8_t)read32(SPI);
}
static void deselect(void) { select_card(0); (void)transfer(0xff); }
static uint8_t wait_r1(void) {
  for (uint32_t i = 0; i < 16; ++i) {
    uint8_t r = transfer(0xff); if (!(r & 0x80)) return r;
  }
  return 0xff;
}
static uint8_t command(uint8_t n, uint32_t arg, uint8_t crc) {
  select_card(1);
  (void)transfer(0x40u | n); (void)transfer((uint8_t)(arg >> 24));
  (void)transfer((uint8_t)(arg >> 16)); (void)transfer((uint8_t)(arg >> 8));
  (void)transfer((uint8_t)arg); (void)transfer(crc);
  return wait_r1();
}
static int sd_init(void) {
  write32(SPI + 12u, 0); select_card(0);
  for (uint32_t i = 0; i < 10; ++i) (void)transfer(0xff);
  if (command(0, 0, 0x95) != 1) return -1;
  deselect();
  if (command(8, 0x1aa, 0x87) != 1) return -1;
  if (transfer(0xff) || transfer(0xff) || transfer(0xff) != 1 ||
      transfer(0xff) != 0xaa) return -1;
  deselect();
  if (command(55, 0, 1) > 1) return -1;
  deselect();
  if (command(41, 0x40000000u, 1) != 0) return -1;
  deselect();
  return 0;
}
static int read_block(uint32_t block) {
  if (command(17, block, 1) != 0) { deselect(); return -1; }
  uint8_t token = 0xff;
  for (uint32_t i = 0; i < 16 && token == 0xff; ++i) token = transfer(0xff);
  if (token != 0xfe) { deselect(); return -1; }
  for (uint32_t i = 0; i < 512; ++i) sector[i] = transfer(0xff);
  (void)transfer(0xff); (void)transfer(0xff); deselect();
  return 0;
}
static uint32_t le32(const uint8_t *p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
         ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
__attribute__((noreturn)) static void fail(uint32_t code) {
  write32(0x00100000u, (code << 16) | 0x3333u);
  for (;;) __asm__ volatile("wfi");
}
int main(void) {
  boot_banner();
  if (sd_init()) fail(1);
  if (read_block(0)) fail(2);
  if (sector[0] != 'A' || sector[1] != 'X' || sector[2] != 'B' ||
      sector[3] != 'T') fail(3);
  const uint32_t blocks = le32(&sector[4]);
  /* RAM is 128 KiB and the top 4 KiB belongs to the kernel bootstrap stack.
   * The ROM header carries the actual length; its safety bound must come from
   * RAM capacity, not from whichever sector the current disk format chooses
   * for its filesystem. */
  if (blocks == 0 || blocks > 248) fail(4);
  volatile uint8_t *dst = (volatile uint8_t *)(uintptr_t)0x80000000u;
  for (uint32_t block = 0; block < blocks; ++block) {
    if (read_block(block + 1)) fail(5);
    for (uint32_t i = 0; i < 512; ++i) *dst++ = sector[i];
  }
  ((void (*)(void))(uintptr_t)0x80000000u)();
  fail(6);
}
