#!/usr/bin/env python3
"""Produce the L2 shadow record by actually simulating each candidate.

Separate from `live_shadow.py` on purpose: this writes records, the checker
only reads them.  Every number in the emitted record comes from the RTL run
performed here, and every verdict is derived by the checker's own functions so
authoring cannot disagree with validation.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "sw/host"))

import candidate_registry as cr  # noqa: E402
import live_shadow as ls  # noqa: E402
from gpu_programs import (  # noqa: E402
    GPU_ADDI, GPU_HALT, GPU_LDX, GPU_MUL, GPU_STX, GPU_TID,
    gpu_insn, reviewed_fast_switch_programs,
)

CONFIG = ROOT / "configs/sim-primer-runtime-gpu.json"
BOOT_ROM = ROOT / "sw/bootrom/build/uart-ram32768/bootrom.hex"
KERNEL = ROOT / "sw/kernel/build/primer-runtime/axos_boot.bin"
SHADOW_PATH = ROOT / "research/live-fpga/shadow/l2-polynomial-horner.json"
N = 10


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tool_versions() -> list[dict[str, str]]:
    verilator = subprocess.run(["verilator", "--version"], capture_output=True,
                               text=True, check=True).stdout.split()
    return [
        {"id": "org.atomix.tool.verilator", "version": verilator[1]},
        {"id": "org.atomix.tool.axhost", "version": "1.0"},
    ]


def identity(logical_id: str, numeric_id: int, workload: str, words: list[int],
             parent: str | None, operator: str,
             parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": logical_id,
        "numeric_id": numeric_id,
        "role": "org.atomix.role.gpu-compute",
        "workload": {"id": workload, "revision": 1},
        "artifact": {
            "format": "org.atomix.gpu-compute.words-le32",
            "sha256": ls.artifact_hash(words),
        },
        "build": {
            "source": {
                "path": "sw/host/gpu_programs.py",
                "sha256": file_sha256(ROOT / "sw/host/gpu_programs.py"),
            },
            "profile": {
                "path": "configs/sim-primer-runtime-gpu.json",
                "sha256": file_sha256(CONFIG),
            },
            "tools": tool_versions(),
        },
        "lineage": {
            "parent": parent,
            "mutation": {"operator": operator, "seed": None,
                         "parameters": parameters},
        },
    }


def polynomial_data() -> list[int]:
    return [i - 4 for i in range(N)] + [0] * N


def polynomial_oracle(constant: int = 7) -> list[int]:
    data = polynomial_data()
    reference = list(data)
    for i in range(N):
        value = data[i]
        reference[N + i] = (value * value + 2 * value + constant) & 0xFFFFFFFF
    return reference


def horner_words(constant: int = 7) -> list[int]:
    """(x + 2) * x + constant — one instruction shorter than the reviewed
    x*x + 2*x + constant form, with identical results."""
    return [
        gpu_insn(GPU_TID, rd=0),
        gpu_insn(GPU_LDX, rd=1, ra=0),
        gpu_insn(GPU_ADDI, rd=2, ra=1, imm=2),
        gpu_insn(GPU_MUL, rd=2, ra=2, rb=1),
        gpu_insn(GPU_ADDI, rd=2, ra=2, imm=constant),
        gpu_insn(GPU_ADDI, rd=4, ra=0, imm=N),
        gpu_insn(GPU_STX, ra=4, rb=2),
        gpu_insn(GPU_HALT),
    ]


def build() -> int:
    reviewed = {program["id"]: program
                for program in reviewed_fast_switch_programs(N)}
    baseline_program = reviewed["org.atomix.gpu-program.polynomial-i32-v1"]
    registry = cr.load_json(cr.DEFAULT_REGISTRY)
    baseline_entry = next(
        candidate for candidate in registry["candidates"]
        if candidate["identity"]["id"] == baseline_program["id"])
    baseline_id = baseline_entry["content_id"]
    workload = "org.atomix.workload.polynomial-i32"
    oracle = ls.artifact_hash(polynomial_oracle())

    horner = horner_words()
    horner_identity = identity(
        "org.atomix.gpu-program.polynomial-i32-v2-horner", 3, workload, horner,
        baseline_id, "org.atomix.mutation.horner-strength-reduction",
        {"removed_instructions": 1, "constant": 7})

    # A plausible-looking regression: same shape, wrong constant.  It passes
    # every static gate, so only the oracle can reject it — which is the point.
    offby = horner_words(constant=8)
    offby_identity = identity(
        "org.atomix.gpu-program.polynomial-i32-v2-offby", 4, workload, offby,
        cr.content_id(horner_identity), "org.atomix.mutation.constant-perturbation",
        {"constant_delta": 1})

    candidates = [
        {
            "candidate": baseline_id,
            "identity": baseline_entry["identity"],
            "label": "reviewed baseline polynomial x*x + 2*x + 7",
            "words": baseline_program["words"],
            "data": baseline_program["data"],
            "oracle": ls.artifact_hash(baseline_program["expected"]),
        },
        {
            "candidate": cr.content_id(horner_identity),
            "identity": horner_identity,
            "label": "Horner candidate (x + 2) * x + 7",
            "words": horner,
            "data": polynomial_data(),
            "oracle": oracle,
        },
        {
            "candidate": cr.content_id(offby_identity),
            "identity": offby_identity,
            "label": "fault-injected Horner candidate with constant 8",
            "words": offby,
            "data": polynomial_data(),
            "oracle": oracle,
        },
    ]

    # Only statically clean candidates are ever simulated.  This mirrors the
    # deployment rule: nothing unproven reaches an execution path.
    runnable = []
    for entry in candidates:
        gates = ls.static_gates(entry["words"], N)
        entry["static_ok"] = all(item["status"] == ls.PASS for item in gates)
        if entry["static_ok"]:
            runnable.append(entry)

    print(f"simulating {len(runnable)} of {len(candidates)} candidates "
          f"(static gates rejected {len(candidates) - len(runnable)})")
    observations = ls.simulate(
        [{"words": entry["words"], "data": entry["data"], "nthreads": N,
          "label": entry["label"]} for entry in runnable],
        CONFIG, BOOT_ROM, KERNEL, 4_000_000)
    for entry, (output_sha256, load_cycles, execute_cycles) in zip(
            runnable, observations):
        entry["observation"] = {
            "simulated": True,
            "output_sha256": output_sha256,
            "load_cycles": load_cycles,
            "execute_cycles": execute_cycles,
        }
        print(f"  {entry['label']}: load={load_cycles} exec={execute_cycles} "
              f"output={output_sha256[:16]}")

    evaluations = []
    for entry in candidates:
        evaluation = {
            "candidate": entry["candidate"],
            "identity": entry["identity"],
            "label": entry["label"],
            "program": {
                "words": entry["words"],
                "nthreads": N,
                "workload": workload,
                "program_sha256": ls.artifact_hash(entry["words"]),
                "oracle_sha256": entry["oracle"],
            },
            "observation": entry.get("observation", {
                "simulated": False, "output_sha256": None,
                "load_cycles": None, "execute_cycles": None}),
        }
        evaluation.update(ls.derive_evaluation(evaluation))
        evaluations.append(evaluation)

    document: dict[str, Any] = {
        "schema": {"id": "org.atomix.live-l2-shadow", "major": 1, "minor": 0},
        "kind": "live-l2-shadow",
        "id": "org.atomix.live-l2-shadow.polynomial-horner",
        "revision": 1,
        "summary": ("L2 shadow evaluation of a Horner polynomial candidate and "
                    "a fault-injected variant against the reviewed baseline"),
        "content_id": "sha256:" + "0" * 64,
        "baseline": baseline_id,
        "environment": {
            "level": "org.atomix.verilator-rtl",
            "timestamp_utc": dt.datetime.now(dt.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
            "role": "org.atomix.role.gpu-compute",
            "limits": {"program_words": ls.PROG_WORDS,
                       "data_words": ls.DATA_WORDS,
                       "lanes": ls.NLANES, "registers": ls.NREGS},
            "tools": tool_versions(),
        },
        "evaluations": evaluations,
        "request": {},
        "extensions": {},
    }
    document["request"] = ls.derive_request(document)
    document["content_id"] = cr.document_content_id(document)

    SHADOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    SHADOW_PATH.write_text(json.dumps(document, indent=2) + "\n",
                           encoding="utf-8")
    print(f"wrote {SHADOW_PATH.relative_to(ROOT)}")
    print(f"request: {document['request']['status']} "
          f"candidate={document['request']['candidate']} "
          f"actuation={document['request']['actuation']}")
    return 0


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    return build()


if __name__ == "__main__":
    raise SystemExit(main())
