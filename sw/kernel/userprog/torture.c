/* An adversarial conformance program for the aXos ABI.
 *
 * `hello.c` demonstrates the ABI: it does what a well-behaved program does and
 * checks the answers are right.  This one attacks it.  Every check here passes
 * a value the kernel is supposed to refuse -- a null pointer, a kernel address,
 * a buffer that starts inside a mapped page and runs off the end of it, a path
 * with no terminator, a descriptor that was closed, a seek that overflows -- and
 * requires the *specific* documented error rather than merely "not a crash".
 *
 * The distinction matters because a demonstration and a test fail differently.
 * A kernel that validates only the first byte of a user buffer, or that checks
 * a pointer is in range without checking the page is writable, passes every
 * test in hello.c and fails several here.
 *
 * It reaches the kernel from the AXFS image rather than the built-in root, so
 * none of this costs the shipped kernel image a byte (docs/design-checklist.md).
 *
 * Each check exits with a distinct code, so a failure names itself:
 *   10-19 syscall dispatch      20-39 user-pointer validation
 *   40-54 descriptors           55-69 seek arithmetic
 *   70-79 path handling         80-89 heap and brk
 * Exit 0 means every refusal was the documented one. */
#include "axlibc.h"

/* Raw syscall numbers, so a check can pass what a libc wrapper would not. */
enum {
  SYS_OPENAT = 56, SYS_CLOSE = 57, SYS_LSEEK = 62, SYS_READ = 63,
  SYS_WRITE = 64, SYS_FSTAT = 80, SYS_GETPID = 172, SYS_BRK = 214,
  SYS_ROLE_INFO = 0x1000,
};

enum {
  AT_FDCWD = -100,
  PATH_MAX = 32,          /* AXOS_PATH_MAX in the syscall component */
  MAX_FDS = 8,            /* AXOS_MAX_FDS */
  FD_FIRST = 3,
  PAGE = 4096,
};

/* Addresses the kernel must never accept from a user program. */
#define KERNEL_VA   0x80000000u   /* where the kernel image lives */
#define BELOW_USER  0x3ffff000u   /* one page below the user region */
#define USER_BASE   0x40000000u

/* A raw syscall returns -errno; the wrappers turn that into -1/errno.  Checking
 * the raw value avoids depending on that translation while testing it. */
static int is_err(long rc, int expected_errno) {
  return rc == -(long)expected_errno;
}

/* ---- 10-19: syscall dispatch ------------------------------------------- */

static int check_dispatch(void) {
  if (!is_err(__libc_syscall(999, 0, 0, 0), ENOSYS)) return 10;
  if (!is_err(__libc_syscall(0x7fffffff, 0, 0, 0), ENOSYS)) return 11;
  /* Just past the private range's last defined call: the range being reserved
   * does not make every number in it implemented. */
  if (!is_err(__libc_syscall(0x1003, 0, 0, 0), ENOSYS)) return 12;
  if (!is_err(__libc_syscall(0x0fff, 0, 0, 0), ENOSYS)) return 13;
  /* A call that takes no pointer must still work with junk in the unused
   * argument registers, or the dispatcher is reading arguments it was not
   * given. */
  if (__libc_syscall(SYS_GETPID, -1, -1, -1) < 0) return 14;
  return 0;
}

/* ---- 20-39: user-pointer validation ------------------------------------ */

