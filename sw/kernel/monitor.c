#include <stdint.h>

#include "evolution.h"
#include "kernel_info.h"
#include "hostlink.h"
#include "page.h"
#include "platform.h"
#include "role.h"

enum {
  MSTATUS_MPP = 3u << 11,
  MSTATUS_MPP_S = 1u << 11,
  SCAUSE_LOAD_ACCESS_FAULT = 5,
  SCAUSE_SUPERVISOR_SOFTWARE = 0x80000001u,
  PTE_V = 1u << 0,
  PTE_R = 1u << 1,
  PTE_W = 1u << 2,
  PTE_X = 1u << 3,
  PTE_A = 1u << 6,
  PTE_D = 1u << 7,
  SATP_MODE_SV32 = 1u << 31,
};

extern void s_entry(void);
extern void machine_timer_trap(void);
extern void shell_run(void);
extern void mmio_probe_fault(void);
extern volatile uint32_t mmio_probe_active;

static volatile uint32_t supervisor_ticks;
static uint32_t allocator_total_pages;
static volatile uint32_t root_pt[1024]
    __attribute__((aligned(4096), section(".page_tables")));

static inline uint32_t pte_leaf(uint32_t physical, uint32_t flags) {
  return ((physical >> 12) << 10) | flags;
}

static void bootstrap_map(void) {
  const uint32_t kernel = PTE_V | PTE_R | PTE_W | PTE_X | PTE_A | PTE_D;
  const uint32_t device = PTE_V | PTE_R | PTE_W | PTE_A | PTE_D;

  root_pt[0x200] = pte_leaf(0x80000000u, kernel);
  root_pt[0x040] = pte_leaf(AX_UART_BASE, device);
  root_pt[0x008] = pte_leaf(AX_CLINT_BASE, device);
  root_pt[AX_ROLE_KERNEL_BASE >> 22] = pte_leaf(AX_ROLE_BASE, device);
  /*
   * The compact profile maps the first 4 MiB as one supervisor-only leaf.
   * This covers the simulation finisher without spending a second 4 KiB page
   * table. No user address spaces exist in this profile.
   */
  root_pt[0] = pte_leaf(0, device);
}

static inline uint32_t csr_read_mstatus(void) {
  uint32_t value;
  __asm__ volatile("csrr %0, mstatus" : "=r"(value));
  return value;
}

static inline uint32_t csr_read_sp(void) {
  uint32_t value;
  __asm__ volatile("mv %0, sp" : "=r"(value));
  return value;
}

static inline uint32_t csr_read_scause(void) {
  uint32_t value;
  __asm__ volatile("csrr %0, scause" : "=r"(value));
  return value;
}

static inline uint32_t csr_read_sepc(void) {
  uint32_t value;
  __asm__ volatile("csrr %0, sepc" : "=r"(value));
  return value;
}

static inline uint32_t csr_read_stval(void) {
  uint32_t value;
  __asm__ volatile("csrr %0, stval" : "=r"(value));
  return value;
}

#ifndef AXOS_HOSTLINK
static void clint_arm_timer(uint32_t delta) {
  volatile uint32_t *const mtimecmp =
      (volatile uint32_t *)(uintptr_t)(AX_CLINT_BASE + 0x4000u);
  volatile const uint32_t *const mtime =
      (volatile const uint32_t *)(uintptr_t)(AX_CLINT_BASE + 0xbff8u);
  mtimecmp[1] = 0xffffffffu;
  mtimecmp[0] = *mtime + delta;
  mtimecmp[1] = 0;
}
#endif

void m_setup(void) {
  bootstrap_map();
  __asm__ volatile("csrw mscratch, %0" :: "r"(csr_read_sp()));
  __asm__ volatile("csrw mtvec, %0" :: "r"((uint32_t)(uintptr_t)machine_timer_trap));
  __asm__ volatile("csrw medeleg, %0" :: "r"(1u << SCAUSE_LOAD_ACCESS_FAULT));
  __asm__ volatile("csrw mideleg, %0" :: "r"(1u << 1));
#ifdef AXOS_HOSTLINK
  /* The host-link personality polls a one-byte UART at high baud.  A periodic
   * timer trap can occupy the hart for longer than one character and silently
   * overrun RX, so this dedicated binary deliberately owns the byte pipe with
   * MTIE disabled.  It has no scheduler or interactive uptime requirement. */
  __asm__ volatile("csrw mie, %0" :: "r"(1u << 1));
#else
  __asm__ volatile("csrw mie, %0" :: "r"((1u << 7) | (1u << 1)));
  clint_arm_timer(0x00100000u);
#endif
  __asm__ volatile("csrw satp, %0" ::
                   "r"(SATP_MODE_SV32 |
                       ((uint32_t)(uintptr_t)root_pt >> 12)));
  __asm__ volatile("sfence.vma zero, zero");
  __asm__ volatile("csrw mepc, %0" :: "r"((uint32_t)(uintptr_t)s_entry));
  __asm__ volatile("csrw mstatus, %0" ::
                   "r"((csr_read_mstatus() & ~MSTATUS_MPP) | MSTATUS_MPP_S));
}

static void put_hex32(uint32_t value) {
  static const char hex[] = "0123456789abcdef";
  for (int shift = 28; shift >= 0; shift -= 4)
    uart_putchar(hex[(value >> shift) & 0xfu]);
}

uint32_t *supervisor_trap(uint32_t *trap_frame) {
  const uint32_t cause = csr_read_scause();
  if (cause == SCAUSE_LOAD_ACCESS_FAULT && mmio_probe_active) {
    __asm__ volatile("csrw sepc, %0" ::
                     "r"((uint32_t)(uintptr_t)mmio_probe_fault));
    return trap_frame;
  }
  if (cause == SCAUSE_SUPERVISOR_SOFTWARE) {
    __asm__ volatile("csrw sip, zero");
    supervisor_ticks++;
    return trap_frame;
  }

  uart_puts("kernel: trap cause=");
  put_hex32(cause);
  uart_puts(" sepc=");
  put_hex32(csr_read_sepc());
  uart_puts(" stval=");
  put_hex32(csr_read_stval());
  uart_puts("\n");
  test_finish(1);
}

uint32_t kernel_uptime_ticks(void) {
  return supervisor_ticks;
}

uint32_t kernel_total_pages(void) {
  return allocator_total_pages;
}

uint32_t kernel_free_pages(void) {
  return page_free_count();
}

uint32_t kernel_task_snapshot(struct kernel_task_info *out, uint32_t capacity) {
  (void)out;
  (void)capacity;
  return 0;
}

static void allocator_self_test(void) {
  const uint32_t before = page_free_count();
  void *const page = page_alloc();
  if (before == 0 || page == 0 ||
      ((uintptr_t)page & (PAGE_SIZE - 1u)) != 0)
    test_finish(1);
  *(volatile uint32_t *)page = 0xa50a5a5au;
  if (*(volatile uint32_t *)page != 0xa50a5a5au) test_finish(1);
  page_free(page);
  if (page_free_count() != before) test_finish(1);
}

void kmain(void) {
  page_init();
  allocator_total_pages = page_free_count();
  allocator_self_test();
  role_init();
#if AX_EVOLUTION_CAPACITY > 0
  evolution_init();
#endif
#ifndef AXOS_HOSTLINK
  clint_arm_timer(2000u);
#endif
#ifdef AXOS_HOSTLINK
  host_service();
#else
  uart_puts("aXos: Primer monitor (32 KiB)\n");
  shell_run();
#endif
}
