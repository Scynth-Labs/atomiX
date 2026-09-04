/* filesystem.axfs: AXFS v1, plus the built-in root a diskless boot mounts.
 *
 * AXFS v1 is deliberately tiny: one 512-byte superblock holding up to eight
 * 24-byte directory entries. Each entry names a contiguous extent, so packaged
 * read-only files may span blocks; runtime writes remain one block. It is a
 * real on-disk format rather than an in-memory table, which is what makes the
 * SD path a storage test.
 *
 * The built-in root exists because a machine with no card still has to have
 * files: the shell's `ls`/`cat` and the ABI's `openat`/`read` both need
 * something to name, and every profile that boots from RAM has no card.  The
 * alternative -- a second filesystem component and a second profile selecting
 * it -- is more machinery than one branch in the mount path, and it would make
 * "can a program read a file" testable only on the storage platforms.  So the
 * fallback is here, explicit, and read-only: a root without a device behind it
 * cannot honestly accept a write. */
#include <stdint.h>

#include "fs.h"
#include "platform.h"
#include "sd.h"

struct fs_entry {
  char name[16];
  uint32_t block;
  uint32_t length;
  /* Non-null when the file is served from the kernel image rather than from a
   * block: the one difference between the two roots, kept to one field so the
   * lookup and read paths stay single. */
  const uint8_t *builtin;
};

static uint8_t sector[512];
static uint8_t metadata[512];
#ifndef AXFS_BLOCK
#define AXFS_BLOCK 0u
#endif
static struct fs_entry entries[8];
static uint32_t entry_count;
static int mounted;
static int mount_state;
/* `sector` caches one block.  Without this a read() served in 128-byte chunks
 * would re-read the same sector over SPI four times. */
static uint32_t cached_block;

/* The diskless root.  These are the strings the shell has always served, kept
 * byte-identical so the boot transcript does not depend on whether a card is
 * present. */
static const char builtin_motd[] = "Welcome to aXos.\n";
static const char builtin_readme[] = "aXos RAM disk. Run `help` for commands.\n";

