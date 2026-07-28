#pragma once

#include <stdint.h>

enum {
  KERNEL_PROCESS_ARG_MAX = 8,
  KERNEL_RUN_ENOENT = -1,
  KERNEL_RUN_ETOOBIG = -2,
  KERNEL_RUN_ELOAD = -3,
  KERNEL_RUN_EBUSY = -4,
};

/* Run one root user process synchronously while the resident S-mode shell acts
 * as the idle context. argv includes argv[0]. Returns the process exit status,
 * or a negative KERNEL_RUN_* error before execution begins. */
int kernel_run_program(const char *name, uint32_t argc,
                       const char *const argv[]);

/* Run the built-in fork/wait ABI fixture and return to the caller. */
int kernel_fork_demo(void);
