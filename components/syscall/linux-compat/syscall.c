/* syscall.linux-compat: the RISC-V Linux syscall subset (docs/abi.md).
 *
 * Numbers come from asm-generic/unistd.h, which is the table RISC-V Linux uses.
 * They are not ours to choose, and that is the point: a standard table means an
 * unmodified newlib or picolibc can be retargeted onto this kernel, and a
 * program built for it is not built for atomiX alone.
 *
 * Errors return -errno, so a libc wrapper is the ordinary "negate into errno,
 * return -1" shim rather than something bespoke.
 *
 * Calls with no Linux equivalent live at 0x1000 and above, a range the
 * asm-generic table will never reach.  The accelerator role driver goes there
 * rather than being disguised as an ioctl -- it is the most interesting thing
 * this machine does and it deserves a name.
 *
 * What this component does NOT decide: how a task is forked, how pages are
 * allocated, or how the console is driven.  Those arrive through
 * `struct syscall_ops`, so replacing the ABI does not mean reimplementing the
 * kernel.
 *
 * Sizes are build-time parameters (see component.json), because a descriptor
 * table large enough for a shell and one small enough for a 128 KiB image are
 * the same design at different settings. */
#include <stdint.h>

#include "role.h"
#include "syscall.h"

#ifndef AXOS_MAX_FDS
#define AXOS_MAX_FDS 8
#endif
#ifndef AXOS_WRITE_MAX
#define AXOS_WRITE_MAX 1024
#endif
#ifndef AXOS_PATH_MAX
#define AXOS_PATH_MAX 32
#endif
#ifndef AXOS_IO_CHUNK
#define AXOS_IO_CHUNK 128
#endif
#ifndef AXOS_ROLE_MAX_PAYLOAD
#define AXOS_ROLE_MAX_PAYLOAD 1280
#endif

/* asm-generic/unistd.h numbers.  Only the ones this kernel answers are named;
 * everything else falls through to -ENOSYS, which a libc handles correctly. */
enum {
  NR_openat = 56,
  NR_close = 57,
  NR_lseek = 62,
  NR_read = 63,
  NR_write = 64,
  NR_fstat = 80,
  NR_exit = 93,
  NR_getpid = 172,
  NR_brk = 214,
  NR_clone = 220,
  NR_wait4 = 260,

  /* Private range: ours to define and to change. */
  NR_ax_role_info = 0x1000,
  NR_ax_role_submit = 0x1001,
  NR_ax_role_wait = 0x1002,
};

/* Standard descriptor numbers.  0, 1 and 2 are the console and are never in the
 * table; the first file a program opens is 3, as it is everywhere else. */
enum { FD_STDIN = 0, FD_STDOUT = 1, FD_STDERR = 2, FD_FIRST = 3 };

/* openat's dirfd for "relative to the working directory".  There are no
 * directories here, so it is the only value accepted -- refusing an unsupported
 * dirfd is better than resolving it against a root that does not exist. */
enum { AT_FDCWD = -100 };

/* open flags.  Only the access mode is inspected; the creation and status flags
 * a program may add are accepted and ignored, which is what a read-only volume
 * can honestly do with them. */
enum { O_ACCMODE = 3, O_RDONLY = 0 };

/* lseek whence values. */
enum { SEEK_SET = 0, SEEK_CUR = 1, SEEK_END = 2 };

/* Descriptor state is per task slot.  The syscall component still owns its
 * layout and numbering; the kernel only tells it which stable slot is calling
 * and when a task is created or cloned. */
struct fd_entry {
  /* The filesystem id *plus one*, so that zero means free.  A table in .bss is
   * then already a table of closed descriptors, and a kernel that forgets to
   * reset a task slot gets no descriptors rather than eight aliases of
   * file 0 -- the failure mode that would be silent. */
  uint32_t file;
  uint32_t offset;
};

static struct fd_entry task_fds[TASK_SLOTS][AXOS_MAX_FDS];

/* One hardware job can own the single role window at a time.  Execution is
 * polling-backed today, so submit has completed the device work when it
 * returns; keeping the result behind a token preserves the submit/wait ABI
 * that an interrupt-backed driver will use later. */
