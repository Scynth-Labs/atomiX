#pragma once

#include <stdint.h>

/* This is the small, intentionally visible contract shared by scheduler and
 * virtual-memory components.  The kernel owns trap/syscall semantics; a
 * component only observes the task state and address-space fields it needs. */
enum {
  TASK_SLOTS = 4,
  TASK_NONE = TASK_SLOTS,
  TASK_UNUSED = 0,
  TASK_RUNNABLE = 1,
  TASK_RUNNING = 2,
  TASK_BLOCKED = 3,
  TASK_ZOMBIE = 4,
};

struct task {
  uint32_t *trap_frame;
  uint32_t sepc;
  uint32_t sstatus;
  uint32_t satp;
  uint32_t *page_root;
  uint32_t *user_pt;
  uint32_t *user_stack;
  uint32_t *kernel_stack;
  /* Program break: the top of the heap brk()/sbrk() move.  Set by the loader
   * to the page after the highest loaded segment, so the heap grows into the
   * gap between the program image and the stack. */
  uint32_t brk;
  /* The floor and ceiling the break may move between.  brk_start is where the
   * loader put the heap, one page past the image; without it a shrink has no
   * lower bound and unmaps the program's own text on its way down. */
  uint32_t brk_start;
  uint32_t brk_limit;
  uint32_t state;
  uint32_t pid;
  uint32_t parent_pid;
  uint32_t exit_status;
  uint32_t wait_status_va;
  char name[16];
};
