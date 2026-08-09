#include <stdint.h>

#include "fs.h"
#include "hostlink.h"
#include "kernel_info.h"
#include "loader.h"
#include "page.h"
#include "platform.h"
#include "console.h"
#include "evolution.h"
#include "plic.h"
#include "process.h"
#include "role.h"
#include "scheduler.h"
#include "syscall.h"
#include "task.h"
#if AXOS_EMBED_USER
#include "userprog_image.h"
#endif
#include "vm.h"

enum {
  MSTATUS_MPP = 3u << 11,
  MSTATUS_MPP_S = 1u << 11,
  SSTATUS_SPIE = 1u << 5,
  TRAP_FRAME_BYTES = 128,
  SCAUSE_LOAD_ACCESS_FAULT = 5,
  SCAUSE_USER_ECALL = 8,
  SCAUSE_SUPERVISOR_SOFTWARE = 0x80000001u,
  SCAUSE_SUPERVISOR_EXTERNAL = 0x80000009u,
  USER_CODE_VA = 0x40000000u,
  USER_STACK_VA = USER_CODE_VA + PAGE_SIZE,
};

extern void s_entry(void);
extern void machine_timer_trap(void);
extern void user_entry(void);
extern void shell_run(void);
extern void mmio_probe_fault(void);
extern volatile uint32_t mmio_probe_active;

static volatile uint32_t supervisor_ticks;
static uint32_t allocator_total_pages;

static struct task tasks[TASK_SLOTS];
static uint32_t current_task = TASK_NONE;
static volatile uint32_t scheduler_running;
static volatile uint32_t scheduled_ticks;
static uint32_t next_pid = 1;
static volatile uint32_t console_mask;
/* The fork demo verifies itself through three console markers; a loaded program
 * reports through its exit code instead.  Without this the exit path would
 * apply the fork demo's assertion to every program that ever runs. */
static volatile uint32_t expect_fork_markers;
static uint32_t scheduler_free_pages;
static volatile uint32_t process_session_active;
static volatile uint32_t process_session_done;
static volatile uint32_t process_session_status;

struct supervisor_context {
  uint32_t *trap_frame;
  uint32_t sepc;
  uint32_t sstatus;
};

static struct supervisor_context supervisor_context;

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
  uint32_t copied = 0;
  if (out == 0) return 0;
  for (uint32_t i = 0; i < TASK_SLOTS && copied < capacity; ++i) {
    if (tasks[i].state == TASK_UNUSED) continue;
    out[copied].pid = tasks[i].pid;
    out[copied].parent_pid = tasks[i].parent_pid;
    out[copied].state = tasks[i].state;
    for (uint32_t j = 0; j < sizeof(out[copied].name); ++j)
      out[copied].name[j] = tasks[i].name[j];
    ++copied;
  }
  return copied;
}

#if !AXOS_EMBED_USER
/* Storage personalities load the first user program from AXFS instead of
 * carrying a second copy inside the kernel image. This staging buffer is BSS,
 * so it consumes runtime RAM but no boot-image sectors. */
enum { STORAGE_USER_IMAGE_MAX = 24u * 1024u };
static uint8_t storage_user_image[STORAGE_USER_IMAGE_MAX]
    __attribute__((aligned(4), section(".noinit")));
#endif

/* Kept below the 128 KiB SoC RAM limit and aligned to their physical pages.
 * The root maps the three superpages needed by the first kernel; test_finisher
 * occupies one 4 KiB L0 mapping because it is not 4 MiB aligned. */
static volatile uint32_t root_pt[1024] __attribute__((aligned(4096)));
static volatile uint32_t low_pt[1024] __attribute__((aligned(4096)));

static inline uint32_t csr_read_mstatus(void) {
  uint32_t value;
  __asm__ volatile("csrr %0, mstatus" : "=r"(value));
  return value;
}

static inline void csr_write_mstatus(uint32_t value) {
  __asm__ volatile("csrw mstatus, %0" :: "r"(value));
}