static uint8_t role_request[AXOS_ROLE_MAX_PAYLOAD];
static uint8_t role_response[AXOS_ROLE_MAX_PAYLOAD];
static uint32_t role_response_len;
static uint32_t role_owner = TASK_SLOTS;
static uint32_t role_token;
static uint32_t role_next_token = 1;
static int role_pending;

void syscall_reset(void) {
  role_pending = 0;
  role_owner = TASK_SLOTS;
  role_next_token = 1;
  for (uint32_t task = 0; task < TASK_SLOTS; ++task)
    syscall_task_reset(task);
}

void syscall_task_reset(uint32_t task_slot) {
  if (task_slot >= TASK_SLOTS) return;
  if (role_pending && role_owner == task_slot) {
    role_pending = 0;
    role_owner = TASK_SLOTS;
  }
  for (uint32_t i = 0; i < AXOS_MAX_FDS; ++i)
    task_fds[task_slot][i].file = 0;
}

void syscall_task_clone(uint32_t child_slot, uint32_t parent_slot) {
  if (child_slot >= TASK_SLOTS || parent_slot >= TASK_SLOTS) return;
  for (uint32_t i = 0; i < AXOS_MAX_FDS; ++i)
    task_fds[child_slot][i] = task_fds[parent_slot][i];
}

static inline uint32_t csr_read_sepc(void) {
  uint32_t value;
  __asm__ volatile("csrr %0, sepc" : "=r"(value));
  return value;
}
static inline void csr_write_sepc(uint32_t value) {
  __asm__ volatile("csrw sepc, %0" :: "r"(value));
}

/* Return `value` to the caller and resume after the ecall.  Every call that
 * returns to its caller ends through here, so sepc advancement happens in
 * exactly one place. */
static uint32_t *ret(uint32_t *trap_frame, uint32_t value) {
  trap_frame[TRAP_FRAME_A0] = value;
  csr_write_sepc(csr_read_sepc() + 4u);
  return trap_frame;
}

/* Errors are negative: -errno as a two's-complement word. */
static uint32_t *err(uint32_t *trap_frame, uint32_t errno_value) {
  return ret(trap_frame, (uint32_t)(-(int32_t)errno_value));
}

/* write(fd, buf, count).
 *
 * Only the console descriptors are writable so far.  The copy is bounded and
 * goes through the kernel's user-copy hook, so a bad pointer becomes -EFAULT
 * rather than a supervisor fault -- a user program must not be able to panic
 * the kernel by passing a garbage address. */
static uint32_t *sys_write(uint32_t *trap_frame, const struct syscall_ops *ops) {
  const uint32_t fd = trap_frame[TRAP_FRAME_A0];
  const uint32_t buf = trap_frame[TRAP_FRAME_A1];
  const uint32_t count = trap_frame[TRAP_FRAME_A2];

  if (fd != FD_STDOUT && fd != FD_STDERR) return err(trap_frame, AX_EBADF);
  if (count == 0) return ret(trap_frame, 0);

  const uint32_t chunk = count > AXOS_WRITE_MAX ? AXOS_WRITE_MAX : count;
  for (uint32_t i = 0; i < chunk; ++i) {
    char c;
    if (!ops->copy_from_user(&c, buf + i, 1)) {
      /* Partial writes are legal and are what Linux does: report what landed. */
      return i ? ret(trap_frame, i) : err(trap_frame, AX_EFAULT);
    }
    ops->console_putc(c);
  }
  return ret(trap_frame, chunk);
}

/* --- files ------------------------------------------------------------------
 *
 * The filesystem gives out ids and byte ranges; descriptors are built here,
 * because which small integer a program gets back, what its offset does, and
 * when it stops being valid are ABI decisions rather than storage ones. */

/* Map a user-visible descriptor to a table slot, or -1 if it is not open. */
static struct fd_entry *active_fds(const struct syscall_ops *ops) {
  const uint32_t slot = ops->task_slot();
  return slot < TASK_SLOTS ? task_fds[slot] : 0;
}

