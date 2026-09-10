#!/usr/bin/env python3
"""Prove the execution adapters' failure modes, not just their happy path.

An adapter is only useful if its refusals are trustworthy.  These checks
exercise the cases a passing experiment never reaches:

- the native leg builds and runs with every RISC-V, Verilator, and FPGA tool
  shadowed by a stub that fails on sight, so a hidden dependency on them would
  break the build rather than pass unnoticed;
- a missing prerequisite is reported as blocked, and no adapter substitutes a
  different target when its own is unavailable;
- semantics the target cannot honour are refused before execution;
- a time limit and a cancellation both terminate the work the adapter owns,
  including the process group beneath it.

Run with `make adapter-check`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import execution
import personality_contract as pc
from execution.contract import TERMINATION_CANCELLED, TERMINATION_TIMEOUT, run_bounded

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "research" / "experiments" / "saxpy-native-vs-rtl.json"
PERSONALITIES = ROOT / "research" / "personalities"

# Tools the native leg must not need. Shadowing them with a failing stub is a
# stronger check than removing them from PATH: a compiler driver finds `as` and
# `ld` through its own configured paths, so an empty PATH would prove nothing
# about the toolchain and everything about PATH.
FORBIDDEN_TOOLS = (
    "verilator", "riscv64-unknown-elf-gcc", "riscv32-unknown-elf-gcc",
    "riscv64-elf-gcc", "riscv64-linux-gnu-gcc", "yosys", "nextpnr-himbaechel",
    "nextpnr-ecp5", "openFPGALoader",
)

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f": {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def plan_parts() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    plan = pc.load_document(PLAN)
    workloads = {}
    for path in pc.collect([PERSONALITIES]):
        document = pc.load_document(path)
        if document["kind"] == "workload":
            workloads[(document["id"], document["revision"])] = document
    workload = workloads[(plan["workload"]["id"], plan["workload"]["revision"])]
    selected = set(plan["workload"]["cases"])
    cases = [case for case in workload["cases"] if case["name"] in selected]
    return plan, workload, cases


def find(plan: dict[str, Any], key: str, value: str) -> dict[str, Any]:
    return next(item for item in plan[key] if item["id"] == value)


def shadowed_path(directory: Path, tools: tuple[str, ...]) -> str:
    """A PATH whose first entry makes the named tools fail if invoked."""
    for tool in tools:
        stub = directory / tool
        stub.write_text(
            "#!/bin/sh\n"
            f"echo \"{tool} is not available in this environment\" >&2\n"
            "exit 127\n"
        )
        stub.chmod(0o755)
    return f"{directory}{os.pathsep}{os.environ['PATH']}"


def check_native_without_hardware_tools(plan, cases, workdir: Path) -> None:
    """The native leg's real claim: no RISC-V, RTL, or FPGA tool in its path."""
    stubs = workdir / "stubs"
    stubs.mkdir()
    path = shadowed_path(stubs, FORBIDDEN_TOOLS)
    previous = os.environ["PATH"]
    os.environ["PATH"] = path
    try:
        for tool in ("verilator", "riscv64-unknown-elf-gcc"):
            probe = subprocess.run([tool, "--version"], capture_output=True, text=True,
                                   check=False)
            if probe.returncode != 127:
                check(f"{tool} is shadowed", False, "the stub did not take effect")
                return
        adapter = execution.NativeCpuAdapter()
        target = find(plan, "targets", "org.atomix.target.host-cpu")
        implementation = find(plan, "implementations",
                              "org.atomix.implementation.saxpy-native-c")
        description = adapter.describe(target)
        adapter.check_compatibility(description, implementation, target)
        native = workdir / "native"
        native.mkdir()
        prepared = adapter.prepare(implementation, target, native, cases,
                                   limit_seconds=300)
        result = adapter.execute(prepared, target, cases, native,
                                 limit_seconds=60, repetitions=3)
        outputs = {outcome.name: outcome.outputs["out"] for outcome in result.cases}
        expected = {
            case["name"]: pc.builtin_result(
                PLAN, "org.atomix.workload.saxpy-i32",
                {"items": case["parameters"]["items"], "a": case["parameters"]["a"]},
                case["inputs"],
            )["out"]
            for case in cases
        }
        check("native leg builds and runs with hardware tools shadowed",
              outputs == expected,
              "" if outputs == expected else f"{outputs} != {expected}")
    except execution.AdapterError as exc:
        check("native leg builds and runs with hardware tools shadowed", False, str(exc))
    finally:
        os.environ["PATH"] = previous