static inline uint32_t csr_read_sp(void) {
  uint32_t value;
  __asm__ volatile("mv %0, sp" : "=r"(value));
  return value;
}

static inline void csr_write_mepc(uint32_t value) {
  __asm__ volatile("csrw mepc, %0" :: "r"(value));
}

static inline void csr_write_satp(uint32_t value) {
  __asm__ volatile("csrw satp, %0" :: "r"(value));
}

static inline void csr_write_mtvec(uint32_t value) {
  __asm__ volatile("csrw mtvec, %0" :: "r"(value));
}

static inline void csr_write_mscratch(uint32_t value) {
  __asm__ volatile("csrw mscratch, %0" :: "r"(value));
}

static inline void csr_write_mideleg(uint32_t value) {
  __asm__ volatile("csrw mideleg, %0" :: "r"(value));
}

static inline void csr_write_medeleg(uint32_t value) {
  __asm__ volatile("csrw medeleg, %0" :: "r"(value));
}

static inline void csr_write_mie(uint32_t value) {
  __asm__ volatile("csrw mie, %0" :: "r"(value));
}

static inline uint32_t csr_read_scause(void) {
  uint32_t value;
  __asm__ volatile("csrr %0, scause" : "=r"(value));
  return value;
}
static inline uint32_t csr_read_stval(void) {
  uint32_t value;
  __asm__ volatile("csrr %0, stval" : "=r"(value));
  return value;
}

static inline uint32_t csr_read_sepc(void) {
  uint32_t value;
  __asm__ volatile("csrr %0, sepc" : "=r"(value));
  return value;
}

static inline void csr_write_sepc(uint32_t value) {
  __asm__ volatile("csrw sepc, %0" :: "r"(value));
}

static inline uint32_t csr_read_sstatus(void) {
  uint32_t value;
  __asm__ volatile("csrr %0, sstatus" : "=r"(value));
  return value;
}

static inline void csr_write_sstatus(uint32_t value) {
  __asm__ volatile("csrw sstatus, %0" :: "r"(value));
}

static inline void csr_write_sip(uint32_t value) {
  __asm__ volatile("csrw sip, %0" :: "r"(value));
}

static void clint_arm_timer(uint32_t delta) {
  volatile uint32_t *const mtimecmp =
      (volatile uint32_t *)(uintptr_t)(AX_CLINT_BASE + 0x4000u);
  volatile const uint32_t *const mtime =
      (volatile const uint32_t *)(uintptr_t)(AX_CLINT_BASE + 0xbff8u);
  mtimecmp[1] = 0xffffffffu;
  mtimecmp[0] = *mtime + delta;
  mtimecmp[1] = 0;
}

void m_setup(void) {
  vm_bootstrap_map(root_pt, low_pt);
  csr_write_mscratch(csr_read_sp());
  csr_write_mtvec((uint32_t)(uintptr_t)machine_timer_trap);
  csr_write_medeleg((1u << SCAUSE_LOAD_ACCESS_FAULT) |
                    (1u << SCAUSE_USER_ECALL));
  /* MTIP stays M-owned; the shim raises delegated SSIP for S-mode policy.
   * SEI is genuinely delegatable, so the PLIC's S-mode context reaches the
   * kernel directly: no M-mode round trip, and the same claim/complete code
   * runs on QEMU, whose virt machine wires context 1 the same way. */
  csr_write_mideleg((1u << 1) | (1u << 9));
  csr_write_mie((1u << 7) | (1u << 1) | (1u << 9));
  /* Keep interrupts out of the bootstrap; kmain arms the first scheduler tick
   * once its allocator, task state, and trap stacks are all ready. */
  clint_arm_timer(0x00100000u);
  csr_write_satp(vm_root_satp(root_pt));
  __asm__ volatile("sfence.vma zero, zero");
  csr_write_mepc((uint32_t)(uintptr_t)s_entry);
  csr_write_mstatus((csr_read_mstatus() & ~MSTATUS_MPP) | MSTATUS_MPP_S);
}

