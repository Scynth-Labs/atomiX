#!/usr/bin/env python3
"""Pin, validate, and mutation-test the bounded L3 morph RTL trial.

The search remains proposal-only.  This tool selects one exact winner for
each reviewed morph mode, derives the testbench header from the checked search
record, and maintains a functional-coverage contract for an external-manager
RTL trial.  It never talks to hardware or grants deployment authority.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import morph_search as search  # noqa: E402


SEARCH_SPACE = ROOT / "research/live-fpga/l3/morph-search-space.json"
SEARCH_RESULT = ROOT / "research/live-fpga/l3/morph-search-results.json"
TRIAL_RECORD = ROOT / "research/live-fpga/l3/morph-rtl-trial.json"
STRATEGY_ID = "seeded-permutation"
WORKLOAD_IDS = ("scalar-recurrence", "simt-saxpy", "systolic-gemm")
MACRO_NAMES = {
    "scalar-recurrence": "SCALAR",
    "simt-saxpy": "SIMT",
    "systolic-gemm": "SYSTOLIC",
}


class TrialError(ValueError):
    pass


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def word_hash(words: list[int]) -> str:
    return sha256_bytes(b"".join(word.to_bytes(4, "little") for word in words))


def load_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    return json.loads(SEARCH_SPACE.read_text()), json.loads(SEARCH_RESULT.read_text())


def select_candidates(space: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    search.validate_space(space)
    if result.get("schema") != "org.atomix.morph-search-result.v1":
        raise TrialError("unsupported morph search result")
    expected_space_id = search.sha256_bytes(search.canonical(space))
    if result["search_space"]["content_id"] != expected_space_id:
        raise TrialError("search result does not identify the checked search space")
    space_source = "research/live-fpga/l3/morph-search-space.json"
    if result["sources"].get(space_source) != sha256_file(SEARCH_SPACE):
        raise TrialError("search result carries a stale search-space source hash")
    if result["authority"]["actuation"] != "org.atomix.not-authorized":
        raise TrialError("search result unexpectedly grants actuation")

    selected: dict[str, Any] = {}
    for workload_id in WORKLOAD_IDS:
        workload = next(
            (item for item in space["workloads"] if item["id"] == workload_id), None)
        observed = next(
            (item for item in result["workloads"] if item["id"] == workload_id), None)
        if workload is None or observed is None:
            raise TrialError(f"missing workload {workload_id}")
        strategy = next(
            (item for item in observed["strategies"] if item["id"] == STRATEGY_ID),
            None,
        )
        if strategy is None or strategy["status"] != "org.atomix.pass":
            raise TrialError(f"{workload_id}: {STRATEGY_ID} has no exact winner")
        winner = strategy.get("winner")
        rollback = observed["rollback"]
        if not winner:
            raise TrialError(f"{workload_id}: missing winner")
        descriptor = winner["descriptor"]
        if (not isinstance(descriptor, int) or isinstance(descriptor, bool) or
                not 0 <= descriptor < space["bounds"]["candidate_count"]):
            raise TrialError(f"{workload_id}: winner descriptor is out of bounds")
        expected_genome = search.candidate_genome(
            workload["reference_genome"], descriptor)
        if winner["genome"] != expected_genome:
            raise TrialError(f"{workload_id}: winner genome does not match descriptor")
        if winner["fields"] != list(search.decode_desc(descriptor)):
            raise TrialError(f"{workload_id}: winner fields do not match descriptor")
        if winner["content_id"] != search.candidate_id(winner["genome"]):
            raise TrialError(f"{workload_id}: winner content ID is invalid")
        if rollback["genome"] != workload["reference_genome"]:
            raise TrialError(f"{workload_id}: rollback differs from reviewed genome")
        rollback_descriptor = rollback["descriptor"]
        expected_rollback_descriptor = workload["reference_genome"][10] & 0x3FFF
        if (rollback_descriptor != expected_rollback_descriptor or
                rollback["genome"][10] !=
                search.descriptor_word(rollback_descriptor)):
            raise TrialError(f"{workload_id}: rollback descriptor is invalid")
        if rollback["content_id"] != search.candidate_id(rollback["genome"]):
            raise TrialError(f"{workload_id}: rollback content ID is invalid")
        if rollback["status"] != "org.atomix.known-good-rtl":
            raise TrialError(f"{workload_id}: rollback is not reviewed RTL")
        if descriptor == rollback_descriptor:
            raise TrialError(f"{workload_id}: winner is the reference descriptor")
        changed = [
            index for index, (before, after) in enumerate(
                zip(rollback["genome"], winner["genome"]))
            if before != after
        ]
        if changed != [10, 11]:
            raise TrialError(f"{workload_id}: winner changed words {changed}")
        score, outputs = search.score_candidate(
            workload, descriptor, space["fabric"]["pes"],
            space["fabric"]["data_words"])
        if score.mismatched_words or word_hash(outputs) != winner["output_sha256"]:
            raise TrialError(f"{workload_id}: winner no longer meets its oracle")
        selected[workload_id] = {
            "workload": workload,
            "strategy": strategy,
            "winner": winner,
            "rollback": rollback,
        }
    if set(selected) != set(WORKLOAD_IDS):
        raise TrialError("candidate coverage does not span every reviewed mode")
    return selected


def derive() -> dict[str, Any]:
    space, result = load_inputs()
    selected = select_candidates(space, result)
    scalar = selected["scalar-recurrence"]
    reference = scalar["rollback"]["genome"]
    fields = search.address_fields(reference)

    # This descriptor ignores stream A. Zero input hides the fault, while the
    # scalar search's nonzero first case exposes it.
    fault_desc = search.encode_desc(
        [search.SRC_ACC, search.SRC_ZERO, search.SRC_IMM0, search.SRC_IMM1,
         search.ACC_LOAD]
    )
    fault_genome = search.candidate_genome(reference, fault_desc)
    fault_primary = {"x": [0] * fields["k"]}
    fault_canary = scalar["workload"]["cases"][0]

    def scalar_outputs(desc: int, case: dict[str, Any]) -> list[int]:
        return search.simulate(
            search.candidate_genome(reference, desc), desc,
            scalar["workload"]["operation"], case,
            space["fabric"]["pes"], space["fabric"]["data_words"],
        )

    primary_oracle = search.oracle(
        scalar["workload"]["operation"], fault_primary, fields)
    canary_oracle = search.oracle(
        scalar["workload"]["operation"], fault_canary, fields)
    if scalar_outputs(scalar["rollback"]["descriptor"], fault_primary) != primary_oracle:
        raise TrialError("rollback fails the fault-injection primary")
    if scalar_outputs(fault_desc, fault_primary) != primary_oracle:
        raise TrialError("fault injection does not pass its narrow primary")
    fault_canary_output = scalar_outputs(fault_desc, fault_canary)
    if fault_canary_output == canary_oracle:
        raise TrialError("fault injection is not exposed by the canary")

    fixtures: dict[str, Any] = {}
    for workload_id, item in selected.items():
        genome_fields = search.address_fields(item["rollback"]["genome"])
        case_records = []
        for index, case in enumerate(item["workload"]["cases"]):
            expected = search.oracle(item["workload"]["operation"], case, genome_fields)
            case_records.append({
                "id": "primary" if index == 0 else "canary",
                "oracle_words": len(expected),
                "oracle_sha256": word_hash(expected),
            })
        if len(case_records) != 2:
            raise TrialError(f"{workload_id}: expected primary and canary cases")
        fixtures[workload_id] = case_records

    return {
        "selected": selected,
        "fault_descriptor": fault_desc,
        "fault_genome": fault_genome,
        "fault_primary_oracle": primary_oracle,
        "fault_canary_oracle": canary_oracle,
        "fault_canary_output": fault_canary_output,
        "fixtures": fixtures,
    }


def build_record() -> dict[str, Any]:
    derived = derive()
    candidates = []
    for workload_id in WORKLOAD_IDS:
        item = derived["selected"][workload_id]
        winner = item["winner"]
        rollback = item["rollback"]
        candidates.append({
            "workload": workload_id,
            "operation": item["workload"]["operation"],
            "origin": {
                "strategy": STRATEGY_ID,
                "evaluations": item["strategy"]["evaluations"],
            },
            "candidate": {
                "descriptor": winner["descriptor"],
                "fields": winner["fields"],
                "genome": winner["genome"],
                "content_id": winner["content_id"],
            },
            "rollback": {
                "descriptor": rollback["descriptor"],
                "genome": rollback["genome"],
                "content_id": rollback["content_id"],
                "status": rollback["status"],
            },
            "fixtures": derived["fixtures"][workload_id],
        })

    fault_genome = derived["fault_genome"]
    source_paths = [
        Path("components/role/morph/morph_fabric.sv"),
        Path("sim/unit/tb_morph_l3.cpp"),
        Path("tools/morph_l3_trial.py"),
        Path("research/live-fpga/l3/morph-search-space.json"),
        Path("research/live-fpga/l3/morph-search-results.json"),
    ]
    return {
        "schema": "org.atomix.morph-rtl-trial.v2",
        "kind": "l3-volatile-rtl-trial",
        "id": "org.atomix.research.l3-morph-all-modes-volatile",
        "revision": 2,
        "evidence_level": "org.atomix.simulation-rtl",
        "claim": (
            "Searched non-reference genomes for scalar, SIMT, and systolic modes "
            "pass primary and canary RTL shadow evaluation on one resident fabric; "
            "a manager-approved scalar volatile trial and canary-detected fault are "
            "followed by verified manager-owned rollback."
        ),
        "command": "make l3-check",
        "authority": {
            "optimizer_actuation": "org.atomix.not-authorized",
            "trial_actuation": "org.atomix.testbench-manager-only",
            "hardware_actuation": "org.atomix.not-authorized",
            "persistence": "org.atomix.none",
        },
        "boundary": {
            "role": "role.morph",
            "mutable_words": [10, 11],
            "resident_rtl_unchanged": True,
            "bitstream_rebuild": False,
            "manager": "sim/unit/tb_morph_l3.cpp",
        },
        "coverage": {
            "reviewed_mode_paths": {"covered": 3, "total": 3},
            "searched_candidates": {"covered": 3, "total": 3},
            "deterministic_oracle_cases": {"covered": 6, "total": 6},
            "known_good_genomes": {"covered": 3, "total": 3},
            "semantic_faults_detected_by_canary": 1,
            "manager_rollbacks_verified": 1,
            "record_mutation_classes": [
                "authority", "content-identity", "mutable-boundary",
                "oracle-digest", "descriptor-binding", "workload-presence",
            ],
        },
        "candidates": candidates,
        "fault_injection": {
            "workload": "scalar-recurrence",
            "descriptor": derived["fault_descriptor"],
            "fields": list(search.decode_desc(derived["fault_descriptor"])),
            "genome": fault_genome,
            "content_id": search.candidate_id(fault_genome),
            "property": "passes all-zero narrow primary; ignores stream A and fails nonzero canary",
            "primary_oracle_sha256": word_hash(derived["fault_primary_oracle"]),
            "canary_oracle_sha256": word_hash(derived["fault_canary_oracle"]),
            "canary_fault_sha256": word_hash(derived["fault_canary_output"]),
        },
        "required_sequence": [
            "establish and shadow primary/canary for all three reviewed modes",
            "run manager-approved volatile scalar candidate trial",
            "show injected scalar fault passes narrow primary and fails canary",
            "reload scalar rollback genome and re-verify primary and canary",
        ],
        "expected_jobs": 17,
        "expected_generation": 17,
        "sources": {str(path): sha256_file(ROOT / path) for path in source_paths},
        "decision": (
            "Functional RTL coverage now spans every searched morph mode and oracle "
            "case. This is not physical-board evidence and does not authorize "
            "autonomous or persistent activation."
        ),
    }


def validate_record(observed: dict[str, Any], expected: dict[str, Any]) -> None:
    if observed != expected:
        raise TrialError("trial record differs from its reproducible contract")


def self_test() -> None:
    space, result = load_inputs()
    select_candidates(space, result)

    def scalar_winner(document: dict[str, Any]) -> dict[str, Any]:
        workload = next(
            item for item in document["workloads"]
            if item["id"] == "scalar-recurrence")
        strategy = next(
            item for item in workload["strategies"]
            if item["id"] == STRATEGY_ID)
        return strategy["winner"]

    input_mutations = []
    mutated = copy.deepcopy(result)
    mutated["authority"]["actuation"] = "org.atomix.authorized"
    input_mutations.append(("authority", mutated))

    mutated = copy.deepcopy(result)
    scalar_winner(mutated)["content_id"] = "sha256:" + "0" * 64
    input_mutations.append(("content-identity", mutated))

    mutated = copy.deepcopy(result)
    winner = scalar_winner(mutated)
    winner["genome"][0] ^= 1
    winner["content_id"] = search.candidate_id(winner["genome"])
    input_mutations.append(("mutable-boundary", mutated))

    mutated = copy.deepcopy(result)
    scalar_winner(mutated)["output_sha256"] = "sha256:" + "f" * 64
    input_mutations.append(("oracle-digest", mutated))

    mutated = copy.deepcopy(result)
    winner = scalar_winner(mutated)
    winner["descriptor"] ^= 1
    winner["fields"] = list(search.decode_desc(winner["descriptor"]))
    input_mutations.append(("descriptor-binding", mutated))

    mutated = copy.deepcopy(result)
    mutated["workloads"] = [
        item for item in mutated["workloads"] if item["id"] != "systolic-gemm"]
    input_mutations.append(("workload-presence", mutated))

    for name, bad_result in input_mutations:
        try:
            select_candidates(space, bad_result)
        except (KeyError, TrialError, search.SearchError):
            continue
        raise TrialError(f"mutation self-test accepted {name}")

    expected = build_record()
    tampered = copy.deepcopy(expected)
    tampered["expected_jobs"] -= 1
    try:
        validate_record(tampered, expected)
    except TrialError:
        pass
    else:
        raise TrialError("exact record gate accepted a tampered job count")
    print("morph L3 trial self-test: PASS (6 input mutations, exact record gate)")


def write_header(path: Path) -> None:
    derived = derive()
    lines = ["// Generated by tools/morph_l3_trial.py; do not edit.", "#pragma once"]

    def array(name: str, words: list[int]) -> None:
        values = ", ".join(f"{word}u" for word in words)
        lines.append(f"static constexpr uint32_t {name}[13] = {{{values}}};")

    for workload_id in WORKLOAD_IDS:
        item = derived["selected"][workload_id]
        macro = MACRO_NAMES[workload_id]
        lines.append(
            f"#define ATOMIX_L3_{macro}_CANDIDATE_DESC "
            f"{item['winner']['descriptor']}u")
        lines.append(
            f"#define ATOMIX_L3_{macro}_ROLLBACK_DESC "
            f"{item['rollback']['descriptor']}u")
        lines.append(
            f"#define ATOMIX_L3_{macro}_CANDIDATE_ID "
            f"\"{item['winner']['content_id']}\"")
        array(f"ATOMIX_L3_{macro}_CANDIDATE_GENOME", item["winner"]["genome"])
        array(f"ATOMIX_L3_{macro}_ROLLBACK_GENOME", item["rollback"]["genome"])
    lines.append(f"#define ATOMIX_L3_FAULT_DESC {derived['fault_descriptor']}u")
    array("ATOMIX_L3_FAULT_GENOME", derived["fault_genome"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    print(f"morph L3 header: {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check", "emit", "header", "self-test"))
    parser.add_argument("--record", type=Path, default=TRIAL_RECORD)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "emit":
            print(json.dumps(build_record(), indent=2, sort_keys=True))
        elif args.command == "header":
            if args.output is None:
                raise TrialError("header requires --output")
            write_header(args.output)
        elif args.command == "self-test":
            self_test()
        else:
            expected = build_record()
            validate_record(json.loads(args.record.read_text()), expected)
            descriptors = ",".join(
                str(item["candidate"]["descriptor"])
                for item in expected["candidates"])
            print(
                "morph L3 trial contract: PASS "
                f"(candidates={descriptors}, jobs={expected['expected_jobs']})")
    except (OSError, json.JSONDecodeError, KeyError, StopIteration, TypeError,
            TrialError, search.SearchError) as exc:
        print(f"morph L3 trial: FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