static int slot_of(const struct fd_entry *fds, int32_t fd) {
  if (fd < FD_FIRST || fd >= FD_FIRST + (int32_t)AXOS_MAX_FDS) return -1;
  const int slot = (int)(fd - FD_FIRST);
  return fds[slot].file == 0 ? -1 : slot;
}

/* The filesystem id behind an open slot. */
static int file_of(const struct fd_entry *fds, int slot) {
  return (int)(fds[slot].file - 1u);
}

/* Pull a NUL-terminated path out of userspace.  Returns 0, or the errno to
 * report: an unterminated path is ENAMETOOLONG rather than a kernel that reads
 * until it faults. */
static uint32_t copy_path(const struct syscall_ops *ops, uint32_t user_va,
                          char *path) {
  for (uint32_t i = 0; i < AXOS_PATH_MAX; ++i) {
    if (!ops->copy_from_user(&path[i], user_va + i, 1)) return AX_EFAULT;
    if (path[i] == 0) return 0;
  }
  return AX_ENAMETOOLONG;
}

/* openat(dirfd, path, flags, mode).
 *
 * Write access is refused outright, even on a writable volume: the filesystem
 * seam replaces a whole file at a time and has no "write through a descriptor"
 * operation, so accepting O_WRONLY here would hand back a descriptor that must
 * fail on first use.  -EROFS at open is the answer a program can act on. */
static uint32_t *sys_openat(uint32_t *trap_frame, const struct syscall_ops *ops) {
  const int32_t dirfd = (int32_t)trap_frame[TRAP_FRAME_A0];
  const uint32_t path_va = trap_frame[TRAP_FRAME_A1];
  const uint32_t flags = trap_frame[TRAP_FRAME_A2];

  if (dirfd != AT_FDCWD) return err(trap_frame, AX_EINVAL);
  if ((flags & O_ACCMODE) != O_RDONLY) return err(trap_frame, AX_EROFS);

  char path[AXOS_PATH_MAX];
  const uint32_t fault = copy_path(ops, path_va, path);
  if (fault != 0) return err(trap_frame, fault);

  const int file = ops->file_open(path);
  if (file < 0) return err(trap_frame, AX_ENOENT);

  struct fd_entry *const fds = active_fds(ops);
  if (fds == 0) return err(trap_frame, AX_EBADF);
  for (uint32_t i = 0; i < AXOS_MAX_FDS; ++i) {
    if (fds[i].file != 0) continue;
    fds[i].file = (uint32_t)file + 1u;
    fds[i].offset = 0;
    return ret(trap_frame, (uint32_t)(FD_FIRST + (int)i));
  }
  return err(trap_frame, AX_EMFILE);
}

static uint32_t *sys_close(uint32_t *trap_frame,
                           const struct syscall_ops *ops) {
  const int32_t fd = (int32_t)trap_frame[TRAP_FRAME_A0];
  /* Closing a console descriptor succeeds and does nothing.  The console is not
   * a file and cannot be reopened, and -EBADF here would break the ordinary
   * libc shutdown path to make a point no program benefits from. */
  if (fd == FD_STDIN || fd == FD_STDOUT || fd == FD_STDERR)
    return ret(trap_frame, 0);

  struct fd_entry *const fds = active_fds(ops);
  if (fds == 0) return err(trap_frame, AX_EBADF);
  const int slot = slot_of(fds, fd);
  if (slot < 0) return err(trap_frame, AX_EBADF);
  fds[slot].file = 0;
  return ret(trap_frame, 0);
}

/* read(fd, buf, count).
 *
 * Copies through a bounded kernel buffer rather than into userspace directly:
 * sstatus.SUM stays clear, so every user byte goes through the translation the
 * kernel validated.  A short read is legal, and is what happens at end of file
 * or at the first unwritable user page. */
