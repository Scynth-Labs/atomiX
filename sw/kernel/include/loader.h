#pragma once

#include <stdint.h>

#include "task.h"

/* The program-loader component seam.
 *
 * The kernel owns tasks and address spaces; the loader owns the *format* of a
 * program image and the contract a program starts under.  Those are separable:
 * a system that wanted flat images, or a compressed format, or a position-
 * independent one, would change only this component.
 *
 * The reference implementation is ELF32 (docs/abi.md), chosen because it is
 * what the toolchain already emits and it carries the per-segment permissions
 * the loader needs in order to map pages correctly. */

enum {
  /* Reported when the image is not a program this loader can run: bad magic,
   * wrong class or endianness, wrong machine, or not an executable. */
  LOADER_EBADIMAGE = -1,
  /* The image is valid but does not fit: a segment outside the mappable user
   * region, or not enough physical pages. */
  LOADER_ENOSPACE = -2,
};

/* Kernel-owned argv is deliberately bounded so malformed management input
 * cannot consume the one-page initial user stack. */
#ifndef LOADER_ARG_MAX
#define LOADER_ARG_MAX 8
#endif

/* Load `image` into `task`'s address space and set up its initial state.
 *
 * The task must already have an empty user address space
 * (vm_create_empty_user_space).  On success `*entry_out` is the address to
 * enter at and `*sp_out` is the initial stack pointer, pointing at a System V
 * frame: argc, argv, NULL, envp, NULL, auxv, AT_NULL.
 *
 * Returns 0, or a negative LOADER_* code.  A failed load may leave pages
 * mapped; the caller destroys the address space, which frees them. */
int loader_load(struct task *task, const uint8_t *image, uint32_t size,
                uint32_t *entry_out, uint32_t *sp_out);

/* The same load with a real argv vector. argv[0] is conventionally the program
 * name; no environment is supplied yet. */
int loader_load_args(struct task *task, const uint8_t *image, uint32_t size,
                     uint32_t argc, const char *const argv[],
                     uint32_t *entry_out, uint32_t *sp_out);
