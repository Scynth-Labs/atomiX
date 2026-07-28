#pragma once

#include <stdint.h>

/* Read-only kernel observability used by the resident management shell.
 *
 * Keeping this as a narrow value-copying interface lets a replaceable shell
 * report useful system state without gaining ownership of the scheduler's task
 * table or the allocator's free list. */

#define AXOS_NAME "aXos"
#define AXOS_VERSION "0.1"
#define AXOS_ARCH "rv32im"

struct kernel_task_info {
  uint32_t pid;
  uint32_t parent_pid;
  uint32_t state;
};

/* Timer interrupts observed since S-mode started.  A tick deliberately has no
 * wall-clock unit: the CLINT timebase is a platform property and aXos has no
 * RTC or device-tree timebase discovery yet. */
uint32_t kernel_uptime_ticks(void);

/* Physical pages managed by the selected allocator. */
uint32_t kernel_total_pages(void);
uint32_t kernel_free_pages(void);

/* Copy up to `capacity` active task records to `out`, returning the number
 * copied.  The shell itself is an S-mode service rather than a user task and is
 * therefore not included. */
uint32_t kernel_task_snapshot(struct kernel_task_info *out, uint32_t capacity);