static uint32_t *sys_read(uint32_t *trap_frame, const struct syscall_ops *ops) {
  const int32_t fd = (int32_t)trap_frame[TRAP_FRAME_A0];
  const uint32_t buf = trap_frame[TRAP_FRAME_A1];
  const uint32_t count = trap_frame[TRAP_FRAME_A2];

  /* No input source is bound to the console; end-of-file keeps a libc's
   * getchar loop terminating instead of spinning. */
  if (fd == FD_STDIN) return ret(trap_frame, 0);
  if (fd == FD_STDOUT || fd == FD_STDERR) return err(trap_frame, AX_EBADF);

  struct fd_entry *const fds = active_fds(ops);
  if (fds == 0) return err(trap_frame, AX_EBADF);
  const int slot = slot_of(fds, fd);
  if (slot < 0) return err(trap_frame, AX_EBADF);

  uint32_t done = 0;
  while (done < count) {
    char chunk[AXOS_IO_CHUNK];
    uint32_t want = count - done;
    if (want > sizeof(chunk)) want = sizeof(chunk);

    const int32_t got =
        ops->file_read(file_of(fds, slot), fds[slot].offset, chunk, want);
    if (got < 0) return done ? ret(trap_frame, done) : err(trap_frame, AX_EIO);
    if (got == 0) break;                                   /* end of file */
    if (!ops->copy_to_user(buf + done, chunk, (uint32_t)got))
      return done ? ret(trap_frame, done) : err(trap_frame, AX_EFAULT);

    fds[slot].offset += (uint32_t)got;
    done += (uint32_t)got;
  }
  return ret(trap_frame, done);
}

/* lseek, in the shape 32-bit RISC-V Linux actually gives it: number 62 is
 * llseek, taking the offset as a register pair and returning the result through
 * a 64-bit out-parameter with 0 in a0.  Following the standard means following
 * this part of it too -- a libc built for rv32 emits exactly this call, and a
 * simplified 32-bit `lseek` would work only with our own libc. */
static uint32_t *sys_llseek(uint32_t *trap_frame, const struct syscall_ops *ops) {
  struct fd_entry *const fds = active_fds(ops);
  if (fds == 0) return err(trap_frame, AX_EBADF);
  const int slot = slot_of(fds, (int32_t)trap_frame[TRAP_FRAME_A0]);
  const uint32_t offset_high = trap_frame[TRAP_FRAME_A1];
  const int32_t offset_low = (int32_t)trap_frame[TRAP_FRAME_A2];
  const uint32_t result_va = trap_frame[TRAP_FRAME_A3];
  const uint32_t whence = trap_frame[TRAP_FRAME_A4];

  if (slot < 0) return err(trap_frame, AX_EBADF);
  /* A v1 file is at most one block, so an offset that does not fit in a
   * sign-extended 32 bits is out of range rather than unsupported. */
  if (offset_high != (uint32_t)(offset_low >> 31))
    return err(trap_frame, AX_EINVAL);

  const int32_t size = ops->file_size(file_of(fds, slot));
  if (size < 0) return err(trap_frame, AX_EBADF);

  int32_t base;
  if (whence == SEEK_SET) base = 0;
  else if (whence == SEEK_CUR) base = (int32_t)fds[slot].offset;
  else if (whence == SEEK_END) base = size;
  else return err(trap_frame, AX_EINVAL);

  const int32_t where = base + offset_low;
  /* Seeking past the end is legal and reads back as end-of-file; seeking before
   * the start is not. */
  if (where < 0) return err(trap_frame, AX_EINVAL);
  fds[slot].offset = (uint32_t)where;

  const uint32_t result[2] = {(uint32_t)where, 0};
  if (!ops->copy_to_user(result_va, result, sizeof(result)))
    return err(trap_frame, AX_EFAULT);
  return ret(trap_frame, 0);
}

/* fstat(fd, statbuf), in the asm-generic 32-bit `struct stat` layout: 80 bytes,
 * word-indexed here so the offsets are stated once and are checkable against
 * the header rather than implied by a struct this kernel would have to keep in
 * step by hand.  Everything not known -- device, inode, owner, times -- reports
 * zero, which is honest for a filesystem that does not record it. */
