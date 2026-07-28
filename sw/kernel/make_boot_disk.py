#!/usr/bin/env python3
"""Build an SDHC image: boot header, raw kernel, then AXFS at block 64."""
import struct
import sys
from pathlib import Path

FS_BLOCK = 64
SPARE_FS_BLOCKS = 16

def main():
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: make_boot_disk.py KERNEL.bin USER.elf OUTPUT.img")
    kernel = Path(sys.argv[1]).read_bytes()
    user_elf = Path(sys.argv[2]).read_bytes()
    files = [
        ("motd", b"Welcome to aXos.\n"),
        ("readme", b"aXos SD disk. Run `help` for commands.\n"),
        ("hello.elf", user_elf),
    ]
    count = (len(kernel) + 511) // 512
    if count == 0 or count >= FS_BLOCK:
        raise SystemExit("kernel does not fit boot image")

    next_block = FS_BLOCK + 1
    extents = []
    for name, content in files:
        blocks = max(1, (len(content) + 511) // 512)
        extents.append((name, content, next_block, blocks))
        next_block += blocks
    # Keep spare sectors after the packaged files for runtime AXFS writes.
    sectors = [bytearray(512)
               for _ in range(next_block + SPARE_FS_BLOCKS)]
    sectors[0][:8] = b"AXBT" + struct.pack("<I", count)
    for block in range(count):
        sectors[block + 1][:] = kernel[block * 512:(block + 1) * 512].ljust(512, b"\0")
    fs = sectors[FS_BLOCK]
    fs[:6] = b"AXFS\x01" + bytes([len(extents)])
    for index, (name, content, first_block, blocks) in enumerate(extents, 1):
        at = 8 + (index - 1) * 24
        fs[at:at + 16] = name.encode().ljust(16, b"\0")
        fs[at + 16:at + 24] = struct.pack("<II", first_block, len(content))
        for block in range(blocks):
            chunk = content[block * 512:(block + 1) * 512]
            sectors[first_block + block][:] = chunk.ljust(512, b"\0")
    Path(sys.argv[3]).write_bytes(b"".join(sectors))
if __name__ == "__main__": main()
