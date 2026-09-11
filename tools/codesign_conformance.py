#!/usr/bin/env python3
"""AX-11 conformance: controlled factors, identity, reuse, and conclusions."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import experiment_contract as ec
import experiment_report as er
import personality_contract as pc


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "research/experiments/saxpy-software-hardware-codesign.json"
RUNNER = ROOT / "tools/experiment_run.py"
failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f": {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def run(plan: Path, records: Path, work: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), str(plan), "--records", str(records),
         "--work", str(work), *extra],
        cwd=ROOT, capture_output=True, text=True, timeout=1800, check=False,
    )


def rejected(plan: dict, needle: str) -> bool:
    try:
        ec.validate_plan(PLAN, plan)
    except pc.ContractError as exc:
        return needle in str(exc)
    return False


def main() -> int:
    plan = pc.load_document(PLAN)
    ec.validate_plan(PLAN, plan)
    expanded = ec.plan_candidates(plan)
    check("the plan declares six bounded candidates", len(expanded) == 6)

    held_drift = copy.deepcopy(plan)
    held_drift["implementations"][1]["build"]["value"]["algorithm"] = \
        "org.atomix.algorithm.saxpy-different"
    check("a control that changes a held factor is rejected",
          rejected(held_drift, "claims to hold"))

    incomplete = copy.deepcopy(plan)
    incomplete["extensions"]["org.atomix.codesign"]["controls"][1]["candidates"].pop()
    check("an incomplete factorial control is rejected",
          rejected(incomplete, "confounded or incomplete"))

    with tempfile.TemporaryDirectory(prefix="atomix-codesign-") as raw:
        scratch = Path(raw)
        records = scratch / "records"
        first = run(PLAN, records, scratch / "work", "--no-reuse")
        check("all compiler and algorithm-by-machine candidates execute",
              first.returncode == 0,
              "" if first.returncode == 0 else (first.stderr or first.stdout)[-500:])
        paths = sorted(records.glob("codesign-*.json"))
        observations = {json.loads(path.read_text())["candidate"]: json.loads(path.read_text())
                        for path in paths}
        check("all six candidates retain passing oracle records",
              len(observations) == 6 and
              all(record["status"] == "org.atomix.pass" for record in observations.values()))

        o0 = observations["org.atomix.candidate.codesign-native-o0"]
        o2 = observations["org.atomix.candidate.codesign-native-o2"]
        check("compiler configurations produce distinct build identities",
              o0["identity"]["implementation"]["build_sha256"] !=
              o2["identity"]["implementation"]["build_sha256"])
        check("compiler configurations produce distinct executables",
              o0["identity"]["implementation"]["artifact_sha256"] !=
              o2["identity"]["implementation"]["artifact_sha256"])
        details = o2["extensions"]["org.atomix.implementation-detail"]
        check("executable, compiler, and runtime-library identities are recorded",
              bool(details.get("compiler_executable")) and
              len(details.get("compiler_sha256", "")) == 64 and
              bool(details.get("runtime_libraries")))

        changed = copy.deepcopy(plan)
        config = changed["implementations"][0]["build"]["value"]["compiler_configuration"]
        config["flags"][0] = "-O1"
        changed_path = scratch / "changed-plan.json"
        changed_path.write_text(json.dumps(changed, indent=2) + "\n")
        rerun = run(changed_path, records, scratch / "changed-work",
                    "--only", "codesign-native-o0")
        state = json.loads((records / "run-state-saxpy-software-hardware-codesign.json").read_text())
        entry = state["candidates"]["org.atomix.candidate.codesign-native-o0"]
        changed_record = json.loads((records / "codesign-native-o0.json").read_text())
        check("a compiler-setting change invalidates reuse",
              rerun.returncode == 0 and entry["disposition"] == "org.atomix.attempted" and
              changed_record["identity"]["implementation"]["build_sha256"] !=
              o0["identity"]["implementation"]["build_sha256"])

        replay_record = records / "codesign-simt-multiply-lanes-4.json"
        replay = run(PLAN, records, scratch / "replay-work", "--replay", str(replay_record))
        check("a hardware/software result replays exactly",
              replay.returncode == 0 and "experiment replay: PASS" in replay.stdout,
              replay.stderr[-300:])

        wrong = copy.deepcopy(observations)
        victim = copy.deepcopy(wrong["org.atomix.candidate.codesign-simt-add-chain-lanes-4"])
        victim["status"] = "org.atomix.fail"
        victim["correctness"]["status"] = "org.atomix.fail"
        victim["extensions"]["org.atomix.oracle-mismatches"] = ["injected mismatch"]
        wrong[victim["candidate"]] = victim
        eligible, excluded = er.eligibility(plan, wrong, {})
        check("a correctness failure is excluded from comparison",
              victim["candidate"] not in eligible and
              any(name == victim["candidate"] and "failed the oracle" in reason
                  for name, reason in excluded))

        def cycles(name: str) -> int:
            return int(observations[name]["measurements"]
                       ["org.atomix.metric.execute-cycles"]["value"])

        mul1 = cycles("org.atomix.candidate.codesign-simt-multiply-lanes-1")
        mul4 = cycles("org.atomix.candidate.codesign-simt-multiply-lanes-4")
        add1 = cycles("org.atomix.candidate.codesign-simt-add-chain-lanes-1")
        add4 = cycles("org.atomix.candidate.codesign-simt-add-chain-lanes-4")
        check("the controlled result supports an explicit tradeoff conclusion",
              mul4 < mul1 and add4 < add1 and mul1 < add1 and mul4 < add4,
              f"multiply={mul1}/{mul4}, add-chain={add1}/{add4} cycles at 1/4 lanes")

    if failures:
        print(f"codesign conformance: FAIL ({len(failures)} checks)")
        return 1
    print("codesign conformance: PASS (12 checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
