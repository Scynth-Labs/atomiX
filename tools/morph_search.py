#!/usr/bin/env python3
"""Reproduce the bounded R3/L3 morph-genome search experiment.

This is deliberately a proposal generator, not an actuator.  It models the
documented role.morph sequencer, searches only the 14-bit PE operation/route
descriptor, and requires exact agreement with deterministic workload oracles.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPACE = ROOT / "research/live-fpga/l3/morph-search-space.json"
DEFAULT_RESULT = ROOT / "research/live-fpga/l3/morph-search-results.json"
MASK32 = 0xFFFF_FFFF

SRC_A, SRC_B, SRC_ACC, SRC_IMM0, SRC_IMM1, SRC_ZERO, SRC_ONE, SRC_CHAIN = range(8)
ACC_HOLD, ACC_LOAD = 0, 1
MODE_SCALAR, MODE_SIMT, MODE_SYSTOLIC = 0, 1, 2


class SearchError(ValueError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def u32(value: int) -> int:
    return value & MASK32


def encode_desc(fields: Iterable[int]) -> int:
    srca, srcb, srcc, srcd, accrule = fields
    return srca | (srcb << 3) | (srcc << 6) | (srcd << 9) | (accrule << 12)


def decode_desc(desc: int) -> tuple[int, int, int, int, int]:
    return (desc & 7, (desc >> 3) & 7, (desc >> 6) & 7,
            (desc >> 9) & 7, (desc >> 12) & 1)


def descriptor_word(desc: int) -> int:
    return desc | (desc << 14)


def candidate_genome(template: list[int], desc: int) -> list[int]:
    genome = list(template)
    genome[10] = descriptor_word(desc)
    genome[11] = descriptor_word(desc)
    return genome


def candidate_id(genome: list[int]) -> str:
    identity = {
        "schema": "org.atomix.morph-genome.v1",
        "role_id": "role.morph",
        "role_revision": 1,
        "words": genome,
    }
    return sha256_bytes(canonical(identity))


def select(source: int, stream_a: int, stream_b: int, acc: int,
           imm0: int, imm1: int, chain: int) -> int:
    values = (stream_a, stream_b, acc, imm0, imm1, 0, 1, chain)
    return values[source]


def pe_step(desc: int, stream_a: int, stream_b: int, acc: int,
            imm0: int, imm1: int, chain: int) -> int:
    srca, srcb, srcc, srcd, accrule = decode_desc(desc)
    if accrule != ACC_LOAD:
        return acc
    a = select(srca, stream_a, stream_b, acc, imm0, imm1, chain)
    b = select(srcb, stream_a, stream_b, acc, imm0, imm1, chain)
    c = select(srcc, stream_a, stream_b, acc, imm0, imm1, chain)
    d = select(srcd, stream_a, stream_b, acc, imm0, imm1, chain)
    return u32(u32(a + b) * c + d)


def address_fields(genome: list[int]) -> dict[str, int]:
    return {
        "mode": genome[0] & 0xF,
        "m": genome[1] & 0xFFFF,
        "n": (genome[1] >> 16) & 0xFFFF,
        "k": genome[2] & 0xFFFF,
        "a_base": genome[3] & 0xFFFF,
        "a_row": (genome[3] >> 16) & 0xFFFF,
        "a_k": genome[4] & 0xFFFF,
        "a_col": (genome[4] >> 16) & 0xFFFF,
        "b_base": genome[5] & 0xFFFF,
        "b_col": (genome[5] >> 16) & 0xFFFF,
        "b_k": genome[6] & 0xFFFF,
        "c_base": genome[7] & 0xFFFF,
        "c_row": (genome[7] >> 16) & 0xFFFF,
        "imm0": genome[8] & MASK32,
        "imm1": genome[9] & MASK32,
        "acc_init": genome[12] & MASK32,
    }


def accepted(genome: list[int], data_words: int) -> bool:
    f = address_fields(genome)
    if f["mode"] not in (MODE_SCALAR, MODE_SIMT, MODE_SYSTOLIC):
        return False
    if not f["m"] or not f["n"] or not f["k"] or f["m"] * f["n"] > data_words:
        return False
    a_last = (f["a_base"] + (f["m"] - 1) * f["a_row"] +
              (f["n"] - 1) * f["a_col"] + (f["k"] - 1) * f["a_k"])
    b_last = f["b_base"] + (f["n"] - 1) * f["b_col"] + (f["k"] - 1) * f["b_k"]
    c_last = f["c_base"] + (f["m"] - 1) * f["c_row"] + f["n"] - 1
    return a_last < data_words and b_last < data_words and c_last < data_words


def expand_vector(value: Any, count: int, name: str) -> list[int]:
    if isinstance(value, list):
        result = value
    elif isinstance(value, dict) and value.get("kind") == "affine":
        result = [value["start"] + index * value["step"] for index in range(count)]
    elif isinstance(value, dict) and value.get("kind") == "cycle":
        result = [(index % value["modulus"]) + value["offset"] for index in range(count)]
    else:
        raise SearchError(f"{name}: expected an explicit, affine, or cycle vector")
    if len(result) != count or any(not isinstance(item, int) for item in result):
        raise SearchError(f"{name}: expected exactly {count} integer elements")
    return result


def load_memory(operation: str, case: dict[str, Any], f: dict[str, int],
                data_words: int) -> list[int]:
    memory = [0] * data_words
    if operation == "scalar-recurrence":
        x = expand_vector(case["x"], f["k"], "scalar.x")
        for kk, value in enumerate(x):
            memory[f["a_base"] + kk * f["a_k"]] = u32(value)
    elif operation == "saxpy":
        x_values = expand_vector(case["x"], f["n"], "saxpy.x")
        y_values = expand_vector(case["y"], f["n"], "saxpy.y")
        for col, (x, y) in enumerate(zip(x_values, y_values)):
            memory[f["a_base"] + col * f["a_col"]] = u32(x)
            memory[f["b_base"] + col * f["b_col"]] = u32(y)
    elif operation == "gemm":
        a_values = expand_vector(case["a"], f["m"] * f["k"], "gemm.a")
        b_values = expand_vector(case["b"], f["k"] * f["n"], "gemm.b")
        for row in range(f["m"]):
            for kk in range(f["k"]):
                memory[f["a_base"] + row * f["a_row"] + kk * f["a_k"]] = \
                    u32(a_values[row * f["k"] + kk])
        for kk in range(f["k"]):
            for col in range(f["n"]):
                memory[f["b_base"] + col * f["b_col"] + kk * f["b_k"]] = \
                    u32(b_values[kk * f["n"] + col])
    else:
        raise SearchError(f"unknown operation {operation!r}")
    return memory


def oracle(operation: str, case: dict[str, Any], f: dict[str, int]) -> list[int]:
    if operation == "scalar-recurrence":
        acc = f["acc_init"]
        for value in expand_vector(case["x"], f["k"], "scalar.x"):
            acc = u32(u32(acc + value) * f["imm0"] + f["imm1"])
        return [acc]
    if operation == "saxpy":
        x_values = expand_vector(case["x"], f["n"], "saxpy.x")
        y_values = expand_vector(case["y"], f["n"], "saxpy.y")
        return [u32(f["imm0"] * x + y) for x, y in zip(x_values, y_values)]
    if operation == "gemm":
        a_values = expand_vector(case["a"], f["m"] * f["k"], "gemm.a")
        b_values = expand_vector(case["b"], f["k"] * f["n"], "gemm.b")
        output = []
        for row in range(f["m"]):
            for col in range(f["n"]):
                acc = f["acc_init"]
                for kk in range(f["k"]):
                    acc = u32(acc + a_values[row * f["k"] + kk] *
                              b_values[kk * f["n"] + col])
                output.append(acc)
        return output
    raise SearchError(f"unknown operation {operation!r}")


def simulate(genome: list[int], desc: int, operation: str,
             case: dict[str, Any], pes: int, data_words: int) -> list[int]:
    if not accepted(genome, data_words):
        raise SearchError("reviewed genome template fails the RTL bounds")
    f = address_fields(genome)
    memory = load_memory(operation, case, f, data_words)
    acc = [f["acc_init"]] * pes

    if f["mode"] == MODE_SCALAR:
        for kk in range(f["k"]):
            addr_a = f["a_base"] + kk * f["a_k"]
            addr_b = f["b_base"] + kk * f["b_k"]
            acc[0] = pe_step(desc, memory[addr_a], memory[addr_b], acc[0],
                             f["imm0"], f["imm1"], 0)
        return [acc[0]]

    if f["mode"] == MODE_SIMT:
        output = []
        for row in range(f["m"]):
            for col0 in range(0, f["n"], pes):
                old = list(acc)
                for lane in range(pes):
                    col = col0 + lane
                    if col >= f["n"]:
                        continue
                    addr_a = f["a_base"] + row * f["a_row"] + col * f["a_col"]
                    addr_b = f["b_base"] + col * f["b_col"]
                    chain = old[lane - 1] if lane else 0
                    acc[lane] = pe_step(desc, memory[addr_a], memory[addr_b],
                                        old[lane], f["imm0"], f["imm1"], chain)
                    output.append(acc[lane])
                acc = [f["acc_init"]] * pes
        return output

    output = []
    for row in range(f["m"]):
        for col in range(f["n"]):
            acc = [f["acc_init"]] * pes
            for kk0 in range(0, f["k"], pes):
                old = list(acc)
                for lane in range(pes):
                    kk = kk0 + lane
                    if kk >= f["k"]:
                        stream_a = stream_b = 0
                    else:
                        addr_a = f["a_base"] + row * f["a_row"] + col * f["a_col"] + kk * f["a_k"]
                        addr_b = f["b_base"] + col * f["b_col"] + kk * f["b_k"]
                        stream_a, stream_b = memory[addr_a], memory[addr_b]
                    chain = old[lane - 1] if lane else 0
                    acc[lane] = pe_step(desc, stream_a, stream_b, old[lane],
                                        f["imm0"], f["imm1"], chain)
            output.append(u32(sum(acc)))
    return output


@dataclass(frozen=True, order=True)
class Score:
    mismatched_words: int
    mismatched_bits: int


def score_candidate(workload: dict[str, Any], desc: int, pes: int,
                    data_words: int) -> tuple[Score, list[int]]:
    genome = candidate_genome(workload["reference_genome"], desc)
    actual_all: list[int] = []
    expected_all: list[int] = []
    f = address_fields(genome)
    for case in workload["cases"]:
        actual = simulate(genome, desc, workload["operation"], case, pes, data_words)
        expected = oracle(workload["operation"], case, f)
        actual_all.extend(actual)
        expected_all.extend(expected)
    mismatches = sum(a != e for a, e in zip(actual_all, expected_all))
    bits = sum((a ^ e).bit_count() for a, e in zip(actual_all, expected_all))
    return Score(mismatches, bits), actual_all


def exhaustive_order(count: int) -> Iterable[int]:
    return range(count)


def affine_order(count: int, offset: int, step: int) -> Iterable[int]:
    return ((offset + step * index) % count for index in range(count))


def linear_search(workload: dict[str, Any], order: Iterable[int], budget: int,
                  pes: int, data_words: int) -> tuple[int | None, Score, int]:
    best_desc = None
    best_score = Score(1 << 30, 1 << 30)
    evaluations = 0
    for desc in order:
        if evaluations >= budget:
            break
        candidate_score, _ = score_candidate(workload, desc, pes, data_words)
        evaluations += 1
        if candidate_score < best_score:
            best_desc, best_score = desc, candidate_score
        if candidate_score.mismatched_words == 0:
            return desc, candidate_score, evaluations
    return best_desc if best_score.mismatched_words == 0 else None, best_score, evaluations


def coordinate_search(workload: dict[str, Any], initial: list[int], max_passes: int,
                      pes: int, data_words: int) -> tuple[int | None, Score, int]:
    fields = list(initial)
    desc = encode_desc(fields)
    best_score, _ = score_candidate(workload, desc, pes, data_words)
    evaluations = 1
    for _ in range(max_passes):
        changed = False
        for field_index in range(5):
            domain = range(2) if field_index == 4 else range(8)
            local_fields = list(fields)
            local_score = best_score
            local_desc = desc
            for value in domain:
                trial_fields = list(fields)
                trial_fields[field_index] = value
                trial_desc = encode_desc(trial_fields)
                trial_score, _ = score_candidate(workload, trial_desc, pes, data_words)
                evaluations += 1
                if (trial_score, trial_desc) < (local_score, local_desc):
                    local_fields, local_score, local_desc = trial_fields, trial_score, trial_desc
            if local_desc != desc:
                fields, best_score, desc = local_fields, local_score, local_desc
                changed = True
            if best_score.mismatched_words == 0:
                return desc, best_score, evaluations
        if not changed:
            break
    return desc if best_score.mismatched_words == 0 else None, best_score, evaluations


def validate_space(space: dict[str, Any]) -> None:
    expected_top = {"schema", "kind", "id", "revision", "hypothesis",
                    "success_criterion", "fabric", "authority", "bounds",
                    "strategies", "workloads"}
    if set(space) != expected_top:
        raise SearchError(f"search-space keys differ: {sorted(set(space) ^ expected_top)}")
    if space["schema"] != "org.atomix.morph-search-space.v1" or space["kind"] != "l3-search-space":
        raise SearchError("unsupported search-space schema or kind")
    fabric = space["fabric"]
    if fabric != {"role": "role.morph", "revision": 1, "pes": 4,
                  "data_words": 256, "cfg_words": 13}:
        raise SearchError("this experiment is pinned to the reviewed four-PE morph model")
    bounds = space["bounds"]
    if bounds["mutable_words"] != [10, 11] or bounds["fixed_words"] != list(range(10)) + [12]:
        raise SearchError("only the two PE descriptor words may be mutable")
    if bounds["candidate_count"] != 8192 or bounds["homogeneous_descriptor"] is not True:
        raise SearchError("the bounded descriptor space must contain 8^4*2 candidates")
    if bounds["descriptor_fields"] != {
        "srca": [0, 7], "srcb": [0, 7], "srcc": [0, 7], "srcd": [0, 7],
        "accumulator_rule": [0, 1],
    } or bounds["reserved_bits"] != 0:
        raise SearchError("descriptor fields must match the RTL's four muxes and load/hold rule")
    if space["authority"]["actuation"] != "org.atomix.not-authorized":
        raise SearchError("an L3 search record cannot grant actuation authority")
    strategy_ids = [strategy["id"] for strategy in space["strategies"]]
    if len(strategy_ids) != len(set(strategy_ids)):
        raise SearchError("strategy IDs must be unique")
    for strategy in space["strategies"]:
        if strategy["kind"] in ("exhaustive-lexicographic", "affine-permutation"):
            if strategy["budget"] < 1 or strategy["budget"] > bounds["candidate_count"]:
                raise SearchError(f"{strategy['id']}: budget leaves the bounded space")
        if strategy["kind"] == "affine-permutation" and (
                math.gcd(strategy["step"], bounds["candidate_count"]) != 1 or
                not 0 <= strategy["offset"] < bounds["candidate_count"]):
            raise SearchError(f"{strategy['id']}: affine order is not a full permutation")
        if strategy["kind"] == "greedy-coordinate" and (
                len(strategy["initial_fields"]) != 5 or strategy["max_passes"] < 1):
            raise SearchError(f"{strategy['id']}: malformed coordinate-search bounds")
    if not space["workloads"]:
        raise SearchError("at least one deterministic workload is required")
    for workload in space["workloads"]:
        genome = workload["reference_genome"]
        if len(genome) != 13 or any(not isinstance(word, int) or word < 0 or word > MASK32 for word in genome):
            raise SearchError(f"{workload['id']}: reference_genome must be thirteen uint32 words")
        desc0 = genome[10] & 0x3FFF
        if genome[10] != descriptor_word(desc0) or genome[11] != descriptor_word(desc0):
            raise SearchError(f"{workload['id']}: reference PE descriptors are not homogeneous")
        if not accepted(genome, fabric["data_words"]):
            raise SearchError(f"{workload['id']}: reference genome fails bounds")
        if not workload["cases"]:
            raise SearchError(f"{workload['id']}: no oracle cases")
        ref_score, _ = score_candidate(workload, desc0, fabric["pes"], fabric["data_words"])
        if ref_score.mismatched_words:
            raise SearchError(f"{workload['id']}: reviewed rollback genome fails its oracle")


def result_for_strategy(workload: dict[str, Any], strategy: dict[str, Any],
                        pes: int, data_words: int, count: int) -> dict[str, Any]:
    kind = strategy["kind"]
    if kind == "exhaustive-lexicographic":
        found, best, evaluations = linear_search(
            workload, exhaustive_order(count), strategy["budget"], pes, data_words)
    elif kind == "affine-permutation":
        found, best, evaluations = linear_search(
            workload, affine_order(count, strategy["offset"], strategy["step"]),
            strategy["budget"], pes, data_words)
    elif kind == "greedy-coordinate":
        found, best, evaluations = coordinate_search(
            workload, strategy["initial_fields"], strategy["max_passes"], pes, data_words)
    else:
        raise SearchError(f"unknown strategy kind {kind!r}")

    result: dict[str, Any] = {
        "id": strategy["id"],
        "status": "org.atomix.pass" if found is not None else "org.atomix.no-solution-in-budget",
        "evaluations": evaluations,
        "best_score": {
            "mismatched_words": best.mismatched_words,
            "mismatched_bits": best.mismatched_bits,
        },
        "winner": None,
    }
    if found is not None:
        genome = candidate_genome(workload["reference_genome"], found)
        _, outputs = score_candidate(workload, found, pes, data_words)
        result["winner"] = {
            "descriptor": found,
            "fields": list(decode_desc(found)),
            "genome": genome,
            "content_id": candidate_id(genome),
            "output_sha256": sha256_bytes(b"".join(word.to_bytes(4, "little") for word in outputs)),
        }
    return result


def build_result(space_path: Path) -> dict[str, Any]:
    space = json.loads(space_path.read_text())
    validate_space(space)
    fabric = space["fabric"]
    workloads = []
    for workload in space["workloads"]:
        reference = workload["reference_genome"]
        reference_desc = reference[10] & 0x3FFF
        results = [result_for_strategy(workload, strategy, fabric["pes"],
                                       fabric["data_words"], space["bounds"]["candidate_count"])
                   for strategy in space["strategies"]]
        if not any(item["status"] == "org.atomix.pass" for item in results):
            raise SearchError(f"{workload['id']}: no strategy met the success criterion")
        for item in results:
            if item["winner"] is None:
                continue
            changed = [index for index, (before, after) in enumerate(
                zip(reference, item["winner"]["genome"])) if before != after]
            if any(index not in space["bounds"]["mutable_words"] for index in changed):
                raise SearchError(f"{workload['id']}: winner escaped the mutable-word boundary")
        outputs: list[int] = []
        for case in workload["cases"]:
            outputs.extend(oracle(workload["operation"], case, address_fields(reference)))
        workloads.append({
            "id": workload["id"],
            "operation": workload["operation"],
            "oracle_cases": len(workload["cases"]),
            "oracle_words": len(outputs),
            "oracle_sha256": sha256_bytes(b"".join(word.to_bytes(4, "little") for word in outputs)),
            "rollback": {
                "descriptor": reference_desc,
                "genome": reference,
                "content_id": candidate_id(reference),
                "status": "org.atomix.known-good-rtl",
            },
            "strategies": results,
        })

    source_paths = [
        Path("components/role/morph/morph_fabric.sv"),
        Path("sim/unit/tb_morph_fabric.cpp"),
        Path("tools/morph_search.py"),
        space_path.relative_to(ROOT),
    ]
    return {
        "schema": "org.atomix.morph-search-result.v1",
        "kind": "l3-search-result",
        "id": "org.atomix.research.l3-morph-search",
        "revision": 1,
        "evidence_level": "org.atomix.deterministic-model-plus-rtl-reference",
        "claim": space["success_criterion"],
        "command": "make l3-check",
        "authority": space["authority"],
        "search_space": {
            "content_id": sha256_bytes(canonical(space)),
            "candidate_count": space["bounds"]["candidate_count"],
            "mutable_words": space["bounds"]["mutable_words"],
            "fixed_words": space["bounds"]["fixed_words"],
        },
        "sources": {str(path): sha256_file(ROOT / path) for path in source_paths},
        "workloads": workloads,
        "decision": "Retain exhaustive search as the completeness oracle; use the lower-evaluation strategy only to propose candidates, and require exact oracle, canary, RTL shadow evaluation, and manager-owned rollback before any volatile activation.",
    }


def command_check(space_path: Path, result_path: Path) -> None:
    expected = build_result(space_path)
    observed = json.loads(result_path.read_text())
    if observed != expected:
        raise SearchError(
            f"{result_path.relative_to(ROOT)} is stale; regenerate with "
            f"python3 tools/morph_search.py emit > {result_path.relative_to(ROOT)}"
        )
    print(f"morph search: PASS ({len(expected['workloads'])} workloads, "
          f"{expected['search_space']['candidate_count']} bounded descriptors)")
    for workload in expected["workloads"]:
        summary = ", ".join(
            f"{item['id']}={item['status'].removeprefix('org.atomix.')}@{item['evaluations']}"
            for item in workload["strategies"])
        print(f"  {workload['id']}: {summary}")


def self_test() -> None:
    for desc in range(8192):
        if encode_desc(decode_desc(desc)) != desc:
            raise SearchError(f"descriptor round trip failed at {desc}")
    space = json.loads(DEFAULT_SPACE.read_text())
    validate_space(space)
    for workload in space["workloads"]:
        reference_desc = workload["reference_genome"][10] & 0x3FFF
        good, _ = score_candidate(workload, reference_desc, 4, 256)
        bad, _ = score_candidate(workload, reference_desc ^ (1 << 12), 4, 256)
        if good.mismatched_words or not bad.mismatched_words:
            raise SearchError(f"{workload['id']}: oracle mutation self-test failed")
    print("morph search self-test: PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check", "emit", "self-test"))
    parser.add_argument("--space", type=Path, default=DEFAULT_SPACE)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    args = parser.parse_args()
    try:
        if args.command == "check":
            command_check(args.space.resolve(), args.result.resolve())
        elif args.command == "emit":
            print(json.dumps(build_result(args.space.resolve()), indent=2, sort_keys=True))
        else:
            self_test()
    except (OSError, json.JSONDecodeError, KeyError, TypeError, SearchError) as exc:
        print(f"morph search: FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
