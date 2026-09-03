#!/usr/bin/env python3
"""Check that the three layers of the ABI actually agree.

An ABI is a contract between a kernel that returns values, a libc that names
them, and a document that publishes them.  Nothing forced those three to match,
and one had already drifted: `wait4` with no children returns `AX_ECHILD = 10`,
which existed only in the kernel's private header.  A program using axlibc got
`errno = 10` with no name for it, and docs/abi.md -- which says it defines the
errno subset -- did not list it.

That kind of gap is invisible to every behavioural test, because the kernel is
right, the libc is right about everything it knows, and the document is right
about everything it mentions.  Only comparing them finds it.  This runs in
under a second and needs no toolchain, so it gates the expensive RTL run rather
than the other way round.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KERNEL_SYSCALL_H = ROOT / "sw/kernel/include/syscall.h"
LIBC_HEADER = ROOT / "components/libc/axlibc/include/axlibc.h"
LIBC_SOURCE = ROOT / "components/libc/axlibc/syscall.c"
SYSCALL_C = ROOT / "components/syscall/linux-compat/syscall.c"
ABI_DOC = ROOT / "docs/abi.md"


def fail(message: str) -> None:
    raise SystemExit(f"[abi] {message}")


def check_errno() -> int:
    """Every errno the kernel can return must be nameable and published."""
    kernel = {m.group(1): int(m.group(2)) for m in re.finditer(
        r"AX_(E[A-Z0-9]+)\s*=\s*(\d+)", KERNEL_SYSCALL_H.read_text())}
    libc = {m.group(1): int(m.group(2)) for m in re.finditer(
        r"#define\s+(E[A-Z0-9]+)\s+(\d+)", LIBC_HEADER.read_text())}
    doc = ABI_DOC.read_text()

    if not kernel:
        fail("no AX_E* values found; the kernel header layout changed")

    for name, value in sorted(kernel.items(), key=lambda kv: kv[1]):
        if name not in libc:
            fail(f"{name} = {value} is returned by the kernel but axlibc "
                 f"does not define it: a program cannot name the error it got")
        if libc[name] != value:
            fail(f"{name} is {value} in the kernel and {libc[name]} in axlibc")
        if not re.search(rf"\|\s*{value}\s*\|\s*`{name}`", doc):
            fail(f"{name} = {value} is not in the errno table in docs/abi.md")

    for name, value in sorted(libc.items()):
        if name not in kernel:
            fail(f"axlibc defines {name} = {value}, which the kernel never "
                 f"returns: either it is dead or a return path is missing")
    return len(kernel)


def check_syscall_numbers() -> int:
    """The dispatcher's numbers, the libc's raw calls, and the table agree."""
    # int(x, 0) handles both spellings: the asm-generic numbers are decimal and
    # the private range is hex.
    numbers = {}
    for match in re.finditer(r"NR_([a-z_0-9]+)\s*=\s*(0[xX][0-9a-fA-F]+|\d+)",
                             SYSCALL_C.read_text()):
        numbers[match.group(1)] = int(match.group(2), 0)
    if not numbers:
        fail("no NR_* values found; the syscall component layout changed")

    doc = ABI_DOC.read_text()
    for name, value in sorted(numbers.items(), key=lambda kv: kv[1]):
        # The private range is published in hex, the asm-generic one in
        # decimal, and its entries drop the `ax_` the enum carries.
        spellings = {name, name[3:]} if name.startswith("ax_") else {name}
        published = any(
            re.search(rf"\|\s*{value}\s*\|\s*`{spelling}`", doc) or
            re.search(rf"\|\s*0x{value:04x}\s*\|\s*`{spelling}`", doc)
            for spelling in spellings)
        if not published:
            fail(f"syscall {name} = {value} is dispatched but not in the "
                 f"syscall table in docs/abi.md")

    # Every raw number the libc issues must be one the dispatcher handles.
    issued = {int(m.group(1), 0) for m in re.finditer(
        r"__libc_syscall5?\(\s*(0[xX][0-9a-fA-F]+|\d+)", LIBC_SOURCE.read_text())}
    known = set(numbers.values())
    for value in sorted(issued):
        if value not in known:
            fail(f"axlibc issues syscall {value}, which the dispatcher does "
                 f"not implement")
    return len(numbers)


def main() -> int:
    errnos = check_errno()
    calls = check_syscall_numbers()
    print(f"[abi] contract: PASS ({errnos} errnos and {calls} syscalls agree "
          f"across the kernel, axlibc, and abi.md)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