enum {
  STAT_WORDS = 20,
  STAT_MODE = 2,
  STAT_NLINK = 3,
  STAT_SIZE = 8,
  STAT_BLKSIZE = 9,
  STAT_BLOCKS = 11,
  S_IFCHR = 0020000,
  S_IFREG = 0100000,
};

static uint32_t *sys_fstat(uint32_t *trap_frame, const struct syscall_ops *ops) {
  const int32_t fd = (int32_t)trap_frame[TRAP_FRAME_A0];
  const uint32_t out_va = trap_frame[TRAP_FRAME_A1];
  uint32_t st[STAT_WORDS];
  for (uint32_t i = 0; i < STAT_WORDS; ++i) st[i] = 0;

  if (fd == FD_STDIN || fd == FD_STDOUT || fd == FD_STDERR) {
    /* A libc checks this to decide whether to line-buffer; a character device
     * is what the console is. */
    st[STAT_MODE] = S_IFCHR | 0666;
    st[STAT_NLINK] = 1;
    st[STAT_BLKSIZE] = 1;
  } else {
    struct fd_entry *const fds = active_fds(ops);
    if (fds == 0) return err(trap_frame, AX_EBADF);
    const int slot = slot_of(fds, fd);
    if (slot < 0) return err(trap_frame, AX_EBADF);
    const int32_t size = ops->file_size(file_of(fds, slot));
    if (size < 0) return err(trap_frame, AX_EBADF);
    st[STAT_MODE] = S_IFREG | 0444;
    st[STAT_NLINK] = 1;
    st[STAT_SIZE] = (uint32_t)size;
    st[STAT_BLKSIZE] = 512;
    st[STAT_BLOCKS] = ((uint32_t)size + 511u) / 512u;
  }
  if (!ops->copy_to_user(out_va, st, sizeof(st)))
    return err(trap_frame, AX_EFAULT);
  return ret(trap_frame, 0);
}

/* role_info(info): copy `{id, version, capabilities}` to userspace.  Absence is
 * a successful query with id/version/capabilities all zero, not an error. */
static uint32_t *sys_role_info(uint32_t *trap_frame,
                               const struct syscall_ops *ops) {
  uint32_t info[3];
  ops->role_info(info);
  if (!ops->copy_to_user(trap_frame[TRAP_FRAME_A0], info, sizeof(info)))
    return err(trap_frame, AX_EFAULT);
  return ret(trap_frame, 0);
}

/* role_submit(op, request, request_len): validate/copy the opaque encoded job,
 * run it through the kernel-owned driver, and return a positive completion
 * token.  Only one result is retained because the hardware window itself
 * accepts one job at a time. */
static uint32_t *sys_role_submit(uint32_t *trap_frame,
                                 const struct syscall_ops *ops) {
  const uint32_t op = trap_frame[TRAP_FRAME_A0];
  const uint32_t request_va = trap_frame[TRAP_FRAME_A1];
  const uint32_t request_len = trap_frame[TRAP_FRAME_A2];
  const uint32_t owner = ops->task_slot();

  if (owner >= TASK_SLOTS) return err(trap_frame, AX_EINVAL);
  if (role_pending) return err(trap_frame, AX_EBUSY);
  if (request_len > sizeof(role_request))
    return err(trap_frame, AX_EMSGSIZE);
  if (request_len != 0 &&
      !ops->copy_from_user(role_request, request_va, request_len))
    return err(trap_frame, AX_EFAULT);

  uint32_t out_len = 0;
  const int rc = ops->role_execute(op, role_request, request_len,
                                   role_response, sizeof(role_response),
                                   &out_len);
  if (rc == AX_ROLE_EXEC_BAD_OP || rc == AX_ROLE_EXEC_BAD_LEN)
    return err(trap_frame, AX_EINVAL);
  if (rc == AX_ROLE_EXEC_NO_ROLE) return err(trap_frame, AX_ENODEV);
  if (rc == AX_ROLE_EXEC_NO_SPACE) return err(trap_frame, AX_EMSGSIZE);
  if (rc == AX_ROLE_EXEC_TIMEOUT) return err(trap_frame, AX_ETIMEDOUT);
  if (rc != AX_ROLE_EXEC_OK || out_len > sizeof(role_response))
    return err(trap_frame, AX_EIO);

  role_token = role_next_token++;
  if (role_next_token == 0 || role_next_token > 0x7fffffffu)
    role_next_token = 1;
  role_response_len = out_len;
  role_owner = owner;
  role_pending = 1;
  return ret(trap_frame, role_token);
}

