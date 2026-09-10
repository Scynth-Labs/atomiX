/* Native host implementation of org.atomix.workload.saxpy-i32.
 *
 * This is the software leg of the native/RTL comparison: an ordinary host
 * process with no SoC, no board, no loader, and no RISC-V toolchain.  It is
 * built by the native-cpu execution adapter with the host's own C compiler.
 *
 * Two properties matter more than speed here.
 *
 * Wrapping is explicit.  The workload declares two's-complement wrap, and
 * signed overflow in C is undefined -- a compiler may legally assume it never
 * happens and delete the very case the int32-wrap fixture exists to check.
 * The arithmetic is therefore done in uint32_t and converted back through a
 * value-preserving path, so the host result matches the oracle bit for bit at
 * INT32_MIN and INT32_MAX rather than by luck of the optimiser.
 *
 * Repetitions are real.  The inputs do not change between repetitions, so at
 * -O2 the kernel is otherwise hoistable: measured once, reported five times.  A
 * compiler barrier between repetitions keeps each one a separate execution.
 * The barrier sits *outside* the timed region so it costs nothing measured.
 *
 * Timing covers the kernel only.  Reading the input, allocating buffers, and
 * printing the result are excluded and belong to the adapter's own boundary.
 */
/* clock_gettime and CLOCK_MONOTONIC are POSIX, not ISO C: a strict -std=c11
 * build hides them until the feature test macro asks for them by name. */
#define _POSIX_C_SOURCE 199309L

#include <inttypes.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#if defined(__GNUC__) || defined(__clang__)
#define AX_BARRIER() __asm__ volatile("" ::: "memory")
#else
#define AX_BARRIER() do { } while (0)
#endif

#define AX_MAX_ITEMS 1048576

static int32_t wrap_i32(uint32_t value) {
  /* Implementation-defined conversion is avoided: values above INT32_MAX are
   * folded down by the modulus before the cast, which is exact in two's
   * complement and defined everywhere. */
  if (value <= (uint32_t)INT32_MAX) return (int32_t)value;
  return (int32_t)(value - (uint32_t)INT32_MAX - 1u) - INT32_MAX - 1;
}

static void saxpy(int32_t *restrict out, const int32_t *restrict x,
                  const int32_t *restrict y, int32_t a, size_t items) {
  const uint32_t factor = (uint32_t)a;
  for (size_t i = 0; i < items; ++i) {
    out[i] = wrap_i32(factor * (uint32_t)x[i] + (uint32_t)y[i]);
  }
}

static uint64_t now_ns(void) {
  struct timespec ts;
  if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) {
    fprintf(stderr, "saxpy_i32: CLOCK_MONOTONIC is unavailable\n");
    exit(3);
  }
  return (uint64_t)ts.tv_sec * 1000000000u + (uint64_t)ts.tv_nsec;
}

struct input {
  long items;
  long a;
  long repetitions;
  int32_t *x;
  int32_t *y;
};

static int read_vector(FILE *stream, int32_t **out, long items, const char *name) {
  int32_t *values = calloc((size_t)items ? (size_t)items : 1, sizeof *values);
  if (!values) return -1;
  for (long i = 0; i < items; ++i) {
    long long value;
    if (fscanf(stream, "%lld", &value) != 1) {
      fprintf(stderr, "saxpy_i32: vector %s ended after %ld of %ld values\n",
              name, i, items);
      free(values);
      return -1;
    }
    if (value < INT32_MIN || value > INT32_MAX) {
      fprintf(stderr, "saxpy_i32: %s[%ld] is outside int32\n", name, i);
      free(values);
      return -1;
    }
    values[i] = (int32_t)value;
  }
  *out = values;
  return 0;
}