static uint32_t *sys_fork(uint32_t *trap_frame);
static uint32_t *sys_wait(uint32_t *trap_frame, uint32_t status_va);
static uint32_t *sys_exit(uint32_t *trap_frame, uint32_t code);
static const struct syscall_ops kernel_ops;

static uint32_t *resume_supervisor_context(void) {
  uint32_t *const frame = supervisor_context.trap_frame;
  if (frame == 0) test_finish(1);
  csr_write_satp(vm_root_satp(root_pt));
  __asm__ volatile("sfence.vma zero, zero");
  csr_write_sepc(supervisor_context.sepc);
  csr_write_sstatus(supervisor_context.sstatus);
  supervisor_context.trap_frame = 0;
  current_task = TASK_NONE;
  return frame;
}

static uint32_t *schedule(uint32_t *trap_frame) {
  if (!scheduler_running) return trap_frame;

  if (current_task != TASK_NONE && tasks[current_task].state == TASK_RUNNING) {
    tasks[current_task].trap_frame = trap_frame;
    tasks[current_task].sepc = csr_read_sepc();
    tasks[current_task].sstatus = csr_read_sstatus();
    tasks[current_task].state = TASK_RUNNABLE;
  }
  const uint32_t next = scheduler_select(tasks, TASK_SLOTS, current_task);
  if (next == TASK_NONE) return resume_supervisor_context();
  current_task = next;
  tasks[current_task].state = TASK_RUNNING;
  csr_write_satp(tasks[current_task].satp);
  __asm__ volatile("sfence.vma zero, zero");
  csr_write_sepc(tasks[current_task].sepc);
  csr_write_sstatus(tasks[current_task].sstatus);
  return tasks[current_task].trap_frame;
}

uint32_t *supervisor_trap(uint32_t *trap_frame) {
  const uint32_t cause = csr_read_scause();

  if (cause == SCAUSE_LOAD_ACCESS_FAULT && mmio_probe_active) {
    csr_write_sepc((uint32_t)(uintptr_t)mmio_probe_fault);
    return trap_frame;
  }

  /* Device interrupts arrive here through the PLIC's S-mode context. The
   * dispatcher claims, hands the source to its device, and completes; it does
   * not schedule, because a device completion says nothing about which task
   * should run next. The interrupted context resumes and observes whatever
   * flag its device handler set. */
  if (cause == SCAUSE_SUPERVISOR_EXTERNAL) {
    plic_dispatch();
    return trap_frame;
  }

  if (cause == SCAUSE_SUPERVISOR_SOFTWARE) {
    /* M-mode turns MTIP into delegated SSIP for scheduler policy. */
    csr_write_sip(0);
    supervisor_ticks++;
    if (!scheduler_running) return trap_frame;
    if (current_task == TASK_NONE && supervisor_context.trap_frame == 0) {
      supervisor_context.trap_frame = trap_frame;
      supervisor_context.sepc = csr_read_sepc();
      supervisor_context.sstatus = csr_read_sstatus();
    }
    scheduled_ticks++;
    return schedule(trap_frame);
  }

  if (cause == SCAUSE_USER_ECALL) return syscall_dispatch(trap_frame, &kernel_ops);

  static const char hex[] = "0123456789abcdef";
  uart_puts("kernel: trap cause=");
  for (int shift = 28; shift >= 0; shift -= 4)
    uart_putchar(hex[(cause >> shift) & 0xfu]);
  uart_puts(" sepc=");
  const uint32_t fault_pc = csr_read_sepc();
  for (int shift = 28; shift >= 0; shift -= 4)
    uart_putchar(hex[(fault_pc >> shift) & 0xfu]);
  uart_puts(" stval=");
  const uint32_t fault_value = csr_read_stval();
  for (int shift = 28; shift >= 0; shift -= 4)
    uart_putchar(hex[(fault_value >> shift) & 0xfu]);
  uart_puts("\n");
  test_finish(1);
}

