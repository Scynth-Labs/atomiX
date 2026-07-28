#!/usr/bin/env python3
"""Drive the aXos in-kernel role driver against role.loopback on the RTL SoC.

This is the first piece of the shell control plane (DESIGN.md §3.3): the kernel
itself — not a bare-metal test program — discovers the accelerator through the
role window mapped into its S-mode address space and drives one job end-to-end
via the shell `role` command.  The host-link service will later call the same
driver on behalf of remote requests.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROLE_INPUT = ROOT / "sw/kernel/role_input.txt"
CONFIG = ROOT / "configs/sim-role-loopback.json"
EXPECTED = (
    "aXos: shell online\n"
    "aXos> role\n"
    "role: loopback v1\n"
    "role: copy ok\n"
    "aXos> exec hello.elf\n"
    "exec: role-user: loopback ok\n"
    "axlibc: pid=1 n=42 hex=beef str=reused motd=17\n"
    "aXos> exit\n"
)


def main() -> None:
    image = ROOT / "sw/kernel/build/axos_boot.hex"
    command = [
        "make", "-s", "--no-print-directory", "-C", str(ROOT / "sim/soc"),
        "run", f"RAM_INIT_FILE={image}", "RESET_PC=0x80000000",
        f"COMPONENT_CONFIG={CONFIG}", "MAX_CYCLES=500000",
        f"UART_INPUT_FILE={ROLE_INPUT}", "BUILD_ID=role-driver",
    ]
    try:
        result = subprocess.run(command, cwd=ROOT, text=True,
                                input=ROLE_INPUT.read_text(),
                                capture_output=True, timeout=180)
    except subprocess.TimeoutExpired:
        raise SystemExit("[kernel] role driver: TIMEOUT")
    if result.returncode:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(f"[kernel] role driver: exit {result.returncode}")
    if result.stdout != EXPECTED:
        sys.stderr.write(result.stderr)
        raise SystemExit(
            "[kernel] role driver: UART mismatch\n"
            f"  expected: {EXPECTED!r}\n"
            f"  got:      {result.stdout!r}")
    print("[kernel] role driver: PASS "
          "(aXos discovered and drove role.loopback through the RTL shell)")


if __name__ == "__main__":
    main()
