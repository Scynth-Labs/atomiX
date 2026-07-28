#!/usr/bin/env python3
"""Exercise the resident shell's generic command and parsing baseline."""
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "sw/kernel/commands_input.txt"


def require(output: str, fragment: str) -> None:
    if fragment not in output:
        raise SystemExit(
            "[kernel] shell commands: missing output\n"
            f"  expected fragment: {fragment!r}\n"
            f"  got: {output!r}")


def main() -> None:
    command = [
        str(ROOT / "sim/axsim/axsim"),
        "--bin", str(ROOT / "sw/kernel/build/axos_boot.elf"),
        "--uart-input-file", str(INPUT),
    ]
    result = subprocess.run(command, cwd=ROOT, text=True,
                            capture_output=True, timeout=30)
    if result.returncode:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(
            f"[kernel] shell commands: ISS exit {result.returncode}")

    output = result.stdout
    fragments = [
        "aXos> help stat\nusage: stat FILE\n"
        "show file size and mount access\n",
        "aXos> uname\naXos\n",
        "aXos> uname -a\naXos 0.1 rv32im riscv\n",
        "aXos> ps\nPID PPID STATE NAME\n"
        "0 0 running [kernel/shell]\n",
        "aXos> pwd\n/\n",
        "aXos> stat motd\nmotd: 17 bytes, read-only\n",
        "00000000  57 65 6c 63 6f 6d 65 20 74 6f 20 61 58 6f 73 2e  "
        "|Welcome to aXos.|\n",
        "00000010  0a                                               "
        "|.|\n",
        'aXos> echo "quoted words" escaped\\ space\n'
        "quoted words escaped space\n",
        "aXos> touch sample\ntouch: no writable disk\n",
        "aXos> cp motd copy\ncp: no writable disk\n",
        "aXos> mv copy moved\nmv: no writable disk\n",
        "aXos> rm moved\nrm: no writable disk\n",
        "aXos> cat missing\ncat: no such file\n",
        "aXos> stat missing\nstat: no such file\n",
        "aXos> clear\n\x1b[2J\x1b[HaXos> not-a-command\n"
        "sh: command not found: not-a-command\n",
        'aXos> echo "unterminated\nsh: unterminated quote\n',
        "aXos> echo 1 2 3 4 5 6 7 8 9 10 11 12\n"
        "sh: too many arguments\n",
        "aXos> exec missing.elf\nexec: no such program\n",
        "aXos> run hello.elf bad\nrun: exit 35\n",
        "aXos> ps\nPID PPID STATE NAME\n"
        "0 0 running [kernel/shell]\n",
        "aXos> shutdown\n",
    ]
    for fragment in fragments:
        require(output, fragment)

    if not re.search(r"aXos> uptime\nuptime: \d+ timer ticks\n", output):
        raise SystemExit(
            f"[kernel] shell commands: malformed uptime output: {output!r}")
    memory = re.search(
        r"aXos> free\nmemory: 4096-byte pages, "
        r"(?P<total>\d+) total, (?P<free>\d+) free, (?P<used>\d+) used\n",
        output)
    if not memory:
        raise SystemExit(
            f"[kernel] shell commands: malformed memory output: {output!r}")
    total = int(memory.group("total"))
    free = int(memory.group("free"))
    used = int(memory.group("used"))
    if total == 0 or free + used != total:
        raise SystemExit(
            "[kernel] shell commands: inconsistent allocator counts "
            f"(total={total}, free={free}, used={used})")
    exec_output = (
        "aXos> exec\n"
        "exec: axlibc: pid=1 n=42 hex=beef str=reused motd=17\n")
    run_output = (
        "aXos> run hello.elf\n"
        "run: axlibc: pid=1 n=42 hex=beef str=reused motd=17\n")
    if output.count(exec_output) != 1 or output.count(run_output) != 1:
        raise SystemExit(
            "[kernel] shell commands: sequential exec/run did not return cleanly "
            f"twice: {output!r}")

    print("[kernel] generic shell commands: PASS on ISS")


if __name__ == "__main__":
    main()
