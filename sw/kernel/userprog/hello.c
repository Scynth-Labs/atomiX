/* The first ordinary C program aXos runs.
 *
 * It is compiled and linked entirely separately from the kernel and reaches it
 * only as an opaque byte array, so nothing here is resolved at kernel link
 * time.  What it demonstrates, beyond the loader, is that a program can now be
 * written *normally*: a main() with a return value, malloc, printf, string
 * functions, 64-bit arithmetic, and open/read/lseek/fstat on a real file, none
 * of which existed a stage ago.
 *
 * Each check exits with a distinct code so a failure says which part broke
 * rather than merely that something did. */
#include "axlibc.h"

/* .rodata, .data and .bss, so the loader's per-segment mapping is still
 * exercised now that a library sits on top of it. */
static const char greeting[] = "axlibc";
static volatile int data_marker = 0x5a;
static volatile int bss_marker;

int main(int argc, char **argv) {
  if (argc != 1 && argc != 3) return 35;
  if (argv == NULL || argv[argc] != NULL) return 36;
  if (strcmp(argv[0], "hello.elf") != 0) return 37;
  if (argc == 3 &&
      (strcmp(argv[1], "one") != 0 || strcmp(argv[2], "two") != 0))
    return 38;

  if (data_marker != 0x5a) return 2;
  if (bss_marker != 0) return 3;
  data_marker = 0x11;
  bss_marker = 0x22;
  if (data_marker != 0x11 || bss_marker != 0x22) return 4;

  /* strings */
  if (strlen(greeting) != 6) return 5;
  if (strcmp(greeting, "axlibc") != 0) return 6;

  /* malloc: allocate, write a pattern, read it back.  The heap has to be
   * mapped by brk() for this to work at all, so a failure here is as likely to
   * be the kernel's brk as the allocator. */
  char *const buf = malloc(200);
  if (buf == NULL) return 7;
  memset(buf, 'x', 200);
  buf[199] = '\0';
  if (strlen(buf) != 199) return 8;

  /* Free and reallocate: exercises the free list rather than only the bump
   * path, and would catch a coalesce that corrupts the chain. */
  free(buf);
  char *const again = malloc(64);
  if (again == NULL) return 9;
  strcpy(again, "reused");
  if (strcmp(again, "reused") != 0) return 10;

  /* calloc must zero. */
  int *const zeros = calloc(16, sizeof(int));
  if (zeros == NULL) return 11;
  for (int i = 0; i < 16; ++i)
    if (zeros[i] != 0) return 12;

  /* A large allocation forces sbrk to grow the heap across a page boundary,
   * which is the path that maps new pages. */
  char *const big = malloc(6000);
  if (big == NULL) return 13;
  big[0] = 'a';
  big[5999] = 'z';
  if (big[0] != 'a' || big[5999] != 'z') return 14;

  /* 64-bit arithmetic: this is what the render benchmark tripped over as an
   * undefined __udivdi3 when nothing supplied libgcc. */
  const uint64_t wide = 1000000007ull * 3ull;
  if (wide / 3ull != 1000000007ull) return 15;

  /* Files.  `motd` is the same seventeen bytes whether it came from the SD card
   * or from the filesystem component's built-in root, so this is one test for
   * both platforms rather than one that only runs where there is storage. */
  static const char motd[] = "Welcome to aXos.\n";
  const int motd_size = (int)sizeof(motd) - 1;

  const int fd = open("motd", O_RDONLY);
  if (fd < 0) return 16;
  /* The first file a program opens is 3.  That is ABI, not an accident. */
  if (fd != 3) return 17;

  struct stat st;
  if (fstat(fd, &st) != 0) return 18;
  if (!S_ISREG(st.st_mode)) return 19;
  if (st.st_size != motd_size) return 20;

  char text[32];
  if (read(fd, text, sizeof(text)) != motd_size) return 21;
  if (memcmp(text, motd, (size_t)motd_size) != 0) return 22;

  /* The descriptor's offset is now at end of file: a further read is 0, not an
   * error and not a repeat of the file. */
  if (read(fd, text, sizeof(text)) != 0) return 23;

  /* Seek back into the middle and re-read, which is the whole point of having
   * an offset rather than a "read the file" call. */
  if (lseek(fd, 11, SEEK_SET) != 11) return 24;
  if (read(fd, text, sizeof(text)) != motd_size - 11) return 25;
  if (memcmp(text, "aXos.\n", 6) != 0) return 26;
  if (lseek(fd, 0, SEEK_END) != motd_size) return 27;

  /* Errors have to be the documented ones, or a libc cannot act on them. */
  if (open("nonesuch", O_RDONLY) != -1 || errno != ENOENT) return 28;
  if (open("motd", O_WRONLY) != -1 || errno != EROFS) return 29;
  if (read(77, text, 1) != -1 || errno != EBADF) return 30;

  if (close(fd) != 0) return 31;
  if (read(fd, text, 1) != -1 || errno != EBADF) return 32;
  /* The slot is free again, so the next open gets the same number back. */
  const int reopened = open("readme", O_RDONLY);
  if (reopened != 3) return 33;
  if (close(reopened) != 0) return 34;

  /* Accelerator access stays mediated by the kernel: discovery copies a
   * fixed record, submit accepts a bounded encoded job, and wait collects the
   * tokenized result.  The default role.none profile exercises discovery of
   * absence; check-role-driver runs this same ELF against role.loopback. */
  struct ax_role_info role;
  if (role_info(&role) != 0) return 39;
  if (role_info((struct ax_role_info *)1) != -1 || errno != EFAULT) return 40;
  if (role.id == 0) {
    if (role.version != 0 || role.capabilities != 0) return 41;
    if (role_submit(AX_ROLE_OP_LOOPBACK, NULL, 0) != -1 ||
        errno != ENODEV) return 50;
  } else if (role.id == AX_ROLE_ID_LOOPBACK) {
    if (role.version == 0 ||
        !(role.capabilities & AX_ROLE_CAP_LOOPBACK)) return 42;

    const uint32_t input[4] = {
        0x10203040u, 0x55667788u, 0xa5a55a5au, 0xdeadbeefu};
    uint8_t request[2 + sizeof(input)];
    request[0] = 4;
    request[1] = 0;
    memcpy(&request[2], input, sizeof(input));

    const long token =
        role_submit(AX_ROLE_OP_LOOPBACK, request, sizeof(request));
    if (token <= 0) return 43;
    if (role_submit(AX_ROLE_OP_LOOPBACK, request, sizeof(request)) != -1 ||
        errno != EBUSY) return 44;

    uint32_t output[4];
    if (role_wait((uint32_t)token + 1u, output, sizeof(output)) != -1 ||
        errno != EINVAL) return 45;
    if (role_wait((uint32_t)token, output, sizeof(output) - 1u) != -1 ||
        errno != EMSGSIZE) return 46;
    if (role_wait((uint32_t)token, output, sizeof(output)) !=
        (ssize_t)sizeof(output)) return 47;
    if (memcmp(input, output, sizeof(input)) != 0) return 48;
    if (role_wait((uint32_t)token, output, sizeof(output)) != -1 ||
        errno != EINVAL) return 49;
    puts("role-user: loopback ok");
  }

  printf("%s: pid=%d n=%d hex=%x str=%s motd=%d\n", greeting,
         getpid() > 0 ? 1 : 0, 42, 0xbeef, again, motd_size);
  return 0;
}