static void page_allocator_self_test(void) {
  void *pages[32];
  const uint32_t total = page_free_count();

  if (total == 0 || total > (sizeof(pages) / sizeof(pages[0]))) test_finish(1);
  for (uint32_t i = 0; i < total; ++i) {
    pages[i] = page_alloc();
    if (pages[i] == 0 || ((uintptr_t)pages[i] & (PAGE_SIZE - 1u)))
      test_finish(1);
    *(volatile uint32_t *)pages[i] = 0xa50a0000u | i;
    if (*(volatile uint32_t *)pages[i] != (0xa50a0000u | i)) test_finish(1);
  }
  if (page_alloc() != 0 || page_free_count() != 0) test_finish(1);
  while (total != page_free_count()) page_free(pages[page_free_count()]);
}

static void task_release(struct task *task) {
  const uint32_t slot = (uint32_t)(task - tasks);
  vm_destroy_user_space(task);
  page_free(task->kernel_stack);
  syscall_task_reset(slot);
  task->kernel_stack = 0;
  task->state = TASK_UNUSED;
  task->pid = 0;
  task->parent_pid = 0;
  task->exit_status = 0;
  task->wait_status_va = 0;
  for (uint32_t i = 0; i < sizeof(task->name); ++i) task->name[i] = 0;
}

static void task_set_name(struct task *task, const char *name) {
  uint32_t i = 0;
  for (; i + 1u < sizeof(task->name) && name[i]; ++i)
    task->name[i] = name[i];
  for (; i < sizeof(task->name); ++i) task->name[i] = 0;
}

/* Create a task from a loaded ELF rather than from the built-in payload.
 *
 * The difference from scheduler_make_user_task is entirely in where the code,
 * the entry point, and the stack come from: this one has no compiled-in
 * knowledge of the program at all, which is what makes it a loader test.  The
 * exit status the program chooses becomes the test's verdict. */
static int scheduler_make_loaded_task(uint32_t slot, const char *name,
                                      const uint8_t *image, uint32_t size,
                                      uint32_t argc,
                                      const char *const argv[]) {
  syscall_task_reset(slot);
  if (vm_create_empty_user_space(&tasks[slot], root_pt))
    return KERNEL_RUN_ELOAD;

  uint32_t entry = 0;
  uint32_t sp = 0;
  const int rc = loader_load_args(&tasks[slot], image, size, argc, argv,
                                  &entry, &sp);
  if (rc != 0) {
    vm_destroy_user_space(&tasks[slot]);
    return KERNEL_RUN_ELOAD;
  }

  uint32_t *const kernel_stack = page_alloc();
  if (kernel_stack == 0) {
    vm_destroy_user_space(&tasks[slot]);
    return KERNEL_RUN_ELOAD;
  }
  uint32_t *const frame =
      (uint32_t *)((uintptr_t)kernel_stack + PAGE_SIZE - TRAP_FRAME_BYTES);
  for (uint32_t i = 0; i < TRAP_FRAME_BYTES / sizeof(*frame); ++i) frame[i] = 0;
  tasks[slot].trap_frame = frame;
  tasks[slot].kernel_stack = kernel_stack;
  tasks[slot].state = TASK_RUNNABLE;
  tasks[slot].pid = next_pid++;
  tasks[slot].parent_pid = 0;
  tasks[slot].exit_status = 0;
  tasks[slot].wait_status_va = 0;
  task_set_name(&tasks[slot], name);
  tasks[slot].sepc = entry;
  /* Nothing is passed in registers: docs/abi.md puts everything on the stack,
   * so a program must not read a0 at entry. */
  frame[TRAP_FRAME_SP] = sp;
  tasks[slot].sstatus = SSTATUS_SPIE;
  return 0;
}

