#!/usr/bin/env python3
"""Benchmark reversible Tang Primer runtime configuration and execution."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sw/host"))
import axhost  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summary(values: list[float]) -> dict[str, float]:
    return {
        "min": round(min(values), 3),
        "mean": round(statistics.fmean(values), 3),
        "max": round(max(values), 3),
    }


def run_checked(command: list[str]) -> str:
    result = subprocess.run(
        command, cwd=ROOT, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    if result.returncode:
        sys.stderr.write(result.stdout)
        raise SystemExit(
            f"benchmark command failed ({result.returncode}): {' '.join(command)}")
    return result.stdout


def labeled_path(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("value must be LABEL=PATH")
    label, raw_path = value.split("=", 1)
    if not label or not raw_path:
        raise argparse.ArgumentTypeError("value must be LABEL=PATH")
    return label, Path(raw_path)


def program_sram(snapshot: str) -> float:
    start = time.monotonic()
    run_checked(["openFPGALoader", "-b", "tangprimer25k", snapshot])
    return (time.monotonic() - start) * 1000.0


def upload_to_marker(pipe: axhost.SerialPipe, kernel: Path,
                     ready_marker: bytes) -> float:
    binary = kernel.read_bytes()
    start = time.monotonic()
    raw = pipe.exchange(
        axhost.kernel_upload_frame(binary), expected_marker=ready_marker)
    elapsed_ms = (time.monotonic() - start) * 1000.0
    ack = b"AXOK" + len(binary).to_bytes(4, "little")
    if ack not in raw or ready_marker not in raw:
        raise SystemExit(
            f"kernel {kernel} did not reach {ready_marker!r}: {raw!r}")
    return elapsed_ms


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bitstream", type=Path, required=True)
    parser.add_argument("--kernel", type=Path,
                        help="host-link runtime kernel to benchmark")
    parser.add_argument(
        "--kernel-profile", action="append", default=[], type=labeled_path,
        metavar="LABEL=PATH",
        help="monitor kernel to time through its ready banner (repeatable)",
    )
    parser.add_argument(
        "--evolver-selftest", action="store_true",
        help="run the monitor's physical fitness/evolution self-test",
    )
    parser.add_argument("--serial", default="/dev/ttyUSB1")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 100:
        parser.error("--iterations must be in 1..100")
    if bool(args.kernel) == bool(args.kernel_profile):
        parser.error("select exactly one of --kernel or --kernel-profile")
    if args.evolver_selftest and not args.kernel_profile:
        parser.error("--evolver-selftest requires --kernel-profile")
    bitstream = args.bitstream.resolve()
    kernels = [("runtime", args.kernel.resolve())] if args.kernel else [
        (label, path.resolve()) for label, path in args.kernel_profile
    ]
    if not bitstream.is_file() or any(not path.is_file() for _, path in kernels):
        parser.error("bitstream and every kernel must be existing files")

    detect = run_checked(["openFPGALoader", "--detect"])
    bitstream_data = bitstream.read_bytes()
    bitstream_digest = hashlib.sha256(bitstream_data).hexdigest()
    runs = []
    with tempfile.NamedTemporaryFile(suffix=".fs") as snapshot:
        # A build may replace its output while this long-running benchmark is
        # active. Program one immutable, pre-hashed snapshot on every trial.
        snapshot.write(bitstream_data)
        snapshot.flush()
        for label, kernel in kernels:
            kernel_bytes = kernel.stat().st_size
            framed_bytes = kernel_bytes + 12
            theoretical_wire_ms = framed_bytes * 10_000.0 / args.baud
            for index in range(args.iterations):
                configure_ms = program_sram(snapshot.name)
                pipe = axhost.SerialPipe(args.serial, args.baud, 5.0)
                if args.kernel:
                    start = time.monotonic()
                    axhost.upload_kernel(pipe, kernel)
                    upload_ms = (time.monotonic() - start) * 1000.0
                    switch_results, switch_ms = axhost.fast_switch(
                        pipe, args.baud)
                    programs = [
                        {"name": name, "load_cycles": load,
                         "execute_cycles": execute}
                        for name, load, execute, _ in switch_results
                    ]
                else:
                    upload_ms = upload_to_marker(
                        pipe, kernel, b"aXos: Primer monitor (32 KiB)\n")
                    switch_ms = None
                    programs = []
                    selftest_result = None
                    if args.evolver_selftest:
                        expected = (b"sh: command not found: evolve" if label == "none"
                                    else b"evolution selftest: PASS")
                        selftest = pipe.exchange_text_paced(
                            b"evolve\n", expected_marker=expected)
                        if expected not in selftest:
                            raise SystemExit(
                                f"{label}: evolver self-test failed: {selftest!r}")
                        outcome = ("predetermined negative-control PASS"
                                   if label == "none"
                                   else "physical evolution selftest PASS")
                        selftest_result = outcome
                        print(f"{label}: {outcome}")
                runs.append({
                    "profile": label,
                    "iteration": index + 1,
                    "configure_sram_ms": round(configure_ms, 3),
                    "kernel_upload_to_ready_ms": round(upload_ms, 3),
                    "fast_switch_round_trip_ms": (
                        round(switch_ms, 3) if switch_ms is not None else None),
                    "kernel": {
                        "bytes": kernel_bytes,
                        "sha256": sha256(kernel),
                        "framed_bytes": framed_bytes,
                        "theoretical_wire_ms": round(theoretical_wire_ms, 3),
                    },
                    "programs": programs,
                    "evolver_selftest": (
                        selftest_result if not args.kernel else None),
                })

    report = {
        "schema": 1,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "board": "org.atomix.board.tang-primer-25k",
        "programming": "volatile-sram-only",
        "flash_written": False,
        "jtag_detect": [line.strip() for line in detect.splitlines() if line.strip()],
        "transport": {"serial": args.serial, "baud": args.baud},
        "bitstream": {
            "bytes": len(bitstream_data),
            "sha256": bitstream_digest,
        },
        "mode": "runtime" if args.kernel else "kernel-profiles",
        "runs": runs,
        "summary": {},
    }
    for label, _ in kernels:
        selected = [run for run in runs if run["profile"] == label]
        profile_summary = {
            "configure_sram_ms": summary(
                [run["configure_sram_ms"] for run in selected]),
            "kernel_upload_to_ready_ms": summary(
                [run["kernel_upload_to_ready_ms"] for run in selected]),
        }
        switch_times = [run["fast_switch_round_trip_ms"] for run in selected
                        if run["fast_switch_round_trip_ms"] is not None]
        if switch_times:
            profile_summary["fast_switch_round_trip_ms"] = summary(switch_times)
        report["summary"][label] = profile_summary
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"benchmark: PASS ({args.iterations} iterations; {output})")


if __name__ == "__main__":
    main()
