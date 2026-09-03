/* axlibc: a small freestanding C library for aXos programs.
 *
 * Deliberately not a full libc.  It provides what a program needs to be an
 * ordinary C program -- startup, syscalls with errno, strings, an allocator,
 * and formatted output -- and nothing else.  It exists so a program can be
 * written normally rather than in syscall assembly, and so the ABI has a
 * consumer that is not the ABI's own test.
 *
 * It is a component: a profile can select a real libc (picolibc, newlib)
 * instead, and nothing above it changes, because everything here is the
 * standard spelling.
 *
 * Not provided, and the reasons are the same as in docs/abi.md: no floating
 * point, no locale, no signals, no threads, and no FILE streams -- files are
 * reached through the raw descriptor calls, which is what the kernel offers. */
#pragma once

typedef unsigned int size_t;
typedef int ssize_t;
typedef int intptr_t;
typedef unsigned int uintptr_t;
typedef long long int64_t;
typedef unsigned long long uint64_t;
typedef int int32_t;
typedef unsigned int uint32_t;
typedef unsigned char uint8_t;

#define NULL ((void *)0)

/* The errno subset docs/abi.md defines. */
#define EPERM  1
#define ENOENT 2
#define EIO    5
#define EBADF  9
#define ECHILD 10
#define EAGAIN 11
#define ENOMEM 12
#define EFAULT 14
#define EBUSY  16
#define ENODEV 19
#define EINVAL 22
#define EMFILE 24
#define EROFS  30
#define ENAMETOOLONG 36
#define ENOSYS 38
#define EMSGSIZE 90
#define ETIMEDOUT 110

extern int errno;
extern char **__libc_environ;

long __libc_syscall(long nr, long a0, long a1, long a2);
long __libc_syscall5(long nr, long a0, long a1, long a2, long a3, long a4);
long __libc_ret(long value);

/* fcntl.  The volume is read-only through the ABI (docs/abi.md), so the write
 * modes are declared for source compatibility and will return -EROFS. */
#define O_RDONLY 0
#define O_WRONLY 1
#define O_RDWR   2

/* The asm-generic 32-bit `struct stat`, field for field.  It is the kernel's
 * layout rather than a convenient one, so a program built against a real libc
 * and a program built against this one see the same 80 bytes. */
#define S_IFMT   0170000
#define S_IFCHR  0020000
#define S_IFREG  0100000
#define S_ISREG(m) (((m) & S_IFMT) == S_IFREG)
#define S_ISCHR(m) (((m) & S_IFMT) == S_IFCHR)

struct stat {
  unsigned long st_dev;
  unsigned long st_ino;
  unsigned int st_mode;
  unsigned int st_nlink;
  unsigned int st_uid;
  unsigned int st_gid;
  unsigned long st_rdev;
  unsigned long __pad1;
  long st_size;
  int st_blksize;
  int __pad2;
  long st_blocks;
  long st_atime_sec;
  unsigned int st_atime_nsec;
  long st_mtime_sec;
  unsigned int st_mtime_nsec;
  long st_ctime_sec;
  unsigned int st_ctime_nsec;
  unsigned int __unused4;
  unsigned int __unused5;
};

/* unistd */
ssize_t write(int fd, const void *buf, size_t count);
ssize_t read(int fd, void *buf, size_t count);
int close(int fd);
long lseek(int fd, long offset, int whence);
int getpid(void);
void *sbrk(intptr_t increment);

#define SEEK_SET 0
#define SEEK_CUR 1
#define SEEK_END 2

/* fcntl / stat */
int open(const char *path, int flags);
int fstat(int fd, struct stat *out);

/* atomiX accelerator ABI.  The role window remains kernel-only; programs pass
 * checked encoded jobs whose formats are shared with docs/host-protocol.md. */
#define AX_ROLE_ID_LOOPBACK 0x4c4f4f50u
#define AX_ROLE_ID_TPU      0x5450554cu
#define AX_ROLE_ID_GPU      0x47505543u

#define AX_ROLE_OP_LOOPBACK 0x10u
#define AX_ROLE_OP_TPU_GEMM 0x11u
#define AX_ROLE_OP_GPU_RUN  0x12u

#define AX_ROLE_CAP_LOOPBACK (1u << 0)
#define AX_ROLE_CAP_TPU_GEMM (1u << 1)
#define AX_ROLE_CAP_GPU_RUN  (1u << 2)

struct ax_role_info {
  uint32_t id;
  uint32_t version;
  uint32_t capabilities;
};

int role_info(struct ax_role_info *out);
long role_submit(uint32_t op, const void *request, size_t request_len);
ssize_t role_wait(uint32_t token, void *response, size_t response_capacity);

/* stdlib */
void exit(int status);
void _exit(int status);
void *malloc(size_t size);
void free(void *ptr);
void *calloc(size_t count, size_t size);
void *realloc(void *ptr, size_t size);

/* string */
void *memcpy(void *dst, const void *src, size_t n);
void *memmove(void *dst, const void *src, size_t n);
void *memset(void *dst, int c, size_t n);
int memcmp(const void *a, const void *b, size_t n);
size_t strlen(const char *s);
int strcmp(const char *a, const char *b);
int strncmp(const char *a, const char *b, size_t n);
char *strcpy(char *dst, const char *src);
char *strchr(const char *s, int c);

/* stdio: a console-only subset.  There are no FILE streams because there is
 * nothing to point them at yet -- adding them before the filesystem is bound
 * would be inventing an interface with no implementation behind it. */
int putchar(int c);
int puts(const char *s);
int printf(const char *fmt, ...);
