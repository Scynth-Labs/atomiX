#!/usr/bin/env python3
"""Build a deterministic, writable AXFS v1 image used by RTL storage tests."""
import struct
import sys
from pathlib import Path


SPARE_BLOCKS = 16


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: make_fs_image.py USER.elf OUTPUT.img")
    files = [
        ("motd", b"Welcome to aXos.\n"),
        ("readme", b"aXos SD disk. Run `help` for commands.\n"),
        ("hello.elf", Path(sys.argv[1]).read_bytes()),
    ]

    next_block = 1
    extents = []
    for name, content in files:
        blocks = max(1, (len(content) + 511) // 512)
        extents.append((name, content, next_block, blocks))
        next_block += blocks

    # Keep spare sectors after the directory so a write regression can create
    # files without relying on host-side image growth semantics.
    sectors = [bytearray(512) for _ in range(next_block + SPARE_BLOCKS)]
    sectors[0][:6] = b"AXFS\x01" + bytes([len(extents)])
    for index, (name, content, first_block, blocks) in enumerate(extents, 1):
        entry = 8 + (index - 1) * 24
        sectors[0][entry:entry + 16] = name.encode().ljust(16, b"\0")
        sectors[0][entry + 16:entry + 24] = struct.pack(
            "<II", first_block, len(content))
        for block in range(blocks):
            chunk = content[block * 512:(block + 1) * 512]
            sectors[first_block + block][:] = chunk.ljust(512, b"\0")
    Path(sys.argv[2]).write_bytes(b"".join(sectors))


if __name__ == "__main__":
    main()