static void scheduler_make_user_task(uint32_t slot) {
  syscall_task_reset(slot);
  if (vm_create_user_space(&tasks[slot], root_pt)) test_finish(1);
  uint32_t *const kernel_stack = page_alloc();
  if (kernel_stack == 0) {
    vm_destroy_user_space(&tasks[slot]);
    test_finish(1);
  }
  uint32_t *const frame =
      (uint32_t *)((uintptr_t)kernel_stack + PAGE_SIZE - TRAP_FRAME_BYTES);
  for (uint32_t i = 0; i < TRAP_FRAME_BYTES / sizeof(*frame); ++i) frame[i] = 0;
  tasks[slot].trap_frame = frame;
  tasks[slot].kernel_stack = kernel_stack;
  tasks[slot].state = TASK_RUNNABLE;
  tasks[slot].pid = next_pid++;
  tasks[slot].parent_pid = 0;
  tasks[slot].exit_status = 0;
  tasks[slot].wait_status_va = 0;
  task_set_name(&tasks[slot], "fork-demo");
  tasks[slot].sepc = USER_CODE_VA +
      ((uint32_t)(uintptr_t)user_entry & (PAGE_SIZE - 1u));
  frame[TRAP_FRAME_A0] = slot;
  frame[TRAP_FRAME_SP] = USER_STACK_VA + PAGE_SIZE;
  /* SRET returns to U mode with supervisor interrupts enabled. */
  tasks[slot].sstatus = SSTATUS_SPIE;
}

/* --- services the syscall component calls back into -------------------------
 * These are kernel policy: how a task is duplicated, how the console is driven,
 * how a user pointer is validated.  The ABI component decides which syscall
 * number reaches which of them, and nothing here knows a syscall number. */

static uint32_t k_getpid(void) {
  return current_task == TASK_NONE ? 0u : tasks[current_task].pid;
}

static uint32_t k_task_slot(void) {
  return current_task;
}

/* brk(addr): move the program break, returning the resulting break.
 *
 * The kernel-level contract is not the libc one: brk reports failure by
 * returning the break *unchanged*, never by an errno, and brk(0) is the query.
 * A libc's sbrk is written against exactly that.
 *
 * Growing maps zero-filled pages; shrinking unmaps and frees whole pages that
 * fall entirely above the new break.  Pages are the granularity, so a break in
 * the middle of a page keeps that page mapped -- shrinking below it later frees
 * it, and a partial page is never handed back while any of it is still in
 * use. */
static uint32_t k_brk(uint32_t addr) {
  if (current_task == TASK_NONE) return 0u;
  struct task *const task = &tasks[current_task];
  if (addr == 0u) return task->brk;
  if (addr < task->brk) {
    /* Shrink: free every page wholly above the new break. */
    const uint32_t first = (addr + PAGE_SIZE - 1u) & ~(PAGE_SIZE - 1u);
    for (uint32_t va = first; va < task->brk; va += PAGE_SIZE)
      vm_unmap_user_page(task, va);
    task->brk = addr;
    return task->brk;
  }
  if (addr > task->brk_limit) return task->brk;   /* would meet the stack */

  for (uint32_t va = (task->brk + PAGE_SIZE - 1u) & ~(PAGE_SIZE - 1u);
       va < addr; va += PAGE_SIZE) {
    if (vm_translate_user(task, va, 0) != 0) continue;   /* already mapped */
    uint32_t *const page = page_alloc();
    if (page == 0) return task->brk;                     /* out of memory */
    for (uint32_t i = 0; i < PAGE_SIZE / sizeof(*page); ++i) page[i] = 0;
    if (vm_map_user_page(task, va, page, VM_R | VM_W) != 0) {
      page_free(page);
      return task->brk;
    }
  }
  task->brk = addr;
  return task->brk;
}

