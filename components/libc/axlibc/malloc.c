/* axlibc: a first-fit free-list allocator over sbrk.
 *
 * Chosen for being small and inspectable rather than fast.  Every block carries
 * a header with its size and a free flag; malloc walks the list for the first
 * block that fits, splitting it when the remainder is worth having, and free
 * coalesces with the following block so repeated alloc/free of similar sizes
 * does not fragment the heap into dust.
 *
 * Backward coalescing is deliberately absent: it needs either a footer per
 * block or a doubly-linked list, and the extra word per allocation costs more
 * on a 128 KiB machine than the fragmentation it avoids.  A program with a
 * pathological alloc/free pattern will fragment; that is an honest limit of a
 * basic allocator rather than a bug. */
#include "axlibc.h"

typedef struct block {
  size_t size;            /* payload bytes, excluding this header */
  struct block *next;
  int free;
} block_t;

#define ALIGN 8u
#define HEADER ((size_t)((sizeof(block_t) + ALIGN - 1u) & ~(ALIGN - 1u)))

/* The largest request that can be served without the size arithmetic wrapping.
 *
 * Two things overflow at the top of the range, and the second is the dangerous
 * one.  `align_up` wraps for the last few sizes, and `HEADER + want` wraps for
 * anything near SIZE_MAX -- `malloc(0xfffffff0)` computes an increment of
 * exactly 0.  `sbrk(0)` is a *query* that returns the current break and never
 * -1, so the allocation appears to succeed, and a header claiming 0xfffffff0
 * bytes goes into the free list.  From then on every malloc finds that block
 * big enough and hands out overlapping memory: silent heap corruption rather
 * than a failed allocation.
 *
 * The bound is INT32_MAX rather than SIZE_MAX because sbrk takes a signed
 * increment, so a larger request could not be expressed even if the header
 * arithmetic held.  `calloc` has always checked its own multiply; this is the
 * same class of bug one level down. */
#define MAX_ALLOC ((size_t)0x7fffffffu - HEADER - ALIGN)

static block_t *heap_head;

static size_t align_up(size_t n) { return (n + ALIGN - 1u) & ~(ALIGN - 1u); }

/* Split `b` when the tail is large enough to be a block in its own right.
 * Splitting for a smaller remainder would create blocks that can never hold an
 * allocation and can only be merged away later. */
static void split(block_t *b, size_t want) {
  if (b->size < want + HEADER + ALIGN) return;
  block_t *const tail = (block_t *)((uint8_t *)b + HEADER + want);
  tail->size = b->size - want - HEADER;
  tail->next = b->next;
  tail->free = 1;
  b->size = want;
  b->next = tail;
}

void *malloc(size_t size) {
  if (size == 0) return NULL;
  if (size > MAX_ALLOC) return NULL;
  const size_t want = align_up(size);

  block_t *prev = NULL;
  for (block_t *b = heap_head; b != NULL; prev = b, b = b->next) {
    if (b->free && b->size >= want) {
      split(b, want);
      b->free = 0;
      return (uint8_t *)b + HEADER;
    }
  }

  /* Nothing fitted: grow the heap.  sbrk returns the old break, which becomes
   * this block's header. */
  void *const raw = sbrk((intptr_t)(HEADER + want));
  if (raw == (void *)-1) return NULL;
  block_t *const b = raw;
  b->size = want;
  b->next = NULL;
  b->free = 0;
  if (prev) prev->next = b; else heap_head = b;
  return (uint8_t *)b + HEADER;
}

void free(void *ptr) {
  if (ptr == NULL) return;
  block_t *const b = (block_t *)((uint8_t *)ptr - HEADER);
  b->free = 1;
  /* Forward coalesce: merging with the next block keeps a run of freed
   * allocations usable as one large block again. */
  while (b->next != NULL && b->next->free) {
    b->size += HEADER + b->next->size;
    b->next = b->next->next;
  }
}

void *calloc(size_t count, size_t size) {
  /* Overflow here would under-allocate and hand back a buffer smaller than the
   * caller believes, which is the classic way this function becomes a bug. */
  if (count != 0 && size > (size_t)-1 / count) return NULL;
  const size_t total = count * size;
  void *const p = malloc(total);
  if (p != NULL) memset(p, 0, total);
  return p;
}

void *realloc(void *ptr, size_t size) {
  if (ptr == NULL) return malloc(size);
  if (size == 0) { free(ptr); return NULL; }
  if (size > MAX_ALLOC) return NULL;
  block_t *const b = (block_t *)((uint8_t *)ptr - HEADER);
  if (b->size >= align_up(size)) return ptr;
  void *const fresh = malloc(size);
  if (fresh == NULL) return NULL;
  memcpy(fresh, ptr, b->size);
  free(ptr);
  return fresh;
}
