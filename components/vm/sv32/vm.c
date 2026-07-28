#include <stdint.h>

#include "page.h"
#include "platform.h"
#include "task.h"
#include "vm.h"

enum {
  PTE_V = 1u << 0,
  PTE_R = 1u << 1,
  PTE_W = 1u << 2,
  PTE_X = 1u << 3,
  PTE_U = 1u << 4,
  PTE_A = 1u << 6,
  PTE_D = 1u << 7,
  /* Sv32 reserves bits 8-9 for supervisor software.  Bit 8 records that the
   * address space owns the physical page and must free it on teardown.
   *
   * Ownership has to be tracked rather than assumed: the built-in payload's
   * code page is part of the kernel image (.usertext) and freeing it would
   * hand kernel memory to the page allocator, while every page a loader maps
   * came from page_alloc and leaks if it is not freed. */
  PTE_OWNED = 1u << 8,
  SATP_MODE_SV32 = 1u << 31,
  USER_CODE_VA = 0x40000000u,
  USER_STACK_VA = USER_CODE_VA + PAGE_SIZE,
};

extern void user_entry(void);

static uint32_t pte_leaf(uint32_t physical, uint32_t flags) {
  return ((physical >> 12) << 10) | flags;
}

static uint32_t pte_pointer(uint32_t physical) {
  return ((physical >> 12) << 10) | PTE_V;
}

static void zero_page(uint32_t *page) {
  for (uint32_t i = 0; i < PAGE_SIZE / sizeof(*page); ++i) page[i] = 0;
}

static void copy_page(uint32_t *dst, const uint32_t *src) {
  for (uint32_t i = 0; i < PAGE_SIZE / sizeof(*dst); ++i) dst[i] = src[i];
}

void vm_bootstrap_map(volatile uint32_t *root_pt, volatile uint32_t *low_pt) {
  const uint32_t kernel_flags = PTE_V | PTE_R | PTE_W | PTE_X | PTE_A | PTE_D;
  const uint32_t device_flags = PTE_V | PTE_R | PTE_W | PTE_A | PTE_D;

  /* Kernel RAM, UART, and CLINT are superpages. The finisher uses one L0 PTE. */
  root_pt[0x200] = pte_leaf(0x80000000u, kernel_flags);
  root_pt[0x040] = pte_leaf(AX_UART_BASE, device_flags);
  root_pt[0x008] = pte_leaf(AX_CLINT_BASE, device_flags);
  /* Accelerator role window: map its physical 0x4000_0000 aperture through a
   * kernel-only 0x5000_0000 alias.  User executables occupy virtual
   * 0x4000_0000, and syscalls keep the process page table active, so an
   * identity mapping would make the driver read user text instead of MMIO. */
  root_pt[AX_ROLE_KERNEL_BASE >> 22] =
      pte_leaf(AX_ROLE_BASE, device_flags);
  root_pt[0x000] = pte_pointer((uint32_t)(uintptr_t)low_pt);
  low_pt[(AX_TEST_BASE >> 12) & 0x3ffu] = pte_leaf(AX_TEST_BASE, device_flags);
}

uint32_t vm_root_satp(const volatile uint32_t *root_pt) {
  return SATP_MODE_SV32 | ((uint32_t)(uintptr_t)root_pt >> 12);
}

/* The shared prologue: a page root carrying the kernel mappings plus one empty
 * L0 table for the user 4 MiB.  Both the built-in payload and the loader start
 * from this and differ only in what they map into it. */
static int user_space_skeleton(struct task *task,
                               const volatile uint32_t *root_pt) {
  uint32_t *const page_root = page_alloc();
  uint32_t *const user_pt = page_alloc();
  if (page_root == 0 || user_pt == 0) {
    if (page_root != 0) page_free(page_root);
    if (user_pt != 0) page_free(user_pt);
    return -1;
  }
  zero_page(page_root);
  zero_page(user_pt);
  for (uint32_t i = 0; i < 1024u; ++i) page_root[i] = root_pt[i];
  page_root[USER_CODE_VA >> 22] = pte_pointer((uint32_t)(uintptr_t)user_pt);
  task->page_root = page_root;
  task->user_pt = user_pt;
  task->user_stack = 0;
  task->satp = vm_root_satp(page_root);
  return 0;
}

int vm_create_empty_user_space(struct task *task,
                               const volatile uint32_t *root_pt) {
  return user_space_skeleton(task, root_pt);
}

int vm_map_user_page(struct task *task, uint32_t user_va, void *page,
                     uint32_t perms) {
  if (task == 0 || task->user_pt == 0 || page == 0) return -1;
  /* Outside the single L0 table's 4 MiB there is nowhere to put the entry. */
  if ((user_va >> 22) != (USER_CODE_VA >> 22)) return -1;
  if ((user_va & (PAGE_SIZE - 1u)) != 0) return -1;

  uint32_t flags = PTE_V | PTE_U | PTE_A | PTE_OWNED;
  if (perms & VM_R) flags |= PTE_R;
  if (perms & VM_W) flags |= PTE_W | PTE_D;
  if (perms & VM_X) flags |= PTE_X;
  /* A page with no access bits is a pointer PTE, not a leaf: refuse rather
   * than silently creating a dangling second-level pointer. */
  if (!(flags & (PTE_R | PTE_W | PTE_X))) return -1;

  task->user_pt[(user_va >> 12) & 0x3ffu] =
      pte_leaf((uint32_t)(uintptr_t)page, flags);
  return 0;
}

