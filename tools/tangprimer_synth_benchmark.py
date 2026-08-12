#!/usr/bin/env python3
"""Fresh synthesis/P&R benchmark for all Tang Primer atomiX profiles."""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
import platform
import subprocess
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FPGA = ROOT / "rtl/fpga"
PRESETS = {
    "cpu": ("configs/tangprimer25k.json", ["PROGRAM=hello"]),
    "ax2": ("configs/tangprimer25k-ax2.json", ["PROGRAM=hello"]),
    "gpu": ("configs/tangprimer25k-gpu.json", ["PROGRAM=gpu_perf"]),
    "tpu": ("configs/tangprimer25k-tpu.json", ["PROGRAM=tpu"]),
    "runtime-gpu": (
        "configs/tangprimer25k-runtime-gpu.json",
        [
            f"RAM_INIT_FILE={ROOT / 'sw/bootrom/blank.hex'}",
            f"ROM_INIT_FILE={ROOT / 'sw/bootrom/build/uart-ram32768/bootrom.hex'}",
        ],
    ),
    "morph-1pe": ("configs/tangprimer25k-morph.json", ["PROGRAM=morph"]),
    "gpu-lane1": ("configs/tangprimer25k-gpu-lane1.json", ["PROGRAM=gpu_lane1"]),
    # The bitstream a board should actually be running: blank RAM and the
    # immutable UART ROM, with no program baked in. It is locked because it is
    # the hardware every shipped payload -- games, examples, kernels -- runs on,
    # so it is the one build whose fit a software change must never touch.
    "runtime": (
        "configs/tangprimer25k-runtime.json",
        [
            f"RAM_INIT_FILE={ROOT / 'sw/bootrom/blank.hex'}",
            f"ROM_INIT_FILE={ROOT / 'sw/bootrom/build/uart-ram32768/bootrom.hex'}",
        ],
    ),
    # The loader form of each remaining baked row: identical hardware, but reset
    # into the ROM with blank RAM, so the row measures a machine instead of a
    # machine-plus-one-program. `reset_pc: 0x00001000` is what selects that, and
    # rtl/fpga/Makefile derives the blank RAM and the correctly sized UART ROM
    # from it, so these presets need no payload arguments at all -- which is the
    # point: there is no payload to name.
    "runtime-ax2": ("configs/tangprimer25k-runtime-ax2.json", []),
    "runtime-gpu4": ("configs/tangprimer25k-runtime-gpu4.json", []),
    "runtime-tpu": ("configs/tangprimer25k-runtime-tpu.json", []),
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def version(command: list[str]) -> str:
    result = subprocess.run(
        command, cwd=ROOT, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines[0] if lines else f"unavailable (exit {result.returncode})"


def run(command: list[str]) -> tuple[int, float, str]:
    start = time.monotonic()
    result = subprocess.run(
        command, cwd=ROOT, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    return result.returncode, (time.monotonic() - start) * 1000.0, result.stdout


def one_profile(label: str, build_root: Path) -> dict[str, object]:
    config_rel, extra = PRESETS[label]
    config = ROOT / config_rel
    build = build_root / label
    common = [
        "make", "-C", str(FPGA), f"BUILD={build}",
        f"COMPONENT_CONFIG={config}", *extra,
    ]
    synth_rc, synth_ms, synth_output = run([*common, "synth"])
    record: dict[str, object] = {
        "profile": label,
        "config": {"path": config_rel, "sha256": digest(config)},
        "synthesis_ms": round(synth_ms, 3),
        "synthesis_status": "PASS" if synth_rc == 0 else "FAIL",
    }
    if synth_rc:
        record["failure_tail"] = synth_output.splitlines()[-30:]
        return record

    pnr_rc, pnr_ms, pnr_output = run([*common, "all"])
    record.update({
        "place_route_pack_ms": round(pnr_ms, 3),
        "total_build_ms": round(synth_ms + pnr_ms, 3),
        "place_route_status": "PASS" if pnr_rc == 0 else "FAIL",
    })
    if pnr_rc:
        record["failure_tail"] = pnr_output.splitlines()[-30:]
        return record

    timing_paths = sorted(build.rglob("*_timing.json"))
    bitstreams = sorted(build.rglob("*.fs"))
    synth_logs = sorted(build.rglob("synth.log"))
    if len(timing_paths) != 1 or len(bitstreams) != 1 or len(synth_logs) != 1:
        raise SystemExit(
            f"{label}: expected one timing report, bitstream, and synth log; "
            f"got {len(timing_paths)}, {len(bitstreams)}, {len(synth_logs)}")
    timing = json.loads(timing_paths[0].read_text())
    record.update({
        "clocks_mhz": timing.get("fmax", {}),
        "utilization": {
            name: value for name, value in sorted(
                timing.get("utilization", {}).items())
            if value.get("used", 0)
        },
        "bitstream": {
            "bytes": bitstreams[0].stat().st_size,
            "sha256": digest(bitstreams[0]),
        },
        "synthesis_log_sha256": digest(synth_logs[0]),
        "timing_report_sha256": digest(timing_paths[0]),
    })
    return record


def write_report(output: Path, base: dict[str, object],
                 results: list[dict[str, object]], status: str) -> None:
    report = dict(base)
    report.update({
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": status,
        "results": results,
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", action="append", choices=sorted(PRESETS),
        help="profile to run (repeatable; default: all)",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--jobs", type=int, default=1, metavar="N",
        help="profiles to build concurrently (default 1). Each place-and-route "
             "peaks over 1 GiB, so keep N*1.5 GiB inside available RAM; on a "
             "6-core/10 GiB WSL, 3 is comfortable.")
    args = parser.parse_args()
    profiles = args.profile or list(PRESETS)
    output = args.output.resolve()
    base = {
        "schema": 1,
        "host": {
            "platform": platform.platform(),
            "logical_cpus": os.cpu_count(),
        },
        "tools": {
            "yosys": version(["yosys", "-V"]),
            "nextpnr_himbaechel": version(["nextpnr-himbaechel", "--version"]),
            "gowin_pack": version(["gowin_pack", "--version"]),
        },
        "method": "fresh isolated synthesis, then place/route/pack; one process",
    }
    results = []
    write_report(output, base, results, "RUNNING")
    base["jobs"] = args.jobs
    try:
        with tempfile.TemporaryDirectory(prefix="atomix-primer-synth-") as raw:
            if args.jobs == 1:
                for label in profiles:
                    print(f"{label}: fresh synthesis/P&R started", flush=True)
                    result = one_profile(label, Path(raw))
                    results.append(result)
                    write_report(output, base, results, "RUNNING")
                    print(f"{label}: synth={result['synthesis_status']} "
                          f"pnr={result.get('place_route_status', 'not-run')} "
                          f"total_ms={result.get('total_build_ms', 'n/a')}",
                          flush=True)
            else:
                # Place-and-route is ~95% of a profile's wall time and is
                # largely single-threaded, so distinct profiles overlap almost
                # perfectly.  Each one already builds in its own directory, so
                # they do not contend for artifacts -- only for CPU and memory,
                # which is why --jobs is bounded by the caller rather than
                # defaulted to the core count.
                print(f"running {len(profiles)} profiles, {args.jobs} at a time",
                      flush=True)
                with cf.ThreadPoolExecutor(max_workers=args.jobs) as pool:
                    futures = {pool.submit(one_profile, label, Path(raw)): label
                               for label in profiles}
                    for future in cf.as_completed(futures):
                        label = futures[future]
                        result = future.result()
                        results.append(result)
                        write_report(output, base, results, "RUNNING")
                        print(f"{label}: synth={result['synthesis_status']} "
                              f"pnr={result.get('place_route_status', 'not-run')} "
                              f"total_ms={result.get('total_build_ms', 'n/a')}",
                              flush=True)
                results.sort(key=lambda r: profiles.index(r["profile"]))
    except KeyboardInterrupt:
        write_report(output, base, results, "INTERRUPTED")
        raise
    write_report(output, base, results, "COMPLETE")
    print(f"synthesis benchmark: complete ({output})")
    if any(result.get("place_route_status") != "PASS" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
