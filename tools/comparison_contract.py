#!/usr/bin/env python3
"""Validate atomiX cross-implementation plans and evidence records."""

from __future__ import annotations

import argparse
import copy
import re
import sys
from pathlib import Path
from typing import Any

import personality_contract as pc


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "research" / "comparisons"
DEFAULT_PERSONALITIES = ROOT / "research" / "personalities"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")

# These are the minimum R2 matrix, not a closed metric registry. A plan may add
# namespaced metrics, but it cannot omit the dimensions the research question
# promised to compare.
BASE_METRICS = {
    "org.atomix.metric.switch-latency": "org.atomix.unit.nanosecond",
    "org.atomix.metric.configuration-transfer-latency": "org.atomix.unit.nanosecond",
    "org.atomix.metric.execute-cycles": "org.atomix.unit.cycle",
    "org.atomix.metric.total-cycles": "org.atomix.unit.cycle",
    "org.atomix.metric.work-items": "org.atomix.unit.item",
    "org.atomix.metric.clock-frequency": "org.atomix.unit.hertz",
    "org.atomix.metric.configuration-bytes": "org.atomix.unit.byte",
    "org.atomix.metric.lut-used": "org.atomix.unit.count",
    "org.atomix.metric.lut-available": "org.atomix.unit.count",
    "org.atomix.metric.flip-flop-used": "org.atomix.unit.count",
    "org.atomix.metric.flip-flop-available": "org.atomix.unit.count",
    "org.atomix.metric.block-ram-bits-used": "org.atomix.unit.bit",
    "org.atomix.metric.block-ram-bits-available": "org.atomix.unit.bit",
    "org.atomix.metric.dsp-used": "org.atomix.unit.count",
    "org.atomix.metric.dsp-available": "org.atomix.unit.count",
    "org.atomix.metric.maximum-frequency": "org.atomix.unit.hertz",
    "org.atomix.metric.total-energy": "org.atomix.unit.joule",
}