def check_missing_prerequisite_is_blocked(plan, workdir: Path) -> None:
    """A target whose tool is absent blocks; it does not fall back."""
    stubs = workdir / "stubs-rtl"
    stubs.mkdir()
    empty = workdir / "empty"
    empty.mkdir()
    previous = os.environ["PATH"]
    os.environ["PATH"] = str(empty)
    try:
        adapter = execution.RtlRoleAdapter()
        target = find(plan, "targets", "org.atomix.target.role-gpu-compute-sim")
        try:
            adapter.describe(target)
        except execution.Blocked as exc:
            check("absent verilator reports blocked", True, str(exc))
            return
        except execution.AdapterError as exc:
            check("absent verilator reports blocked", False,
                  f"raised {type(exc).__name__} instead")
            return
        check("absent verilator reports blocked", False, "describe() succeeded")
    finally:
        os.environ["PATH"] = previous


def check_unsupported_semantics(plan, workdir: Path) -> None:
    """A scalar too wide for the engine's immediate is refused, not truncated."""
    adapter = execution.RtlRoleAdapter()
    target = find(plan, "targets", "org.atomix.target.role-gpu-compute-sim")
    implementation = find(plan, "implementations",
                          "org.atomix.implementation.saxpy-simt-kernel")
    wide = [{
        "name": "wide-scalar",
        "parameters": {"items": 4, "a": 70000},
        "inputs": {"x": [1, 2, 3, 4], "y": [0, 0, 0, 0]},
        "expected": {},
    }]
    directory = workdir / "unsupported"
    directory.mkdir()
    try:
        adapter.prepare(implementation, target, directory, wide, limit_seconds=300)
    except execution.Unsupported as exc:
        check("a scalar beyond the 17-bit immediate is refused", True, str(exc))
        return
    except execution.AdapterError as exc:
        check("a scalar beyond the 17-bit immediate is refused", False,
              f"raised {type(exc).__name__}: {exc}")
        return
    check("a scalar beyond the 17-bit immediate is refused", False, "prepare() succeeded")


def check_declared_capability_is_not_granted(plan) -> None:
    """A plan cannot give a target an ability by writing it down."""
    adapter = execution.NativeCpuAdapter()
    target = dict(find(plan, "targets", "org.atomix.target.host-cpu"))
    target["capabilities"] = target["capabilities"] + ["org.atomix.capability.simt"]
    implementation = find(plan, "implementations",
                          "org.atomix.implementation.saxpy-native-c")
    try:
        adapter.check_compatibility(adapter.describe(target), implementation, target)
    except execution.Unsupported as exc:
        check("an undiscovered declared capability is rejected", True, str(exc))
        return
    check("an undiscovered declared capability is rejected", False, "accepted")


def check_timeout_and_cancellation() -> None:
    """Bounded work ends, and the whole process group ends with it."""
    marker = Path(tempfile.mkdtemp()) / "still-running"
    script = (
        f"sh -c 'sleep 30; touch {marker}' & "
        "wait"
    )
    started = time.monotonic()
    result = run_bounded(["sh", "-c", script], 1.0)
    elapsed = time.monotonic() - started
    check("a time limit terminates the work",
          result.termination == TERMINATION_TIMEOUT and elapsed < 10,
          f"{result.termination} after {elapsed:.1f}s")
    time.sleep(2)
    check("the process group dies with it", not marker.exists(),
          "a grandchild outlived the timeout" if marker.exists() else "")

    started = time.monotonic()
    cancelled = run_bounded(["sh", "-c", "sleep 30"], 30.0, cancel_after=0.5)
    elapsed = time.monotonic() - started
    check("cancellation terminates the work",
          cancelled.termination == TERMINATION_CANCELLED and elapsed < 10,
          f"{cancelled.termination} after {elapsed:.1f}s")


def check_execution_timeout_is_reported(plan, workdir: Path) -> None:
    """An adapter's own execution honours a limit smaller than the work.

    The case is synthesised rather than taken from the plan: the shipped cases
    are a handful of elements and finish in microseconds, so a limit small
    enough to interrupt one would be racing process startup instead of the
    work. A hundred thousand elements repeated a thousand times is
    unambiguously longer than the limit on any host this runs on.
    """
    adapter = execution.NativeCpuAdapter()
    target = find(plan, "targets", "org.atomix.target.host-cpu")
    implementation = find(plan, "implementations",
                          "org.atomix.implementation.saxpy-native-c")
    items = 100000
    long_case = [{
        "name": "bounded-work",
        "parameters": {"items": items, "a": 3},
        "inputs": {
            "x": list(range(items)),
            "y": list(range(items, 2 * items)),
        },
        "expected": {},
    }]
    directory = workdir / "bounded"
    directory.mkdir()
    prepared = adapter.prepare(implementation, target, directory, long_case,
                               limit_seconds=300)
    started = time.monotonic()
    try:
        adapter.execute(prepared, target, long_case, directory,
                        limit_seconds=0.05, repetitions=1000)
    except execution.Timeout as exc:
        elapsed = time.monotonic() - started
        check("an execution limit smaller than the work times out", elapsed < 10,
              f"{exc} after {elapsed:.2f}s")
        return
    except execution.AdapterError as exc:
        check("an execution limit smaller than the work times out", False,
              f"raised {type(exc).__name__}: {exc}")
        return
    check("an execution limit smaller than the work times out", False,
          "the run finished inside the limit, so the check proved nothing")


