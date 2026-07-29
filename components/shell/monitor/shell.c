#include <stdint.h>

#include "kernel_info.h"
#include "page.h"
#include "platform.h"
#include "role.h"

enum { SHELL_LINE_MAX = 96 };

static int streq(const char *a, const char *b) {
  while (*a && *a == *b) {
    ++a;
    ++b;
  }
  return *a == *b;
}

static void put_u32(uint32_t value) {
  char digits[10];
  uint32_t count = 0;
  do {
    digits[count++] = (char)('0' + value % 10u);
    value /= 10u;
  } while (value);
  while (count) uart_putchar(digits[--count]);
}

static void readline(char *line, uint32_t capacity) {
  uint32_t length = 0;
  for (;;) {
    const char c = uart_getchar();
    if (c == '\r' || c == '\n') {
      line[length] = 0;
      uart_puts("\n");
      return;
    }
    if ((c == '\b' || c == 0x7f) && length) {
      --length;
      uart_puts("\b \b");
    } else if (c >= ' ' && c <= '~' && length + 1u < capacity) {
      line[length++] = c;
      uart_putchar(c);
    }
  }
}

static char *skip_space(char *text) {
  while (*text == ' ' || *text == '\t') ++text;
  return text;
}

static char *argument(char *line) {
  while (*line && *line != ' ' && *line != '\t') ++line;
  if (!*line) return line;
  *line++ = 0;
  return skip_space(line);
}

static void command_free(void) {
  const uint32_t total = kernel_total_pages();
  const uint32_t free = kernel_free_pages();
  uart_puts("memory: ");
  put_u32(PAGE_SIZE);
  uart_puts("-byte pages, ");
  put_u32(total);
  uart_puts(" total, ");
  put_u32(free);
  uart_puts(" free, ");
  put_u32(total - free);
  uart_puts(" used\n");
}

static void command_role(void) {
  const uint32_t id = role_discover();
  if (id == 0) {
    uart_puts("role: none\n");
    return;
  }
  uart_puts("role: ");
  uart_puts(role_name(id));
  uart_puts(" v");
  put_u32(role_version());
  uart_puts("\n");
  if (id == AX_ROLE_ID_LOOPBACK)
    uart_puts(role_loopback_selftest() == 0 ? "role: copy ok\n"
                                            : "role: copy FAIL\n");
}

static void dispatch(char *line) {
  line = skip_space(line);
  char *const arg = argument(line);

  if (!*line) return;
  if (streq(line, "help")) {
    uart_puts("commands: help clear uname uptime free ps echo role shutdown exit\n");
  } else if (streq(line, "clear")) {
    uart_puts("\033[2J\033[H");
  } else if (streq(line, "uname")) {
    if (!*arg) uart_puts(AXOS_NAME "\n");
    else if (streq(arg, "-a"))
      uart_puts(AXOS_NAME " " AXOS_VERSION " " AXOS_ARCH " riscv monitor\n");
    else uart_puts("uname: usage uname [-a]\n");
  } else if (streq(line, "uptime")) {
    uart_puts("uptime: ");
    put_u32(kernel_uptime_ticks());
    uart_puts(" timer ticks\n");
  } else if (streq(line, "free")) {
    command_free();
  } else if (streq(line, "ps")) {
    uart_puts("PID PPID STATE NAME\n0 0 running [kernel/monitor]\n");
  } else if (streq(line, "echo")) {
    uart_puts(arg);
    uart_puts("\n");
  } else if (streq(line, "role")) {
    command_role();
  } else if (streq(line, "shutdown") || streq(line, "exit")) {
    test_finish(0);
  } else {
    uart_puts("sh: command not found: ");
    uart_puts(line);
    uart_puts("\n");
  }
}

void shell_run(void) {
  char line[SHELL_LINE_MAX];
  uart_puts("aXos: monitor shell online\n");
  for (;;) {
    uart_puts("aXos> ");
    readline(line, sizeof(line));
    dispatch(line);
  }
}
