#!/usr/bin/env python3
"""Validate content-addressed Live FPGA candidates and their record chain."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import personality_contract as pc


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "research/live-fpga/registry/reviewed-gpu.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CONTENT_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
U32_MAX = (1 << 32) - 1


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def content_id(value: Any) -> str:
    return f"sha256:{canonical_sha256(value)}"


def document_content_id(document: dict[str, Any]) -> str:
    return content_id({key: value for key, value in document.items()
                       if key != "content_id"})


def sha256_value(path: Path, value: Any, name: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise pc.error(path, f"{name} must be a lowercase SHA-256")
    return value


def content_id_value(path: Path, value: Any, name: str) -> str:
    if not isinstance(value, str) or not CONTENT_ID.fullmatch(value):
        raise pc.error(path, f"{name} must be sha256:<lowercase digest>")
    return value


def u32(path: Path, value: Any, name: str, positive: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= U32_MAX:
        raise pc.error(path, f"{name} must be a uint32")
    if positive and value == 0:
        raise pc.error(path, f"{name} must be positive")
    return value


def repo_path(path: Path, value: Any, name: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise pc.error(path, f"{name} must be a repository-relative path")
    resolved = (ROOT / value).resolve()
    if resolved != ROOT and ROOT not in resolved.parents:
        raise pc.error(path, f"{name} escapes the repository")
    return resolved


def validate_file_identity(path: Path, value: Any, name: str) -> None:
    identity = pc.object_value(path, value, name)
    pc.exact_keys(path, identity, name, {"path", "sha256"})
    target = repo_path(path, identity["path"], f"{name}.path")
    digest = sha256_value(path, identity["sha256"], f"{name}.sha256")
    if not target.is_file():
        raise pc.error(path, f"{name}.path does not exist")
    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise pc.error(path, f"{name}.sha256 does not match {identity['path']}")


def validate_ref(path: Path, value: Any, name: str) -> tuple[str, Path]:
    ref = pc.object_value(path, value, name)
    pc.exact_keys(path, ref, name, {"content_id", "path"})
    identity = content_id_value(path, ref["content_id"], f"{name}.content_id")
    target = repo_path(path, ref["path"], f"{name}.path")
    if not target.is_file():
        raise pc.error(path, f"{name}.path does not exist")
    return identity, target


def validate_tools(path: Path, value: Any, name: str) -> None:
    tools = pc.list_value(path, value, name)
    if not tools:
        raise pc.error(path, f"{name} must not be empty")
    ids: set[str] = set()
    for index, item in enumerate(tools):
        item_name = f"{name}[{index}]"
        tool = pc.object_value(path, item, item_name)
        pc.exact_keys(path, tool, item_name, {"id", "version"})
        identity = pc.namespaced(path, tool["id"], f"{item_name}.id")
        if identity in ids:
            raise pc.error(path, f"{name} contains duplicate tool IDs")
        ids.add(identity)
        if not isinstance(tool["version"], str) or not tool["version"].strip():
            raise pc.error(path, f"{item_name}.version must be non-empty")


def validate_identity(path: Path, value: Any, name: str) -> dict[str, Any]:
    identity = pc.object_value(path, value, name)
    pc.exact_keys(
        path, identity, name,
        {"id", "numeric_id", "role", "workload", "artifact", "build", "lineage"},
    )
    pc.namespaced(path, identity["id"], f"{name}.id")
    u32(path, identity["numeric_id"], f"{name}.numeric_id", positive=True)
    pc.namespaced(path, identity["role"], f"{name}.role")
    workload = pc.object_value(path, identity["workload"], f"{name}.workload")
    pc.exact_keys(path, workload, f"{name}.workload", {"id", "revision"})
    pc.namespaced(path, workload["id"], f"{name}.workload.id")
    u32(path, workload["revision"], f"{name}.workload.revision", positive=True)

    artifact = pc.object_value(path, identity["artifact"], f"{name}.artifact")
    pc.exact_keys(path, artifact, f"{name}.artifact", {"format", "sha256"})
    pc.namespaced(path, artifact["format"], f"{name}.artifact.format")
    sha256_value(path, artifact["sha256"], f"{name}.artifact.sha256")

    build = pc.object_value(path, identity["build"], f"{name}.build")
    pc.exact_keys(path, build, f"{name}.build", {"source", "profile", "tools"})
    validate_file_identity(path, build["source"], f"{name}.build.source")
    validate_file_identity(path, build["profile"], f"{name}.build.profile")
    validate_tools(path, build["tools"], f"{name}.build.tools")

    lineage = pc.object_value(path, identity["lineage"], f"{name}.lineage")
    pc.exact_keys(path, lineage, f"{name}.lineage", {"parent", "mutation"})
    if lineage["parent"] is not None:
        content_id_value(path, lineage["parent"], f"{name}.lineage.parent")
    mutation = pc.object_value(path, lineage["mutation"], f"{name}.lineage.mutation")
    pc.exact_keys(path, mutation, f"{name}.lineage.mutation",
                  {"operator", "seed", "parameters"})
    pc.namespaced(path, mutation["operator"], f"{name}.lineage.mutation.operator")
    if mutation["seed"] is not None:
        u32(path, mutation["seed"], f"{name}.lineage.mutation.seed")
    pc.object_value(path, mutation["parameters"], f"{name}.lineage.mutation.parameters")
    return identity


def validate_registry(path: Path, document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    pc.exact_keys(
        path, document, "candidate registry",
        {"schema", "kind", "id", "revision", "summary", "hash", "candidates",
         "extensions"},
    )
    if document["kind"] != "candidate-registry":
        raise pc.error(path, "kind must be 'candidate-registry'")
    pc.common(path, document, "org.atomix.candidate-registry")
    if document["hash"] != "org.atomix.sha256-canonical-json-v1":
        raise pc.error(path, "unsupported candidate hash contract")
    candidates = pc.list_value(path, document["candidates"], "candidates")
    if not candidates:
        raise pc.error(path, "candidates must not be empty")
    by_content: dict[str, dict[str, Any]] = {}
    logical_ids: set[str] = set()
    numeric_ids: set[int] = set()
    for index, item in enumerate(candidates):
        name = f"candidates[{index}]"
        candidate = pc.object_value(path, item, name)
        pc.exact_keys(path, candidate, name, {"content_id", "identity", "records"})
        identity = validate_identity(path, candidate["identity"],
                                     f"{name}.identity")
        declared = content_id_value(path, candidate["content_id"], f"{name}.content_id")
        expected = content_id(identity)
        if declared != expected:
            raise pc.error(path, f"{name}.content_id does not match canonical identity")
        if declared in by_content or identity["id"] in logical_ids or \
                identity["numeric_id"] in numeric_ids:
            raise pc.error(path, "candidate content, logical, and numeric IDs must be unique")
        by_content[declared] = candidate
        logical_ids.add(identity["id"])
        numeric_ids.add(identity["numeric_id"])
        records = pc.object_value(path, candidate["records"], f"{name}.records")
        pc.exact_keys(path, records, f"{name}.records", {"evidence", "deployments"})
        for record_kind in ("evidence", "deployments"):
            refs = pc.list_value(path, records[record_kind], f"{name}.records.{record_kind}")
            if not refs:
                raise pc.error(path, f"{name}.records.{record_kind} must not be empty")
            for ref_index, ref in enumerate(refs):
                validate_ref(path, ref, f"{name}.records.{record_kind}[{ref_index}]")

    for candidate_id, candidate in by_content.items():
        parent = candidate["identity"]["lineage"]["parent"]
        if parent is not None and (parent not in by_content or parent == candidate_id):
            raise pc.error(path, f"candidate {candidate_id} has an unknown/self parent")
        seen = {candidate_id}
        while parent is not None:
            if parent in seen:
                raise pc.error(path, "candidate lineage contains a cycle")
            seen.add(parent)
            parent = by_content[parent]["identity"]["lineage"]["parent"]
    return by_content


def validate_timestamp(path: Path, value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise pc.error(path, f"{name} must be an RFC3339 UTC timestamp")
    try:
        dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise pc.error(path, f"{name} is not a valid timestamp") from exc


def validate_record_common(path: Path, document: dict[str, Any], schema: str,
                           kind: str) -> list[str]:
    if document.get("kind") != kind:
        raise pc.error(path, f"kind must be {kind!r}")
    pc.common(path, document, schema)
    declared = content_id_value(path, document["content_id"], "content_id")
    if declared != document_content_id(document):
        raise pc.error(path, "content_id does not match canonical document")
    candidates = pc.list_value(path, document["candidates"], "candidates")
    if not candidates:
        raise pc.error(path, "candidates must not be empty")
    result = [content_id_value(path, item, "candidate reference") for item in candidates]
    if len(result) != len(set(result)):
        raise pc.error(path, "candidates contains duplicates")
    return result


def validate_evidence(path: Path, document: dict[str, Any]) -> list[str]:
    pc.exact_keys(
        path, document, "candidate evidence",
        {"schema", "kind", "id", "revision", "summary", "content_id",
         "candidates", "environment", "test", "artifacts", "extensions"},
    )
    candidates = validate_record_common(
        path, document, "org.atomix.candidate-evidence", "candidate-evidence")
    environment = pc.object_value(path, document["environment"], "environment")
    pc.exact_keys(path, environment, "environment", {"level", "timestamp_utc", "tools"})
    pc.namespaced(path, environment["level"], "environment.level")
    validate_timestamp(path, environment["timestamp_utc"], "environment.timestamp_utc")
    validate_tools(path, environment["tools"], "environment.tools")
    test = pc.object_value(path, document["test"], "test")
    pc.exact_keys(path, test, "test", {"id", "command", "status", "oracle", "results"})
    pc.namespaced(path, test["id"], "test.id")
    command = pc.list_value(path, test["command"], "test.command")
    if not command or not all(isinstance(arg, str) and arg for arg in command):
        raise pc.error(path, "test.command must be a non-empty argument array")
    if pc.namespaced(path, test["status"], "test.status") != "org.atomix.pass":
        raise pc.error(path, "candidate evidence currently requires a passing test")
    pc.namespaced(path, test["oracle"], "test.oracle")
    results = pc.list_value(path, test["results"], "test.results")
    seen: set[str] = set()
    for index, item in enumerate(results):
        name = f"test.results[{index}]"
        result = pc.object_value(path, item, name)
        pc.exact_keys(path, result, name,
                      {"candidate", "program_sha256", "output_sha256",
                       "load_cycles", "execute_cycles"})
        candidate = content_id_value(path, result["candidate"], f"{name}.candidate")
        if candidate in seen:
            raise pc.error(path, "test.results contains duplicate candidates")
        seen.add(candidate)
        sha256_value(path, result["program_sha256"], f"{name}.program_sha256")
        sha256_value(path, result["output_sha256"], f"{name}.output_sha256")
        u32(path, result["load_cycles"], f"{name}.load_cycles", positive=True)
        u32(path, result["execute_cycles"], f"{name}.execute_cycles", positive=True)
    if seen != set(candidates):
        raise pc.error(path, "test.results must cover every referenced candidate")
    artifacts = pc.list_value(path, document["artifacts"], "artifacts")
    if not artifacts:
        raise pc.error(path, "artifacts must not be empty")
    for index, item in enumerate(artifacts):
        name = f"artifacts[{index}]"
        artifact = pc.object_value(path, item, name)
        pc.exact_keys(path, artifact, name, {"id", "sha256", "path"})
        pc.namespaced(path, artifact["id"], f"{name}.id")
        sha256_value(path, artifact["sha256"], f"{name}.sha256")
        if artifact["path"] is not None:
            target = repo_path(path, artifact["path"], f"{name}.path")
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != \
                    artifact["sha256"]:
                raise pc.error(path, f"{name} does not match its tracked artifact")
    return candidates


def validate_deployment(path: Path, document: dict[str, Any]) -> list[str]:
    pc.exact_keys(
        path, document, "deployment record",
        {"schema", "kind", "id", "revision", "summary", "content_id",
         "candidates", "environment", "outcomes", "extensions"},
    )
    candidates = validate_record_common(
        path, document, "org.atomix.candidate-deployment", "candidate-deployment")
    environment = pc.object_value(path, document["environment"], "environment")
    pc.exact_keys(path, environment, "environment", {"board", "device", "connected"})
    for field in ("board", "device"):
        pc.namespaced(path, environment[field], f"environment.{field}")
    if not isinstance(environment["connected"], bool):
        raise pc.error(path, "environment.connected must be boolean")
    outcomes = pc.list_value(path, document["outcomes"], "outcomes")
    seen: set[str] = set()
    for index, item in enumerate(outcomes):
        name = f"outcomes[{index}]"
        outcome = pc.object_value(path, item, name)
        pc.exact_keys(path, outcome, name,
                      {"candidate", "status", "attempts", "last_generation",
                       "volatile_only", "reason"})
        candidate = content_id_value(path, outcome["candidate"], f"{name}.candidate")
        if candidate in seen:
            raise pc.error(path, "outcomes contains duplicate candidates")
        seen.add(candidate)
        status = pc.namespaced(path, outcome["status"], f"{name}.status")
        attempts = u32(path, outcome["attempts"], f"{name}.attempts")
        if outcome["last_generation"] is not None:
            u32(path, outcome["last_generation"], f"{name}.last_generation")
        if not isinstance(outcome["volatile_only"], bool):
            raise pc.error(path, f"{name}.volatile_only must be boolean")
        pc.namespaced(path, outcome["reason"], f"{name}.reason")
        if status == "org.atomix.not-deployed" and \
                (attempts != 0 or outcome["last_generation"] is not None):
            raise pc.error(path, f"{name} claims no deployment but records an attempt")
    if seen != set(candidates):
        raise pc.error(path, "outcomes must cover every referenced candidate")
    return candidates


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise pc.error(path, str(exc)) from exc
    return pc.object_value(path, value, "document")


def check(path: Path = DEFAULT_REGISTRY) -> dict[str, dict[str, Any]]:
    path = path.resolve()
    registry = load_json(path)
    by_content = validate_registry(path, registry)
    loaded: dict[Path, tuple[str, set[str]]] = {}
    for candidate_id, candidate in by_content.items():
        for record_kind, expected_kind in (("evidence", "candidate-evidence"),
                                           ("deployments", "candidate-deployment")):
            for index, ref in enumerate(candidate["records"][record_kind]):
                declared, record_path = validate_ref(
                    path, ref, f"candidate {candidate_id} {record_kind}[{index}]")
                if record_path not in loaded:
                    document = load_json(record_path)
                    candidates = validate_evidence(record_path, document) \
                        if expected_kind == "candidate-evidence" \
                        else validate_deployment(record_path, document)
                    loaded[record_path] = (document["content_id"], set(candidates))
                actual, covered = loaded[record_path]
                if declared != actual:
                    raise pc.error(path, f"record reference digest does not match {record_path}")
                if candidate_id not in covered:
                    raise pc.error(path, f"record {record_path} does not cover {candidate_id}")
    known = set(by_content)
    for record_path, (_, covered) in loaded.items():
        if not covered <= known:
            raise pc.error(record_path, "record references an unknown candidate")
    print(f"candidate registry: PASS ({len(by_content)} candidates, "
          f"{len(loaded)} content-addressed records)")
    return by_content


def record_validator(kind: str):
    if kind == "candidate-evidence":
        return validate_evidence
    if kind == "candidate-deployment":
        return validate_deployment
    raise pc.ContractError(f"unsupported record kind {kind!r}")


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def seal(paths: list[Path], refresh_artifacts: bool = False,
         registry_path: Path = DEFAULT_REGISTRY) -> int:
    """Recompute record content IDs and update every registry reference.

    Sealing never invents measurements.  It refuses a record whose tracked
    artifact digests have drifted unless the caller states explicitly that the
    artifacts were re-run, which keeps `check` the honest gate.
    """
    registry_path = registry_path.resolve()
    registry = load_json(registry_path)
    remapped: dict[str, str] = {}
    for path in paths:
        path = path.resolve()
        document = load_json(path)
        kind = document.get("kind")
        validate = record_validator(kind)
        if refresh_artifacts:
            for artifact in document.get("artifacts", []):
                if isinstance(artifact, dict) and artifact.get("path"):
                    target = repo_path(path, artifact["path"], "artifacts[].path")
                    if target.is_file():
                        artifact["sha256"] = hashlib.sha256(
                            target.read_bytes()).hexdigest()
        previous = document.get("content_id")
        document["content_id"] = document_content_id(document)
        validate(path, document)
        write_json(path, document)
        if isinstance(previous, str) and previous != document["content_id"]:
            remapped[previous] = document["content_id"]
        print(f"sealed {path.relative_to(ROOT)} -> {document['content_id']}")

    updated = 0
    for candidate in registry.get("candidates", []):
        for record_kind in ("evidence", "deployments"):
            for ref in candidate.get("records", {}).get(record_kind, []):
                target = (ROOT / ref["path"]).resolve()
                if target not in {p.resolve() for p in paths}:
                    continue
                actual = load_json(target)["content_id"]
                if ref["content_id"] != actual:
                    ref["content_id"] = actual
                    updated += 1
    if updated:
        write_json(registry_path, registry)
        print(f"updated {updated} registry reference(s)")
    check(registry_path)
    return 0


def self_test() -> int:
    registry = load_json(DEFAULT_REGISTRY)
    validate_registry(Path("<registry>"), registry)
    tampered = copy.deepcopy(registry)
    tampered["candidates"][0]["identity"]["artifact"]["sha256"] = "0" * 64
    try:
        validate_registry(Path("<tampered-identity>"), tampered)
    except pc.ContractError:
        pass
    else:
        raise pc.ContractError("tampered candidate identity retained its content ID")

    bad_parent = copy.deepcopy(registry)
    bad_parent["candidates"][0]["identity"]["lineage"]["parent"] = \
        "sha256:" + "1" * 64
    bad_parent["candidates"][0]["content_id"] = \
        content_id(bad_parent["candidates"][0]["identity"])
    try:
        validate_registry(Path("<bad-parent>"), bad_parent)
    except pc.ContractError:
        pass
    else:
        raise pc.ContractError("unknown parent was accepted")

    evidence_path = ROOT / registry["candidates"][0]["records"]["evidence"][0]["path"]
    evidence = load_json(evidence_path)
    tampered_evidence = copy.deepcopy(evidence)
    tampered_evidence["test"]["results"][0]["execute_cycles"] += 1
    try:
        validate_evidence(Path("<tampered-evidence>"), tampered_evidence)
    except pc.ContractError:
        pass
    else:
        raise pc.ContractError("tampered evidence retained its content ID")
    print("candidate registry: SELF-TEST PASS (identity, lineage, files, records)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_REGISTRY)
    seal_parser = subparsers.add_parser(
        "seal", help="recompute record content IDs and registry references")
    seal_parser.add_argument("records", nargs="+", type=Path)
    seal_parser.add_argument(
        "--refresh-artifacts", action="store_true",
        help="also re-digest path-backed artifacts; only valid after re-running "
             "the record's own test command")
    seal_parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    subparsers.add_parser("self-test")
    args = parser.parse_args()
    try:
        if args.command == "self-test":
            return self_test()
        if args.command == "seal":
            return seal(args.records, args.refresh_artifacts, args.registry)
        check(args.path)
        return 0
    except pc.ContractError as exc:
        print(f"candidate registry: FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
