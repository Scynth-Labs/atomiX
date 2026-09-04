/* Coverage-guided fuzzing of filesystem.axfs metadata.
 *
 * AXFS starts with one externally supplied 512-byte sector: a magic/version,
 * an entry count, and up to eight {name, block, length} records.  The kernel
 * must reject malformed metadata before it can turn a bogus extent into a
 * read.  This harness drives the production component, not a format model.
 *
 * The component owns mount state as file-local statics, which is the right
 * production design: a volume is mounted once.  Including its one C source in
 * this host-only harness puts that state in this translation unit, so every
 * libFuzzer input can reset it without adding a test-only API to the component.
 * The linker still sees the same parser text and sanitizers attribute findings
 * to components/filesystem/axfs/fs.c.
 */
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "fs.h"

static const uint8_t *disk_image;
static size_t disk_size;
static uint8_t superblock[512];
static int reading_file;

/* The real component is deliberately included rather than copied. */
#include "../../components/filesystem/axfs/fs.c"

int sd_init(void) { return 0; }

int sd_read_block(uint32_t block, uint8_t *data) {
  assert(!reading_file || block != 0u);
  if (block == 0) {
    memcpy(data, superblock, sizeof(superblock));
    return 0;
  }
  const size_t blocks = disk_size / 512u;
  if (block >= blocks) return -1;
  memcpy(data, disk_image + (size_t)block * 512u, 512u);
  return 0;
}

int sd_write_block(uint32_t block, const uint8_t *data) {
  (void)block;
  (void)data;
  return -1;  /* This parser harness is read-only. */
}

static void reset_filesystem(void) {
  memset(sector, 0, sizeof(sector));
  memset(metadata, 0, sizeof(metadata));
  memset(entries, 0, sizeof(entries));
  entry_count = 0;
  mounted = 0;
  mount_state = FS_MOUNT_RO;
  cached_block = 0xffffffffu;
  reading_file = 0;
}

/* Make a structurally valid directory alongside the raw input.  The raw pass
 * covers magic/version/count rejection; this pass reaches every entry parser
 * on every input instead of making libFuzzer rediscover five fixed bytes. */
static void make_reachable_superblock(const uint8_t *data, size_t size) {
  memset(superblock, 0, sizeof(superblock));
  if (size > sizeof(superblock)) size = sizeof(superblock);
  memcpy(superblock, data, size);
  superblock[0] = 'A';
  superblock[1] = 'X';
  superblock[2] = 'F';
  superblock[3] = 'S';
  superblock[4] = 1;
  superblock[5] &= 7u;
  for (uint32_t i = 0; i < superblock[5]; ++i) {
    uint8_t *const entry = &superblock[8u + i * 24u];
    entry[15] = 0;
    /* An extent may point anywhere except the metadata sector.  Preserve an
     * arbitrary nonzero block number so the overflow check is fuzzed too. */
    if (!(entry[16] | entry[17] | entry[18] | entry[19]))
      entry[16] = (uint8_t)(i + 1u);
  }
}

static void exercise_reads(const uint8_t *data, size_t size) {
  uint8_t out[64];
  for (uint32_t id = 0; id < entry_count; ++id) {
    const int32_t length = fs_size((int)id);
    assert(length >= 0);
    const uint32_t offset = size >= 4
        ? (uint32_t)data[0] | ((uint32_t)data[1] << 8) |
              ((uint32_t)data[2] << 16) | ((uint32_t)data[3] << 24)
        : 0;
    reading_file = 1;
    const int32_t result = fs_read((int)id, offset, out, sizeof(out));
    reading_file = 0;
    assert(result >= -1 && result <= (int32_t)sizeof(out));
  }
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  if (size > (1u << 20)) return 0;
  disk_image = data;
  disk_size = size;

  /* First run the bytes exactly as received. */
  memset(superblock, 0, sizeof(superblock));
  if (size > sizeof(superblock)) size = sizeof(superblock);
  memcpy(superblock, data, size);
  reset_filesystem();
  const int raw_mount = fs_mount();
  assert(raw_mount == FS_MOUNT_RW || raw_mount == FS_MOUNT_RO);

  /* Then force only the fixed framing, so malformed entry contents reach the
   * same parser and read path a valid on-disk volume uses. */
  make_reachable_superblock(data, disk_size);
  reset_filesystem();
  const int mounted_volume = fs_mount();
  assert(mounted_volume == FS_MOUNT_RW || mounted_volume == FS_MOUNT_RO);
  assert(entry_count <= 8);
  if (mounted_volume == FS_MOUNT_RW) exercise_reads(data, disk_size);
  assert(fs_mount() == mounted_volume);  /* documented idempotence */
  return 0;
}
