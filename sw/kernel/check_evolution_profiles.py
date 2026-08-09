#!/usr/bin/env python3
"""Exercise every bounded evolution profile inside the Primer RAM envelope."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
KERNEL = ROOT / "sw/kernel"
INPUT = KERNEL / "evolution_input.txt"
AXSIM = ROOT / "sim/axsim/axsim"
PROFILES = {
    "none": (0, 12288),
    "small": (96, 16384),
    "mid": (336, 20480),
    "large": (1296, 24576),
}


def cross_tool(suffix: str) -> str:
    for prefix in (
        "riscv64-unknown-elf-",
        "riscv32-unknown-elf-",
        "riscv64-elf-",
        "riscv32-elf-",
        "riscv64-linux-gnu-",
    ):
        found = shutil.which(prefix + suffix)
        if found:
            return found
    raise SystemExit(f"[evolution] cannot find a RISC-V {suffix} tool")


def image_metrics(elf: Path) -> tuple[int, int, int, bool, bool]:
    result = subprocess.run(
        [cross_tool("nm"), "-n", "-S", str(elf)],
        text=True,
        capture_output=True,
        check=True,
    )
    symbols: dict[str, int] = {}
    state_size = 0
    policy_linked = False
    fitness_linked = False
    for line in result.stdout.splitlines():
        match = re.fullmatch(
            r"([0-9a-fA-F]+)(?:\s+([0-9a-fA-F]+))?\s+\S\s+(\S+)", line
        )
        if not match:
            continue
        if match.group(3) in {"_end", "__stack_bottom"}:
            symbols[match.group(3)] = int(match.group(1), 16)
        elif match.group(3) == "evolution_state_store" and match.group(2) is not None:
            state_size = int(match.group(2), 16)
        elif match.group(3) == "evolution_record_candidate":
            policy_linked = True
        elif match.group(3) == "fitness_evaluate":
            fitness_linked = True
    if set(symbols) != {"_end", "__stack_bottom"}:
        raise SystemExit(f"[evolution] {elf}: missing linker budget symbols")
    return (
        symbols["_end"] - 0x80000000,
        symbols["__stack_bottom"] - symbols["_end"],
        state_size,
        policy_linked,
        fitness_linked,
    )


def main() -> None:
    for tier, (state_bytes, resident_budget) in PROFILES.items():
        elf = KERNEL / f"build/evolution-{tier}/axos_boot.elf"
        result = subprocess.run(
            [
                str(AXSIM),
                "--bin",
                str(elf),
                "--ram-bytes",
                "32768",
                "--uart-input-file",
                str(INPUT),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        capacities = {"small": 4, "mid": 16, "large": 64}
        interaction = "sh: command not found: evolve\n" if tier == "none" else (
            f"evolution: {tier} capacity={capacities[tier]} "
            f"state={state_bytes}\n"
            "evolution selftest: PASS best=2 fitness=10240 rejected=1\n"
        )
        expected = (
            "aXos: Primer monitor (32 KiB)\n"
            "aXos: monitor shell online\n"
            "aXos> evolve\n"
            f"{interaction}"
            "aXos> exit\n"
        )
        if result.returncode != 0 or result.stdout != expected:
            sys.stderr.write(result.stderr)
            raise SystemExit(
                f"[evolution] {tier}: ISS mismatch (exit {result.returncode})\n"
                f"  expected: {expected!r}\n  got: {result.stdout!r}"
            )
        resident, headroom, linked_state, policy_linked, fitness_linked = \
            image_metrics(elf)
        if headroom < 0:
            raise SystemExit(f"[evolution] {tier}: image crosses reserved stack")
        if resident > resident_budget:
            raise SystemExit(
                f"[evolution] {tier}: resident size {resident} exceeds "
                f"tier budget {resident_budget}"
            )
        if linked_state != state_bytes:
            raise SystemExit(
                f"[evolution] {tier}: linked state is {linked_state}, "
                f"expected {state_bytes} bytes"
            )
        if policy_linked != (tier != "none"):
            raise SystemExit(
                f"[evolution] {tier}: callable policy linked={policy_linked}"
            )
        if fitness_linked != (tier != "none"):
            raise SystemExit(
                f"[evolution] {tier}: callable fitness linked={fitness_linked}"
            )
        print(
            f"[evolution] {tier}: PASS; resident={resident} bytes, "
            f"pre-stack headroom={headroom} bytes"
        )

    print("[evolution] all profiles fit the exact Tang Primer 32 KiB RAM gate")


if __name__ == "__main__":
    main()
