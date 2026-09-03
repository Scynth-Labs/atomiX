/* loader.elf32: load a static ELF32 RISC-V executable into a user address
 * space (docs/abi.md).
 *
 * Why ELF rather than a flat image: it is what the toolchain already emits, and
 * it carries per-segment permissions.  A flat image would force every segment
 * to share one permission set -- writable text, or read-only data -- which
 * throws away the protection the MMU exists to provide.
 *
 * Scope is deliberately static executables (ET_EXEC) with no dynamic linking:
 * PT_DYNAMIC, PT_INTERP, and relocations are not handled, and an image needing
 * them is rejected rather than half-loaded.  That is the whole of what a
 * statically linked libc produces.
 *
 * All multi-byte reads go through byte-wise little-endian helpers.  The image
 * is a byte array whose alignment the loader does not control, and an unaligned
 * 32-bit load is a trap on this machine -- reading the header must not be able
 * to fault the kernel. */
#include <stdint.h>

#include "loader.h"
#include "page.h"
#include "vm.h"

enum {
  ET_EXEC = 2,
  EM_RISCV = 243,
  PT_LOAD = 1,
  PF_X = 1, PF_W = 2, PF_R = 4,

  EI_CLASS = 4, EI_DATA = 5, EI_VERSION = 6,
  ELFCLASS32 = 1, ELFDATA2LSB = 1, EV_CURRENT = 1,

  EHDR_SIZE = 52,
  PHDR_SIZE = 32,

  /* Auxiliary vector entries a libc _start reads. */
  AT_NULL = 0, AT_PHDR = 3, AT_PHENT = 4, AT_PHNUM = 5, AT_PAGESZ = 6,
  AT_ENTRY = 9,

  USER_CODE_VA = 0x40000000u,
  USER_REGION_SIZE = 4u * 1024u * 1024u,      /* the one L0 table's reach */
  USER_STACK_TOP = USER_CODE_VA + USER_REGION_SIZE,
};

static uint16_t rd16(const uint8_t *p) {
  return (uint16_t)(p[0] | ((uint16_t)p[1] << 8));
}
static uint32_t rd32(const uint8_t *p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) |
         ((uint32_t)p[3] << 24);
}

/* Find the page already mapped at `va`, or allocate, zero, and map one.
 *
 * Segments share pages: a page can hold the tail of .text and the head of
 * .rodata, and .bss usually starts inside the last .data page.  Allocating per
 * segment would map the same virtual page twice and lose the first copy, so
 * mapping is idempotent and permissions accumulate. */
static void *page_for(struct task *task, uint32_t va, uint32_t perms) {
  const uint32_t page_va = va & ~(PAGE_SIZE - 1u);
  void *existing = vm_translate_user(task, page_va, 0);
  if (existing != 0) {
    /* Widen the existing mapping if this segment needs more than the last one.
     * Erring towards the union is correct here: refusing would reject ordinary
     * images whose sections merely share a page boundary. */
    (void)vm_map_user_page(task, page_va, existing, perms);
    return existing;
  }
  void *const page = page_alloc();
  if (page == 0) return 0;
  uint8_t *const bytes = page;
  for (uint32_t i = 0; i < PAGE_SIZE; ++i) bytes[i] = 0;
  if (vm_map_user_page(task, page_va, page, perms) != 0) {
    page_free(page);
    return 0;
  }
  return page;
}

/* Copy `len` bytes into the user address space at `va`, crossing pages.
 *
 * Chunked per page rather than per byte: translating a user address is a page
 * walk, and doing one per byte made loading a 4 KiB segment cost several
 * thousand walks -- enough to dominate the whole load.  One walk per page is
 * the same code with the loop nested the other way round. */
static int write_user(struct task *task, uint32_t va, const uint8_t *src,
                      uint32_t len, uint32_t perms) {
  uint32_t done = 0;
  while (done < len) {
    const uint32_t at = va + done;
    const uint32_t page_off = at & (PAGE_SIZE - 1u);
    uint32_t chunk = PAGE_SIZE - page_off;
    if (chunk > len - done) chunk = len - done;

    if (page_for(task, at, perms) == 0) return -1;
    uint8_t *const dst = vm_translate_user(task, at, 0);
    if (dst == 0) return -1;
    for (uint32_t i = 0; i < chunk; ++i) dst[i] = src[done + i];
    done += chunk;
  }
  return 0;
}

/* Ensure [va, va+len) is mapped, without writing: the .bss tail. */
static int reserve_user(struct task *task, uint32_t va, uint32_t len,
                        uint32_t perms) {
  if (len == 0) return 0;
  for (uint32_t at = va & ~(PAGE_SIZE - 1u); at < va + len; at += PAGE_SIZE)
    if (page_for(task, at, perms) == 0) return -1;
  return 0;
}

static uint32_t perms_of(uint32_t p_flags) {
  uint32_t perms = 0;
  if (p_flags & PF_R) perms |= VM_R;
  if (p_flags & PF_W) perms |= VM_W;
  if (p_flags & PF_X) perms |= VM_X;
  /* An unreadable segment is not something this machine can express usefully;
   * treat any mapped segment as at least readable. */
  if (!perms) perms = VM_R;
  return perms;
}

