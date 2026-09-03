#!/usr/bin/env python3
"""Volatile L1/L2 A/B trial: activate, canary, detect, and roll back.

Runs the same sequence against the RTL model or the physical Tang Primer, in
one resident aXos/FPGA image, with no resynthesis and no configuration write:

  1. establish the reviewed baseline on a primary and a canary workload;
  2. A/B the signed-off candidate against it on the primary workload;
  3. activate the candidate volatilely and gate it on the canary;
  4. inject a candidate that is correct on the primary but wrong on the canary,
     prove the canary catches it, and roll back to the last known good;
  5. re-verify the restored baseline.

The manager here is the host: it owns activation, the per-job deadline, the
last-known-good pointer, and rollback.  The role's own `watchdog_event` line is
still tied to zero in components/soc/reference/soc_top.sv, so this records a
manager-side deadline, not a fabric watchdog.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import struct
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "sw/host"))

import candidate_registry as cr  # noqa: E402
import live_shadow as ls  # noqa: E402
import axhost  # noqa: E402
from gpu_programs import (  # noqa: E402
    GPU_ADDI, GPU_HALT, GPU_LDX, GPU_MUL, GPU_STX, GPU_TID,
    gpu_insn, reviewed_fast_switch_programs,
)

GPU_LI, GPU_MIN, GPU_MAX = 2, 15, 16
N = 10
CONFIG = ROOT / "configs/sim-primer-runtime-gpu.json"
BOOT_ROM = ROOT / "sw/bootrom/build/uart-ram32768/bootrom.hex"
KERNEL = ROOT / "sw/kernel/build/primer-runtime/axos_boot.bin"


def poly_reference(values: list[int]) -> list[int]:
    reference = list(values) + [0] * N
    for index, value in enumerate(values):
        reference[N + index] = (value * value + 2 * value + 7) & 0xFFFFFFFF
    return reference


PRIMARY = [i - 4 for i in range(N)]
# Deliberately ranges outside the primary's [-4, 5] so a candidate that only
# works over the primary's value range cannot hide.
CANARY = [2 * i - 9 for i in range(N)]


def baseline_words() -> list[int]:
    return reviewed_fast_switch_programs(N)[1]["words"]


def horner_words() -> list[int]:
    return [
        gpu_insn(GPU_TID, rd=0),
        gpu_insn(GPU_LDX, rd=1, ra=0),
        gpu_insn(GPU_ADDI, rd=2, ra=1, imm=2),
        gpu_insn(GPU_MUL, rd=2, ra=2, rb=1),
        gpu_insn(GPU_ADDI, rd=2, ra=2, imm=7),
        gpu_insn(GPU_ADDI, rd=4, ra=0, imm=N),
        gpu_insn(GPU_STX, ra=4, rb=2),
        gpu_insn(GPU_HALT),
    ]


def clamped_words() -> list[int]:
    """Passes every static gate and the primary oracle, but silently clamps its
    input to the primary workload's value range.  Only a canary with a wider
    range can catch it."""
    return [
        gpu_insn(GPU_TID, rd=0),
        gpu_insn(GPU_LDX, rd=1, ra=0),
        gpu_insn(GPU_LI, rd=5, imm=-4),
        gpu_insn(GPU_MAX, rd=1, ra=1, rb=5),
        gpu_insn(GPU_LI, rd=6, imm=5),
        gpu_insn(GPU_MIN, rd=1, ra=1, rb=6),
        gpu_insn(GPU_ADDI, rd=2, ra=1, imm=2),
        gpu_insn(GPU_MUL, rd=2, ra=2, rb=1),
        gpu_insn(GPU_ADDI, rd=2, ra=2, imm=7),
        gpu_insn(GPU_ADDI, rd=4, ra=0, imm=N),
        gpu_insn(GPU_STX, ra=4, rb=2),
        gpu_insn(GPU_HALT),
    ]


class Session:
    """One resident aXos session.  Jobs are submitted as a batch so the RTL
    model runs the whole batch in a single boot, exactly like the physical
    board runs it in a single resident session."""

    def __init__(self, pipe, deadline_ms: float):
        self.pipe = pipe
        self.deadline_ms = deadline_ms
        self.jobs = 0
        self.deadline_misses = 0

    def run_batch(self, jobs: list[tuple[list[int], list[int]]]) -> list[dict[str, Any]]:
        requests: list[bytes] = []
        for words, data in jobs:
            requests.append(axhost.request(
                axhost.OP_GPU_LOAD, axhost.gpu_load_payload(words)))
            requests.append(axhost.request(
                axhost.OP_GPU_EXEC, axhost.gpu_exec_payload(N, data)))
        started = time.monotonic()
        if isinstance(self.pipe, axhost.SerialPipe):
            frames = self.pipe.exchange_paced(requests)
        else:
            stream = b"".join(requests) + axhost.request(axhost.OP_BYE)
            frames = axhost.parse_responses(
                self.pipe.exchange(stream, len(requests)))
        elapsed_ms = (time.monotonic() - started) * 1000.0
        if len(frames) < len(requests):
            raise SystemExit(
                f"live-ab: expected {len(requests)} frames, got {len(frames)}")

        per_job_ms = elapsed_ms / len(jobs)
        results = []
        for index in range(len(jobs)):
            load_frame, exec_frame = frames[index * 2], frames[index * 2 + 1]
            if load_frame[0] != axhost.ST_OK or len(load_frame[1]) != 4:
                raise SystemExit(f"live-ab: LOAD rejected {load_frame!r}")
            if exec_frame[0] != axhost.ST_OK or len(exec_frame[1]) < 4:
                raise SystemExit(f"live-ab: EXEC rejected {exec_frame!r}")
            payload = exec_frame[1][4:]
            output = list(struct.unpack(f"<{len(payload) // 4}I", payload))
            missed = per_job_ms > self.deadline_ms
            self.jobs += 1
            if missed:
                self.deadline_misses += 1
            results.append({
                "load_cycles": struct.unpack("<I", load_frame[1])[0],
                "execute_cycles": struct.unpack("<I", exec_frame[1][:4])[0],
                "output_sha256": ls.artifact_hash(output),
                "elapsed_ms": round(per_job_ms, 3),
                "deadline_exceeded": missed,
            })
        return results


def verify(result: dict[str, Any], expected: list[int]) -> bool:
    return result["output_sha256"] == ls.artifact_hash(expected)


def open_session(serial: str | None, baud: int, deadline_ms: float) -> Session:
    if serial:
        pipe = axhost.SerialPipe(serial, baud, timeout=5.0)
        # An earlier session may have left aXos resident, in which case the ROM
        # loader is long gone and re-uploading would hang.  Probe first and only
        # boot a kernel when nothing answers.
        try:
            frames = pipe.exchange_paced([axhost.request(axhost.OP_PING)])
        except SystemExit:
            frames = []
        if not frames or frames[0] != (axhost.ST_OK, b"aXHL"):
            print("  no resident kernel answered PING; uploading through the ROM")
            axhost.upload_kernel(pipe, KERNEL)
            frames = pipe.exchange_paced([axhost.request(axhost.OP_PING)])
            if not frames or frames[0] != (axhost.ST_OK, b"aXHL"):
                raise SystemExit(f"live-ab: bad PING {frames!r}")
        else:
            print("  reusing the resident aXos session (no kernel upload)")
        return Session(pipe, deadline_ms)
    pipe = axhost.SimPipe(None, str(CONFIG), 4_000_000, boot_rom=str(BOOT_ROM),
                          kernel_binary=str(KERNEL))
    return Session(pipe, deadline_ms)


def trial(serial: str | None, baud: int, deadline_ms: float) -> dict[str, Any]:
    session = open_session(serial, baud, deadline_ms)
    primary_data = PRIMARY + [0] * N
    canary_data = CANARY + [0] * N
    primary_oracle = poly_reference(PRIMARY)
    canary_oracle = poly_reference(CANARY)
    steps: list[dict[str, Any]] = []

    def record(name: str, result: dict[str, Any], expected: list[int],
               note: str) -> bool:
        ok = verify(result, expected)
        steps.append({"step": name, "correct": ok, "note": note, **result})
        print(f"  {name:<34} exec={result['execute_cycles']:>5} "
              f"{result['elapsed_ms']:>8.2f} ms  {'ok' if ok else 'MISMATCH'}")
        return ok

    plan = [
        (baseline_words(), primary_data),
        (baseline_words(), canary_data),
        (horner_words(), primary_data),
        (horner_words(), canary_data),
        (clamped_words(), primary_data),
        (clamped_words(), canary_data),
    ]
    observed = session.run_batch(plan)

    print("1. establish reviewed baseline")
    base_primary = observed[0]
    ok_a = record("baseline/primary", observed[0], primary_oracle,
                  "last-known-good established")
    ok_b = record("baseline/canary", observed[1], canary_oracle,
                  "baseline passes the canary workload")
    if not (ok_a and ok_b):
        raise SystemExit("live-ab: baseline failed its own oracle")
    last_known_good = "baseline"

    print("2. A/B the signed-off candidate")
    cand_primary = observed[2]
    if not record("candidate/primary", observed[2], primary_oracle,
                  "A/B against the baseline"):
        raise SystemExit("live-ab: signed-off candidate failed the primary oracle")
    improvement = base_primary["execute_cycles"] - cand_primary["execute_cycles"]

    print("3. activate volatilely and gate on the canary")
    promoted = record("candidate/canary", observed[3], canary_oracle,
                      "post-activation canary")
    if promoted:
        last_known_good = "candidate"
    print(f"   canary {'passed' if promoted else 'failed'}; "
          f"last known good = {last_known_good}")

    print("4. inject a candidate that only works on the primary workload")
    record("injected/primary", observed[4], primary_oracle,
           "fault-injected candidate hides on the primary workload")
    injected_result = observed[5]
    if record("injected/canary", observed[5], canary_oracle,
              "canary must reject it"):
        raise SystemExit("live-ab: the fault-injected candidate was not caught")
    print("   canary rejected the injected candidate; rolling back")

    print("5. roll back to the last known good and re-verify")
    rollback_words = horner_words() if last_known_good == "candidate" \
        else baseline_words()
    restored = session.run_batch([(rollback_words, primary_data),
                                  (rollback_words, canary_data)])
    ok_g = record("rollback/primary", restored[0], primary_oracle,
                  f"restored {last_known_good}")
    ok_h = record("rollback/canary", restored[1], canary_oracle,
                  f"restored {last_known_good} passes the canary")
    if not (ok_g and ok_h):
        raise SystemExit("live-ab: rollback did not restore a correct configuration")

    return {
        "steps": steps,
        "jobs": session.jobs,
        "deadline_ms": deadline_ms,
        "deadline_misses": session.deadline_misses,
        "baseline_execute_cycles": base_primary["execute_cycles"],
        "candidate_execute_cycles": cand_primary["execute_cycles"],
        "improvement_cycles": improvement,
        "candidate_promoted": promoted,
        "injected_caught_by": "org.atomix.gate.canary-workload",
        "injected_execute_cycles": injected_result["execute_cycles"],
        "last_known_good": last_known_good,
        "rollback_verified": ok_g and ok_h,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", help="physical UART, e.g. /dev/ttyUSB1")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--deadline-ms", type=float, default=250.0,
                        help="manager-side per-job deadline")
    parser.add_argument("--output", type=Path, help="write a JSON evidence file")
    args = parser.parse_args()

    level = "org.atomix.physical-tang-primer-25k" if args.serial \
        else "org.atomix.verilator-rtl"
    print(f"live A/B trial ({level})")
    summary = trial(args.serial, args.baud, args.deadline_ms)

    print(f"\nbaseline {summary['baseline_execute_cycles']} -> candidate "
          f"{summary['candidate_execute_cycles']} execute cycles "
          f"({summary['improvement_cycles']} fewer)")
    print(f"deadline misses: {summary['deadline_misses']}/{summary['jobs']} jobs")
    print("live A/B trial: PASS (canary caught the injected candidate, "
          "rollback restored a verified configuration)")

    if args.output:
        document = {
            "schema": {"id": "org.atomix.live-ab-trial", "major": 1, "minor": 0},
            "kind": "live-ab-trial",
            "id": "org.atomix.live-ab-trial.polynomial-horner",
            "revision": 1,
            "summary": ("Volatile L1/L2 A/B trial with canary rejection and "
                        "manager-owned rollback"),
            "environment": {
                "level": level,
                "timestamp_utc": dt.datetime.now(dt.timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"),
                "role": "org.atomix.role.gpu-compute",
                "resident_image": True,
                "reconfigured_fabric": False,
                "persistent_flash_written": False,
                "fabric_watchdog_wired": False,
            },
            "workloads": {
                "primary": {"values": PRIMARY,
                            "oracle_sha256": ls.artifact_hash(poly_reference(PRIMARY))},
                "canary": {"values": CANARY,
                           "oracle_sha256": ls.artifact_hash(poly_reference(CANARY))},
            },
            "programs": {
                "baseline_sha256": ls.artifact_hash(baseline_words()),
                "candidate_sha256": ls.artifact_hash(horner_words()),
                "injected_sha256": ls.artifact_hash(clamped_words()),
            },
            "result": summary,
            "extensions": {},
        }
        document["content_id"] = cr.document_content_id(document)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(document, indent=2) + "\n",
                               encoding="utf-8")
        print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
