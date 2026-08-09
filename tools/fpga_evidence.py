#!/usr/bin/env python3
"""Record a machine-readable identity and timing summary for an FPGA image."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.metadata
import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_identity(path: Path) -> dict[str, object]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"required evidence input does not exist: {resolved}")
    try:
        display_path = resolved.relative_to(ROOT)
    except ValueError:
        # Custom profiles may intentionally reference payloads outside this
        # worktree; retain an absolute identity instead of rejecting them.
        display_path = resolved
    return {
        "path": str(display_path),
        "bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }


def command_output(command: list[str], marker: str | None = None) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=os.environ,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError as exc:
        return f"unavailable ({exc})"
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if marker:
        matching = [line for line in lines if marker in line]
        if matching:
            return matching[-1]
    return lines[0] if lines else f"unavailable (exit {result.returncode})"


def git_output(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True
    ).strip()


def source_identity() -> dict[str, object]:
    status = git_output("status", "--porcelain=v1")
    diff = subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=ROOT)
    return {
        "commit": git_output("rev-parse", "HEAD"),
        "dirty": bool(status),
        "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "changed_paths": [line[3:] for line in status.splitlines()],
    }


def tool_versions() -> dict[str, str]:
    try:
        apycula = importlib.metadata.version("apycula")
    except importlib.metadata.PackageNotFoundError:
        apycula = "unavailable"
    return {
        "yosys": command_output(["yosys", "-V"]),
        "nextpnr_himbaechel": command_output(
            ["nextpnr-himbaechel", "--version"], "Next Generation Place and Route"
        ),
        "apycula_gowin_pack": apycula,
        "openFPGALoader": command_output(["openFPGALoader", "--version"]),
    }


def parse_labeled_path(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("input must be LABEL=PATH")
    label, path = value.split("=", 1)
    if not label or not path:
        raise argparse.ArgumentTypeError("input must be LABEL=PATH")
    return label, Path(path)


def parse_labeled_value(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("gate must be LABEL=RESULT")
    label, result = value.split("=", 1)
    if not label or not result:
        raise argparse.ArgumentTypeError("gate must be LABEL=RESULT")
    return label, result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--bitstream", type=Path, required=True)
    parser.add_argument("--timing-report", type=Path, required=True)
    parser.add_argument("--synth-log", type=Path, required=True)
    parser.add_argument(
        "--input", action="append", default=[], type=parse_labeled_path,
        help="additional artifact identity as LABEL=PATH (repeatable)",
    )
    parser.add_argument(
        "--gate", action="append", default=[], type=parse_labeled_value,
        metavar="LABEL=RESULT",
        help="completed prerequisite and result (repeatable)",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    timing_path = args.timing_report.resolve()
    if not timing_path.is_file():
        parser.error(f"timing report does not exist: {timing_path}")
    with timing_path.open() as handle:
        timing = json.load(handle)

    clocks = timing.get("fmax", {})
    if not clocks:
        parser.error(f"timing report contains no fmax results: {timing_path}")
    timing_met = all(
        clock["achieved"] >= clock["constraint"] for clock in clocks.values()
    )
    utilization = {
        name: {"used": values["used"], "available": values["available"]}
        for name, values in sorted(timing.get("utilization", {}).items())
        if values.get("used", 0)
    }

    try:
        inputs = {
            label: file_identity(path)
            for label, path in args.input
        }
        report = {
            "schema": 1,
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "result": "PASS" if timing_met else "FAIL",
            "source": source_identity(),
            "tools": tool_versions(),
            "config": file_identity(args.config),
            "inputs": inputs,
            "prerequisite_gates": {
                label: result for label, result in args.gate
            },
            "bitstream": file_identity(args.bitstream),
            "synthesis_log": file_identity(args.synth_log),
            "timing_report": file_identity(args.timing_report),
            "clocks_mhz": clocks,
            "utilization": utilization,
        }
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        parser.error(str(exc))

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")

    print(f"FPGA evidence: {report['result']}")
    for name, clock in clocks.items():
        print(f"  {name}: {clock['achieved']:.2f} MHz "
              f"(constraint {clock['constraint']:.2f} MHz)")
    print(f"  bitstream: {report['bitstream']['sha256']}")
    print(f"  report: {output}")
    if not timing_met:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
