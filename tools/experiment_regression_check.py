#!/usr/bin/env python3
"""Run and enforce the deterministic preview experiment regression policy."""
from __future__ import annotations

import copy
import json
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
PLAN_PATH = ROOT / "research" / "experiments" / "same-binary-cores.json"
RUNNER = ROOT / "tools" / "experiment_run.py"
REPORT = ROOT / "tools" / "experiment_report.py"
POLICY_ID = "org.atomix.regression-policy"
CASE_OUTCOMES = "org.atomix.case-outcomes"


class RegressionError(Exception):
    pass


def exact_keys(value: dict[str, Any], name: str, keys: set[str]) -> None:
    actual = set(value)
    if actual != keys:
        raise RegressionError(
            f"{name} fields differ: missing={sorted(keys - actual)}, "
            f"extra={sorted(actual - keys)}")


def positive_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RegressionError(f"{name} must be a positive integer")
    return value


def load_policy(plan: dict[str, Any]) -> dict[str, Any]:
    policy = plan.get("extensions", {}).get(POLICY_ID)
    if not isinstance(policy, dict):
        raise RegressionError(f"{PLAN_PATH}: missing {POLICY_ID}")
    exact_keys(policy, POLICY_ID, {
        "schema", "revision", "summary", "baseline", "candidates",
        "diagnostic_metrics", "change_process", "history",
    })
    schema = policy["schema"]
    if schema != {
        "id": "org.atomix.experiment-regression-policy", "major": 1, "minor": 0,
    }:
        raise RegressionError("unsupported experiment regression policy schema")
    revision = positive_int(policy["revision"], "policy.revision")
    if not isinstance(policy["summary"], str) or not policy["summary"].strip():
        raise RegressionError("policy.summary must be non-empty")
    if not isinstance(policy["change_process"], str) or not policy["change_process"].strip():
        raise RegressionError("policy.change_process must be non-empty")

    baseline = policy["baseline"]
    if not isinstance(baseline, dict):
        raise RegressionError("policy.baseline must be an object")
    exact_keys(baseline, "policy.baseline", {
        "recorded_on", "reason", "workload", "artifact",
    })
    workload = baseline["workload"]
    exact_keys(workload, "policy.baseline.workload", {
        "id", "revision", "case", "outputs", "output_sha256",
    })
    if (workload["id"], workload["revision"]) != (
            plan["workload"]["id"], plan["workload"]["revision"]):
        raise RegressionError("regression baseline names another workload identity")
    if workload["case"] not in plan["workload"]["cases"]:
        raise RegressionError("regression baseline names a case outside the plan")
    artifact = baseline["artifact"]
    exact_keys(artifact, "policy.baseline.artifact", {
        "sha256", "build_sha256", "maximum_bytes",
    })
    positive_int(artifact["maximum_bytes"], "artifact.maximum_bytes")

    candidates = policy["candidates"]
    planned = {item["id"] for item in ec.plan_candidates(plan)}
    if not isinstance(candidates, dict) or set(candidates) != planned:
        raise RegressionError(
            "policy candidates must exactly cover the plan's preview candidates")
    for candidate_id, rule in candidates.items():
        if not isinstance(rule, dict):
            raise RegressionError(f"policy candidate {candidate_id} must be an object")
        exact_keys(rule, f"policy.candidates.{candidate_id}", {
            "implementation", "target", "profile_sha256",
            "maximum_execute_cycles", "maximum_total_cycles",
        })
        positive_int(rule["maximum_execute_cycles"],
                     f"{candidate_id}.maximum_execute_cycles")
        positive_int(rule["maximum_total_cycles"],
                     f"{candidate_id}.maximum_total_cycles")

    if policy["diagnostic_metrics"] != [
            "org.atomix.metric.simulator-host-elapsed-median"]:
        raise RegressionError(
            "simulator wall time must be the sole diagnostic metric, not a threshold")
    history = policy["history"]
    if not isinstance(history, list) or not history:
        raise RegressionError("policy.history must record every threshold revision")
    revisions = []
    for index, entry in enumerate(history):
        if not isinstance(entry, dict):
            raise RegressionError(f"policy.history[{index}] must be an object")
        exact_keys(entry, f"policy.history[{index}]", {"revision", "date", "reason"})
        revisions.append(positive_int(entry["revision"], f"history[{index}].revision"))
        if not isinstance(entry["reason"], str) or not entry["reason"].strip():
            raise RegressionError(f"policy.history[{index}].reason must be non-empty")
    if revisions != list(range(1, revision + 1)):
        raise RegressionError(
            "policy.history must contain every revision in order through policy.revision")
    return policy


