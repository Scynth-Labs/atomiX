#!/usr/bin/env python3
"""Prove what a bounded, resumable sweep must do when things go wrong.

A sweep that only works when every candidate is valid, every tool is present,
and nobody presses Ctrl-C is not a sweep; it is a script that happened to
finish.  These checks exercise the parts that decide whether its records can
be trusted afterwards:

- an invalid parameter combination is refused before anything is built, and
  says which limit it broke;
- an interrupted run leaves a valid state file, and resuming it keeps the
  outcomes it already had instead of quietly redoing them;
- an evaluation bound leaves the untried candidates recorded as not-run rather
  than absent, because "this lost" and "this was never tried" are different
  results;
- a result is reused only while its inputs are identical, and a changed
  compiler option is enough to make it stale;
- a wrong answer is recorded in full and is not rankable.

Run with `make experiment-sweep-check`.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import experiment_contract as ec
import personality_contract as pc

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "research" / "experiments" / "saxpy-native-vs-rtl.json"
RUNNER = ROOT / "tools" / "experiment_run.py"

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f": {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def run(plan: Path, records: Path, work: Path, *arguments: str,
        interrupt_after: float | None = None) -> subprocess.CompletedProcess:
    command = [sys.executable, str(RUNNER), str(plan), "--records", str(records),
               "--work", str(work), *arguments]
    if interrupt_after is None:
        return subprocess.run(command, capture_output=True, text=True, check=False)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    time.sleep(interrupt_after)
    os.killpg(os.getpgid(process.pid), signal.SIGINT)
    stdout, stderr = process.communicate(timeout=300)
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def variant(directory: Path, name: str, mutate) -> Path:
    plan = pc.load_document(PLAN)
    mutate(plan)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(plan, indent=2) + "\n")
    ec.validate_plan(path, plan)
    return path


def record_for(records: Path, name: str) -> dict[str, Any] | None:
    path = records / f"{name}.json"
    return pc.load_document(path) if path.is_file() else None


STATE = "run-state-saxpy-native-vs-rtl.json"


def state_of(records: Path) -> dict[str, Any]:
    return pc.load_document(records / STATE)


def check_bound_is_declared() -> None:
    """A plan cannot declare a sweep larger than it is willing to spend."""
    plan = pc.load_document(PLAN)
    plan["budget"]["max_candidates"] = 2
    try:
        ec.validate_plan(Path("<bound-check>"), plan)
    except pc.ContractError as exc:
        check("a sweep wider than the budget is refused", True, str(exc))
        return
    check("a sweep wider than the budget is refused", False, "accepted")


def check_invalid_combination(root: Path) -> None:
    """An out-of-range point is refused before its model is built."""
    directory = root / "invalid"
    directory.mkdir()

    def widen(plan):
        for candidate in plan["candidates"]:
            if "sweep" in candidate:
                candidate["sweep"]["target_parameters"]["lanes"] = [8, 512]
        plan["budget"]["max_candidates"] = 3

    plan = variant(directory, "invalid-lanes", widen)
    records, work = directory / "records", directory / "work"
    completed = run(plan, records, work, "--only", "saxpy-simt-rtl-lanes-512")
    record = record_for(records, "saxpy-simt-rtl-lanes-512")
    if record is None:
        check("an out-of-range sweep point is blocked", False,
              completed.stderr.strip() or "no record was written")
        return
    reason = record["extensions"].get("org.atomix.blocked-reason", {})
    blocked = (
        record["status"] == "org.atomix.blocked" and
        reason.get("kind") == "unsupported" and
        "maximum" in reason.get("detail", "")
    )
    check("an out-of-range sweep point is blocked", blocked,
          reason.get("detail", record["status"]))
    built = list(work.rglob("tb_role_saxpy"))
    check("the blocked point was refused before it was built", not built,
          f"found {built}" if built else "")


def check_interrupt_and_resume(root: Path) -> None:
    """Ctrl-C leaves a usable run, and resuming keeps what it already had."""
    directory = root / "resume"
    directory.mkdir()
    records, work = directory / "records", directory / "work"

    interrupted = run(PLAN, records, work, interrupt_after=3.0)
    if not (records / STATE).is_file():
        check("an interrupted run leaves a valid state file", False,
              "no state file was written")
        return
    state = state_of(records)
    try:
        ec.validate_run_state(records / STATE, state)
        valid = True
        detail = f"exit {interrupted.returncode}"
    except pc.ContractError as exc:
        valid, detail = False, str(exc)
    check("an interrupted run leaves a valid state file", valid, detail)

    finished = {
        name: entry for name, entry in state["candidates"].items()
        if entry["disposition"] != "org.atomix.not-attempted"
    }
    check("the interrupted run kept the outcomes it had reached",
          bool(finished), f"{len(finished)} candidate(s) settled before the signal")

    resumed = run(PLAN, records, work, "--resume")
    after = state_of(records)
    kept = all(
        after["candidates"][name]["timestamp_utc"] == entry["timestamp_utc"]
        for name, entry in finished.items()
    )
    check("resuming did not re-attempt a settled candidate", kept)
    complete = all(
        entry["disposition"] != "org.atomix.not-attempted"
        for entry in after["candidates"].values()
    )
    check("resuming finished the candidates that were never tried", complete,
          "" if complete else resumed.stdout.strip().splitlines()[-1:] or "")


def check_evaluation_bound(root: Path) -> None:
    """An evaluation limit records what it did not try."""
    directory = root / "bounded"
    directory.mkdir()
    records, work = directory / "records", directory / "work"
    run(PLAN, records, work, "--max-candidates", "2")
    state = state_of(records)
    not_attempted = [
        name for name, entry in state["candidates"].items()
        if entry["disposition"] == "org.atomix.not-attempted"
    ]
    attempted = [
        name for name, entry in state["candidates"].items()
        if entry["disposition"] == "org.atomix.attempted"
    ]
    check("an evaluation bound stops at its limit", len(attempted) == 2,
          f"attempted {len(attempted)}")
    check("the candidates it never tried are recorded as not-run",
          len(not_attempted) == 3 and all(
              state["candidates"][name]["status"] == "org.atomix.not-run"
              for name in not_attempted
          ), f"{len(not_attempted)} not attempted")


def check_reuse_and_staleness(root: Path) -> None:
    """A result is reused while its inputs hold, and only while they hold."""
    directory = root / "reuse"
    directory.mkdir()
    records, work = directory / "records", directory / "work"
    first = run(PLAN, records, work, "--only", "saxpy-native")
    if "wrote" not in first.stdout:
        check("an unchanged candidate is reused", False,
              first.stderr.strip() or first.stdout.strip())
        return
    again = run(PLAN, records, work, "--only", "saxpy-native")
    check("an unchanged candidate is reused", "reused" in again.stdout,
          again.stdout.strip().splitlines()[0] if again.stdout else "")

    def optimise_less(plan):
        for implementation in plan["implementations"]:
            if implementation["build"]["kind"] == "org.atomix.host-cc":
                flags = implementation["build"]["value"]["flags"]
                implementation["build"]["value"]["flags"] = \
                    ["-O1" if flag == "-O2" else flag for flag in flags]

    # A different repetition count is a different measurement, even though the
    # binary it runs is byte-identical.
    more = run(PLAN, records, work, "--only", "saxpy-native", "--repetitions", "9")
    check("a changed repetition count is not reused",
          "wrote" in more.stdout and "reused" not in more.stdout,
          more.stdout.strip().splitlines()[0] if more.stdout else "")
    same = run(PLAN, records, work, "--only", "saxpy-native", "--repetitions", "9")
    check("repeating that same request is reused", "reused" in same.stdout,
          same.stdout.strip().splitlines()[0] if same.stdout else "")

    changed = variant(directory, "optimise-less", optimise_less)
    stale = run(changed, records, work, "--only", "saxpy-native")
    check("a changed compiler option makes the result stale",
          "wrote" in stale.stdout and "reused" not in stale.stdout,
          stale.stdout.strip().splitlines()[0] if stale.stdout else "")


def check_wrong_answer_is_not_rankable(root: Path) -> None:
    """A wrong result is recorded in full and excluded from ranking.

    The measurements stay in the record on purpose. Deleting them would hide
    why the candidate was attractive enough to try; keeping them while refusing
    to rank it is what makes correctness a gate rather than a tiebreak.
    """
    original = (ROOT / "sw" / "native" / "saxpy_i32.c").read_text()
    broken = original.replace(
        "out[i] = wrap_i32(factor * (uint32_t)x[i] + (uint32_t)y[i]);",
        "out[i] = wrap_i32(factor * (uint32_t)x[i] + (uint32_t)y[i] + 1u);",
    )
    if broken == original:
        check("a wrong answer is recorded and excluded from ranking", False,
              "the native kernel line to break was not found")
        return
    # A plan's build paths resolve against the repository root, so the
    # deliberately wrong source is staged under the ignored build tree rather
    # than referenced from a temporary directory outside it.
    staged = ROOT / "build" / "experiments" / "wrong" / "saxpy_off_by_one.c"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text(broken)

    def point_at_staged(plan):
        for implementation in plan["implementations"]:
            if implementation["build"]["kind"] == "org.atomix.host-cc":
                implementation["build"]["value"]["source"] = str(
                    staged.relative_to(ROOT))

    directory = root / "wrong"
    directory.mkdir()
    plan = variant(directory, "wrong-answer", point_at_staged)
    records, work = directory / "records", directory / "work"
    completed = run(plan, records, work, "--only", "saxpy-native")
    record = record_for(records, "saxpy-native")
    if record is None:
        check("a wrong answer is recorded and excluded from ranking", False,
              completed.stderr.strip() or "no record")
        return
    excluded = (
        record["status"] == "org.atomix.fail" and
        record["correctness"]["status"] == "org.atomix.fail" and
        not ec.is_rankable(record) and
        bool(record["extensions"].get("org.atomix.oracle-mismatches")) and
        any(measurement["status"] == "org.atomix.measured"
            for measurement in record["measurements"].values())
    )
    check("a wrong answer is recorded and excluded from ranking", excluded,
          str(record["extensions"].get("org.atomix.oracle-mismatches", [])[:1]))


def main() -> int:
    print("experiment sweep:")
    root = Path(tempfile.mkdtemp(prefix="ax-sweep-"))
    try:
        check_bound_is_declared()
        check_invalid_combination(root)
        check_evaluation_bound(root)
        check_interrupt_and_resume(root)
        check_reuse_and_staleness(root)
        check_wrong_answer_is_not_rankable(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(ROOT / "build" / "experiments" / "wrong", ignore_errors=True)
    if failures:
        print(f"experiment sweep: FAIL ({len(failures)} of the gates did not hold)")
        return 1
    print("experiment sweep: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
