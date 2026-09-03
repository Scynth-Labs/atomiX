#pragma once

#include <stdint.h>

#include "task.h"

/* The syscall component seam.
 *
 * The kernel owns the trap: it decodes scause, saves the register file, and
 * decides which task runs next.  What it does NOT own is what an `ecall` from
 * U-mode *means* -- that is the ABI, and the ABI is selectable.  A component
 * implementing this header defines the syscall numbers, their signatures, and
 * their error convention; the reference implementation follows the RISC-V Linux
 * subset documented in docs/abi.md, and an out-of-tree component can define
 * something else without touching the kernel, the scheduler, or the loader.
 *
 * Trap-frame layout is fixed by trap.S and shared with the kernel. */
enum {
  TRAP_FRAME_A0 = 8,
  TRAP_FRAME_A1 = 9,
  TRAP_FRAME_A2 = 10,
  TRAP_FRAME_A3 = 11,
  TRAP_FRAME_A4 = 12,
  TRAP_FRAME_A5 = 13,
  TRAP_FRAME_A7 = 15,
  TRAP_FRAME_SP = 30,
};

/* Linux errno values, negated on return (docs/abi.md).  A return in
 * [-4095, -1] is an error; anything else is a result.  This is what lets a libc
 * wrapper be the standard "negate into errno, return -1" shim. */
enum {
  AX_EPERM = 1,
  AX_ENOENT = 2,
  AX_EIO = 5,
  AX_EBADF = 9,
  AX_ECHILD = 10,
  AX_EAGAIN = 11,
  AX_ENOMEM = 12,
  AX_EFAULT = 14,
  AX_EBUSY = 16,
  AX_ENODEV = 19,
  AX_EINVAL = 22,
  AX_EMFILE = 24,
  AX_EROFS = 30,
  AX_ENAMETOOLONG = 36,
  AX_ENOSYS = 38,
  AX_EMSGSIZE = 90,
  AX_ETIMEDOUT = 110,
};

/* Services the syscall component needs from the kernel.  Passed in rather than
 * linked against so the component does not have to know how the kernel names
 * its internals -- the same leniency the RTL seams have.
 *
 * Each process-control hook returns the trap frame to resume on, which may
 * belong to a different task than the one that trapped (fork returning into the
 * child, exit switching away from a dead task). */
struct syscall_ops {
  /* Duplicate the calling task.  Returns the frame to resume; sets the child's
   * a0 to 0 and the parent's to the child pid. */
  uint32_t *(*fork)(uint32_t *trap_frame);
  /* Block until a child exits.  `status_va` is a user pointer or 0. */
  uint32_t *(*wait)(uint32_t *trap_frame, uint32_t status_va);
  /* Terminate the calling task with `code`. */
  uint32_t *(*exit)(uint32_t *trap_frame, uint32_t code);
  /* pid of the calling task. */
  uint32_t (*getpid)(void);
  /* Stable task-table slot of the caller, used for ABI-owned per-process
   * descriptor state. */
  uint32_t (*task_slot)(void);
  /* Move the program break; returns the new break, or the current one when
   * `addr` is 0.  Returns 0 if the request cannot be satisfied. */
  uint32_t (*brk)(uint32_t addr);
  /* Write one console byte.  The console is a kernel resource, not a file, so
   * write(1|2) funnels through here. */
  void (*console_putc)(char c);
  /* Copy `len` bytes out of the calling task's address space into `dst`.
   * Returns non-zero on success; a bad user pointer is a clean failure so the
   * kernel returns -EFAULT rather than faulting itself. */
  int (*copy_from_user)(void *dst, uint32_t user_va, uint32_t len);
  /* The same in the other direction, for calls that return data rather than a
   * scalar (read, fstat).  Fails cleanly on an unwritable page, so a program
   * cannot make the kernel write through a bad pointer. */
  int (*copy_to_user)(uint32_t user_va, const void *src, uint32_t len);

  /* Files.  These are the filesystem seam as the ABI sees it: names resolve to
   * ids, ids read byte ranges.  Descriptors -- the offset and the lifetime --
   * belong to the syscall component, because which numbers a program gets back
   * and what it may do with them is exactly what an ABI decides.
   *
   * Returns a file id >= 0, or negative when there is no such file. */
  int (*file_open)(const char *name);
  /* Size in bytes, or negative for a bad id. */
  int32_t (*file_size)(int file);
  /* Read up to `len` bytes at `offset`; 0 means end of file, negative is an
   * error. */
  int32_t (*file_read)(int file, uint32_t offset, void *dst, uint32_t len);

  /* Accelerator role control.  The kernel owns MMIO and returns only checked
   * discovery data and encoded job results to the ABI component. */
  void (*role_info)(uint32_t info[3]); /* id, version, capability word */
  int (*role_execute)(uint32_t op, const uint8_t *request,
                      uint32_t request_len, uint8_t *response,
                      uint32_t response_cap, uint32_t *response_len);
};

/* Descriptor lifecycle remains owned by the ABI component. Reset every table,
 * reset one stable task slot, or copy a parent's table during clone. */
void syscall_reset(void);
void syscall_task_reset(uint32_t task_slot);
void syscall_task_clone(uint32_t child_slot, uint32_t parent_slot);

/* Called by the kernel for every U-mode ecall.  Returns the trap frame to
 * resume.  The component is responsible for advancing sepc past the ecall for
 * calls that return to the caller; calls that switch tasks leave that to the
 * kernel hook they delegate to. */
uint32_t *syscall_dispatch(uint32_t *trap_frame, const struct syscall_ops *ops);