static void k_console_putc(char c) {
  uart_putchar(c);
  /* The fork demo's completion check watches for its three markers. */
  if (c == 'P') console_mask |= 1u;
  else if (c == 'C') console_mask |= 2u;
  else if (c == 'W') console_mask |= 4u;
}

/* Byte-at-a-time so a buffer spanning a page boundary is handled without the
 * caller knowing, and so a partial copy stops exactly at the first bad page
 * rather than being all-or-nothing. */
static int k_copy_from_user(void *dst, uint32_t user_va, uint32_t len) {
  if (current_task == TASK_NONE) return 0;
  uint8_t *out = dst;
  for (uint32_t i = 0; i < len; ++i) {
    const uint8_t *src = vm_translate_user(&tasks[current_task], user_va + i, 0);
    if (src == 0) return 0;
    out[i] = *src;
  }
  return 1;
}

static int copy_to_task(struct task *task, uint32_t user_va, const void *src,
                        uint32_t len) {
  const uint8_t *in = src;
  for (uint32_t i = 0; i < len; ++i) {
    uint8_t *dst = vm_translate_user(task, user_va + i, 1);
    if (dst == 0) return 0;
    *dst = in[i];
  }
  return 1;
}

static int k_copy_to_user(uint32_t user_va, const void *src, uint32_t len) {
  if (current_task == TASK_NONE) return 0;
  return copy_to_task(&tasks[current_task], user_va, src, len);
}

/* --- files ------------------------------------------------------------------
 * Thin passthrough to the selected filesystem component.  The mount is done
 * here rather than assumed, so a personality that never runs the shell (the
 * host-managed one) still has files; fs_mount is idempotent, so the cost after
 * the first call is a compare. */

static int k_file_open(const char *name) {
  if (fs_mount() < 0) return -1;
  return fs_lookup(name);
}

static int32_t k_file_size(int file) { return fs_size(file); }

static int32_t k_file_read(int file, uint32_t offset, void *dst, uint32_t len) {
  return fs_read(file, offset, dst, len);
}

static void k_role_info(uint32_t info[3]) {
  const uint32_t id = role_discover();
  info[0] = id;
  info[1] = id == 0 ? 0 : role_version();
  info[2] = role_capabilities(id);
}

static int k_role_execute(uint32_t op, const uint8_t *request,
                          uint32_t request_len, uint8_t *response,
                          uint32_t response_cap, uint32_t *response_len) {
  return role_execute(op, request, request_len, response, response_cap,
                      response_len);
}

static const struct syscall_ops kernel_ops = {
    .fork = sys_fork,
    .wait = sys_wait,
    .exit = sys_exit,
    .getpid = k_getpid,
    .task_slot = k_task_slot,
    .brk = k_brk,
    .console_putc = k_console_putc,
    .copy_from_user = k_copy_from_user,
    .copy_to_user = k_copy_to_user,
    .file_open = k_file_open,
    .file_size = k_file_size,
    .file_read = k_file_read,
    .role_info = k_role_info,
    .role_execute = k_role_execute,
};

static uint32_t *sys_fork(uint32_t *trap_frame) {
  if (current_task == TASK_NONE || tasks[current_task].state != TASK_RUNNING)
    test_finish(1);
  uint32_t slot = TASK_NONE;
  for (uint32_t i = 0; i < TASK_SLOTS; ++i)
    if (tasks[i].state == TASK_UNUSED) { slot = i; break; }
  if (slot == TASK_NONE) test_finish(1);

  struct task *const parent = &tasks[current_task];
  struct task *const child = &tasks[slot];
  if (vm_clone_user_space(child, parent)) test_finish(1);
  uint32_t *const kernel_stack = page_alloc();
  if (kernel_stack == 0) {
    vm_destroy_user_space(child);
    test_finish(1);
  }
  uint32_t *const frame =
      (uint32_t *)((uintptr_t)kernel_stack + PAGE_SIZE - TRAP_FRAME_BYTES);
  for (uint32_t i = 0; i < TRAP_FRAME_BYTES / sizeof(*frame); ++i)
    frame[i] = trap_frame[i];
  if (child->user_stack[0] != 0x51a00001u) test_finish(1);
  child->trap_frame = frame;
  child->sepc = csr_read_sepc() + 4u;
  child->sstatus = csr_read_sstatus();
  child->kernel_stack = kernel_stack;
  child->state = TASK_RUNNABLE;
  child->pid = next_pid++;
  child->parent_pid = parent->pid;
  child->exit_status = 0;
  child->wait_status_va = 0;
  task_set_name(child, parent->name);
  syscall_task_clone(slot, current_task);
  frame[TRAP_FRAME_A0] = 0;
  trap_frame[TRAP_FRAME_A0] = child->pid;
  csr_write_sepc(child->sepc);
  return trap_frame;
}