static int read_input(const char *path, struct input *input) {
  FILE *stream = fopen(path, "r");
  if (!stream) {
    fprintf(stderr, "saxpy_i32: cannot open %s\n", path);
    return -1;
  }
  input->items = -1;
  input->a = 0;
  input->repetitions = 1;
  input->x = NULL;
  input->y = NULL;
  char key[32];
  int status = 0;
  while (fscanf(stream, "%31s", key) == 1) {
    if (strcmp(key, "items") == 0) {
      if (fscanf(stream, "%ld", &input->items) != 1 || input->items < 0 ||
          input->items > AX_MAX_ITEMS) {
        fprintf(stderr, "saxpy_i32: items must be 0..%d\n", AX_MAX_ITEMS);
        status = -1;
        break;
      }
    } else if (strcmp(key, "a") == 0) {
      if (fscanf(stream, "%ld", &input->a) != 1 ||
          input->a < INT32_MIN || input->a > INT32_MAX) {
        status = -1;
        break;
      }
    } else if (strcmp(key, "repetitions") == 0) {
      if (fscanf(stream, "%ld", &input->repetitions) != 1 ||
          input->repetitions < 1 || input->repetitions > 100000) {
        status = -1;
        break;
      }
    } else if (strcmp(key, "x") == 0 || strcmp(key, "y") == 0) {
      if (input->items < 0) {
        fprintf(stderr, "saxpy_i32: items must precede the %s vector\n", key);
        status = -1;
        break;
      }
      int32_t **target = key[0] == 'x' ? &input->x : &input->y;
      if (*target) {
        fprintf(stderr, "saxpy_i32: vector %s appears twice\n", key);
        status = -1;
        break;
      }
      if (read_vector(stream, target, input->items, key) != 0) {
        status = -1;
        break;
      }
    } else {
      fprintf(stderr, "saxpy_i32: unknown input key %s\n", key);
      status = -1;
      break;
    }
  }
  fclose(stream);
  if (status == 0 && (input->items < 0 || !input->x || !input->y)) {
    fprintf(stderr, "saxpy_i32: input needs items, x, and y\n");
    status = -1;
  }
  return status;
}

int main(int argc, char **argv) {
  const char *path = NULL;
  for (int i = 1; i < argc; ++i) {
    if (strcmp(argv[i], "--input") == 0 && i + 1 < argc) {
      path = argv[++i];
    } else {
      fprintf(stderr, "usage: %s --input <case-file>\n", argv[0]);
      return 2;
    }
  }
  if (!path) {
    fprintf(stderr, "usage: %s --input <case-file>\n", argv[0]);
    return 2;
  }

  struct input input;
  if (read_input(path, &input) != 0) return 2;

  const size_t items = (size_t)input.items;
  int32_t *out = calloc(items ? items : 1, sizeof *out);
  uint64_t *elapsed = calloc((size_t)input.repetitions, sizeof *elapsed);
  if (!out || !elapsed) {
    fprintf(stderr, "saxpy_i32: out of memory\n");
    return 3;
  }

  for (long repetition = 0; repetition < input.repetitions; ++repetition) {
    AX_BARRIER();
    const uint64_t started = now_ns();
    saxpy(out, input.x, input.y, (int32_t)input.a, items);
    const uint64_t finished = now_ns();
    AX_BARRIER();
    elapsed[repetition] = finished - started;
  }

  printf("{\"workload\":\"org.atomix.workload.saxpy-i32\",\"items\":%ld,\"a\":%ld,",
         input.items, input.a);
  printf("\"repetitions\":%ld,\"out\":[", input.repetitions);
  for (size_t i = 0; i < items; ++i) {
    printf("%s%" PRId32, i ? "," : "", out[i]);
  }
  printf("],\"elapsed_ns\":[");
  for (long i = 0; i < input.repetitions; ++i) {
    printf("%s%" PRIu64, i ? "," : "", elapsed[i]);
  }
  printf("]}\n");

  free(out);
  free(elapsed);
  free(input.x);
  free(input.y);
  return 0;
}
