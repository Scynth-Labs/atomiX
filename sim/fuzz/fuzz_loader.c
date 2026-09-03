/* Coverage-guided fuzzing of loader.elf32 against malformed program images.
 *
 * This is the kernel's largest untrusted-input surface.  An ELF reaching
 * `loader_load` came off an AXFS image or a UART upload, so every field in it
 * is attacker-controlled: segment counts, file offsets, memory sizes, virtual
 * addresses, and the entry point.  The existing tests feed it images the
 * toolchain produced -- which is exactly the input class that does not find
 * parser bugs.
 *
 * Grey rather than black box: the fuzzer drives the component's real code
 * through its real seam, and the harness knows enough about the seam to model
 * it and to assert the invariants the kernel depends on.  It does not know the
 * ELF format, and deliberately so: libFuzzer's coverage feedback derives the
 * structure from the branches it reaches.
 *
 * What is being looked for, in order of how much it would matter:
 *
 *   1. A read outside the image.  The loader is handed a pointer and a length,
 *      and every offset in the header is untrusted.  ASan's redzones around
 *      the exact-sized copy below turn a one-byte overread into a crash.
 *   2. A write outside a mapped page, which ASan catches the same way.
 *   3. A W+X mapping.  The loader exists so segments carry separate
 *      permissions; a malformed image talking it into a writable executable
 *      page defeats the reason it is not a flat loader.
 *   4. A success return whose entry point or stack pointer is not mapped --
 *      the kernel would enter userspace on an unmapped PC.
 *
 * Build and run:  make -C sim/fuzz run          (a bounded run, as CI does)
 *                 make -C sim/fuzz explore      (until you stop it)
 */
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "loader.h"
#include "page.h"
#include "task.h"
#include "vm.h"

/* A page pool small enough that exhaustion is reachable within a fuzz case --
 * the ENOSPACE paths are as much of the loader as the success path, and a pool
 * that never runs out would never execute them. */
#define POOL_PAGES 64
#define MAX_MAPPINGS 256

struct mapping {
  uint32_t va;
  void *page;
  uint32_t perms;
};

static uint8_t pool[POOL_PAGES][PAGE_SIZE] __attribute__((aligned(PAGE_SIZE)));
static int pool_taken[POOL_PAGES];
static struct mapping mappings[MAX_MAPPINGS];
static unsigned mapping_count;
static unsigned live_pages;

static void model_reset(void) {
  memset(pool_taken, 0, sizeof pool_taken);
  memset(mappings, 0, sizeof mappings);
  mapping_count = 0;
  live_pages = 0;
}

void *page_alloc(void) {
  for (int i = 0; i < POOL_PAGES; i++) {
    if (!pool_taken[i]) {
      pool_taken[i] = 1;
      live_pages++;
      /* The real allocator hands back zeroed pages and the loader relies on it
       * for .bss; a harness that skipped this would report phantom findings. */
      memset(pool[i], 0, PAGE_SIZE);
      return pool[i];
    }
  }
  return 0;
}

void page_free(void *page) {
  for (int i = 0; i < POOL_PAGES; i++) {
    if (pool[i] == page) {
      assert(pool_taken[i] && "double free of a loader page");
      pool_taken[i] = 0;
      live_pages--;
      return;
    }
  }
  assert(0 && "page_free of a pointer this allocator never handed out");
}

int vm_map_user_page(struct task *task, uint32_t user_va, void *page,
                     uint32_t perms) {
  (void)task;
  assert((user_va & (PAGE_SIZE - 1u)) == 0 && "unaligned user mapping");
  /* Invariant 3: the whole point of a per-segment loader. */
  assert(!((perms & VM_W) && (perms & VM_X)) && "loader mapped a W+X page");
  if (mapping_count == MAX_MAPPINGS) return -1;
  for (unsigned i = 0; i < mapping_count; i++) {
    if (mappings[i].va == user_va) {
      mappings[i].page = page;
      mappings[i].perms |= perms;
      return 0;
    }
  }
  mappings[mapping_count++] = (struct mapping){user_va, page, perms};
  return 0;
}

void *vm_translate_user(const struct task *task, uint32_t user_va, int write) {
  (void)task;
  for (unsigned i = 0; i < mapping_count; i++) {
    if (mappings[i].va == (user_va & ~(PAGE_SIZE - 1u))) {
      if (write && !(mappings[i].perms & VM_W)) return 0;
      return (uint8_t *)mappings[i].page + (user_va & (PAGE_SIZE - 1u));
    }
  }
  return 0;
}

static int mapped(uint32_t va, uint32_t perm) {
  for (unsigned i = 0; i < mapping_count; i++)
    if (mappings[i].va == (va & ~(PAGE_SIZE - 1u)))
      return (mappings[i].perms & perm) == perm;
  return 0;
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  if (size > 1u << 20) return 0;
  model_reset();

  /* Copy to an exact-sized allocation. The input libFuzzer supplies lives in a
   * larger buffer, so a one-byte overread of `data` would go unnoticed; with
   * this, ASan has a redzone immediately after the last valid byte, which is
   * the whole reason the copy is here. */
  uint8_t *image = (uint8_t *)malloc(size ? size : 1);
  if (!image) return 0;
  memcpy(image, data, size);

  struct task task;
  memset(&task, 0, sizeof task);

  uint32_t entry = 0, sp = 0;
  const char *const argv[] = {"fuzz", "arg"};
  const int rc = loader_load_args(&task, image, (uint32_t)size, 2, argv,
                                  &entry, &sp);
  if (rc == 0) {
    /* Invariant 4: a success the kernel could not act on is a bug that shows up
     * as a fault in userspace, far from here. */
    assert(mapped(entry, VM_X) && "entry point is not mapped executable");
    assert(mapped(sp, VM_R | VM_W) && "initial stack pointer is not writable");
    assert((sp & 3u) == 0 && "initial stack pointer is misaligned");
  } else {
    assert((rc == LOADER_EBADIMAGE || rc == LOADER_ENOSPACE) &&
           "loader returned a code outside its documented contract");
  }
  /* Not asserted: that a failed load freed everything. loader.h says a failed
   * load may leave pages mapped and the caller destroys the address space, so
   * requiring otherwise would test a contract the kernel does not rely on. */

  free(image);
  return 0;
}
