#!/usr/bin/env python3
"""Generate an isolated riscv-formal worktree below formal/build/.

One worktree per core under proof.  Each core contributes only a formal
wrapper and a checks configuration; the ISA properties, the check generator,
and the RTL are shared, so proving a second core is a matter of naming its
trace, not of duplicating a verification environment.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build" / "riscv-formal"
REFERENCE = Path("/opt/riscv-formal")

# core name -> (configuration, formal wrapper).  The wrapper keeps its own file
# name inside the generated worktree, which is what each configuration's
# [verilog-files] section refers to.
CORES = {
    "axcore": (
        "checks.cfg",
        "components/core/pipeline5/axcore_rvfi_wrapper.sv",
    ),
    "ax2": (
        "checks-ax2.cfg",
        "components/core/ax2/ax2_rvfi_wrapper.sv",
    ),
    # Shares the reference core's wrapper file name; the worktrees are per-core
    # directories, so the two never collide.
    "minimal": (
        "checks-minimal.cfg",
        "components/core/minimal/axcore_rvfi_wrapper.sv",
    ),
}


def link(source: Path, destination: Path) -> None:
    if destination.is_symlink() or destination.exists():
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    destination.symlink_to(source)


def generate(core: str) -> None:
    config, wrapper = CORES[core]
    core_dir = BUILD / "cores" / core
    core_dir.mkdir(parents=True, exist_ok=True)
    link(REFERENCE / "checks", BUILD / "checks")
    link(REFERENCE / "insns", BUILD / "insns")
    link(ROOT.parent / "components", BUILD / "components")
    link(ROOT / config, core_dir / "checks.cfg")
    wrapper_path = ROOT.parent / wrapper
    link(wrapper_path, core_dir / wrapper_path.name)
    subprocess.run(
        ["python3", str(REFERENCE / "checks" / "genchecks.py")],
        cwd=core_dir,
        check=True,
    )
    # riscv-formal's generator defaults to Yosys's legacy SystemVerilog
    # frontend. Both cores use a standard package/import for shared types, so
    # use the Slang frontend bundled with current upstream Yosys instead.
    for job in (core_dir / "checks").glob("*.sby"):
        contents = job.read_text()
        contents = contents.replace(
            "read -sv ", "read_slang --std 1800-2017 "
        )
        job.write_text(contents)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", choices=sorted(CORES), default="axcore")
    args = parser.parse_args()
    if not REFERENCE.is_dir():
        raise SystemExit("missing /opt/riscv-formal; see docs/toolchain.md")
    generate(args.core)


if __name__ == "__main__":
    main()