/* role_wait(token, response, capacity): collect and retire the caller's saved
 * result.  A bad pointer or short buffer leaves it pending so the caller may
 * retry instead of losing a completed hardware job. */
static uint32_t *sys_role_wait(uint32_t *trap_frame,
                               const struct syscall_ops *ops) {
  const uint32_t token = trap_frame[TRAP_FRAME_A0];
  const uint32_t response_va = trap_frame[TRAP_FRAME_A1];
  const uint32_t capacity = trap_frame[TRAP_FRAME_A2];

  if (!role_pending || role_owner != ops->task_slot() || token != role_token)
    return err(trap_frame, AX_EINVAL);
  if (capacity < role_response_len) return err(trap_frame, AX_EMSGSIZE);
  if (role_response_len != 0 &&
      !ops->copy_to_user(response_va, role_response, role_response_len))
    return err(trap_frame, AX_EFAULT);

  const uint32_t result = role_response_len;
  role_pending = 0;
  role_owner = TASK_SLOTS;
  return ret(trap_frame, result);
}

uint32_t *syscall_dispatch(uint32_t *trap_frame, const struct syscall_ops *ops) {
  const uint32_t nr = trap_frame[TRAP_FRAME_A7];

  switch (nr) {
    /* --- process control -------------------------------------------------
     * These delegate to the kernel and may resume a different task, so they
     * do not go through ret(): the hook owns sepc for the frame it returns. */
    case NR_clone:
      /* RISC-V has no fork.  clone with a zero stack pointer and no thread
       * flags is fork; anything else needs a thread model this kernel does not
       * have, so it is refused rather than silently mishandled. */
      if (trap_frame[TRAP_FRAME_A1] != 0) return err(trap_frame, AX_EINVAL);
      return ops->fork(trap_frame);

    case NR_wait4:
      /* wait4(pid, status, options, rusage); only the blocking any-child form
       * exists.  rusage is accepted and ignored, as a null pointer would be. */
      return ops->wait(trap_frame, trap_frame[TRAP_FRAME_A1]);

    case NR_exit:
      return ops->exit(trap_frame, trap_frame[TRAP_FRAME_A0]);

    /* --- simple returns --------------------------------------------------- */
    case NR_getpid:
      return ret(trap_frame, ops->getpid());

    case NR_brk: {
      const uint32_t requested = trap_frame[TRAP_FRAME_A0];
      const uint32_t result = ops->brk(requested);
      /* brk reports failure by returning the unchanged break, not by an errno:
       * that is the kernel-level contract a libc's sbrk is written against. */
      return ret(trap_frame, result);
    }

    case NR_write:
      return sys_write(trap_frame, ops);

    case NR_read:
      return sys_read(trap_frame, ops);

    /* --- files ------------------------------------------------------------ */
    case NR_openat:
      return sys_openat(trap_frame, ops);

    case NR_close:
      return sys_close(trap_frame, ops);

    case NR_lseek:
      return sys_llseek(trap_frame, ops);

    case NR_fstat:
      return sys_fstat(trap_frame, ops);

    /* --- atomiX private range --------------------------------------------- */
    case NR_ax_role_info:
      return sys_role_info(trap_frame, ops);

    case NR_ax_role_submit:
      return sys_role_submit(trap_frame, ops);

    case NR_ax_role_wait:
      return sys_role_wait(trap_frame, ops);

    default:
      /* An unknown number is never fatal.  A libc probing for a call it can
       * live without must get -ENOSYS back, not a dead process. */
      return err(trap_frame, AX_ENOSYS);
  }
}