static int check_pointers(int fd, char *heap, const char *rodata) {
  /* A destination the kernel writes to. */
  if (!is_err(__libc_syscall(SYS_FSTAT, fd, 0, 0), EFAULT)) return 20;
  if (!is_err(__libc_syscall(SYS_FSTAT, fd, (long)KERNEL_VA, 0), EFAULT))
    return 21;
  if (!is_err(__libc_syscall(SYS_FSTAT, fd, (long)BELOW_USER, 0), EFAULT))
    return 22;

  /* A source the kernel reads from. */
  if (!is_err(__libc_syscall(SYS_WRITE, 1, 0, 4), EFAULT)) return 23;
  if (!is_err(__libc_syscall(SYS_WRITE, 1, (long)KERNEL_VA, 4), EFAULT))
    return 24;

  /* A path pointer. */
  if (!is_err(__libc_syscall5(SYS_OPENAT, AT_FDCWD, 0, O_RDONLY, 0, 0),
              EFAULT))
    return 25;
  if (!is_err(__libc_syscall5(SYS_OPENAT, AT_FDCWD, (long)KERNEL_VA,
                              O_RDONLY, 0, 0), EFAULT))
    return 26;

  /* Read into a page that is mapped but *not writable*.  Range checking alone
   * accepts this; only checking the page's write permission refuses it.  A
   * kernel that gets this wrong silently rewrites the program's own
   * constants. */
  if (!is_err(__libc_syscall(SYS_READ, fd, (long)rodata, 4), EFAULT)) return 27;
  if (!is_err(__libc_syscall(SYS_FSTAT, fd, (long)rodata, 0), EFAULT))
    return 28;

  /* Read into the program's own text, which is executable and not writable. */
  if (!is_err(__libc_syscall(SYS_READ, fd, (long)(void *)&check_dispatch, 4),
              EFAULT))
    return 29;

  /* A buffer that starts in a mapped page and runs off the end of it.  This is
   * the check that separates per-byte validation from validating the first
   * byte and trusting the length. */
  const uintptr_t brk_now = (uintptr_t)sbrk(0);
  const uintptr_t page_end = (brk_now + PAGE - 1u) & ~(uintptr_t)(PAGE - 1u);
  char *const straddle = (char *)(page_end - 8u);
  if ((uintptr_t)straddle < brk_now) return 30;   /* no room to straddle */
  /* Wholly past the last mapped page: unambiguously a fault. */
  if (!is_err(__libc_syscall(SYS_FSTAT, fd, (long)(page_end + PAGE), 0),
              EFAULT))
    return 31;
  /* Straddling: fstat writes 80 bytes, so 8 mapped and 72 unmapped.  fstat is
   * all-or-nothing -- unlike read(), it has no partial result to report -- so
   * the only correct answer is EFAULT. */
  if (!is_err(__libc_syscall(SYS_FSTAT, fd, (long)straddle, 0), EFAULT))
    return 32;

  /* The same shape as a source rather than a destination. */
  if (!is_err(__libc_syscall5(SYS_OPENAT, AT_FDCWD, (long)(page_end + PAGE),
                              O_RDONLY, 0, 0), EFAULT))
    return 33;

  /* Having refused all of those, an ordinary pointer must still work: a
   * validator that rejects everything would pass every check above. */
  struct stat st;
  if (__libc_syscall(SYS_FSTAT, fd, (long)&st, 0) != 0) return 34;
  if (st.st_size <= 0) return 35;
  heap[0] = 'k';
  if (__libc_syscall(SYS_READ, fd, (long)heap, 1) != 1) return 36;
  return 0;
}

/* ---- 40-54: descriptors ------------------------------------------------ */

static int check_descriptors(void) {
  if (!is_err(__libc_syscall(SYS_READ, 77, (long)"x", 1), EBADF)) return 40;
  if (!is_err(__libc_syscall(SYS_READ, -1, (long)"x", 1), EBADF)) return 41;
  if (!is_err(__libc_syscall(SYS_CLOSE, 77, 0, 0), EBADF)) return 42;
  /* FD_FIRST + MAX_FDS is the first index past the table: an off-by-one in the
   * bound check accepts it. */
  if (!is_err(__libc_syscall(SYS_FSTAT, FD_FIRST + MAX_FDS, 0, 0), EBADF))
    return 43;

  /* Fill the table, then require EMFILE rather than a ninth descriptor. */
  int fds[MAX_FDS];
  int opened = 0;
  for (int i = 0; i < MAX_FDS; ++i) {
    fds[i] = open("motd", O_RDONLY);
    if (fds[i] < 0) break;
    if (fds[i] != FD_FIRST + i) return 44;   /* lowest free slot, in order */
    opened++;
  }
  if (opened != MAX_FDS) return 45;
  if (open("motd", O_RDONLY) != -1 || errno != EMFILE) return 46;

  /* Freeing one slot must make exactly that number available again. */
  if (close(fds[3]) != 0) return 47;
  const int reused = open("motd", O_RDONLY);
  if (reused != fds[3]) return 48;
  if (close(reused) != 0) return 49;
  if (close(reused) != -1 || errno != EBADF) return 50;

  for (int i = 0; i < MAX_FDS; ++i)
    if (i != 3 && close(fds[i]) != 0) return 51;
  return 0;
}

/* ---- 55-69: seek arithmetic -------------------------------------------- */

static int check_seek(int fd, long size) {
  if (lseek(fd, -1, SEEK_SET) != -1 || errno != EINVAL) return 55;
  if (lseek(fd, 0, 3) != -1 || errno != EINVAL) return 56;
  if (lseek(fd, 0, -1) != -1 || errno != EINVAL) return 57;

  /* Seeking past the end is legal; reading there returns end-of-file rather
   * than an error or the file's contents. */
  if (lseek(fd, size + 1000, SEEK_SET) != size + 1000) return 58;
  char buf[8];
  if (read(fd, buf, sizeof(buf)) != 0) return 59;

  /* SEEK_CUR from a large offset, and SEEK_END with a large delta, are where
   * a signed 32-bit `base + offset` overflows.  Whatever the kernel decides,
   * it must not be a negative offset it then accepts. */
  if (lseek(fd, 0x7ffffff0, SEEK_SET) != 0x7ffffff0) return 60;
  const long over = lseek(fd, 0x7ffffff0, SEEK_CUR);
  if (over != -1 && over < 0) return 61;
  if (over == -1 && errno != EINVAL) return 62;
  const long from_end = lseek(fd, 0x7fffffff, SEEK_END);
  if (from_end != -1 && from_end < 0) return 63;
  if (from_end == -1 && errno != EINVAL) return 64;

  /* Whatever happened above, the descriptor must still be usable. */
  if (lseek(fd, 0, SEEK_SET) != 0) return 65;
  if (read(fd, buf, 1) != 1) return 66;
  if (lseek(fd, 0, SEEK_CUR) != 1) return 67;
  return 0;
}

