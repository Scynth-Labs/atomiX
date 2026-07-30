#!/usr/bin/env python3
"""Run generated riscv-formal checks with Yosys's built-in SAT engine."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parent


def check_root(core: str) -> Path:
    return ROOT / "build" / "riscv-formal" / "cores" / core / "checks"


def work_root(core: str) -> Path:
    # Per-core solver trees, so one core's counterexamples are never mistaken
    # for another's.
    return ROOT / "build" / "sat" / core


def script_from(config: Path) -> str:
    lines: list[str] = []
    in_script = False
    for line in config.read_text().splitlines():
        if line == "[script]":
            in_script = True
            continue
        if in_script and line.startswith("["):
            break
        if in_script:
            lines.append(line)
    if not lines:
        raise RuntimeError(f"no [script] section in {config}")
    return "\n".join(lines) + "\n"


def proof_depth(config: Path) -> int:
    match = re.search(r"^skip\s+(\d+)$", config.read_text(), re.MULTILINE)
    if not match:
        raise RuntimeError(f"no bounded-check skip depth in {config}")
    return int(match.group(1))


def run_check(core: str, check: str) -> bool:
    job = check_root(core) / f"{check}.sby"
    if not job.is_file():
        raise RuntimeError(f"unknown generated check for {core}: {check}")

    work = work_root(core) / check
    subprocess.run(
        ["sby", "--setup", "-f", "-d", str(work), str(job)], check=True
    )
    config = work / "config.sby"
    depth = proof_depth(config)
    script = script_from(config)
    script += (
        # Keep this preparation equivalent to SBY's SMT2 model preparation,
        # then map the register file before using Yosys's SAT backend.
        "scc -select; simplemap; select -clear\n"
        "memory_nordff\n"
        "async2sync\n"
        "chformal -assume -early\n"
        "opt_clean\n"
        "formalff -setundef -clk2ff -ff2anyinit -hierarchy\n"
        "chformal -live -fair -cover -remove\n"
        "opt_clean\n"
        "check\n"
        "setundef -undriven -anyseq\n"
        "opt -fast\n"
        "rename -witness\n"
        "opt_clean\n"
        "memory_map\n"
        "opt\n"
        f"sat -seq {depth} -prove-asserts -set-assumes\n"
    )
    sat_script = work / "sat.ys"
    sat_script.write_text(script)
    result = subprocess.run(
        ["yosys", "-ql", str(work / "yosys.log"), "-s", str(sat_script)],
        cwd=work / "src",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    yosys_log = (work / "yosys.log").read_text()
    (work / "result.log").write_text(result.stdout + yosys_log)
    passed = result.returncode == 0 and "SUCCESS!" in yosys_log
    print(f"[formal] {core}/{check}: {'PASS' if passed else 'FAIL'}")
    if not passed:
        print(result.stdout + yosys_log, file=sys.stderr)
    return passed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("checks", nargs="*")
    parser.add_argument("--core", default="axcore")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    checks = args.checks
    if args.all:
        checks = sorted(path.stem for path in check_root(args.core).glob("*.sby"))
    if not checks:
        parser.error("specify one or more checks, or --all")
    # Run every check rather than stopping at the first failure: a formal run is
    # slow enough that seeing all the counterexamples from one pass matters.
    results = [run_check(args.core, check) for check in checks]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
