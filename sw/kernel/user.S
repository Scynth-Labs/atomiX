/* The first aXos user payload: a hand-written ABI conformance test.
 *
 * It exists to check docs/abi.md before any C library does, so the ABI is
 * verified against the contract rather than against whatever a libc happens to
 * emit.  No library, no stack frame, no relocations.
 *
 * It enters S-mode through ecall, then spends time in U-mode so CLINT-driven
 * preemption and resumption are exercised by the kernel test.
 *
 * Every syscall here uses the standard RISC-V Linux numbers: there is no
 * `fork` or `wait` on this architecture, only `clone` and `wait4`, and the
 * console is reached by `write(1, ...)` rather than by a bespoke call. */
  .equ NR_write,  64
  .equ NR_exit,   93
  .equ NR_getpid, 172
  .equ NR_clone,  220
  .equ NR_wait4,  260
  .equ ENOSYS,    38

  .section .usertext, "ax", @progbits
  .globl user_entry
  .type user_entry, @function
user_entry:
  lui t0, 0x40001                 /* private user stack: 0x40001000 */
  li t1, 0x51a00000
  ori t1, t1, 1                   /* marker copied into the child stack */
  sw t1, 0(t0)

  /* --- ABI checks before forking -----------------------------------------
   * A wrong answer here parks in a spin loop, which the kernel's cycle budget
   * turns into a test failure.  Checking before the fork keeps the console
   * output the kernel asserts on unchanged. */

  /* An unimplemented number must return -ENOSYS, not kill the process: a libc
   * probing for a call it can live without depends on this. */
  li a7, 1234
  ecall
  li t2, -ENOSYS
  bne a0, t2, abi_fail

  /* getpid returns a plausible pid.  The first user task is pid 1. */
  li a7, NR_getpid
  ecall
  blez a0, abi_fail

  /* write() to a bad user pointer is -EFAULT, not a supervisor fault: an
   * unprivileged program must not be able to fault the kernel. */
  li a0, 1
  li a1, 0                        /* unmapped */
  li a2, 4
  li a7, NR_write
  ecall
  li t2, -14                      /* -EFAULT */
  bne a0, t2, abi_fail

  /* write() to a bad descriptor is -EBADF. */
  li a0, 99
  lui a1, 0x40001
  li a2, 1
  li a7, NR_write
  ecall
  li t2, -9                       /* -EBADF */
  bne a0, t2, abi_fail

  /* --- the fork demo the kernel asserts on -------------------------------- */
  li a0, 0                        /* clone flags */
  li a1, 0                        /* child stack 0 => fork */
  li a7, NR_clone
  ecall
  bnez a0, 1f

  /* child */
  li a0, 'C'
  jal ra, putc
  li a0, 7                        /* parent must observe 7 << 8 via wait4 */
  li a7, NR_exit
  ecall
0:
  nop
  j 0b

1:
  /* parent */
  li a0, 'P'
  jal ra, putc
  li a0, -1                       /* wait4(-1, &status, 0, NULL): any child */
  lui a1, 0x40001
  addi a1, a1, 12
  li a2, 0
  li a3, 0
  li a7, NR_wait4
  ecall
  blez a0, abi_fail
  lui t0, 0x40001
  lw t1, 12(t0)
  li t2, 0x700
  bne t1, t2, abi_fail
  li a0, 'W'
  jal ra, putc
  li a0, 0
  li a7, NR_exit
  ecall
  j 0b

abi_fail:
2:
  j 2b                            /* spin: the kernel's cycle budget fails */

/* putc(a0): write one byte to stdout through the standard call.  The byte is
 * staged on the user stack because write() takes a pointer, which also
 * exercises the kernel's user-pointer translation on a real address. */
putc:
  lui t0, 0x40001
  addi t0, t0, 8                  /* clear of the 0x51a00001 marker at [0] */
  sb a0, 0(t0)
  li a0, 1                        /* stdout */
  mv a1, t0
  li a2, 1
  li a7, NR_write
  ecall
  ret
  .size user_entry, . - user_entry