/* ---- 70-79: path handling ---------------------------------------------- */

static int check_paths(void) {
  if (open("", O_RDONLY) != -1 || errno != ENOENT) return 70;

  /* PATH_MAX-1 characters plus a terminator is the longest path that fits. */
  char just_fits[PATH_MAX];
  for (int i = 0; i < PATH_MAX - 1; ++i) just_fits[i] = 'a';
  just_fits[PATH_MAX - 1] = '\0';
  if (open(just_fits, O_RDONLY) != -1 || errno != ENOENT) return 71;

  /* One character longer has no room for the terminator: the kernel must say
   * the name is too long rather than read past what it copied. */
  char too_long[PATH_MAX + 8];
  for (int i = 0; i < PATH_MAX + 7; ++i) too_long[i] = 'a';
  too_long[PATH_MAX + 7] = '\0';
  if (open(too_long, O_RDONLY) != -1 || errno != ENAMETOOLONG) return 72;

  /* A path with no terminator before the end of mapped memory.  A kernel that
   * scans for a NUL without bounding the scan faults inside itself here; the
   * documented answer is that the caller gets an error. */
  const uintptr_t brk_now = (uintptr_t)sbrk(0);
  const uintptr_t page_end = (brk_now + PAGE - 1u) & ~(uintptr_t)(PAGE - 1u);
  char *const tail = (char *)(page_end - 4u);
  if ((uintptr_t)tail >= brk_now) {
    for (int i = 0; i < 4; ++i) tail[i] = 'a';
    const long rc = __libc_syscall5(SYS_OPENAT, AT_FDCWD, (long)tail,
                                    O_RDONLY, 0, 0);
    if (!is_err(rc, EFAULT) && !is_err(rc, ENAMETOOLONG)) return 73;
  }
  return 0;
}

/* ---- 80-89: heap and brk ----------------------------------------------- */

static int check_heap(void) {
  void *const zero = malloc(0);
  free(zero);                       /* either answer is fine; a crash is not */

  /* An allocation larger than the whole address space must fail cleanly. */
  if (malloc((size_t)0xfffffff0u) != NULL) return 80;

  /* brk must not be movable below its own start, and a refused move must
   * leave it where it was. */
  const uintptr_t before = (uintptr_t)sbrk(0);
  if (before == 0) return 81;
  if (__libc_syscall(SYS_BRK, (long)USER_BASE, 0, 0) > (long)before) return 82;
  if ((uintptr_t)sbrk(0) != before) return 83;

  /* Growth across a page boundary, then content survives a realloc that has to
   * move the block. */
  char *p = malloc(64);
  if (p == NULL) return 84;
  for (int i = 0; i < 64; ++i) p[i] = (char)i;
  p = realloc(p, 9000);
  if (p == NULL) return 85;
  for (int i = 0; i < 64; ++i)
    if (p[i] != (char)i) return 86;
  p[8999] = 'z';
  if (p[8999] != 'z') return 87;
  free(p);
  if ((uintptr_t)sbrk(0) < before) return 88;
  return 0;
}

int main(void) {
  static const char rodata[] = "constant";

  char *const heap = malloc(256);
  if (heap == NULL) return 1;

  const int fd = open("motd", O_RDONLY);
  if (fd < 0) return 2;
  struct stat st;
  if (fstat(fd, &st) != 0) return 3;

  int rc;
  if ((rc = check_dispatch()) != 0) return rc;
  if ((rc = check_pointers(fd, heap, rodata)) != 0) return rc;
  if ((rc = check_seek(fd, st.st_size)) != 0) return rc;
  if ((rc = check_paths()) != 0) return rc;
  if (close(fd) != 0) return 4;
  if ((rc = check_descriptors()) != 0) return rc;
  if ((rc = check_heap()) != 0) return rc;

  /* The constants must be exactly what they were: if any refusal above was not
   * actually a refusal, this is where the damage shows up. */
  if (strcmp(rodata, "constant") != 0) return 5;
  free(heap);

  printf("torture: ok\n");
  return 0;
}
