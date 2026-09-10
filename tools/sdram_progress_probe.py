#!/usr/bin/env python3
"""Why does a workload stop making progress on one memory and not another?

A cycle limit cannot answer that.  Raising it is not a diagnosis either: a run
that needs 10x more cycles because the hart is stalled on a bus, one that needs
them because it keeps re-entering a trap handler, and one that is simply doing
slow useful work all look identical from outside, and want three different
fixes.

So this boots the *same* software, from the same SD image through the same ROM
loader, on three memories -- on-chip RAM, the delayed backing model, and the
pin-level SDRAM controller -- with the SoC's progress monitor compiled in, and
records what each machine actually did: instructions retired per privilege
mode, handler entries and exits, timer arrivals, pending interrupt bits, and
the cycles spent waiting on the instruction and data ports.

The three signatures are distinguishable:

  interrupt starvation  user retirements stop while handler entries keep
                        climbing -- the hart is servicing, not working.
  stalled transaction   stall cycles approach the run length and retirements
                        of every mode stop together.
  slow useful work      every counter advances, just at a lower rate.

It makes no board claim: all three are simulations.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROGRESS = re.compile(r"\[soc\] progress: (.*)")
PINS = re.compile(r"\[soc\] sdram-pins: (.*)")
EXIT = re.compile(r"\[soc\] exit 0 \(cycles=(\d+)\)")
FAIL = re.compile(r"\[soc\] FAIL finished=(\d+) exit=(\d+) cycles=(\d+)")

# Every machine boots the same image through the same ROM, so the memory is
# the only intended difference.  The pin-level profile fixes its own RAM size
# and cache setting in the harness top, so the other two are given the same
# ones explicitly rather than inheriting a profile default -- a comparison
# whose cache differs silently is not a comparison.
MACHINES = {
    "bram": {
        "config": "configs/sim-progress-bram.json",
        "target": "run",
        "extra": ["RAM_BYTES=33554432", "CACHES=1", "RESET_PC=0x00001000"],
        "note": "on-chip RAM array, single-cycle, 32 MiB, I/D caches on",
    },
    "delayed": {
        "config": "configs/sim-progress-delayed.json",
        "target": "run",
        "extra": ["RAM_BYTES=33554432", "CACHES=1", "RESET_PC=0x00001000"],
        "note": "delayed backing-store model, 32 MiB, I/D caches on",
    },
    "sdram": {
        "config": "configs/sim-progress-sdram.json",
        "target": "run-config",
        "extra": [],
        "note": "axsdram controller against the CAS-2 x16 pin model, "
                "32 MiB, I/D caches on (fixed by the harness top)",
    },
}

WORKLOADS = {
    "shell": "sw/kernel/shell_input.txt",
    "fork": "sw/kernel/fork_input.txt",
    "exec": "sw/kernel/exec_input.txt",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_pairs(text: str) -> dict[str, int]:
    fields = {}
    for key, value in re.findall(r"(\w+)=(0x[0-9a-f]+|\d+)", text):
        fields[key] = int(value, 0)
    return fields


def resolve(config: Path) -> dict[str, str]:
    out = subprocess.run(
        [sys.executable, str(ROOT / "tools/configure.py"), "resolve",
         "--config", str(config)],
        cwd=ROOT, text=True, capture_output=True, check=True).stdout
    resolved = {}
    for line in out.splitlines():
        if ":=" in line:
            key, _, value = line.partition(":=")
            resolved[key.strip()] = value.strip()
    return resolved


def run_machine(name: str, workload: str, max_cycles: int,
                timeout: int) -> dict:
    spec = MACHINES[name]
    config = ROOT / spec["config"]
    resolved = resolve(config)
    boot_rom = ROOT / "sw/bootrom/build/bootrom.hex"
    sd_image = ROOT / "sw/kernel/build/axos_boot.img"
    for required in (boot_rom, sd_image):
        if not required.exists():
            raise SystemExit(
                f"{required} is missing; run: make -C sw/kernel boot-disk")
    command = [
        "make", "-s", "--no-print-directory", "-C", str(ROOT / "sim/soc"),
        spec["target"], f"COMPONENT_CONFIG={config}",
        f"ROM_INIT_FILE={boot_rom}", f"SD_IMAGE={sd_image}",
        f"UART_INPUT_FILE={ROOT / WORKLOADS[workload]}",
        f"MAX_CYCLES={max_cycles}", f"BUILD_ID=progress-{name}",
    ] + spec["extra"]
    started = time.monotonic()
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True,
                            timeout=timeout)
    elapsed = time.monotonic() - started

    progress = PROGRESS.search(result.stderr)
    pins = PINS.search(result.stderr)
    exited = EXIT.search(result.stderr)
    failed = FAIL.search(result.stderr)
    if progress is None:
        sys.stderr.write(result.stderr)
        raise SystemExit(
            f"{name}: the run reported no progress line; the profile did not "
            "select the monitor, or the model predates it")
    record = {
        "machine": name,
        "note": spec["note"],
        "profile": resolved["COMPONENT_CONFIG_NAME"],
        "memory": resolved["COMPONENT_MEMORY_ID"],
        "cache": resolved["COMPONENT_CACHE_ID"],
        "harness": resolved["COMPONENT_HARNESS_ID"],
        "sim_top": resolved["COMPONENT_SIM_TOP"],
        "defines": resolved.get("COMPONENT_DEFINES", ""),
        "command": [c.replace(str(ROOT), "{root}") for c in command],
        "max_cycles": max_cycles,
        "finished": bool(exited),
        "cycles": int(exited.group(1)) if exited else
                  (int(failed.group(3)) if failed else None),
        "wall_seconds": round(elapsed, 1),
        "progress": parse_pairs(progress.group(1)),
        "sdram_pins": (parse_pairs(pins.group(1))
                       if pins and not pins.group(1).startswith("absent")
                       else None),
        "uart": result.stdout,
    }
    return record


def classify(record: dict) -> dict:
    """Derived shares, kept separate from the counters they come from.

    The two ports stall independently and can stall in the same cycle, so
    their shares are reported separately and never summed -- a combined
    figure would exceed 100% and mean nothing.
    """
    p = record["progress"]
    cycles = p.get("cycles", 0) or 1
    derived = {
        "retired_per_1k_cycles": round(1000 * p.get("retired", 0) / cycles, 2),
        "user_retired_per_1k_cycles": round(1000 * p.get("user", 0) / cycles, 3),
        "ifetch_stall_share": round(p.get("ifetch_stall", 0) / cycles, 4),
        "dmem_stall_share": round(p.get("dmem_stall", 0) / cycles, 4),
        "idle_share": round(p.get("idle", 0) / cycles, 4),
    }
    if record["finished"]:
        summary = ("completed in %d cycles; %.1f%% of cycles waiting on "
                   "instruction fetch, %.1f%% on data"
                   % (record["cycles"] or 0,
                      100 * derived["ifetch_stall_share"],
                      100 * derived["dmem_stall_share"]))
    elif p.get("user_entries", 0) > 0 and p.get("user", 0) == 0:
        summary = ("did not complete: reached user mode %d times and retired "
                   "no user instruction" % p["user_entries"])
    elif p.get("user_entries", 0) == 0:
        summary = ("did not complete: never reached user mode; last commit at "
                   "0x%08x in mode %d" % (p.get("last_pc", 0),
                                          p.get("last_prv", 0)))
    else:
        summary = ("did not complete: %d user instructions retired over %d "
                   "handler entries" % (p.get("user", 0), p["user_entries"]))
    derived["summary"] = summary
    return derived


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", default="exec", choices=sorted(WORKLOADS))
    parser.add_argument("--machines", default="bram,delayed,sdram")
    parser.add_argument("--max-cycles", type=int, default=40_000_000)
    parser.add_argument("--timeout", type=int, default=5400,
                        help="wall-clock seconds per machine, build included")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    names = [n.strip() for n in args.machines.split(",") if n.strip()]
    for name in names:
        if name not in MACHINES:
            raise SystemExit(f"unknown machine {name!r}; "
                             f"choose from {', '.join(sorted(MACHINES))}")

    inputs = {
        "sw/kernel/build/axos_boot.img": sha256(
            ROOT / "sw/kernel/build/axos_boot.img"),
        "sw/kernel/build/axos_boot.bin": sha256(
            ROOT / "sw/kernel/build/axos_boot.bin"),
        "sw/bootrom/build/bootrom.hex": sha256(
            ROOT / "sw/bootrom/build/bootrom.hex"),
        WORKLOADS[args.workload]: sha256(ROOT / WORKLOADS[args.workload]),
    }
    verilator = subprocess.run(["verilator", "--version"], text=True,
                               capture_output=True).stdout.strip()

    records = []
    for name in names:
        print(f"[progress] {name}: {MACHINES[name]['note']}", flush=True)
        record = run_machine(name, args.workload, args.max_cycles, args.timeout)
        record["derived"] = classify(record)
        p = record["progress"]
        print(f"[progress] {name}: {record['derived']['summary']}")
        print(f"[progress] {name}: retired={p.get('retired')} "
              f"user={p.get('user')} supervisor={p.get('supervisor')} "
              f"machine={p.get('machine')} entries={p.get('user_entries')} "
              f"exits={p.get('user_exits')} "
              f"timer_arrivals={p.get('timer_arrivals')} "
              f"ifetch_stall={p.get('ifetch_stall')} "
              f"dmem_stall={p.get('dmem_stall')} idle={p.get('idle')} "
              f"last_pc=0x{p.get('last_pc', 0):08x} "
              f"last_mip=0x{p.get('last_mip', 0):08x} "
              f"last_prv={p.get('last_prv')}", flush=True)
        records.append(record)

    document = {
        "schema": "org.atomix.progress-probe.v1",
        "evidence_kind": "simulation",
        "scope": "No FPGA synthesis, programming, or physical-board result.",
        "workload": args.workload,
        "max_cycles": args.max_cycles,
        "verilator": verilator,
        "input_sha256": inputs,
        "machines": records,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(document, indent=2) + "\n")
        print(f"[progress] wrote {args.output}")
    else:
        print(json.dumps(document, indent=2))


if __name__ == "__main__":
    main()