int vm_create_user_space(struct task *task, const volatile uint32_t *root_pt) {
  uint32_t *const page_root = page_alloc();
  uint32_t *const user_pt = page_alloc();
  uint32_t *const user_stack = page_alloc();
  if (page_root == 0 || user_pt == 0 || user_stack == 0) {
    if (page_root != 0) page_free(page_root);
    if (user_pt != 0) page_free(user_pt);
    if (user_stack != 0) page_free(user_stack);
    return -1;
  }
  zero_page(page_root);
  zero_page(user_pt);
  for (uint32_t i = 0; i < 1024u; ++i) page_root[i] = root_pt[i];
  page_root[USER_CODE_VA >> 22] = pte_pointer((uint32_t)(uintptr_t)user_pt);
  user_pt[0] = pte_leaf((uint32_t)(uintptr_t)user_entry & ~(PAGE_SIZE - 1u),
                        PTE_V | PTE_R | PTE_X | PTE_U | PTE_A);
  user_pt[1] = pte_leaf((uint32_t)(uintptr_t)user_stack,
                        PTE_V | PTE_R | PTE_W | PTE_U | PTE_A | PTE_D | PTE_OWNED);
  task->page_root = page_root;
  task->user_pt = user_pt;
  task->user_stack = user_stack;
  task->satp = vm_root_satp(page_root);
  return 0;
}

int vm_clone_user_space(struct task *child, const struct task *parent) {
  uint32_t *const page_root = page_alloc();
  uint32_t *const user_pt = page_alloc();
  uint32_t *const user_stack = page_alloc();
  if (page_root == 0 || user_pt == 0 || user_stack == 0) {
    if (page_root != 0) page_free(page_root);
    if (user_pt != 0) page_free(user_pt);
    if (user_stack != 0) page_free(user_stack);
    return -1;
  }
  copy_page(page_root, parent->page_root);
  copy_page(user_pt, parent->user_pt);
  copy_page(user_stack, parent->user_stack);
  user_pt[1] = pte_leaf((uint32_t)(uintptr_t)user_stack,
                        PTE_V | PTE_R | PTE_W | PTE_U | PTE_A | PTE_D | PTE_OWNED);
  page_root[USER_CODE_VA >> 22] = pte_pointer((uint32_t)(uintptr_t)user_pt);
  child->page_root = page_root;
  child->user_pt = user_pt;
  child->user_stack = user_stack;
  child->satp = vm_root_satp(page_root);
  return 0;
}

void *vm_translate_user(const struct task *task, uint32_t user_va, int write) {
  if (task == 0 || task->page_root == 0) return 0;
  /* Sv32 walk: root index, then either a 4 MiB leaf or an L0 table.  RAM is
   * identity-mapped for the kernel, so a physical address is directly usable
   * once the walk has proved the page is user-accessible. */
  const uint32_t l1 = task->page_root[user_va >> 22];
  if (!(l1 & PTE_V)) return 0;

  uint32_t leaf;
  uint32_t page_offset;
  if (l1 & (PTE_R | PTE_X)) {           /* megapage leaf */
    leaf = l1;
    page_offset = user_va & 0x3fffffu;
  } else {
    const uint32_t *const l0 = (const uint32_t *)(uintptr_t)((l1 >> 10) << 12);
    leaf = l0[(user_va >> 12) & 0x3ffu];
    if (!(leaf & PTE_V)) return 0;
    page_offset = user_va & (PAGE_SIZE - 1u);
  }
  /* U must be set: a kernel page reachable only because it shares the address
   * space is not a legal target for a syscall pointer. */
  if (!(leaf & PTE_U) || !(leaf & PTE_R)) return 0;
  if (write && !(leaf & PTE_W)) return 0;
  return (void *)(uintptr_t)((((leaf >> 10) << 12)) + page_offset);
}

void vm_unmap_user_page(struct task *task, uint32_t user_va) {
  if (task == 0 || task->user_pt == 0) return;
  if ((user_va >> 22) != (USER_CODE_VA >> 22)) return;
  const uint32_t idx = (user_va >> 12) & 0x3ffu;
  const uint32_t pte = task->user_pt[idx];
  if (!(pte & PTE_V)) return;
  if (pte & PTE_OWNED) page_free((void *)(uintptr_t)((pte >> 10) << 12));
  task->user_pt[idx] = 0;
  /* The caller is responsible for the TLB: the kernel fences on the next
   * context switch, and a task cannot observe its own stale entry because it is
   * not running while the kernel services its syscall. */
}

void vm_destroy_user_space(struct task *task) {
  /* Free every page the address space owns.  Walking the leaves is what makes
   * this work for a loaded program, whose segment count is not known here --
   * the fixed three-pointer teardown only ever freed the built-in layout and
   * leaked everything a loader mapped. */
  if (task->user_pt != 0) {
    for (uint32_t i = 0; i < 1024u; ++i) {
      const uint32_t pte = task->user_pt[i];
      if ((pte & PTE_V) && (pte & PTE_OWNED))
        page_free((void *)(uintptr_t)((pte >> 10) << 12));
      task->user_pt[i] = 0;
    }
  }
  if (task->page_root != 0) page_free(task->page_root);
  if (task->user_pt != 0) page_free(task->user_pt);
  task->page_root = 0;
  task->user_pt = 0;
  task->user_stack = 0;
  task->satp = 0;
}