/* Push one word onto the descending user stack. */
static int push(struct task *task, uint32_t *sp, uint32_t value) {
  *sp -= 4u;
  uint32_t *const slot = vm_translate_user(task, *sp, 1);
  if (slot == 0) return -1;
  *slot = value;
  return 0;
}

int loader_load_args(struct task *task, const uint8_t *image, uint32_t size,
                     uint32_t argc, const char *const argv[],
                     uint32_t *entry_out, uint32_t *sp_out) {
  if (task == 0 || image == 0 || size < EHDR_SIZE) return LOADER_EBADIMAGE;
  if (argc > LOADER_ARG_MAX || (argc != 0 && argv == 0))
    return LOADER_ENOSPACE;

  if (!(image[0] == 0x7f && image[1] == 'E' && image[2] == 'L' &&
        image[3] == 'F'))
    return LOADER_EBADIMAGE;
  if (image[EI_CLASS] != ELFCLASS32 || image[EI_DATA] != ELFDATA2LSB ||
      image[EI_VERSION] != EV_CURRENT)
    return LOADER_EBADIMAGE;
  if (rd16(image + 16) != ET_EXEC) return LOADER_EBADIMAGE;
  if (rd16(image + 18) != EM_RISCV) return LOADER_EBADIMAGE;

  const uint32_t e_entry = rd32(image + 24);
  const uint32_t e_phoff = rd32(image + 28);
  const uint16_t e_phentsize = rd16(image + 42);
  const uint16_t e_phnum = rd16(image + 44);

  if (e_phentsize < PHDR_SIZE || e_phnum == 0) return LOADER_EBADIMAGE;
  /* The program-header table must lie inside the image; a truncated or
   * malicious header must not send the loader reading past the buffer. */
  if (e_phoff > size || (uint32_t)e_phnum * e_phentsize > size - e_phoff)
    return LOADER_EBADIMAGE;

  uint32_t phdr_va = 0;
  uint32_t image_end = USER_CODE_VA;      /* highest byte any segment occupies */
  int entry_executable = 0;               /* e_entry lands in a PF_X segment */

  for (uint16_t i = 0; i < e_phnum; ++i) {
    const uint8_t *const ph = image + e_phoff + (uint32_t)i * e_phentsize;
    const uint32_t p_type = rd32(ph + 0);
    if (p_type != PT_LOAD) continue;

    const uint32_t p_offset = rd32(ph + 4);
    const uint32_t p_vaddr = rd32(ph + 8);
    const uint32_t p_filesz = rd32(ph + 16);
    const uint32_t p_memsz = rd32(ph + 20);
    const uint32_t p_flags = rd32(ph + 24);

    if (p_filesz > p_memsz) return LOADER_EBADIMAGE;
    if (p_offset > size || p_filesz > size - p_offset) return LOADER_EBADIMAGE;
    /* W^X.  A segment asking to be both writable and executable gives up
     * exactly the protection per-segment permissions exist to provide, and
     * nothing a static toolchain emits needs it.  Refusing here rather than
     * mapping it keeps `perms_of` a translation rather than a policy: the
     * loader never produces a user page it would not be willing to defend. */
    if ((p_flags & PF_W) && (p_flags & PF_X)) return LOADER_EBADIMAGE;
    /* Everything must land in the mappable window, and must not run into the
     * stack page at the top of it. */
    if (p_vaddr < USER_CODE_VA) return LOADER_ENOSPACE;
    if (p_memsz > USER_REGION_SIZE) return LOADER_ENOSPACE;
    if (p_vaddr + p_memsz > USER_STACK_TOP - PAGE_SIZE) return LOADER_ENOSPACE;

    const uint32_t perms = perms_of(p_flags);
    if (write_user(task, p_vaddr, image + p_offset, p_filesz, perms) != 0)
      return LOADER_ENOSPACE;
    /* p_memsz beyond p_filesz is .bss: mapped and already zero, because
     * page_for zeroes every page it allocates. */
    if (reserve_user(task, p_vaddr + p_filesz, p_memsz - p_filesz, perms) != 0)
      return LOADER_ENOSPACE;

    /* AT_PHDR wants the program headers as the *program* sees them, which is
     * only meaningful if a PT_LOAD segment happens to cover them. */
    if (p_vaddr + p_memsz > image_end) image_end = p_vaddr + p_memsz;

    if ((p_flags & PF_X) && e_entry >= p_vaddr && e_entry < p_vaddr + p_memsz)
      entry_executable = 1;

    if (e_phoff >= p_offset && e_phoff < p_offset + p_filesz)
      phdr_va = p_vaddr + (e_phoff - p_offset);
  }

  if (e_entry < USER_CODE_VA || e_entry >= USER_STACK_TOP)
    return LOADER_ENOSPACE;
  if (vm_translate_user(task, e_entry & ~(PAGE_SIZE - 1u), 0) == 0)
    return LOADER_EBADIMAGE;   /* entry point is not in any loaded segment */
  /* ...and in a segment that can be executed.  Being mapped is not enough: an
   * entry pointing into rodata or into .data passes the check above and then
   * faults on the first instruction fetch, which surfaces as a dead task with
   * no explanation rather than as a rejected image.  The MMU does contain it
   * either way -- this is about refusing a bad image here instead of letting
   * it fail later.  Found by sim/fuzz. */
  if (!entry_executable)
    return LOADER_EBADIMAGE;   /* entry point is in a segment without PF_X */

  /* --- initial stack -------------------------------------------------------
   * One page below the top of the user region, holding argument strings and
   * the System V frame a libc _start reads. */
  void *const stack = page_alloc();
  if (stack == 0) return LOADER_ENOSPACE;
  uint8_t *const stack_bytes = stack;
  for (uint32_t i = 0; i < PAGE_SIZE; ++i) stack_bytes[i] = 0;
  const uint32_t stack_va = USER_STACK_TOP - PAGE_SIZE;
  if (vm_map_user_page(task, stack_va, stack, VM_R | VM_W) != 0) {
    page_free(stack);
    return LOADER_ENOSPACE;
  }
  task->user_stack = stack;

  uint32_t sp = USER_STACK_TOP;
  uint32_t arg_va[LOADER_ARG_MAX];
  for (uint32_t i = argc; i != 0; --i) {
    const char *const text = argv[i - 1u];
    if (text == 0) return LOADER_ENOSPACE;
    uint32_t len = 0;
    while (text[len] && len < 255u) ++len;
    if (text[len] != 0 || sp - stack_va <= len) return LOADER_ENOSPACE;
    ++len;                         /* include the NUL */
    sp -= len;
    uint8_t *const dst = vm_translate_user(task, sp, 1);
    if (dst == 0) return LOADER_ENOSPACE;
    for (uint32_t j = 0; j < len; ++j) dst[j] = (uint8_t)text[j];
    arg_va[i - 1u] = sp;
  }
  sp &= ~3u;

  /* The frame contains three mandatory auxv pairs, optionally three PHDR
   * pairs, two vector terminators, argc argv pointers, and argc itself.
   * Padding lives above AT_NULL so sp itself still points at argc. */
  const uint32_t frame_words = 9u + argc + (phdr_va != 0 ? 6u : 0u);
  while ((sp - frame_words * 4u) & 0xfu)
    if (push(task, &sp, 0) != 0) return LOADER_ENOSPACE;

  /* Built top-down, so it reads bottom-up as argc, argv, NULL, envp, NULL,
   * auxv, AT_NULL -- the order _start walks. */
  if (push(task, &sp, AT_NULL) != 0) return LOADER_ENOSPACE;
  if (push(task, &sp, 0) != 0) return LOADER_ENOSPACE;
  if (push(task, &sp, e_entry) != 0) return LOADER_ENOSPACE;
  if (push(task, &sp, AT_ENTRY) != 0) return LOADER_ENOSPACE;
  if (push(task, &sp, PAGE_SIZE) != 0) return LOADER_ENOSPACE;
  if (push(task, &sp, AT_PAGESZ) != 0) return LOADER_ENOSPACE;
  if (phdr_va != 0) {
    if (push(task, &sp, e_phnum) != 0) return LOADER_ENOSPACE;
    if (push(task, &sp, AT_PHNUM) != 0) return LOADER_ENOSPACE;
    if (push(task, &sp, e_phentsize) != 0) return LOADER_ENOSPACE;
    if (push(task, &sp, AT_PHENT) != 0) return LOADER_ENOSPACE;
    if (push(task, &sp, phdr_va) != 0) return LOADER_ENOSPACE;
    if (push(task, &sp, AT_PHDR) != 0) return LOADER_ENOSPACE;
  }
  if (push(task, &sp, 0) != 0) return LOADER_ENOSPACE;   /* envp terminator */
  if (push(task, &sp, 0) != 0) return LOADER_ENOSPACE;   /* argv terminator */
  for (uint32_t i = argc; i != 0; --i)
    if (push(task, &sp, arg_va[i - 1u]) != 0) return LOADER_ENOSPACE;
  if (push(task, &sp, argc) != 0) return LOADER_ENOSPACE;

  /* The heap starts on the first page past the image and may grow until it
   * would meet the stack.  Leaving a one-page guard between them means a heap
   * that grows too far fails a brk() rather than silently colliding with the
   * stack, which is the failure mode that is impossible to debug. */
  task->brk = (image_end + PAGE_SIZE - 1u) & ~(PAGE_SIZE - 1u);
  task->brk_start = task->brk;
  task->brk_limit = USER_STACK_TOP - (2u * PAGE_SIZE);

  *entry_out = e_entry;
  *sp_out = sp;
  return 0;
}

int loader_load(struct task *task, const uint8_t *image, uint32_t size,
                uint32_t *entry_out, uint32_t *sp_out) {
  return loader_load_args(task, image, size, 0, 0, entry_out, sp_out);
}
