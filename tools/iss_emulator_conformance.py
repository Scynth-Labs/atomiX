#!/usr/bin/env python3
"""AX-12 conformance: one ELF on aXsim and QEMU, including failure paths."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import execution
import personality_contract as pc


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "research" / "experiments" / "riscv-software-models.json"
WORKLOAD = ROOT / "research" / "personalities" / "workloads" / "cpu-perf.json"
EXPECTED_CHECKSUM = 0xE9266745
failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f": {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def find(document: dict[str, Any], group: str, suffix: str) -> dict[str, Any]:
    return next(item for item in document[group] if item["id"].endswith(suffix))


def require_tools() -> bool:
    try:
        plan = pc.load_document(PLAN)
        execution.AxsimAdapter().describe(find(plan, "targets", "axsim-rv32im"))
        execution.QemuRiscvAdapter().describe(find(plan, "targets", "qemu-virt-rv32im"))
    except execution.Blocked as exc:
        print(f"ISS/emulator adapters: BLOCKED: {exc}")
        return False
    return True


def run_plan(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    records = root / "records"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "experiment_run.py"), str(PLAN),
         "--records", str(records), "--work", str(root / "work"), "--no-reuse"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if completed.returncode:
        check("shared ELF runs on aXsim and QEMU", False,
              completed.stderr.strip() or completed.stdout.strip())
        return {}, {}
    axsim = pc.load_document(records / "cpu-perf-on-axsim.json")
    qemu = pc.load_document(records / "cpu-perf-on-qemu-virt.json")
    check("shared ELF runs on aXsim and QEMU",
          axsim["status"] == qemu["status"] == "org.atomix.pass")
    check("both targets ran identical artifact bytes",
          axsim["identity"]["implementation"]["artifact_sha256"] ==
          qemu["identity"]["implementation"]["artifact_sha256"])
    outputs = [
        record["extensions"]["org.atomix.case-outcomes"]
        ["resident-integer-mix"]["outputs"]["checksum"]
        for record in (axsim, qemu)
    ]
    check("both targets reproduce the exact checksum",
          outputs == [[EXPECTED_CHECKSUM], [EXPECTED_CHECKSUM]], str(outputs))
    check("aXsim records deterministic retired instructions",
          axsim["measurements"]["org.atomix.metric.iss-total-retired-instructions"]
          ["status"] == "org.atomix.measured",
          str(axsim["measurements"]
              ["org.atomix.metric.iss-total-retired-instructions"]["value"]))
    check("QEMU records its guest counter under a different metric",
          qemu["measurements"]["org.atomix.metric.emulator-guest-mcycle"]
          ["status"] == "org.atomix.measured",
          str(qemu["measurements"]["org.atomix.metric.emulator-guest-mcycle"]["value"]))
    return axsim, qemu


def check_missing_qemu(plan: dict[str, Any], root: Path) -> None:
    target = find(plan, "targets", "qemu-virt-rv32im")
    previous_path = os.environ.get("PATH", "")
    previous_qemu = os.environ.pop("QEMU", None)
    empty = root / "empty-path"
    empty.mkdir()
    os.environ["PATH"] = str(empty)
    try:
        try:
            execution.QemuRiscvAdapter().describe(target)
        except execution.Blocked as exc:
            check("a missing QEMU reports blocked without ISS fallback",
                  "QEMU emulator" in str(exc), str(exc))
            return
        check("a missing QEMU reports blocked without ISS fallback", False,
              "describe() succeeded")
    finally:
        os.environ["PATH"] = previous_path
        if previous_qemu is not None:
            os.environ["QEMU"] = previous_qemu


def check_unsupported_requests(plan: dict[str, Any]) -> None:
    requests = {
        "role": "org.atomix.capability.role-window",
        "privilege": "org.atomix.capability.supervisor-mode-entry",
        "device": "org.atomix.capability.virtio-block-device",
    }
    implementation = find(plan, "implementations", "cpu-perf-rv32-elf")
    adapters = (
        ("aXsim", execution.AxsimAdapter(), find(plan, "targets", "axsim-rv32im")),
        ("QEMU", execution.QemuRiscvAdapter(),
         find(plan, "targets", "qemu-virt-rv32im")),
    )
    for model, adapter, target in adapters:
        description = adapter.describe(target)
        for label, capability in requests.items():
            requested = copy.deepcopy(implementation)
            requested["requires"].append(capability)
            try:
                adapter.check_compatibility(description, requested, target)
            except execution.Unsupported:
                check(f"{model} refuses unsupported {label} before execution", True)
                continue
            check(f"{model} refuses unsupported {label} before execution", False)


def check_bounded_execution(plan: dict[str, Any], qemu_record: dict[str, Any],
                            root: Path) -> None:
    adapter = execution.QemuRiscvAdapter()
    target = find(plan, "targets", "qemu-virt-rv32im")
    implementation = find(plan, "implementations", "cpu-perf-rv32-elf")
    workload = pc.load_document(WORKLOAD)
    cases = workload["cases"]
    work = root / "bounded"
    work.mkdir()
    prepared = adapter.prepare(implementation, target, work, cases, limit_seconds=120)
    check("bounded run uses the same QEMU model identity",
          prepared.target_build_sha256 ==
          qemu_record["identity"]["target"]["build_sha256"])
    try:
        adapter.execute(prepared, target, cases, work,
                        limit_seconds=0.000001, repetitions=1)
    except execution.Timeout:
        check("QEMU execution is terminated by its adapter bound", True)
        return
    except execution.AdapterError as exc:
        check("QEMU execution is terminated by its adapter bound", False,
              f"raised {type(exc).__name__}: {exc}")
        return
    check("QEMU execution is terminated by its adapter bound", False,
          "the deliberately tiny limit completed")


def check_failed_replay(plan: dict[str, Any], root: Path) -> None:
    personality = root / "failed-personalities" / "workloads"
    personality.mkdir(parents=True)
    workload = pc.load_document(WORKLOAD)
    workload["cases"][0]["expected"]["checksum"] = [EXPECTED_CHECKSUM ^ 1]
    (personality / "cpu-perf.json").write_text(json.dumps(workload, indent=2) + "\n")
    plan_path = root / "failed-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2) + "\n")
    records = root / "failed-records"
    command = [
        sys.executable, str(ROOT / "tools" / "experiment_run.py"), str(plan_path),
        "--personality-root", str(personality.parent), "--only", "cpu-perf-on-axsim",
        "--records", str(records), "--work", str(root / "failed-work"), "--no-reuse",
    ]
    first = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    record = records / "cpu-perf-on-axsim.json"
    if first.returncode != 1 or not record.is_file():
        check("a failed oracle run is retained", False,
              first.stderr.strip() or first.stdout.strip())
        return
    failed = pc.load_document(record)
    check("a failed oracle run is retained", failed["status"] == "org.atomix.fail")
    replay = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "experiment_run.py"), str(plan_path),
         "--personality-root", str(personality.parent), "--replay", str(record),
         "--work", str(root / "replay-work")],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    check("failed-run replay reproduces the failure",
          replay.returncode == 0 and "failed result" in replay.stdout,
          replay.stderr.strip() or replay.stdout.strip().splitlines()[-1])


def main() -> int:
    print("ISS/emulator adapter conformance:")
    if not require_tools():
        return 2
    plan = pc.load_document(PLAN)
    root = Path(tempfile.mkdtemp(prefix="ax-iss-emulator-"))
    try:
        _, qemu = run_plan(root)
        if not qemu:
            return 1
        check_missing_qemu(plan, root)
        check_unsupported_requests(plan)
        check_bounded_execution(plan, qemu, root)
        check_failed_replay(plan, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if failures:
        print(f"ISS/emulator adapter conformance: FAIL ({len(failures)} gates)")
        return 1
    print("ISS/emulator adapter conformance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
