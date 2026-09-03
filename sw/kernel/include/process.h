#pragma once

#include <stdint.h>

#include "loader.h"

/* The program a diskless build embeds, and the one `exec` runs when given no
 * name.  These have to be the same string -- the shell's default must be a
 * program the kernel can find -- so it is one define rather than two literals
 * in different components.  sw/kernel/Makefile derives it from the embedded
 * ELF's own filename, so changing which program is embedded does not mean
 * editing the kernel. */
#ifndef AXOS_EMBED_USER_NAME
#define AXOS_EMBED_USER_NAME "hello.elf"
#endif

/* The most arguments the shell will accept for a program.  It follows the
 * loader's bound by default rather than being a second number that can
 * silently disagree with it; kernel.c asserts the relationship for the case
 * where a profile sets them apart deliberately. */
#ifndef KERNEL_PROCESS_ARG_MAX
#define KERNEL_PROCESS_ARG_MAX LOADER_ARG_MAX
#endif

enum {
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
