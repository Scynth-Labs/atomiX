#!/usr/bin/env python3
"""Validate and evaluate the Live FPGA L1 reviewed-program selection policy."""

from __future__ import annotations

import argparse
import copy
import hashlib
import re
import struct
import sys
from pathlib import Path
from typing import Any

import personality_contract as pc
import candidate_registry as cr


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "research/live-fpga/policy/l1-reviewed-gpu.json"
DEFAULT_REGISTRY = ROOT / "research/live-fpga/registry/reviewed-gpu.json"
sys.path.insert(0, str(ROOT / "sw/host"))
from gpu_programs import reviewed_fast_switch_programs  # noqa: E402

U32_MAX = (1 << 32) - 1
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def u32(path: Path, value: Any, name: str, positive: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= U32_MAX:
        raise pc.error(path, f"{name} must be a uint32")
    if positive and value == 0:
        raise pc.error(path, f"{name} must be positive")
    return value


def artifact_hash(words: list[int]) -> str:
    payload = b"".join(struct.pack("<I", word & U32_MAX) for word in words)
    return hashlib.sha256(payload).hexdigest()


def document_ref(path: Path, value: Any, name: str) -> tuple[str, int]:
    ref = pc.object_value(path, value, name)
    pc.exact_keys(path, ref, name, {"id", "revision"})
    return pc.namespaced(path, ref["id"], f"{name}.id"), \
        u32(path, ref["revision"], f"{name}.revision", positive=True)


def validate_program(path: Path, value: Any, index: int) -> dict[str, Any]:
    name = f"programs[{index}]"
    program = pc.object_value(path, value, name)
    pc.exact_keys(
        path, program, name,
        {"id", "candidate", "numeric_id", "workload", "role", "artifact", "review"},
    )
    pc.namespaced(path, program["id"], f"{name}.id")
    cr.content_id_value(path, program["candidate"], f"{name}.candidate")
    u32(path, program["numeric_id"], f"{name}.numeric_id", positive=True)
    document_ref(path, program["workload"], f"{name}.workload")
    pc.namespaced(path, program["role"], f"{name}.role")

    artifact = pc.object_value(path, program["artifact"], f"{name}.artifact")
    pc.exact_keys(
        path, artifact, f"{name}.artifact",
        {"format", "words", "sha256", "source"},
    )
    pc.namespaced(path, artifact["format"], f"{name}.artifact.format")
    pc.namespaced(path, artifact["source"], f"{name}.artifact.source")
    words = pc.list_value(path, artifact["words"], f"{name}.artifact.words")
    if not words:
        raise pc.error(path, f"{name}.artifact.words must not be empty")
    for word_index, word in enumerate(words):
        u32(path, word, f"{name}.artifact.words[{word_index}]")
    digest = artifact["sha256"]
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        raise pc.error(path, f"{name}.artifact.sha256 must be lowercase SHA-256")
    if artifact_hash(words) != digest:
        raise pc.error(path, f"{name}.artifact.sha256 does not match its words")

    review = pc.object_value(path, program["review"], f"{name}.review")
    pc.exact_keys(path, review, f"{name}.review", {"status", "oracle", "note"})
    status = pc.namespaced(path, review["status"], f"{name}.review.status")
    if status not in {"org.atomix.approved", "org.atomix.rejected"}:
        raise pc.error(path, f"{name}.review.status is unsupported")
    pc.namespaced(path, review["oracle"], f"{name}.review.oracle")
    if not isinstance(review["note"], str) or not review["note"].strip():
        raise pc.error(path, f"{name}.review.note must be non-empty")
    return program


def validate_document(path: Path, document: dict[str, Any],
                      registry: dict[str, dict[str, Any]] | None = None) -> None:
    required = {
        "schema", "kind", "id", "revision", "summary", "programs",
        "request", "result", "extensions",
    }
    pc.exact_keys(path, document, "L1 policy", required)
    if document["kind"] != "live-l1-policy":
        raise pc.error(path, "kind must be 'live-l1-policy'")
    pc.common(path, document, "org.atomix.live-l1-policy")

    programs = pc.list_value(path, document["programs"], "programs")
    if not programs:
        raise pc.error(path, "programs must not be empty")
    ids: set[str] = set()
    numeric_ids: set[int] = set()
    for index, value in enumerate(programs):
        program = validate_program(path, value, index)
        if program["id"] in ids or program["numeric_id"] in numeric_ids:
            raise pc.error(path, "program IDs and numeric IDs must be unique")
        ids.add(program["id"])
        numeric_ids.add(program["numeric_id"])

    if registry is not None:
        registry_by_logical = {
            candidate["identity"]["id"]: (candidate_id, candidate["identity"])
            for candidate_id, candidate in registry.items()
        }
        for index, program in enumerate(programs):
            name = f"programs[{index}]"
            if program["id"] not in registry_by_logical:
                raise pc.error(path, f"{name}.id is absent from the candidate registry")
            candidate_id, identity = registry_by_logical[program["id"]]
            if program["candidate"] != candidate_id:
                raise pc.error(path, f"{name}.candidate does not resolve to its registry identity")
            if program["numeric_id"] != identity["numeric_id"] or \
                    program["role"] != identity["role"] or \
                    program["workload"] != identity["workload"] or \
                    program["artifact"]["format"] != identity["artifact"]["format"] or \
                    program["artifact"]["sha256"] != identity["artifact"]["sha256"]:
                raise pc.error(path, f"{name} disagrees with its immutable registry identity")

    request = pc.object_value(path, document["request"], "request")
    pc.exact_keys(
        path, request, "request",
        {"workload", "role", "current", "objective_id", "minimum_improvement",
         "fitness_records"},
    )
    document_ref(path, request["workload"], "request.workload")
    pc.namespaced(path, request["role"], "request.role")
    if request["current"] is not None:
        pc.namespaced(path, request["current"], "request.current")
        if request["current"] not in ids:
            raise pc.error(path, "request.current is not allow-listed")
    objective = u32(path, request["objective_id"], "request.objective_id", positive=True)
    u32(path, request["minimum_improvement"], "request.minimum_improvement")
    records = pc.list_value(path, request["fitness_records"], "request.fitness_records")
    seen_records: set[str] = set()
    for index, value in enumerate(records):
        name = f"request.fitness_records[{index}]"
        record = pc.object_value(path, value, name)
        pc.exact_keys(
            path, record, name,
            {"id", "program", "eligible", "objective_id", "fitness",
             "evidence_generation"},
        )
        pc.namespaced(path, record["id"], f"{name}.id")
        program_id = pc.namespaced(path, record["program"], f"{name}.program")
        if program_id not in ids:
            raise pc.error(path, f"{name}.program is not allow-listed")
        if program_id in seen_records:
            raise pc.error(path, f"duplicate fitness record for {program_id}")
        seen_records.add(program_id)
        if not isinstance(record["eligible"], bool):
            raise pc.error(path, f"{name}.eligible must be boolean")
        if u32(path, record["objective_id"], f"{name}.objective_id", True) != objective:
            raise pc.error(path, f"{name} mixes fitness objectives")
        u32(path, record["fitness"], f"{name}.fitness")
        u32(path, record["evidence_generation"], f"{name}.evidence_generation")

    expected = derive(document)
    if pc.object_value(path, document["result"], "result") != expected:
        raise pc.error(path, "result does not exactly match deterministic selection")
    pc.extensions(path, document["extensions"])


def derive(document: dict[str, Any]) -> dict[str, Any]:
    request = document["request"]
    workload_key = (request["workload"]["id"], request["workload"]["revision"])
    programs = {program["id"]: program for program in document["programs"]}
    compatible = {
        identity for identity, program in programs.items()
        if program["review"]["status"] == "org.atomix.approved"
        and program["role"] == request["role"]
        and (program["workload"]["id"], program["workload"]["revision"])
        == workload_key
    }
    scored = [
        record for record in request["fitness_records"]
        if record["program"] in compatible and record["eligible"]
    ]
    current = request["current"]
    if not scored:
        return {
            "action": "org.atomix.no-candidate",
            "current": current,
            "current_candidate": programs[current]["candidate"]
            if current is not None else None,
            "proposed": None,
            "proposed_candidate": None,
            "reason": ["org.atomix.policy.no-eligible-reviewed-program"],
            "current_fitness": None,
            "best_fitness": None,
            "actuation": "org.atomix.not-authorized",
        }

    best = min(scored, key=lambda record: (record["fitness"], record["program"]))
    by_program = {record["program"]: record for record in scored}
    if current == best["program"]:
        action = "org.atomix.hold"
        reasons = ["org.atomix.policy.current-is-best"]
    elif current not in compatible:
        action = "org.atomix.propose"
        reasons = [
            "org.atomix.policy.current-incompatible",
            "org.atomix.policy.reviewed-workload-match",
        ]
    elif current not in by_program:
        action = "org.atomix.propose"
        reasons = ["org.atomix.policy.current-has-no-eligible-fitness"]
    else:
        improvement = by_program[current]["fitness"] - best["fitness"]
        if improvement >= request["minimum_improvement"]:
            action = "org.atomix.propose"
            reasons = ["org.atomix.policy.fitness-improvement"]
        else:
            action = "org.atomix.hold"
            reasons = ["org.atomix.policy.improvement-below-threshold"]
    proposed_program = best["program"] if action == "org.atomix.propose" else current
    return {
        "action": action,
        "current": current,
        "current_candidate": programs[current]["candidate"]
        if current is not None else None,
        "proposed": proposed_program,
        "proposed_candidate": programs[proposed_program]["candidate"]
        if proposed_program is not None else None,
        "reason": reasons,
        "current_fitness": by_program.get(current, {}).get("fitness"),
        "best_fitness": best["fitness"],
        "actuation": "org.atomix.not-authorized",
    }


def check_axhost_sync(path: Path, document: dict[str, Any],
                      registry: dict[str, dict[str, Any]] | None = None) -> None:
    actual = {program["id"]: program for program in reviewed_fast_switch_programs()}
    declared = {
        program["id"]: program for program in document["programs"]
        if program["artifact"]["source"] == "org.atomix.axhost.fast-switch"
    }
    if actual.keys() != declared.keys():
        raise pc.error(path, "axhost reviewed-program IDs differ from the allow-list")
    for identity, runtime in actual.items():
        entry = declared[identity]
        if entry["artifact"]["words"] != runtime["words"] or \
                entry["workload"]["id"] != runtime["workload"] or \
                entry["workload"]["revision"] != runtime["revision"]:
            raise pc.error(path, f"{identity} differs from the axhost runtime program")
        if registry is not None:
            candidate_id = entry["candidate"]
            expected_output = artifact_hash(runtime["expected"])
            matching_evidence = False
            for ref in registry[candidate_id]["records"]["evidence"]:
                evidence_path = ROOT / ref["path"]
                evidence = cr.load_json(evidence_path)
                cr.validate_evidence(evidence_path, evidence)
                for result in evidence["test"]["results"]:
                    if result["candidate"] != candidate_id:
                        continue
                    matching_evidence = True
                    if result["program_sha256"] != artifact_hash(runtime["words"]) or \
                            result["output_sha256"] != expected_output:
                        raise pc.error(
                            path, f"{identity} evidence disagrees with runtime/oracle bytes")
            if not matching_evidence:
                raise pc.error(path, f"{identity} has no matching registry evidence")


def load(path: Path) -> dict[str, Any]:
    return pc.load_document(path)


def check(path: Path, registry_path: Path = DEFAULT_REGISTRY) -> int:
    document = load(path)
    registry = cr.check(registry_path)
    validate_document(path, document, registry)
    check_axhost_sync(path, document, registry)
    print(
        f"live L1 policy: PASS ({len(document['programs'])} reviewed programs, "
        f"action={document['result']['action']})"
    )
    return 0


def self_test() -> int:
    policy = load(DEFAULT_POLICY)
    registry_document = cr.load_json(DEFAULT_REGISTRY)
    registry = cr.validate_registry(DEFAULT_REGISTRY, registry_document)
    validate_document(DEFAULT_POLICY, policy, registry)
    check_axhost_sync(DEFAULT_POLICY, policy, registry)

    faster_wrong_workload = copy.deepcopy(policy)
    faster_wrong_workload["request"]["fitness_records"][1]["fitness"] = 1
    faster_wrong_workload["result"] = derive(faster_wrong_workload)
    validate_document(Path("<wrong-workload>"), faster_wrong_workload, registry)
    if faster_wrong_workload["result"]["proposed"] != \
            "org.atomix.gpu-program.saxpy-i32-v1":
        raise pc.ContractError("wrong-workload program won by fitness")

    failed = copy.deepcopy(policy)
    failed["request"]["fitness_records"][0]["eligible"] = False
    failed["result"] = derive(failed)
    validate_document(Path("<failed-oracle>"), failed, registry)
    if failed["result"]["action"] != "org.atomix.no-candidate":
        raise pc.ContractError("ineligible program was proposed")

    mixed = copy.deepcopy(policy)
    mixed["request"]["fitness_records"][0]["objective_id"] = 2
    mixed["result"] = derive(mixed)
    try:
        validate_document(Path("<mixed-objective>"), mixed, registry)
    except pc.ContractError:
        pass
    else:
        raise pc.ContractError("mixed fitness objectives were accepted")

    tampered = copy.deepcopy(policy)
    tampered["programs"][0]["artifact"]["words"][0] ^= 1
    try:
        validate_document(Path("<tampered-program>"), tampered, registry)
    except pc.ContractError:
        pass
    else:
        raise pc.ContractError("tampered reviewed program was accepted")

    anonymous = copy.deepcopy(policy)
    anonymous["programs"][0]["candidate"] = anonymous["programs"][1]["candidate"]
    anonymous["result"] = derive(anonymous)
    try:
        validate_document(Path("<wrong-candidate-identity>"), anonymous, registry)
    except pc.ContractError:
        pass
    else:
        raise pc.ContractError("program was allowed to borrow another candidate identity")

    print("live L1 policy: SELF-TEST PASS (allow-list, workload, gates, objectives)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check", help="validate an L1 policy record")
    check_parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_POLICY)
    check_parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    subparsers.add_parser("self-test", help="exercise selection safety gates")
    args = parser.parse_args()
    try:
        return self_test() if args.command == "self-test" else \
            check(args.path, args.registry)
    except pc.ContractError as exc:
        print(f"live L1 policy: FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