def measured(record: dict[str, Any], metric: str,
             problems: list[str]) -> int | None:
    value = record.get("measurements", {}).get(metric)
    if not isinstance(value, dict) or value.get("status") != "org.atomix.measured":
        problems.append(f"{record['candidate']}: {metric} is not measured")
        return None
    number = value.get("value")
    if not isinstance(number, int) or isinstance(number, bool):
        problems.append(f"{record['candidate']}: {metric} has no integer value")
        return None
    return number


def evaluate(plan: dict[str, Any], policy: dict[str, Any],
             records: dict[str, dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    baseline = policy["baseline"]
    expected_artifact = baseline["artifact"]
    expected_workload = baseline["workload"]
    for candidate_id, rule in policy["candidates"].items():
        record = records.get(candidate_id)
        if record is None:
            problems.append(f"{candidate_id}: no record")
            continue
        if not ec.is_rankable(record):
            problems.append(f"{candidate_id}: is not comparison-eligible")
            continue

        implementation = record["identity"]["implementation"]
        target = record["identity"]["target"]
        identity_problems = []
        expected_identity = {
            "implementation id": (implementation["id"], rule["implementation"]),
            "implementation artifact": (
                implementation["artifact_sha256"], expected_artifact["sha256"]),
            "implementation build inputs": (
                implementation["build_sha256"], expected_artifact["build_sha256"]),
            "target id": (target["id"], rule["target"]),
            "target profile": (target["profile_sha256"], rule["profile_sha256"]),
        }
        for name, (actual, expected) in expected_identity.items():
            if actual != expected:
                identity_problems.append(
                    f"{candidate_id}: stale {name}: {actual} != {expected}")
        if identity_problems:
            problems.extend(identity_problems)
            # Thresholds are meaningful only for the declared workload,
            # payload, and profile. Do not compare a stale record's numbers.
            continue

        outcomes = record.get("extensions", {}).get(CASE_OUTCOMES, {})
        case = outcomes.get(expected_workload["case"], {})
        if case.get("outputs") != expected_workload["outputs"]:
            problems.append(
                f"{candidate_id}: wrong oracle output {case.get('outputs')!r}")
        if record["correctness"].get("output_sha256") != \
                expected_workload["output_sha256"]:
            problems.append(f"{candidate_id}: wrong oracle output identity")

        artifact_bytes = measured(
            record, "org.atomix.metric.artifact-bytes", problems)
        execute_cycles = measured(
            record, "org.atomix.metric.execute-cycles", problems)
        total_cycles = measured(
            record, "org.atomix.metric.total-cycles", problems)
        thresholds = (
            ("artifact bytes", artifact_bytes, expected_artifact["maximum_bytes"]),
            ("execute cycles", execute_cycles, rule["maximum_execute_cycles"]),
            ("total cycles", total_cycles, rule["maximum_total_cycles"]),
        )
        for name, actual, maximum in thresholds:
            if actual is not None and actual > maximum:
                problems.append(
                    f"{candidate_id}: {name} threshold breached: {actual} > {maximum}")
    return problems


def load_records(directory: Path, plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = {}
    for candidate in ec.plan_candidates(plan):
        path = directory / f"{candidate['id'].split('.')[-1]}.json"
        if not path.is_file():
            continue
        record = pc.load_document(path)
        ec.validate_record(path, record)
        ec.validate_record_against_plan(path, record, plan)
        records[record["candidate"]] = record
    return records


def require_detected(name: str, problems: list[str], phrase: str) -> None:
    if not any(phrase in problem for problem in problems):
        raise RegressionError(
            f"injected {name} was not detected; problems were {problems!r}")
    print(f"PASS injected {name} detected")


def self_test(plan: dict[str, Any], policy: dict[str, Any],
              records: dict[str, dict[str, Any]]) -> None:
    candidate_id = "org.atomix.candidate.cpu-perf-on-pipeline5"

    wrong = copy.deepcopy(records)
    wrong[candidate_id]["extensions"][CASE_OUTCOMES]["resident-integer-mix"][
        "outputs"]["checksum"] = [3911608134]
    require_detected("wrong result", evaluate(plan, policy, wrong), "wrong oracle output")

    stale = copy.deepcopy(records)
    stale[candidate_id]["identity"]["target"]["profile_sha256"] = "0" * 64
    require_detected("stale input", evaluate(plan, policy, stale), "stale target profile")

    breached = copy.deepcopy(records)
    breached[candidate_id]["measurements"][
        "org.atomix.metric.execute-cycles"]["value"] = \
        policy["candidates"][candidate_id]["maximum_execute_cycles"] + 1
    require_detected(
        "threshold breach", evaluate(plan, policy, breached), "threshold breached")


def main() -> int:
    started = time.monotonic()
    try:
        plan = pc.load_document(PLAN_PATH)
        ec.validate_plan(PLAN_PATH, plan)
        policy = load_policy(plan)
        with tempfile.TemporaryDirectory(prefix="atomix-experiment-regression-") as temporary:
            root = Path(temporary)
            records_path = root / "records"
            command = [
                sys.executable, str(RUNNER), str(PLAN_PATH),
                "--records", str(records_path), "--work", str(root / "work"),
                "--no-reuse",
            ]
            for candidate_id in policy["candidates"]:
                command.extend(["--only", candidate_id])
            run = subprocess.run(
                command, cwd=ROOT, capture_output=True, text=True, check=False)
            if run.returncode != 0:
                raise RegressionError(
                    f"experiment run failed (exit {run.returncode})\n"
                    f"{run.stdout}{run.stderr}")
            records = load_records(records_path, plan)
            problems = evaluate(plan, policy, records)
            if problems:
                raise RegressionError("regression gate failed:\n  - " + "\n  - ".join(problems))

            report = subprocess.run(
                [sys.executable, str(REPORT), "render", str(PLAN_PATH),
                 "--records", str(records_path)],
                cwd=ROOT, capture_output=True, text=True, check=False)
            eligible = f"Eligible for comparison: {len(policy['candidates'])} of " \
                       f"{len(policy['candidates'])} candidates"
            if report.returncode != 0 or eligible not in report.stdout:
                raise RegressionError(
                    f"comparison eligibility failed\n{report.stdout}{report.stderr}")
            print(f"PASS {eligible.lower()}")
            for candidate_id, rule in policy["candidates"].items():
                record = records[candidate_id]
                execute = record["measurements"][
                    "org.atomix.metric.execute-cycles"]["value"]
                total = record["measurements"][
                    "org.atomix.metric.total-cycles"]["value"]
                print(
                    f"PASS {candidate_id.split('.')[-1]}: oracle exact, "
                    f"execute={execute}/{rule['maximum_execute_cycles']}, "
                    f"total={total}/{rule['maximum_total_cycles']}")
            self_test(plan, policy, records)
    except (RegressionError, pc.ContractError, OSError, json.JSONDecodeError) as exc:
        print(f"experiment regression: FAIL: {exc}", file=sys.stderr)
        return 1
    elapsed = time.monotonic() - started
    print(
        f"experiment regression: PASS (policy revision {policy['revision']}, "
        f"stage cost {elapsed:.3f}s; simulator wall time diagnostic only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