static uint32_t read_u32(const uint8_t *p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
         ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static int same(const char *a, const char *b) {
  while (*a && *a == *b) { ++a; ++b; }
  return *a == *b;
}

static uint32_t length(const char *text) {
  uint32_t result = 0;
  while (text[result]) ++result;
  return result;
}

static void write_u32(uint8_t *p, uint32_t value) {
  p[0] = (uint8_t)value;
  p[1] = (uint8_t)(value >> 8);
  p[2] = (uint8_t)(value >> 16);
  p[3] = (uint8_t)(value >> 24);
}

static uint32_t next_free_block(void) {
  uint32_t result = AXFS_BLOCK + 1;
  for (uint32_t i = 0; i < entry_count; ++i) {
    uint32_t blocks = (entries[i].length + 511u) / 512u;
    if (blocks == 0) blocks = 1;  /* Runtime-created empty files own a block. */
    const uint32_t end = entries[i].block + blocks;
    if (end > result) result = end;
  }
  return result;
}

static void set_name(struct fs_entry *entry, const char *name) {
  uint32_t i = 0;
  for (; i < 15 && name[i]; ++i) entry->name[i] = name[i];
  for (; i < 16; ++i) entry->name[i] = 0;
}

static int mount_disk(void) {
  if (sd_init() || sd_read_block(AXFS_BLOCK, sector)) return -1;
  if (sector[0] != 'A' || sector[1] != 'X' || sector[2] != 'F' ||
      sector[3] != 'S' || sector[4] != 1 || sector[5] > 8) return -1;
  entry_count = sector[5];
  for (uint32_t i = 0; i < entry_count; ++i) {
    const uint8_t *const disk = &sector[8 + i * 24];
    for (uint32_t j = 0; j < 16; ++j) entries[i].name[j] = (char)disk[j];
    entries[i].name[15] = 0;
    entries[i].block = read_u32(disk + 16);
    entries[i].length = read_u32(disk + 20);
    entries[i].builtin = 0;
    /* fs_size() uses a signed result so -1 remains its invalid-id sentinel.
     * A larger on-disk extent would otherwise mount successfully and become
     * indistinguishable from that error to every caller. */
    if (entries[i].block <= AXFS_BLOCK || entries[i].length > 0x7fffffffu)
      return -1;
    const uint32_t blocks = (entries[i].length + 511u) / 512u;
    if (blocks != 0u && entries[i].block > 0xffffffffu - (blocks - 1u))
      return -1;
  }
  return 0;
}

static void mount_builtin(void) {
  static const struct { const char *name; const char *data; } files[] = {
      {"motd", builtin_motd},
      {"readme", builtin_readme},
  };
  entry_count = sizeof(files) / sizeof(files[0]);
  for (uint32_t i = 0; i < entry_count; ++i) {
    set_name(&entries[i], files[i].name);
    entries[i].block = 0;
    entries[i].length = length(files[i].data);
    entries[i].builtin = (const uint8_t *)files[i].data;
  }
}

int fs_mount(void) {
  if (mounted) return mount_state;
  mounted = 1;
  cached_block = 0xffffffffu;
  if (mount_disk() == 0) {
    mount_state = FS_MOUNT_RW;
  } else {
    mount_builtin();
    mount_state = FS_MOUNT_RO;
  }
  return mount_state;
}

void fs_list(void) {
  for (uint32_t i = 0; i < entry_count; ++i) {
    uart_puts(entries[i].name);
    uart_puts("\n");
  }
}

int fs_lookup(const char *name) {
  for (uint32_t i = 0; i < entry_count; ++i)
    if (same(name, entries[i].name)) return (int)i;
  return -1;
}

int32_t fs_size(int id) {
  if (id < 0 || (uint32_t)id >= entry_count) return -1;
  return (int32_t)entries[id].length;
}

int32_t fs_read(int id, uint32_t offset, void *dst, uint32_t len) {
  if (id < 0 || (uint32_t)id >= entry_count) return -1;
  const struct fs_entry *const file = &entries[id];
  if (offset >= file->length) return 0;          /* end of file */
  uint32_t count = file->length - offset;
  if (count > len) count = len;

  if (file->builtin) {
    const uint8_t *const src = file->builtin + offset;
    uint8_t *const out = dst;
    for (uint32_t i = 0; i < count; ++i) out[i] = src[i];
    return (int32_t)count;
  }

  uint8_t *const out = dst;
  uint32_t cursor = offset;
  uint32_t copied = 0;
  while (copied < count) {
    const uint32_t block = file->block + cursor / 512u;
    const uint32_t within = cursor % 512u;
    uint32_t chunk = 512u - within;
    if (chunk > count - copied) chunk = count - copied;
    if (cached_block != block) {
      if (sd_read_block(block, sector)) {
        cached_block = 0xffffffffu;
        return -1;
      }
      cached_block = block;
    }
    for (uint32_t i = 0; i < chunk; ++i)
      out[copied + i] = sector[within + i];
    copied += chunk;
    cursor += chunk;
  }
  return (int32_t)count;
}

int fs_write_bytes(const char *name, const void *data, uint32_t data_length) {
  if (mount_state != FS_MOUNT_RW) return -1;
  const uint32_t name_length = length(name);
  if (!name_length || name_length > 15 || data_length > 512 ||
      (data == 0 && data_length != 0)) return -2;

  uint32_t index = entry_count;
  for (uint32_t i = 0; i < entry_count; ++i) {
    if (same(name, entries[i].name)) { index = i; break; }
  }
  if (index == entry_count && entry_count == 8) return -3;

  uint32_t block = index == entry_count ? next_free_block() : entries[index].block;
  /* `sector` is the read cache; reusing it as the write buffer invalidates it. */
  cached_block = 0xffffffffu;
  for (uint32_t i = 0; i < 512; ++i) sector[i] = 0;
  const uint8_t *const source = data;
  for (uint32_t i = 0; i < data_length; ++i) sector[i] = source[i];
  if (sd_write_block(block, sector)) return -4;

  if (sd_read_block(AXFS_BLOCK, metadata)) return -5;
  const uint32_t at = 8 + index * 24;
  for (uint32_t i = 0; i < 16; ++i) metadata[at + i] = 0;
  for (uint32_t i = 0; i < name_length; ++i) metadata[at + i] = (uint8_t)name[i];
  write_u32(&metadata[at + 16], block);
  write_u32(&metadata[at + 20], data_length);
  if (index == entry_count) metadata[5] = (uint8_t)(entry_count + 1);
  if (sd_write_block(AXFS_BLOCK, metadata)) return -6;

  set_name(&entries[index], name);
  entries[index].block = block;
  entries[index].length = data_length;
  entries[index].builtin = 0;
  if (index == entry_count) ++entry_count;
  return 0;
}

int fs_write(const char *name, const char *data) {
  return fs_write_bytes(name, data, length(data));
}

int fs_remove(const char *name) {
  if (mount_state != FS_MOUNT_RW) return -1;
  const int found = fs_lookup(name);
  if (found < 0) return -2;
  const uint32_t index = (uint32_t)found;

  if (sd_read_block(AXFS_BLOCK, metadata)) return -3;
  for (uint32_t i = index; i + 1 < entry_count; ++i) {
    const uint32_t dst = 8 + i * 24;
    const uint32_t src = dst + 24;
    for (uint32_t j = 0; j < 24; ++j) metadata[dst + j] = metadata[src + j];
  }
  const uint32_t last = 8 + (entry_count - 1u) * 24;
  for (uint32_t j = 0; j < 24; ++j) metadata[last + j] = 0;
  metadata[5] = (uint8_t)(entry_count - 1u);
  if (sd_write_block(AXFS_BLOCK, metadata)) return -4;

  for (uint32_t i = index; i + 1 < entry_count; ++i) {
    for (uint32_t j = 0; j < 16; ++j)
      entries[i].name[j] = entries[i + 1].name[j];
    entries[i].block = entries[i + 1].block;
    entries[i].length = entries[i + 1].length;
    entries[i].builtin = entries[i + 1].builtin;
  }
  --entry_count;
  return 0;
}

int fs_rename(const char *old_name, const char *new_name) {
  if (mount_state != FS_MOUNT_RW) return -1;
  const uint32_t name_length = length(new_name);
  if (!name_length || name_length > 15) return -2;
  const int found = fs_lookup(old_name);
  if (found < 0) return -3;
  if (same(old_name, new_name)) return 0;
  if (fs_lookup(new_name) >= 0) return -4;
  const uint32_t index = (uint32_t)found;

  if (sd_read_block(AXFS_BLOCK, metadata)) return -5;
  const uint32_t at = 8 + index * 24;
  for (uint32_t i = 0; i < 16; ++i) metadata[at + i] = 0;
  for (uint32_t i = 0; i < name_length; ++i)
    metadata[at + i] = (uint8_t)new_name[i];
  if (sd_write_block(AXFS_BLOCK, metadata)) return -6;
  set_name(&entries[index], new_name);
  return 0;
}