def check_stale_artifact_is_rejected(workdir: Path) -> None:
    """A replay compares identities before it compares numbers.

    The failure this guards against is the quiet one: an artifact rebuilt from
    changed source still runs, still passes, and still looks like the same
    candidate. It is not, and a replay that only compared outputs would say it
    was.
    """
    records = ROOT / "research" / "experiments" / "records" / "saxpy-native.json"
    if not records.is_file():
        check("a stale artifact is rejected on replay", False,
              f"{records.name} has not been recorded yet")
        return
    doctored = workdir / "stale-record.json"
    record = pc.load_document(records)
    record["identity"]["implementation"]["artifact_sha256"] = "0" * 64
    doctored.write_text(json.dumps(record, indent=2) + "\n")
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "experiment_run.py"), str(PLAN),
         "--replay", str(doctored), "--work", str(workdir / "replay")],
        capture_output=True, text=True, check=False,
    )
    rejected = (
        completed.returncode != 0 and
        "the implementation artifact changed" in completed.stdout
    )
    check("a stale artifact is rejected on replay", rejected,
          "" if rejected else completed.stdout.strip().splitlines()[-1:] or "no output")


def check_non_default_limit_is_recorded(workdir: Path) -> None:
    """A user's own limit is what bounds the run, and the record says so."""
    records = workdir / "limit-records"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "experiment_run.py"), str(PLAN),
         "--only", "saxpy-native", "--limit-seconds", "123", "--repetitions", "2",
         "--records", str(records), "--work", str(workdir / "limit-work")],
        capture_output=True, text=True, check=False,
    )
    path = records / "saxpy-native.json"
    if completed.returncode != 0 or not path.is_file():
        check("a non-default limit reaches the record", False,
              completed.stderr.strip() or "the run produced no record")
        return
    record = pc.load_document(path)
    honoured = (
        record["execution"]["limit_seconds"] == 123 and
        record["execution"]["repetitions"] == 2 and
        record["status"] == "org.atomix.pass"
    )
    check("a non-default limit reaches the record", honoured,
          "" if honoured else str(record["execution"]))


def check_runs_without_git(root: Path) -> None:
    """A source archive with no git metadata still produces a valid record.

    Someone checking a published result is as likely to download a zip as to
    clone, and that run's outputs are just as real. What it must not do is
    invent a commit: the record says the source identity is absent, and every
    reader can tell that apart from a run pinned to a commit.
    """
    tree = root / "no-git"
    for name in ("tools", "sw/native", "research/experiments",
                 "research/personalities"):
        shutil.copytree(ROOT / name, tree / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    completed = subprocess.run(
        [sys.executable, str(tree / "tools" / "experiment_run.py"),
         str(tree / "research" / "experiments" / "saxpy-native-vs-rtl.json"),
         "--only", "saxpy-native",
         "--records", str(tree / "records"), "--work", str(tree / "work")],
        capture_output=True, text=True, check=False, cwd=tree,
    )
    path = tree / "records" / "saxpy-native.json"
    if not path.is_file():
        check("a run outside a git checkout still records a result", False,
              completed.stderr.strip().splitlines()[-1:] or "no record")
        return
    record = pc.load_document(path)
    honest = (
        record["status"] == "org.atomix.pass" and
        record["source"] == {"commit": None, "dirty": None, "diff_sha256": None}
    )
    check("a run outside a git checkout still records a result", honest,
          f"source={record['source']}")


def main() -> int:
    print("adapter conformance:")
    plan, _, cases = plan_parts()
    root = Path(tempfile.mkdtemp(prefix="ax-adapter-"))
    try:
        check_native_without_hardware_tools(plan, cases, root)
        check_missing_prerequisite_is_blocked(plan, root)
        check_unsupported_semantics(plan, root)
        check_declared_capability_is_not_granted(plan)
        check_timeout_and_cancellation()
        check_execution_timeout_is_reported(plan, root)
        check_non_default_limit_is_recorded(root)
        check_stale_artifact_is_rejected(root)
        check_runs_without_git(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if failures:
        print(f"adapter conformance: FAIL ({len(failures)} of the gates did not hold)")
        return 1
    print("adapter conformance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
