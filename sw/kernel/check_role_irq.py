#!/usr/bin/env python3
"""Prove aXos takes role completion as an interrupt rather than polling for it.

check_role_driver.py already shows the kernel drives a job end to end.  This is
the narrower claim underneath it: the job finished because the role's
level-sensitive line reached S-mode through the PLIC's supervisor context, and
the kernel never read STATUS to find out.

Two jobs, not one.  A single completion would also pass with a handler that
claims but never completes, or with a gateway that latches an edge; the second
job only arrives if the source was properly completed and re-armed.  The
`irq=N polled=M` counters the shell prints are what make that checkable --
`polled` staying at zero is the whole point, and any fallback to the polled
path shows up there instead of silently succeeding.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROLE_INPUT = ROOT / "sw/kernel/role_irq_input.txt"
CONFIG = ROOT / "configs/sim-role-loopback.json"
EXPECTED = (
    "aXos: shell online\n"
    "aXos> role\n"
    "role: loopback v1\n"
    "role: copy ok irq=1 polled=0\n"
    "aXos> role\n"
    "role: loopback v1\n"
    "role: copy ok irq=2 polled=0\n"
    "aXos> exit\n"
)


def main() -> None:
    image = ROOT / "sw/kernel/build/axos_boot.hex"
    command = [
        "make", "-s", "--no-print-directory", "-C", str(ROOT / "sim/soc"),
        "run", f"RAM_INIT_FILE={image}", "RESET_PC=0x80000000",
        f"COMPONENT_CONFIG={CONFIG}", "MAX_CYCLES=500000",
        f"UART_INPUT_FILE={ROLE_INPUT}", "BUILD_ID=role-irq",
    ]
    try:
        result = subprocess.run(command, cwd=ROOT, text=True,
                                input=ROLE_INPUT.read_text(),
                                capture_output=True, timeout=180)
    except subprocess.TimeoutExpired:
        raise SystemExit("[kernel] role irq: TIMEOUT")
    if result.returncode:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(f"[kernel] role irq: exit {result.returncode}")
    if result.stdout != EXPECTED:
        sys.stderr.write(result.stderr)
        raise SystemExit(
            "[kernel] role irq: UART mismatch\n"
            f"  expected: {EXPECTED!r}\n"
            f"  got:      {result.stdout!r}")
    print("[kernel] role irq: PASS "
          "(two jobs completed through the PLIC S-context; STATUS never polled)")


if __name__ == "__main__":
    main()