static uint32_t *sys_wait(uint32_t *trap_frame, uint32_t status_va) {
  if (current_task == TASK_NONE) test_finish(1);
  struct task *const parent = &tasks[current_task];
  int has_child = 0;
  for (uint32_t i = 0; i < TASK_SLOTS; ++i) {
    struct task *const child = &tasks[i];
    if (child->parent_pid != parent->pid) continue;
    has_child = 1;
    if (child->state == TASK_ZOMBIE) {
      const uint32_t status = (child->exit_status & 0xffu) << 8;
      if (status_va != 0 &&
          !copy_to_task(parent, status_va, &status, sizeof(status))) {
        trap_frame[TRAP_FRAME_A0] = (uint32_t)(-(int32_t)AX_EFAULT);
        csr_write_sepc(csr_read_sepc() + 4u);
        return trap_frame;
      }
      trap_frame[TRAP_FRAME_A0] = child->pid;
      task_release(child);
      csr_write_sepc(csr_read_sepc() + 4u);
      return trap_frame;
    }
  }
  if (!has_child) {
    trap_frame[TRAP_FRAME_A0] = (uint32_t)(-(int32_t)AX_ECHILD);
    csr_write_sepc(csr_read_sepc() + 4u);
    return trap_frame;
  }
  parent->trap_frame = trap_frame;
  parent->sepc = csr_read_sepc() + 4u;
  parent->sstatus = csr_read_sstatus();
  parent->state = TASK_BLOCKED;
  parent->wait_status_va = status_va;
  return schedule(trap_frame);
}

static uint32_t *sys_exit(uint32_t *trap_frame, uint32_t code) {
  if (current_task == TASK_NONE) test_finish(1);
  struct task *const task = &tasks[current_task];
  /* Descriptors and any uncollected role result stop belonging to the process
   * at exit, not later when its parent happens to reap the zombie. */
  syscall_task_reset(current_task);
  task->exit_status = code;
  task->state = TASK_ZOMBIE;
  for (uint32_t i = 0; i < TASK_SLOTS; ++i) {
    struct task *const parent = &tasks[i];
    if (parent->state == TASK_BLOCKED && parent->pid == task->parent_pid) {
      const uint32_t status = (code & 0xffu) << 8;
      const int status_ok = parent->wait_status_va == 0 ||
          copy_to_task(parent, parent->wait_status_va, &status, sizeof(status));
      parent->trap_frame[TRAP_FRAME_A0] = status_ok ? task->pid :
          (uint32_t)(-(int32_t)AX_EFAULT);
      parent->wait_status_va = 0;
      parent->state = TASK_RUNNABLE;
      if (status_ok) task_release(task);
      return schedule(trap_frame);
    }
  }
  if (task->parent_pid != 0) return schedule(trap_frame);
  process_session_status = code;
  task_release(task);
  if (expect_fork_markers && console_mask != 7u) test_finish(1);
  /* Every page the task used must come back, for either demo: a loader that
   * leaks a segment page fails here rather than silently. */
  if (page_free_count() != scheduler_free_pages) {
    uart_puts("kernel: process page leak\n");
    test_finish(1);
  }
  scheduler_running = 0;
  process_session_done = 1;
  return resume_supervisor_context();
}

