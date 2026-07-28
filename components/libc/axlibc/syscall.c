/* axlibc: the syscall layer.
 *
 * One inline `ecall` wrapper per argument count, plus the errno convention from
 * docs/abi.md: a return in [-4095, -1] is -errno, anything else is a result.
 * That is the whole reason to follow a standard ABI -- this file is the
 * ordinary three-line shim rather than something bespoke.
 *
 * Numbers are the asm-generic ones; they are not repeated as an enum here so
 * there is exactly one place (the kernel component) that defines them and one
 * place (docs/abi.md) that documents them. */
#include "axlibc.h"

int errno;
char **__libc_environ;

/* Turn a raw kernel return into the (result, errno) pair C expects. */
long __libc_ret(long value) {
  if (value < 0 && value >= -4095) {
    errno = (int)-value;
    return -1;
  }
  return value;
}

/* One `ecall` site, taking the widest argument list any wrapper here needs.
 * Calls with fewer arguments pass zeros: the kernel never reads a register a
 * call does not define, so this costs two register moves rather than a second
 * copy of the trap sequence. */
long __libc_syscall5(long nr, long a0, long a1, long a2, long a3, long a4) {
  register long r_a7 __asm__("a7") = nr;
  register long r_a0 __asm__("a0") = a0;
  register long r_a1 __asm__("a1") = a1;
  register long r_a2 __asm__("a2") = a2;
  register long r_a3 __asm__("a3") = a3;
  register long r_a4 __asm__("a4") = a4;
  __asm__ volatile("ecall"
                   : "+r"(r_a0)
                   : "r"(r_a7), "r"(r_a1), "r"(r_a2), "r"(r_a3), "r"(r_a4)
                   : "memory");
  return r_a0;
}

long __libc_syscall(long nr, long a0, long a1, long a2) {
  return __libc_syscall5(nr, a0, a1, a2, 0, 0);
}

ssize_t write(int fd, const void *buf, size_t count) {
  return __libc_ret(__libc_syscall(64, fd, (long)buf, (long)count));
}

ssize_t read(int fd, void *buf, size_t count) {
  return __libc_ret(__libc_syscall(63, fd, (long)buf, (long)count));
}

/* open() over openat(), which is the only one RISC-V has: AT_FDCWD says the
 * path is not relative to a directory descriptor. */
int open(const char *path, int flags) {
  return (int)__libc_ret(__libc_syscall5(56, -100, (long)path, flags, 0, 0));
}

int close(int fd) { return (int)__libc_ret(__libc_syscall(57, fd, 0, 0)); }

/* Number 62 on rv32 is llseek: the offset arrives as a register pair and the
 * result comes back through a pointer, with 0 in a0.  Collapsing that to a
 * 32-bit offset is this wrapper's job, not the kernel's -- the kernel has to
 * keep the shape the standard gives it so a real libc port works unmodified. */
long lseek(int fd, long offset, int whence) {
  long long result = 0;
  const long rc = __libc_syscall5(62, fd, offset >> 31, offset,
                                  (long)&result, whence);
  if (__libc_ret(rc) < 0) return -1;
  return (long)result;
}

int fstat(int fd, struct stat *out) {
  return (int)__libc_ret(__libc_syscall(80, fd, (long)out, 0));
}

int role_info(struct ax_role_info *out) {
  return (int)__libc_ret(__libc_syscall(0x1000, (long)out, 0, 0));
}

long role_submit(uint32_t op, const void *request, size_t request_len) {
  return __libc_ret(__libc_syscall(0x1001, op, (long)request, request_len));
}

ssize_t role_wait(uint32_t token, void *response, size_t response_capacity) {
  return (ssize_t)__libc_ret(
      __libc_syscall(0x1002, token, (long)response, response_capacity));
}

int getpid(void) { return (int)__libc_ret(__libc_syscall(172, 0, 0, 0)); }

void _exit(int status) {
  __libc_syscall(93, status, 0, 0);
  for (;;) {}                     /* exit does not return */
}

void exit(int status) { _exit(status); }

/* sbrk over the kernel's brk.  The kernel reports failure by returning the
 * break unchanged, never by an errno, so that is what is checked here. */
void *sbrk(intptr_t increment) {
  const long current = __libc_syscall(214, 0, 0, 0);
  if (current <= 0) { errno = ENOMEM; return (void *)-1; }
  if (increment == 0) return (void *)current;

  const long wanted = current + increment;
  const long got = __libc_syscall(214, wanted, 0, 0);
  if (got != wanted) { errno = ENOMEM; return (void *)-1; }
  return (void *)current;
}
