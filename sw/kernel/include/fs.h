#pragma once

#include <stdint.h>

/* The filesystem component seam.
 *
 * Two callers share it: the shell's `ls`/`cat`/`write`, and the syscall
 * component's `openat`/`read`/`lseek`/`fstat` by way of the kernel's file
 * hooks.  That is why reading is a byte range rather than "print the file to
 * the console" -- one lookup and one read path serve both, so the shell test
 * and the ABI test exercise the same code instead of two implementations that
 * can drift apart.
 *
 * A file is named by a small integer id, valid until the next mount.  Nothing
 * here is a file *descriptor*: descriptors carry an offset and a lifetime and
 * belong to the ABI, which is a different component. */

enum {
  /* fs_mount() reports which root it got, because "no writable disk" and "no
   * disk at all" are different answers to the shell's `write`. */
  FS_MOUNT_RW = 0,  /* a writable volume */
  FS_MOUNT_RO = 1,  /* a read-only root */
};

/* Mount the volume.  Idempotent: a second call returns the first call's result
 * without touching the device, so a caller that needs the filesystem does not
 * have to know whether someone else has already mounted it. */
int fs_mount(void);

/* Print the names in the root, one per line. */
void fs_list(void);

/* Resolve a name to a file id, or -1 if there is no such file. */
int fs_lookup(const char *name);

/* Size of `id` in bytes, or -1 for a bad id. */
int32_t fs_size(int id);

/* Read up to `len` bytes of `id` starting at `offset` into `dst`.  Returns the
 * number of bytes read -- 0 at or past end of file, which is how a caller
 * detects it -- or -1 on a bad id or a device error. */
int32_t fs_read(int id, uint32_t offset, void *dst, uint32_t len);

/* Create or replace `name` with the NUL-terminated `data`.  Runtime AXFS files
 * occupy one sector.  Returns 0, or a negative implementation-defined code. */
int fs_write(const char *name, const char *data);

/* Binary-safe form of fs_write. */
int fs_write_bytes(const char *name, const void *data, uint32_t len);

/* Remove a file or rename it within the flat root. */
int fs_remove(const char *name);
int fs_rename(const char *old_name, const char *new_name);