static int process_session_wait(void) {
  if (process_session_active) return KERNEL_RUN_EBUSY;
  process_session_active = 1;
  process_session_done = 0;
  process_session_status = 0;
  supervisor_context.trap_frame = 0;
  scheduler_running = 1;
  clint_arm_timer(2000);
  while (!process_session_done) __asm__ volatile("wfi");
  process_session_active = 0;
  return (int)process_session_status;
}

static int scheduler_start(void) {
  scheduler_free_pages = page_free_count();
  scheduler_make_user_task(0);
  return process_session_wait();
}

int kernel_fork_demo(void) {
  expect_fork_markers = 1;
  console_mask = 0;
  return scheduler_start();
}

#if AXOS_EMBED_USER
static int same_name(const char *a, const char *b) {
  while (*a && *a == *b) { ++a; ++b; }
  return *a == *b;
}
#endif

int kernel_run_program(const char *name, uint32_t argc,
                       const char *const argv[]) {
  if (process_session_active) return KERNEL_RUN_EBUSY;
  expect_fork_markers = 0;
  scheduler_free_pages = page_free_count();
#if AXOS_EMBED_USER
  if (!same_name(name, "hello.elf")) return KERNEL_RUN_ENOENT;
  const int loaded = scheduler_make_loaded_task(
      0, name, axos_user_image, axos_user_image_size, argc, argv);
#else
  (void)fs_mount();
  const int file = fs_lookup(name);
  const int32_t size = file < 0 ? -1 : fs_size(file);
  if (size <= 0) return KERNEL_RUN_ENOENT;
  if ((uint32_t)size > sizeof(storage_user_image)) return KERNEL_RUN_ETOOBIG;
  if (fs_read(file, 0, storage_user_image, (uint32_t)size) != size)
    return KERNEL_RUN_ELOAD;
  const int loaded = scheduler_make_loaded_task(
      0, name, storage_user_image, (uint32_t)size, argc, argv);
#endif
  if (loaded != 0) return loaded;
  return process_session_wait();
}

void kmain(void) {
  page_init();
  allocator_total_pages = page_free_count();
  page_allocator_self_test();
  plic_init();
  role_init();
#if AX_EVOLUTION_CAPABILITIES != 0
  evolution_init();
#endif
  /* Interrupt policy lives here rather than in the drivers: the kernel owns
   * the controller, so it decides that role completion is source 2 on its own
   * S-mode context.  With no controller (the ISS) or no accelerator, nothing
   * is routed and the role driver keeps polling. */
  if (plic_present() && role_discover() != 0) {
    plic_route(AX_PLIC_SRC_ROLE, 1u);
    role_enable_irq();
  }
#ifdef AXOS_HOSTLINK
  /* Host-managed personality: the console byte pipe carries the host-link
   * protocol instead of the interactive shell (DESIGN.md §3.3). */
  /* Deliberately no console_init() here.  Routing the UART source would put an
   * interrupt handler on the receive path, and host_service reads the device
   * directly -- two readers of a one-byte holding register, where the handler
   * always wins and the poller starves.  A streaming transport has no idle to
   * reclaim anyway; the console interrupt exists for the shell, which waits on
   * a human. */
  host_service();
#else
  /* Deliberately no timer armed here.  Starting the tick before the shell
   * would give the console a guaranteed wake source and let it park on the
   * very first read -- but the M-mode shim re-arms at a fixed 2000-cycle
   * period, so the tick would then also fire throughout every shell command.
   * Measured, that cost more than it saved: `exec`, the AXFS write/readback,
   * and the full aXos profile all overran their cycle bounds.  The console
   * driver instead parks once the console interrupt has proved itself, which
   * the first keystroke does. */
  console_init();
  shell_run();
#endif
}