def positive_revision(path: Path, value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise pc.error(path, f"{name} must be a positive integer")
    return value


def document_reference(path: Path, value: Any, name: str) -> tuple[str, int]:
    reference = pc.object_value(path, value, name)
    pc.exact_keys(path, reference, name, {"id", "revision"})
    identity = pc.namespaced(path, reference["id"], f"{name}.id")
    revision = positive_revision(path, reference["revision"], f"{name}.revision")
    return identity, revision


def validate_candidate(path: Path, value: Any, index: int) -> dict[str, Any]:
    name = f"candidates[{index}]"
    candidate = pc.object_value(path, value, name)
    pc.exact_keys(
        path, candidate, name,
        {
            "id", "implementation_class", "personality", "workload",
            "work", "selector",
        },
    )
    pc.namespaced(path, candidate["id"], f"{name}.id")
    pc.namespaced(
        path, candidate["implementation_class"], f"{name}.implementation_class"
    )
    document_reference(path, candidate["personality"], f"{name}.personality")

    workload = pc.object_value(path, candidate["workload"], f"{name}.workload")
    pc.exact_keys(
        path, workload, f"{name}.workload", {"id", "revision", "parameters"}
    )
    pc.namespaced(path, workload["id"], f"{name}.workload.id")
    positive_revision(path, workload["revision"], f"{name}.workload.revision")
    pc.validate_parameter_values(
        path, workload["parameters"], f"{name}.workload.parameters"
    )

    work = pc.object_value(path, candidate["work"], f"{name}.work")
    pc.exact_keys(path, work, f"{name}.work", {"unit", "count"})
    pc.namespaced(path, work["unit"], f"{name}.work.unit")
    if not isinstance(work["count"], int) or isinstance(work["count"], bool) \
            or work["count"] < 1:
        raise pc.error(path, f"{name}.work.count must be a positive integer")

    selector = pc.object_value(path, candidate["selector"], f"{name}.selector")
    pc.exact_keys(path, selector, f"{name}.selector", {"kind", "value"})
    pc.namespaced(path, selector["kind"], f"{name}.selector.kind")
    # selector.value is intentionally opaque JSON and belongs to selector.kind.
    return candidate


def validate_plan(path: Path, document: dict[str, Any]) -> None:
    required = {
        "schema", "kind", "id", "revision", "summary", "candidates",
        "metrics", "policy", "extensions",
    }
    pc.exact_keys(path, document, "comparison plan", required)
    if document["kind"] != "comparison-plan":
        raise pc.error(path, "kind must be 'comparison-plan'")
    pc.common(path, document, "org.atomix.comparison-plan")

    candidates = pc.list_value(path, document["candidates"], "candidates")
    if not candidates:
        raise pc.error(path, "candidates must not be empty")
    candidate_ids = []
    for index, value in enumerate(candidates):
        candidate = validate_candidate(path, value, index)
        candidate_ids.append(candidate["id"])
    if len(candidate_ids) != len(set(candidate_ids)):
        raise pc.error(path, "candidate IDs must be unique")

    metrics = pc.list_value(path, document["metrics"], "metrics")
    metric_units: dict[str, str] = {}
    for index, metric_value in enumerate(metrics):
        name = f"metrics[{index}]"
        metric = pc.object_value(path, metric_value, name)
        pc.exact_keys(
            path, metric, name, {"id", "unit", "direction", "requirement"}
        )
        metric_id = pc.namespaced(path, metric["id"], f"{name}.id")
        if metric_id in metric_units:
            raise pc.error(path, f"duplicate metric {metric_id!r}")
        metric_units[metric_id] = pc.namespaced(path, metric["unit"], f"{name}.unit")
        pc.namespaced(path, metric["direction"], f"{name}.direction")
        pc.namespaced(path, metric["requirement"], f"{name}.requirement")
    missing = BASE_METRICS.keys() - metric_units.keys()
    if missing:
        raise pc.error(path, f"comparison plan is missing base metrics {sorted(missing)!r}")
    for metric_id, expected_unit in BASE_METRICS.items():
        if metric_units[metric_id] != expected_unit:
            raise pc.error(
                path, f"{metric_id} must use unit {expected_unit}, not "
                f"{metric_units[metric_id]}"
            )

    policy = pc.object_value(path, document["policy"], "policy")
    pc.exact_keys(
        path, policy, "policy",
        {"correctness", "missing_measurement", "ranking", "cross_device_resources"},
    )
    for name, value in policy.items():
        pc.namespaced(path, value, f"policy.{name}")


def validate_source(path: Path, source: Any, template: bool) -> None:
    value = pc.object_value(path, source, "source")
    pc.exact_keys(path, value, "source", {"commit", "dirty", "diff_sha256"})
    if template:
        if any(item is not None for item in value.values()):
            raise pc.error(path, "a template source identity must be null")
        return
    if not isinstance(value["commit"], str) or not GIT_COMMIT.fullmatch(value["commit"]):
        raise pc.error(path, "source.commit must be a 40- or 64-digit lowercase hash")
    if not isinstance(value["dirty"], bool):
        raise pc.error(path, "source.dirty must be boolean")
    if value["diff_sha256"] is not None and (
        not isinstance(value["diff_sha256"], str) or
        not SHA256.fullmatch(value["diff_sha256"])
    ):
        raise pc.error(path, "source.diff_sha256 must be null or lowercase SHA-256")
    if value["dirty"] and value["diff_sha256"] is None:
        raise pc.error(path, "a dirty source requires diff_sha256")


def validate_environment(path: Path, environment: Any, template: bool) -> None:
    value = pc.object_value(path, environment, "environment")
    pc.exact_keys(
        path, value, "environment",
        {"evidence_level", "timestamp_utc", "board", "device", "tools"},
    )
    level = pc.namespaced(path, value["evidence_level"], "environment.evidence_level")
    if template:
        if level != "org.atomix.not-run" or value["timestamp_utc"] is not None:
            raise pc.error(path, "a template environment must be not-run and untimestamped")
    else:
        if level == "org.atomix.not-run":
            raise pc.error(path, "an observation evidence level cannot be not-run")
        if not isinstance(value["timestamp_utc"], str) or not value["timestamp_utc"]:
            raise pc.error(path, "an observation requires timestamp_utc")
    for name in ("board", "device"):
        if value[name] is not None and not isinstance(value[name], str):
            raise pc.error(path, f"environment.{name} must be null or a string")
    tools = pc.object_value(path, value["tools"], "environment.tools")
    if not all(isinstance(name, str) and isinstance(version, str)
               for name, version in tools.items()):
        raise pc.error(path, "environment.tools must map strings to strings")


def validate_correctness(path: Path, correctness: Any, template: bool) -> None:
    value = pc.object_value(path, correctness, "correctness")
    pc.exact_keys(
        path, value, "correctness",
        {"status", "oracle_cases", "output_sha256", "method"},
    )
    status = pc.namespaced(path, value["status"], "correctness.status")
    cases = pc.nonnegative_int(path, value["oracle_cases"], "correctness.oracle_cases")
    digest = value["output_sha256"]
    if digest is not None and (not isinstance(digest, str) or not SHA256.fullmatch(digest)):
        raise pc.error(path, "correctness.output_sha256 must be null or lowercase SHA-256")
    if not isinstance(value["method"], str) or not value["method"].strip():
        raise pc.error(path, "correctness.method must be non-empty")
    if template:
        if status != "org.atomix.not-run" or cases != 0 or digest is not None:
            raise pc.error(path, "a template correctness result must be not-run and empty")
    else:
        if status not in {"org.atomix.pass", "org.atomix.fail"}:
            raise pc.error(path, "an observation correctness status must be pass or fail")
        if status == "org.atomix.pass" and (cases < 1 or digest is None):
            raise pc.error(path, "a passing observation needs cases and output SHA-256")


def validate_transition(path: Path, transition: Any, template: bool) -> None:
    value = pc.object_value(path, transition, "transition")
    pc.exact_keys(
        path, value, "transition",
        {"from", "mechanism", "retains_management_shell"},
    )
    mechanism = pc.namespaced(path, value["mechanism"], "transition.mechanism")
    if template:
        if value["from"] is not None or mechanism != "org.atomix.not-run" or \
                value["retains_management_shell"] is not None:
            raise pc.error(path, "a template transition must be empty and not-run")
        return
    pc.namespaced(path, value["from"], "transition.from")
    if mechanism == "org.atomix.not-run":
        raise pc.error(path, "an observation transition mechanism cannot be not-run")
    if not isinstance(value["retains_management_shell"], bool):
        raise pc.error(path, "transition.retains_management_shell must be boolean")


def validate_measurements(path: Path, measurements: Any) -> None:
    values = pc.object_value(path, measurements, "measurements")
    for metric_id, measurement_value in values.items():
        pc.namespaced(path, metric_id, "measurement key")
        name = f"measurements.{metric_id}"
        measurement = pc.object_value(path, measurement_value, name)
        pc.exact_keys(path, measurement, name, {"value", "unit", "status", "method"})
        pc.namespaced(path, measurement["unit"], f"{name}.unit")
        status = pc.namespaced(path, measurement["status"], f"{name}.status")
        numeric = measurement["value"]
        if status == "org.atomix.unavailable":
            if numeric is not None:
                raise pc.error(path, f"{name} unavailable value must be null")
        elif not isinstance(numeric, (int, float)) or isinstance(numeric, bool) or numeric < 0:
            raise pc.error(path, f"{name} available value must be a non-negative number")
        if not isinstance(measurement["method"], str) or not measurement["method"].strip():
            raise pc.error(path, f"{name}.method must be non-empty")


def validate_evidence(path: Path, document: dict[str, Any]) -> None:
    required = {
        "schema", "kind", "id", "revision", "summary", "claim", "plan",
        "candidate", "transition", "source", "environment", "correctness",
        "measurements", "extensions",
    }
    pc.exact_keys(path, document, "comparison evidence", required)
    if document["kind"] != "comparison-evidence":
        raise pc.error(path, "kind must be 'comparison-evidence'")
    pc.common(path, document, "org.atomix.comparison-evidence")
    claim = pc.namespaced(path, document["claim"], "claim")
    if claim not in {"org.atomix.template", "org.atomix.observation"}:
        raise pc.error(path, "claim must be org.atomix.template or org.atomix.observation")
    template = claim == "org.atomix.template"
    document_reference(path, document["plan"], "plan")
    pc.namespaced(path, document["candidate"], "candidate")
    validate_transition(path, document["transition"], template)
    validate_source(path, document["source"], template)
    validate_environment(path, document["environment"], template)
    validate_correctness(path, document["correctness"], template)
    validate_measurements(path, document["measurements"])


def plan_metric_units(plan: dict[str, Any]) -> dict[str, str]:
    return {metric["id"]: metric["unit"] for metric in plan["metrics"]}


def validate_evidence_against_plan(path: Path, evidence: dict[str, Any],
                                   plan: dict[str, Any]) -> None:
    expected = plan_metric_units(plan)
    actual = evidence["measurements"]
    if actual.keys() != expected.keys():
        missing = expected.keys() - actual.keys()
        extra = actual.keys() - expected.keys()
        raise pc.error(path, f"measurement set differs from plan; missing={sorted(missing)}, extra={sorted(extra)}")
    for metric_id, unit in expected.items():
        if actual[metric_id]["unit"] != unit:
            raise pc.error(path, f"{metric_id} unit does not match the plan")

    candidates = {candidate["id"]: candidate for candidate in plan["candidates"]}
    if evidence["candidate"] not in candidates:
        raise pc.error(path, f"unknown plan candidate {evidence['candidate']!r}")
    candidate = candidates[evidence["candidate"]]
    work_items = actual["org.atomix.metric.work-items"]
    if work_items["value"] is not None and work_items["value"] != candidate["work"]["count"]:
        raise pc.error(path, "work-items does not match the candidate's logical work count")

    execute = actual["org.atomix.metric.execute-cycles"]["value"]
    total = actual["org.atomix.metric.total-cycles"]["value"]
    if execute is not None and total is not None and total < execute:
        raise pc.error(path, "total-cycles cannot be less than execute-cycles")
    for used_id, available_id in (
        ("org.atomix.metric.lut-used", "org.atomix.metric.lut-available"),
        ("org.atomix.metric.flip-flop-used", "org.atomix.metric.flip-flop-available"),
        ("org.atomix.metric.block-ram-bits-used", "org.atomix.metric.block-ram-bits-available"),
        ("org.atomix.metric.dsp-used", "org.atomix.metric.dsp-available"),
    ):
        used, available = actual[used_id]["value"], actual[available_id]["value"]
        if used is not None and available is not None and used > available:
            raise pc.error(path, f"{used_id} cannot exceed {available_id}")


def personality_documents(root: Path) -> dict[tuple[str, int], dict[str, Any]]:
    result = {}
    for path in pc.collect([root]):
        document = pc.load_document(path)
        pc.validate_document(path, document)
        result[(document["id"], document["revision"])] = document
    return result


def validate_plan_references(path: Path, plan: dict[str, Any],
                             documents: dict[tuple[str, int], dict[str, Any]]) -> None:
    for candidate in plan["candidates"]:
        personality_id = (
            candidate["personality"]["id"], candidate["personality"]["revision"]
        )
        workload_id = (candidate["workload"]["id"], candidate["workload"]["revision"])
        personality = documents.get(personality_id)
        workload = documents.get(workload_id)
        if personality is None or personality["kind"] != "personality":
            raise pc.error(path, f"unknown personality {personality_id!r}")
        if workload is None or workload["kind"] != "workload":
            raise pc.error(path, f"unknown workload {workload_id!r}")
        bound = {
            (item["id"], item["revision"])
            for item in personality["workload_bindings"]
        }
        if workload_id not in bound:
            raise pc.error(path, f"{personality_id!r} does not bind {workload_id!r}")
        unknown = candidate["workload"]["parameters"].keys() - workload["parameters"].keys()
        if unknown:
            raise pc.error(path, f"candidate has unknown workload parameters {sorted(unknown)!r}")


def is_rankable(evidence: dict[str, Any]) -> bool:
    return (
        evidence["claim"] == "org.atomix.observation" and
        evidence["correctness"]["status"] == "org.atomix.pass"
    )


def check(paths: list[Path], personalities: Path) -> int:
    files = pc.collect(paths)
    if not files:
        raise pc.ContractError("no comparison JSON documents found")
    plans: dict[tuple[str, int], tuple[Path, dict[str, Any]]] = {}
    evidence_records: list[tuple[Path, dict[str, Any]]] = []
    identities: dict[tuple[str, int], Path] = {}
    for path in files:
        document = pc.load_document(path)
        schema = pc.object_value(path, document.get("schema"), "schema").get("id")
        if schema == "org.atomix.comparison-plan":
            validate_plan(path, document)
            plans[(document["id"], document["revision"])] = (path, document)
        elif schema == "org.atomix.comparison-evidence":
            validate_evidence(path, document)
            evidence_records.append((path, document))
        else:
            raise pc.error(path, f"unsupported comparison schema {schema!r}")
        identity = (document["id"], document["revision"])
        if identity in identities:
            raise pc.error(path, f"duplicate identity also appears in {identities[identity]}")
        identities[identity] = path

    personalities_by_id = personality_documents(personalities)
    for path, plan in plans.values():
        validate_plan_references(path, plan, personalities_by_id)
    observations = templates = 0
    for path, evidence in evidence_records:
        plan_id = (evidence["plan"]["id"], evidence["plan"]["revision"])
        if plan_id not in plans:
            raise pc.error(path, f"unknown comparison plan {plan_id!r}")
        validate_evidence_against_plan(path, evidence, plans[plan_id][1])
        if evidence["claim"] == "org.atomix.template":
            templates += 1
        else:
            observations += 1
    print(
        f"comparison contract: PASS ({len(plans)} plan, {templates} template, "
        f"{observations} observations)"
    )
    return 0


def self_test() -> int:
    plan_path = DEFAULT_ROOT / "r2-morph-vs-hard.json"
    evidence_path = DEFAULT_ROOT / "evidence-template.json"
    plan = pc.load_document(plan_path)
    evidence = pc.load_document(evidence_path)
    validate_plan(plan_path, plan)
    validate_evidence(evidence_path, evidence)
    validate_evidence_against_plan(evidence_path, evidence, plan)

    open_plan = copy.deepcopy(plan)
    external = copy.deepcopy(open_plan["candidates"][0])
    external["id"] = "dev.example.candidate.wavefront-tree"
    external["implementation_class"] = "dev.example.wavefront-tree"
    external["selector"] = {
        "kind": "dev.example.remote-compiler",
        "value": ["arbitrary", {"topology": [2, 3]}],
    }
    open_plan["candidates"].append(external)
    validate_plan(Path("<open-candidate-self-test>"), open_plan)

    zero = copy.deepcopy(evidence)
    zero["measurements"]["org.atomix.metric.dsp-used"] = {
        "value": 0,
        "unit": "org.atomix.unit.count",
        "status": "org.atomix.measured",
        "method": "post-route report",
    }
    validate_evidence(Path("<zero-self-test>"), zero)
    validate_evidence_against_plan(Path("<zero-self-test>"), zero, plan)

    bad_null = copy.deepcopy(zero)
    bad_null["measurements"]["org.atomix.metric.dsp-used"]["value"] = None
    try:
        validate_evidence(Path("<missing-self-test>"), bad_null)
    except pc.ContractError:
        pass
    else:
        raise pc.ContractError("self-test accepted measured null as zero")

    bad_unit = copy.deepcopy(evidence)
    bad_unit["measurements"]["org.atomix.metric.execute-cycles"]["unit"] = \
        "org.atomix.unit.nanosecond"
    try:
        validate_evidence_against_plan(Path("<unit-self-test>"), bad_unit, plan)
    except pc.ContractError:
        pass
    else:
        raise pc.ContractError("self-test accepted a metric with the wrong unit")
    if is_rankable(evidence):
        raise pc.ContractError("self-test ranked a template without passing correctness")
    print("comparison contract: SELF-TEST PASS (open candidates, gates, null semantics)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check", help="validate plans and evidence")
    check_parser.add_argument("paths", nargs="*", type=Path, default=[DEFAULT_ROOT])
    check_parser.add_argument(
        "--personality-root", type=Path, default=DEFAULT_PERSONALITIES,
        help="personality/workload documents referenced by the plans",
    )
    subparsers.add_parser("self-test", help="exercise openness and evidence gates")
    args = parser.parse_args()
    try:
        if args.command == "self-test":
            return self_test()
        return check(args.paths, args.personality_root)
    except pc.ContractError as exc:
        print(f"comparison contract: FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
