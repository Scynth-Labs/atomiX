#!/usr/bin/env python3
"""Build a deterministic, writable AXFS v1 image used by RTL storage tests."""
import struct
import sys
from pathlib import Path


SPARE_BLOCKS = 16


def wx_elf() -> bytes:
    """A minimal, otherwise-valid ELF whose only segment is W+X.

    It exists to test a rejection, so everything about it except the flags is
    deliberately correct: real magic, ET_EXEC, EM_RISCV, an in-range vaddr, and
    a payload that is three real instructions calling exit(0).  A loader that
    does not enforce W^X therefore *runs* it and the program exits 0, while one
    that does refuses to map it -- which is what makes the check falsifiable
    rather than merely present.
    """
    payload = struct.pack("<3I",
                          0x00000513,   # li a0, 0
                          0x05d00893,   # li a7, 93   (asm-generic exit)
                          0x00000073)   # ecall
    ehsize, phentsize = 52, 32
    phoff = ehsize
    offset = ehsize + phentsize
    header = (b"\x7fELF" + bytes([1, 1, 1, 0]) + bytes(8) +
              struct.pack("<HHIIIIIHHHHHH",
                          2,            # e_type   = ET_EXEC
                          243,          # e_machine= EM_RISCV
                          1,            # e_version
                          0x40000000,   # e_entry
                          phoff, 0, 0,
                          ehsize, phentsize, 1, 0, 0, 0))
    phdr = struct.pack("<8I",
                       1,               # p_type = PT_LOAD
                       offset,
                       0x40000000,      # p_vaddr
                       0x40000000,      # p_paddr
                       len(payload), len(payload),
                       7,               # p_flags = R+W+X
                       0x1000)
    return header + phdr + payload


def main() -> None:
    argv = [a for a in sys.argv[1:] if a != "--with-wx"]
    with_wx = "--with-wx" in sys.argv
    if len(argv) != 2:
        raise SystemExit("usage: make_fs_image.py [--with-wx] USER.elf OUTPUT.img")
    files = [
        ("motd", b"Welcome to aXos.\n"),
        ("readme", b"aXos SD disk. Run `help` for commands.\n"),
        ("hello.elf", Path(argv[0]).read_bytes()),
    ]
    if with_wx:
        files.append(("wx.elf", wx_elf()))

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
    Path(argv[1]).write_bytes(b"".join(sectors))


if __name__ == "__main__":
    main()
