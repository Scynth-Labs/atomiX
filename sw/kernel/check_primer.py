#!/usr/bin/env python3
"""Validate the compact aXos profile at the Primer's exact 32 KiB RAM size."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
KERNEL = ROOT / "sw/kernel/build/primer/axos_boot.elf"
IMAGE = ROOT / "sw/kernel/build/primer/axos_boot.hex"
INPUT = ROOT / "sw/kernel/primer_input.txt"
EXPECTED = re.compile(
    r"aXos: Primer monitor \(32 KiB\)\n"
    r"aXos: monitor shell online\n"
    r"aXos> help\n"
    r"commands: help clear uname uptime free ps echo role shutdown exit\n"
    r"aXos> uname -a\n"
    r"aXos 0\.1 rv32im riscv monitor\n"
    r"aXos> uptime\n"
    r"uptime: [1-9][0-9]* timer ticks\n"
    r"aXos> free\n"
    r"memory: 4096-byte pages, 4 total, 4 free, 0 used\n"
    r"aXos> ps\n"
    r"PID PPID STATE NAME\n"
    r"0 0 running \[kernel/monitor\]\n"
    r"aXos> role\n"
    r"role: none\n"
    r"aXos> echo simulation ok\n"
    r"simulation ok\n"
    r"aXos> exit\n"
)


def run(label: str, command: list[str]) -> None:
    result = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True, timeout=90
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(f"[kernel-primer] {label}: exit {result.returncode}")
    if EXPECTED.fullmatch(result.stdout) is None:
        sys.stderr.write(result.stderr)
        raise SystemExit(
            f"[kernel-primer] {label}: UART mismatch\n"
            f"  got: {result.stdout!r}"
        )
    print(f"[kernel-primer] {label}: PASS")


def main() -> None:
    run(
        "ISS, exact 32768-byte RAM",
        [
            str(ROOT / "sim/axsim/axsim"),
            "--bin",
            str(KERNEL),
            "--ram-bytes",
            "32768",
            "--uart-input-file",
            str(INPUT),
        ],
    )
    run(
        "RTL, 32768-byte synchronous BSRAM model",
        [
            "make",
            "-s",
            "--no-print-directory",
            "-C",
            str(ROOT / "sim/soc"),
            "run",
            f"RAM_INIT_FILE={IMAGE}",
            "RESET_PC=0x80000000",
            "RAM_BYTES=32768",
            "EXTERNAL_MEMORY=0",
            "CACHES=0",
            "SYNC_READ=1",
            "MAX_CYCLES=250000",
            f"UART_INPUT_FILE={INPUT}",
            "BUILD_ID=primer-kernel",
        ],
    )
    print("[kernel-primer] simulation gate: PASS; hardware programming remains separate")


if __name__ == "__main__":
    main()
