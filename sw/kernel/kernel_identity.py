#!/usr/bin/env python3
"""Write the build-identity stamp, but only when the identity changed.

The kernel's outputs live at one set of paths under `build/` whatever profile
and personality produced them, so make needs something that changes when the
configuration changes.  A file rewritten on every invocation would rebuild the
kernel every time; one that is never rewritten leaves the previous profile's
kernel in place and calls it success.  This writes only on a real difference,
so the stamp's timestamp means exactly "the configuration changed here".

The identity arrives through the environment rather than the command line
because it contains CPPFLAGS, whose quoting does not survive a shell.
"""
import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: kernel_identity.py <stamp-path>\n")
        return 2
    identity = os.environ.get("KERNEL_IDENTITY")
    if not identity:
        sys.stderr.write(
            "kernel_identity.py: KERNEL_IDENTITY is empty; the Makefile must "
            "export it\n")
        return 2
    # One whitespace-separated token per line.  Not one setting per line --
    # CPPFLAGS alone is dozens of tokens -- but that is the point: a diff of
    # two stamps shows the individual flags that differ instead of one long
    # line that differs somewhere.
    text = "\n".join(token for token in identity.split() if token) + "\n"
    stamp = Path(sys.argv[1])
    if stamp.exists() and stamp.read_text() == text:
        return 0
    stamp.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
