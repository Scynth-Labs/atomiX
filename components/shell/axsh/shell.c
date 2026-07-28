#include <stdint.h>

#include "fs.h"
#include "kernel_info.h"
#include "page.h"
#include "platform.h"
#include "process.h"
#include "role.h"
#include "task.h"

enum {
  SHELL_LINE_MAX = 128,
  SHELL_ARGS_MAX = 12,
};

/* What fs_mount() reported: FS_MOUNT_RW, FS_MOUNT_RO, or negative.  The shell
 * keeps no files of its own -- every file operation uses the selected
 * filesystem component. */
static int mount_state;

static int streq(const char *a, const char *b) {
  while (*a && *a == *b) { ++a; ++b; }
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

static void put_hex_digit(uint32_t value) {
  uart_putchar((char)(value < 10u ? '0' + value : 'a' + value - 10u));
}

static void put_hex8(uint8_t value) {
  put_hex_digit(value >> 4);
  put_hex_digit(value & 0xfu);
}

static void put_hex32(uint32_t value) {
  for (uint32_t shift = 28;; shift -= 4) {
    put_hex_digit((value >> shift) & 0xfu);
    if (shift == 0) return;
  }
}

static void readline(char *line, uint32_t capacity) {
  uint32_t length = 0;
  for (;;) {
    const char c = uart_getchar();
    if (c == '\r' || c == '\n') {
      uart_puts("\n");
      line[length] = 0;
      return;
    }
    if ((c == '\b' || c == 0x7f) && length) {
      --length;
      uart_puts("\b \b");
    } else if (c == 0x15) {
      while (length) {
        --length;
        uart_puts("\b \b");
      }
    } else if (c >= ' ' && c <= '~' && length + 1 < capacity) {
      line[length++] = c;
      uart_putchar(c);
    }
  }
}

/* Split a command line in place. Single and double quotes group arguments;
 * backslash quotes the next character.  This is intentionally expansion-free:
 * a management shell should not imply environment variables or globbing that
 * the kernel does not implement. */
static int32_t split_line(char *line, char **argv, uint32_t capacity) {
  char *read = line;
  char *write = line;
  uint32_t argc = 0;

  while (*read) {
    while (*read == ' ' || *read == '\t') ++read;
    if (!*read) break;
    if (argc == capacity) return -2;
    argv[argc++] = write;
    char quote = 0;
    while (*read) {
      const char c = *read;
      if (!quote && (c == ' ' || c == '\t')) break;
      if (c == '\\' && read[1]) {
        *write++ = read[1];
        read += 2;
      } else if (c == '\'' || c == '"') {
        if (!quote) {
          quote = c;
          ++read;
        } else if (quote == c) {
          quote = 0;
          ++read;
        } else {
          *write++ = *read++;
        }
      } else {
        *write++ = *read++;
      }
    }
    if (quote) {
      *write = 0;
      return -1;
    }
    while (*read == ' ' || *read == '\t') ++read;
    *write++ = 0;
  }
  return argc;
}

/* Rebuild argv[first..] as one space-separated string at argv[first].  It
 * preserves spaces protected by quotes while retaining the historical
 * `write NAME any number of words` and `echo any number of words` behavior. */
static char *join_args(uint32_t argc, char **argv, uint32_t first) {
  char *const result = argv[first];
  char *out = result;
  for (uint32_t i = first; i < argc; ++i) {
    const char *in = argv[i];
    while (*in) *out++ = *in++;
    if (i + 1 < argc) *out++ = ' ';
  }
  *out = 0;
  return result;
}

/* Discover the accelerator role through the shell control plane and, when the
 * loopback contract-proof role is present, drive one job end-to-end. */
static void shell_role(void) {
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

static void shell_cat(const char *name) {
  const int file = fs_lookup(name);
  if (file < 0) {
    uart_puts("cat: no such file\n");
    return;
  }
  char chunk[64];
  for (uint32_t offset = 0;;) {
    const int32_t got = fs_read(file, offset, chunk, sizeof(chunk));
    if (got <= 0) {
      if (got < 0) uart_puts("cat: read error\n");
      return;
    }
    for (int32_t i = 0; i < got; ++i) uart_putchar(chunk[i]);
    offset += (uint32_t)got;
  }
}

struct shell_command {
  const char *name;
  const char *usage;
  const char *summary;
  void (*run)(uint32_t argc, char **argv);
};

extern const struct shell_command shell_commands[];
static uint32_t command_count(void);
static const struct shell_command *find_command(const char *name);

static void command_help(uint32_t argc, char **argv) {
  if (argc == 1) {
    uart_puts("commands:");
    for (uint32_t i = 0; i < command_count(); ++i) {
      uart_puts(" ");
      uart_puts(shell_commands[i].name);
    }
    uart_puts("\n");
    return;
  }
  if (argc != 2) {
    uart_puts("help: usage help [COMMAND]\n");
    return;
  }
  const struct shell_command *const command = find_command(argv[1]);
  if (command == 0) {
    uart_puts("help: no such command\n");
    return;
  }
  uart_puts("usage: ");
  uart_puts(command->usage);
  uart_puts("\n");
  uart_puts(command->summary);
  uart_puts("\n");
}

static void command_clear(uint32_t argc, char **argv) {
  (void)argv;
  if (argc != 1) {
    uart_puts("clear: usage clear\n");
    return;
  }
  uart_puts("\033[2J\033[H");
}

static void command_uname(uint32_t argc, char **argv) {
  if (argc == 1) {
    uart_puts(AXOS_NAME "\n");
  } else if (argc == 2 && streq(argv[1], "-a")) {
    uart_puts(AXOS_NAME " " AXOS_VERSION " " AXOS_ARCH " riscv\n");
  } else {
    uart_puts("uname: usage uname [-a]\n");
  }
}

static void command_uptime(uint32_t argc, char **argv) {
  (void)argv;
  if (argc != 1) {
    uart_puts("uptime: usage uptime\n");
    return;
  }
  uart_puts("uptime: ");
  put_u32(kernel_uptime_ticks());
  uart_puts(" timer ticks\n");
}

static void command_free(uint32_t argc, char **argv) {
  (void)argv;
  if (argc != 1) {
    uart_puts("free: usage free\n");
    return;
  }
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

static const char *task_state_name(uint32_t state) {
  switch (state) {
    case TASK_RUNNABLE: return "runnable";
    case TASK_RUNNING: return "running";
    case TASK_BLOCKED: return "blocked";
    case TASK_ZOMBIE: return "zombie";
    default: return "unknown";
  }
}

static void command_ps(uint32_t argc, char **argv) {
  (void)argv;
  if (argc != 1) {
    uart_puts("ps: usage ps\n");
    return;
  }
  struct kernel_task_info tasks[TASK_SLOTS];
  const uint32_t count = kernel_task_snapshot(tasks, TASK_SLOTS);
  uart_puts("PID PPID STATE NAME\n");
  uart_puts("0 0 running [kernel/shell]\n");
  for (uint32_t i = 0; i < count; ++i) {
    put_u32(tasks[i].pid);
    uart_puts(" ");
    put_u32(tasks[i].parent_pid);
    uart_puts(" ");
    uart_puts(task_state_name(tasks[i].state));
    uart_puts(" ");
    uart_puts(tasks[i].name[0] ? tasks[i].name : "user");
    uart_puts("\n");
  }
}

static void command_pwd(uint32_t argc, char **argv) {
  (void)argv;
  if (argc != 1) {
    uart_puts("pwd: usage pwd\n");
    return;
  }
  uart_puts("/\n");
}

static void command_ls(uint32_t argc, char **argv) {
  (void)argv;
  if (argc != 1) {
    uart_puts("ls: usage ls\n");
    return;
  }
  fs_list();
}

static void command_cat(uint32_t argc, char **argv) {
  if (argc != 2) {
    uart_puts("cat: usage cat FILE\n");
    return;
  }
  shell_cat(argv[1]);
}

static void command_stat(uint32_t argc, char **argv) {
  if (argc != 2) {
    uart_puts("stat: usage stat FILE\n");
    return;
  }
  const int file = fs_lookup(argv[1]);
  const int32_t size = file < 0 ? -1 : fs_size(file);
  if (size < 0) {
    uart_puts("stat: no such file\n");
    return;
  }
  uart_puts(argv[1]);
  uart_puts(": ");
  put_u32((uint32_t)size);
  uart_puts(" bytes, ");
  uart_puts(mount_state == FS_MOUNT_RW ? "read-write\n" : "read-only\n");
}

static void command_hexdump(uint32_t argc, char **argv) {
  if (argc != 2) {
    uart_puts("hexdump: usage hexdump FILE\n");
    return;
  }
  const int file = fs_lookup(argv[1]);
  if (file < 0) {
    uart_puts("hexdump: no such file\n");
    return;
  }
  uint8_t bytes[16];
  for (uint32_t offset = 0;; offset += sizeof(bytes)) {
    const int32_t got = fs_read(file, offset, bytes, sizeof(bytes));
    if (got <= 0) {
      if (got < 0) uart_puts("hexdump: read error\n");
      return;
    }
    put_hex32(offset);
    uart_puts("  ");
    for (uint32_t i = 0; i < sizeof(bytes); ++i) {
      if (i < (uint32_t)got) put_hex8(bytes[i]);
      else uart_puts("  ");
      uart_putchar(' ');
    }
    uart_puts(" |");
    for (int32_t i = 0; i < got; ++i)
      uart_putchar(bytes[i] >= ' ' && bytes[i] <= '~' ? (char)bytes[i] : '.');
    uart_puts("|\n");
  }
}

static void command_touch(uint32_t argc, char **argv) {
  if (argc != 2) {
    uart_puts("touch: usage touch FILE\n");
  } else if (mount_state != FS_MOUNT_RW) {
    uart_puts("touch: no writable disk\n");
  } else if (fs_lookup(argv[1]) < 0 &&
             fs_write_bytes(argv[1], 0, 0) != 0) {
    uart_puts("touch: failed\n");
  }
}

static void command_cp(uint32_t argc, char **argv) {
  if (argc != 3) {
    uart_puts("cp: usage cp SOURCE DEST\n");
    return;
  }
  if (mount_state != FS_MOUNT_RW) {
    uart_puts("cp: no writable disk\n");
    return;
  }
  const int file = fs_lookup(argv[1]);
  const int32_t size = file < 0 ? -1 : fs_size(file);
  if (size < 0) {
    uart_puts("cp: no such file\n");
    return;
  }
  if ((uint32_t)size > 512u) {
    uart_puts("cp: source exceeds one writable sector\n");
    return;
  }
  uint8_t data[512];
  if (fs_read(file, 0, data, (uint32_t)size) != size ||
      fs_write_bytes(argv[2], data, (uint32_t)size) != 0)
    uart_puts("cp: failed\n");
}

static void command_mv(uint32_t argc, char **argv) {
  if (argc != 3) {
    uart_puts("mv: usage mv SOURCE DEST\n");
  } else if (mount_state != FS_MOUNT_RW) {
    uart_puts("mv: no writable disk\n");
  } else {
    const int rc = fs_rename(argv[1], argv[2]);
    if (rc == -3) uart_puts("mv: no such file\n");
    else if (rc == -4) uart_puts("mv: destination exists\n");
    else if (rc != 0) uart_puts("mv: failed\n");
  }
}

static void command_rm(uint32_t argc, char **argv) {
  if (argc != 2) {
    uart_puts("rm: usage rm FILE\n");
  } else if (mount_state != FS_MOUNT_RW) {
    uart_puts("rm: no writable disk\n");
  } else {
    const int rc = fs_remove(argv[1]);
    if (rc == -2) uart_puts("rm: no such file\n");
    else if (rc != 0) uart_puts("rm: failed\n");
  }
}

static void command_write(uint32_t argc, char **argv) {
  if (argc < 3) {
    uart_puts("write: usage write NAME TEXT\n");
  } else if (mount_state != FS_MOUNT_RW) {
    uart_puts("write: no writable disk\n");
  } else if (fs_write(argv[1], join_args(argc, argv, 2))) {
    uart_puts("write: failed\n");
  }
}

static void command_echo(uint32_t argc, char **argv) {
  if (argc > 1) uart_puts(join_args(argc, argv, 1));
  uart_puts("\n");
}

static void command_fork(uint32_t argc, char **argv) {
  (void)argv;
  if (argc != 1) {
    uart_puts("fork: usage fork\n");
    return;
  }
  uart_puts("fork demo: ");
  const int status = kernel_fork_demo();
  if (status != 0) {
    uart_puts("fork: exit ");
    put_u32((uint32_t)status);
    uart_puts("\n");
  }
}

static void command_exec(uint32_t argc, char **argv) {
  const char *default_argv[] = {"hello.elf"};
  const char *const label = argv[0];
  const char *name;
  const char *const *program_argv;
  uint32_t program_argc;
  if (argc == 1) {
    if (streq(label, "run")) {
      uart_puts("run: usage run FILE [ARG ...]\n");
      return;
    }
    name = default_argv[0];
    program_argv = default_argv;
    program_argc = 1;
  } else {
    name = argv[1];
    program_argv = (const char *const *)&argv[1];
    program_argc = argc - 1u;
  }
  if (program_argc > KERNEL_PROCESS_ARG_MAX) {
    uart_puts(label);
    uart_puts(": too many program arguments\n");
    return;
  }
  uart_puts(label);
  uart_puts(": ");
  const int status =
      kernel_run_program(name, program_argc, program_argv);
  if (status == KERNEL_RUN_ENOENT) {
    uart_puts("no such program\n");
  } else if (status == KERNEL_RUN_ETOOBIG) {
    uart_puts("program too large\n");
  } else if (status < 0) {
    uart_puts("load failed\n");
  } else if (status != 0) {
    uart_puts("exit ");
    put_u32((uint32_t)status);
    uart_puts("\n");
  }
}

static void command_role(uint32_t argc, char **argv) {
  (void)argv;
  if (argc != 1) {
    uart_puts("role: usage role\n");
    return;
  }
  shell_role();
}

static void command_shutdown(uint32_t argc, char **argv) {
  if (argc != 1) {
    uart_puts(argv[0]);
    uart_puts(": usage ");
    uart_puts(argv[0]);
    uart_puts("\n");
    return;
  }
  test_finish(0);
}

const struct shell_command shell_commands[] = {
    {"help", "help [COMMAND]", "list commands or describe one command", command_help},
    {"clear", "clear", "clear an ANSI-compatible console", command_clear},
    {"uname", "uname [-a]", "print kernel and architecture information", command_uname},
    {"uptime", "uptime", "print elapsed kernel timer ticks", command_uptime},
    {"free", "free", "show physical page allocator usage", command_free},
    {"ps", "ps", "show kernel and user task state", command_ps},
    {"pwd", "pwd", "print the current AXFS root", command_pwd},
    {"ls", "ls", "list files in the AXFS root", command_ls},
    {"cat", "cat FILE", "print a file", command_cat},
    {"stat", "stat FILE", "show file size and mount access", command_stat},
    {"hexdump", "hexdump FILE", "print a hexadecimal file dump", command_hexdump},
    {"touch", "touch FILE", "create an empty AXFS file", command_touch},
    {"cp", "cp SOURCE DEST", "copy a file of at most one sector", command_cp},
    {"mv", "mv SOURCE DEST", "rename a file", command_mv},
    {"rm", "rm FILE", "remove a file", command_rm},
    {"write", "write NAME TEXT", "create or replace a one-sector AXFS file", command_write},
    {"echo", "echo [TEXT ...]", "print text", command_echo},
    {"fork", "fork", "run the fork/wait scheduler demonstration", command_fork},
    {"exec", "exec [FILE [ARG ...]]", "load a user ELF and return to the shell", command_exec},
    {"run", "run FILE [ARG ...]", "run a named user ELF and return to the shell", command_exec},
    {"role", "role", "discover and test the accelerator role", command_role},
    {"shutdown", "shutdown", "halt the machine cleanly", command_shutdown},
    {"exit", "exit", "leave the shell and halt the machine", command_shutdown},
};

static uint32_t command_count(void) {
  return sizeof(shell_commands) / sizeof(shell_commands[0]);
}

static const struct shell_command *find_command(const char *name) {
  for (uint32_t i = 0; i < command_count(); ++i)
    if (streq(name, shell_commands[i].name)) return &shell_commands[i];
  return 0;
}

void shell_run(void) {
  char line[SHELL_LINE_MAX];
  char *argv[SHELL_ARGS_MAX];
  mount_state = fs_mount();
  uart_puts("aXos: shell online\n");
  for (;;) {
    uart_puts("aXos> ");
    readline(line, sizeof(line));
    const int32_t parsed = split_line(line, argv, SHELL_ARGS_MAX);
    if (parsed == -1) {
      uart_puts("sh: unterminated quote\n");
      continue;
    }
    if (parsed == -2) {
      uart_puts("sh: too many arguments\n");
      continue;
    }
    const uint32_t argc = (uint32_t)parsed;
    if (argc == 0) continue;
    const struct shell_command *const command = find_command(argv[0]);
    if (command == 0) {
      uart_puts("sh: command not found: ");
      uart_puts(argv[0]);
      uart_puts("\n");
      continue;
    }
    command->run(argc, argv);
  }
}
