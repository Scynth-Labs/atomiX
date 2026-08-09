#!/usr/bin/env python3
"""Validate vendor-neutral atomiX personality and workload research records."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "research" / "personalities"
NAMESPACED = re.compile(
    r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$"
)
LOCAL_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
SUPPORTED_SCHEMAS = {"org.atomix.personality", "org.atomix.workload"}


class ContractError(Exception):
    pass


def error(path: Path, message: str) -> ContractError:
    return ContractError(f"{path}: {message}")


def object_value(path: Path, value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise error(path, f"{name} must be an object")
    return value


def list_value(path: Path, value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise error(path, f"{name} must be an array")
    return value


def exact_keys(path: Path, value: dict[str, Any], name: str,
               required: set[str], optional: set[str] = frozenset()) -> None:
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    if missing:
        raise error(path, f"{name} is missing {', '.join(sorted(missing))}")
    if unknown:
        raise error(path, f"{name} has unknown fields {', '.join(sorted(unknown))}")


def namespaced(path: Path, value: Any, name: str) -> str:
    if not isinstance(value, str) or not NAMESPACED.fullmatch(value):
        raise error(path, f"{name} must be a lowercase namespaced identifier")
    return value


def namespaced_list(path: Path, value: Any, name: str) -> list[str]:
    values = list_value(path, value, name)
    result = [namespaced(path, item, f"{name} entry") for item in values]
    if len(result) != len(set(result)):
        raise error(path, f"{name} contains duplicates")
    return result


def nonnegative_int(path: Path, value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise error(path, f"{name} must be a non-negative integer")
    return value


def version(path: Path, value: Any, name: str) -> None:
    version_value = object_value(path, value, name)
    exact_keys(path, version_value, name, {"major", "minor"})
    nonnegative_int(path, version_value["major"], f"{name}.major")
    nonnegative_int(path, version_value["minor"], f"{name}.minor")


def extensions(path: Path, value: Any) -> None:
    extension_value = object_value(path, value, "extensions")
    for key in extension_value:
        namespaced(path, key, "extension key")


def common(path: Path, document: dict[str, Any], schema_id: str) -> None:
    schema = object_value(path, document["schema"], "schema")
    exact_keys(path, schema, "schema", {"id", "major", "minor"})
    if schema["id"] != schema_id:
        raise error(path, f"schema.id must be {schema_id!r}")
    schema_major = nonnegative_int(path, schema["major"], "schema.major")
    if schema_major != 1:
        raise error(path, f"unsupported {schema_id} major version {schema_major!r}")
    nonnegative_int(path, schema["minor"], "schema.minor")
    namespaced(path, document["id"], "id")
    if not isinstance(document["revision"], int) or isinstance(document["revision"], bool) \
            or document["revision"] < 1:
        raise error(path, "revision must be a positive integer")
    if not isinstance(document["summary"], str) or not document["summary"].strip():
        raise error(path, "summary must be a non-empty string")
    extensions(path, document["extensions"])


def validate_parameter_values(path: Path, values: Any, name: str) -> None:
    params = object_value(path, values, name)
    for key, value in params.items():
        if not LOCAL_NAME.fullmatch(key):
            raise error(path, f"{name} key {key!r} is not a local identifier")
        # JSON parsing already guarantees a portable value tree. Structured
        # values are intentional: a future topology or tensor parameter must
        # not require a base-schema change.


def validate_personality(path: Path, document: dict[str, Any]) -> None:
    required = {
        "schema", "kind", "id", "revision", "summary", "execution_model",
        "requires", "prefers", "parameters", "reconfiguration",
        "workload_bindings", "implementations", "extensions",
    }
    exact_keys(path, document, "personality", required)
    if document["kind"] != "personality":
        raise error(path, "kind must be 'personality'")
    common(path, document, "org.atomix.personality")
    namespaced(path, document["execution_model"], "execution_model")
    namespaced_list(path, document["requires"], "requires")
    namespaced_list(path, document["prefers"], "prefers")

    params = object_value(path, document["parameters"], "parameters")
    for name, spec_value in params.items():
        if not LOCAL_NAME.fullmatch(name):
            raise error(path, f"parameter {name!r} is not a local identifier")
        spec = object_value(path, spec_value, f"parameter {name}")
        exact_keys(path, spec, f"parameter {name}", {"value", "mutability", "doc"})
        namespaced(path, spec["mutability"], f"parameter {name}.mutability")
        if not isinstance(spec["doc"], str) or not spec["doc"].strip():
            raise error(path, f"parameter {name}.doc must be non-empty")

    reconfiguration = object_value(
        path, document["reconfiguration"], "reconfiguration"
    )
    exact_keys(
        path, reconfiguration, "reconfiguration",
        {"scope", "quiesce", "state", "rollback"},
    )
    for name in ("scope", "state", "rollback"):
        namespaced(path, reconfiguration[name], f"reconfiguration.{name}")
    if not isinstance(reconfiguration["quiesce"], bool):
        raise error(path, "reconfiguration.quiesce must be boolean")

    bindings = list_value(path, document["workload_bindings"], "workload_bindings")
    if not bindings:
        raise error(path, "workload_bindings must not be empty")
    for index, binding_value in enumerate(bindings):
        binding = object_value(path, binding_value, f"workload_bindings[{index}]")
        exact_keys(
            path, binding, f"workload_bindings[{index}]",
            {"id", "revision", "parameters"}
        )
        namespaced(path, binding["id"], f"workload_bindings[{index}].id")
        if not isinstance(binding["revision"], int) or \
                isinstance(binding["revision"], bool) or binding["revision"] < 1:
            raise error(path, f"workload_bindings[{index}].revision must be positive")
        validate_parameter_values(
            path, binding["parameters"], f"workload_bindings[{index}].parameters"
        )

    implementations = list_value(
        path, document["implementations"], "implementations"
    )
    if not implementations:
        raise error(path, "implementations must not be empty")
    for index, implementation_value in enumerate(implementations):
        name = f"implementations[{index}]"
        implementation = object_value(path, implementation_value, name)
        exact_keys(
            path, implementation, name,
            {"format", "version", "requires", "payload"},
        )
        namespaced(path, implementation["format"], f"{name}.format")
        version(path, implementation["version"], f"{name}.version")
        namespaced_list(path, implementation["requires"], f"{name}.requires")
        # Payload is intentionally opaque JSON. Its namespaced format owns any
        # stronger validation, including artifact hashes or instruction words.


def validate_element(path: Path, value: Any, name: str) -> None:
    element = object_value(path, value, name)
    exact_keys(path, element, name, {"format", "properties"})
    namespaced(path, element["format"], f"{name}.format")
    object_value(path, element["properties"], f"{name}.properties")


def signed32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def builtin_result(path: Path, operation: str, parameters: dict[str, Any],
                   inputs: dict[str, Any]) -> dict[str, Any]:
    if operation == "org.atomix.workload.scalar-recurrence-i32":
        values = inputs.get("x")
        if not isinstance(values, list) or len(values) != parameters.get("items"):
            raise error(path, "scalar oracle input x does not match items")
        acc = int(parameters["seed"])
        for value in values:
            acc = signed32((acc + int(value)) * int(parameters["multiplier"]) +
                           int(parameters["increment"]))
        return {"result": [acc]}
    if operation == "org.atomix.workload.saxpy-i32":
        x, y = inputs.get("x"), inputs.get("y")
        count = parameters.get("items")
        if not isinstance(x, list) or not isinstance(y, list) or \
                len(x) != count or len(y) != count:
            raise error(path, "SAXPY oracle inputs do not match items")
        a = int(parameters["a"])
        return {"out": [signed32(a * int(xv) + int(yv)) for xv, yv in zip(x, y)]}
    if operation == "org.atomix.workload.gemm-i8-i32":
        a, b = inputs.get("a"), inputs.get("b")
        m, k, n = (int(parameters[key]) for key in ("m", "k", "n"))
        if not isinstance(a, list) or len(a) != m or \
                not all(isinstance(row, list) and len(row) == k for row in a):
            raise error(path, "GEMM oracle input a does not match m by k")
        if not isinstance(b, list) or len(b) != k or \
                not all(isinstance(row, list) and len(row) == n for row in b):
            raise error(path, "GEMM oracle input b does not match k by n")
        result = []
        for row in range(m):
            result.append([
                signed32(sum(int(a[row][inner]) * int(b[inner][column])
                             for inner in range(k)))
                for column in range(n)
            ])
        return {"c": result}
    raise error(path, f"no built-in oracle for {operation!r}")


def validate_workload(path: Path, document: dict[str, Any]) -> None:
    required = {
        "schema", "kind", "id", "revision", "summary", "operation",
        "parameters", "buffers", "oracle", "cases", "metrics", "extensions",
    }
    exact_keys(path, document, "workload", required)
    if document["kind"] != "workload":
        raise error(path, "kind must be 'workload'")
    common(path, document, "org.atomix.workload")
    operation = namespaced(path, document["operation"], "operation")
    validate_parameter_values(path, document["parameters"], "parameters")

    buffers = list_value(path, document["buffers"], "buffers")
    if not buffers:
        raise error(path, "buffers must not be empty")
    buffer_names: list[str] = []
    for index, buffer_value in enumerate(buffers):
        name = f"buffers[{index}]"
        buffer = object_value(path, buffer_value, name)
        exact_keys(
            path, buffer, name,
            {"name", "direction", "element", "shape", "layout"},
        )
        if not isinstance(buffer["name"], str) or not LOCAL_NAME.fullmatch(buffer["name"]):
            raise error(path, f"{name}.name is not a local identifier")
        buffer_names.append(buffer["name"])
        namespaced(path, buffer["direction"], f"{name}.direction")
        validate_element(path, buffer["element"], f"{name}.element")
        shape = list_value(path, buffer["shape"], f"{name}.shape")
        if not shape or not all(
            (isinstance(item, int) and not isinstance(item, bool) and item > 0) or
            (isinstance(item, str) and LOCAL_NAME.fullmatch(item))
            for item in shape
        ):
            raise error(path, f"{name}.shape entries must be positive integers or parameters")
        namespaced(path, buffer["layout"], f"{name}.layout")
    if len(buffer_names) != len(set(buffer_names)):
        raise error(path, "buffer names must be unique")

    oracle = object_value(path, document["oracle"], "oracle")
    exact_keys(
        path, oracle, "oracle", {"kind", "operation", "properties"},
        {"reference"}
    )
    oracle_kind = namespaced(path, oracle["kind"], "oracle.kind")
    oracle_operation = namespaced(path, oracle["operation"], "oracle.operation")
    if oracle_operation != operation:
        raise error(path, "oracle.operation must equal operation")
    if "reference" in oracle and not isinstance(oracle["reference"], str):
        raise error(path, "oracle.reference must be a string")
    object_value(path, oracle["properties"], "oracle.properties")

    cases = list_value(path, document["cases"], "cases")
    if not cases:
        raise error(path, "cases must not be empty")
    for index, case_value in enumerate(cases):
        name = f"cases[{index}]"
        case = object_value(path, case_value, name)
        exact_keys(path, case, name, {"name", "parameters", "inputs", "expected"})
        if not isinstance(case["name"], str) or not case["name"]:
            raise error(path, f"{name}.name must be non-empty")
        validate_parameter_values(path, case["parameters"], f"{name}.parameters")
        inputs = object_value(path, case["inputs"], f"{name}.inputs")
        expected = object_value(path, case["expected"], f"{name}.expected")
        if oracle_kind == "org.atomix.builtin-exact":
            params = dict(document["parameters"])
            params.update(case["parameters"])
            actual = builtin_result(path, operation, params, inputs)
            if actual != expected:
                raise error(path, f"{name} oracle mismatch: got {actual!r}")
    namespaced_list(path, document["metrics"], "metrics")


def load_document(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise error(path, "file does not exist") from exc
    except json.JSONDecodeError as exc:
        raise error(path, f"invalid JSON: {exc}") from exc
    return object_value(path, document, "document")


def validate_document(path: Path, document: dict[str, Any]) -> None:
    schema = object_value(path, document.get("schema"), "schema")
    schema_id = schema.get("id")
    if schema_id not in SUPPORTED_SCHEMAS:
        raise error(path, f"unsupported schema {schema_id!r}")
    if schema_id == "org.atomix.personality":
        validate_personality(path, document)
    else:
        validate_workload(path, document)


def collect(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    for path in paths:
        if path.is_dir():
            result.extend(sorted(path.rglob("*.json")))
        else:
            result.append(path)
    return sorted(set(item.resolve() for item in result))


def check(paths: list[Path]) -> int:
    files = collect(paths)
    if not files:
        raise ContractError("no JSON contract documents found")
    documents: dict[tuple[str, int], tuple[Path, dict[str, Any]]] = {}
    for path in files:
        document = load_document(path)
        validate_document(path, document)
        identity = (document["id"], document["revision"])
        if identity in documents:
            raise error(
                path, f"duplicate identity {identity!r} also appears in "
                f"{documents[identity][0]}"
            )
        documents[identity] = (path, document)
    workloads = {
        identity: document for identity, (_, document) in documents.items()
        if document["kind"] == "workload"
    }
    for path, document in documents.values():
        if document["kind"] != "personality":
            continue
        for binding in document["workload_bindings"]:
            identity = (binding["id"], binding["revision"])
            if identity not in workloads:
                raise error(path, f"unknown workload binding {identity!r}")
            unknown_parameters = (
                binding["parameters"].keys() - workloads[identity]["parameters"].keys()
            )
            if unknown_parameters:
                raise error(
                    path, "workload binding has unknown parameters "
                    f"{', '.join(sorted(unknown_parameters))}"
                )
    print(f"personality contract: PASS ({len(documents)} documents)")
    return 0


def self_test() -> int:
    sample_path = DEFAULT_ROOT / "personalities" / "scalar.json"
    sample = load_document(sample_path)
    open_sample = copy.deepcopy(sample)
    open_sample["id"] = "dev.example.personality.experimental"
    open_sample["execution_model"] = "dev.example.execution.wavefront-tree"
    open_sample["requires"].append("dev.example.capability.custom-router")
    open_sample["parameters"]["topology"] = {
        "value": {"dimensions": [2, 3], "wrap": False},
        "mutability": "dev.example.load-time",
        "doc": "Structured external parameter",
    }
    open_sample["implementations"][0]["format"] = "dev.example.encoding.egraph"
    open_sample["implementations"][0]["payload"] = ["opaque", 1, {"node": 7}]
    open_sample["extensions"]["dev.example.trace-policy"] = {"enabled": True}
    validate_personality(Path("<extension-self-test>"), open_sample)

    closed_sample = copy.deepcopy(sample)
    closed_sample["requires"].append("company_opcode_7")
    try:
        validate_personality(Path("<namespace-self-test>"), closed_sample)
    except ContractError:
        pass
    else:
        raise ContractError("self-test accepted an unnamespaced capability")

    workload_path = DEFAULT_ROOT / "workloads" / "saxpy.json"
    broken_workload = load_document(workload_path)
    broken_workload["cases"][0]["expected"]["out"][0] += 1
    try:
        validate_workload(Path("<oracle-self-test>"), broken_workload)
    except ContractError:
        pass
    else:
        raise ContractError("self-test accepted a wrong workload oracle result")
    print("personality contract: SELF-TEST PASS (open extensions accepted)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check", help="validate contract JSON files")
    check_parser.add_argument("paths", nargs="*", type=Path, default=[DEFAULT_ROOT])
    subparsers.add_parser("self-test", help="test open extension and rejection rules")
    args = parser.parse_args()
    try:
        return check(args.paths) if args.command == "check" else self_test()
    except ContractError as exc:
        print(f"personality contract: FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
