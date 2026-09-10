#!/usr/bin/env python3
"""Stage AX experiment bundles for the browser handoff page.

The browser does not invent a lighter experiment format.  It receives the
same AX-03 bundle that ``experiment_report.py reproduce`` consumes, plus a
small generated manifest that binds that bundle to one staged WASM machine.
The page may append browser observations under ``extensions``; every original
field therefore remains a native-replay input.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import experiment_contract as ec
import experiment_report as er
import personality_contract as pc
from execution.contract import ROOT, sha256_file

SCHEMA = "org.atomix.browser-experiment-manifest.v1"
EXECUTE_CYCLES = "org.atomix.metric.execute-cycles"
TOTAL_CYCLES = "org.atomix.metric.total-cycles"


def measured(record: dict[str, Any], metric: str) -> int:
    entry = record["measurements"].get(metric, {})
    value = entry.get("value")
    if entry.get("status") != "org.atomix.measured" or \
            not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{record['candidate']}: {metric} is not an integer measurement")
    return value


def stage(plan_path: Path, records_path: Path, machines_path: Path,
          output: Path, max_bundle_bytes: int) -> None:
    if max_bundle_bytes < 4096:
        raise ValueError("max bundle bytes must be at least 4096")
    plan, workload, _ = er.load_plan(plan_path)
    records = er.load_records(records_path, plan)
    machines = pc.load_document(machines_path)
    by_name = {entry["name"]: entry for entry in machines.get("machines", [])}
    if len(by_name) != len(machines.get("machines", [])):
        raise ValueError(f"{machines_path}: machine names must be unique")

    implementations = {item["id"]: item for item in plan["implementations"]}
    targets = {item["id"]: item for item in plan["targets"]}
    candidates = {item["id"]: item for item in ec.plan_candidates(plan)}
    staged: list[dict[str, Any]] = []
    bundle_dir = output / "experiments"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    for candidate_id in sorted(candidates):
        record = records.get(candidate_id)
        if record is None or record["status"] not in ec.EXECUTED_STATUS:
            raise ValueError(f"{candidate_id}: no executed record to hand off")
        candidate = candidates[candidate_id]
        target = targets[candidate["target"]]
        implementation = implementations[candidate["implementation"]]
        if target["class"] != "org.atomix.rtl-simulator":
            raise ValueError(f"{candidate_id}: browser handoff needs an RTL simulator target")
        profile_name = Path(target["profile"]["value"]["config"]).stem
        machine = by_name.get(profile_name)
        if machine is None:
            raise ValueError(
                f"{candidate_id}: machine {profile_name!r} is not staged; refusing another"
            )

        profile_path = ROOT / target["profile"]["value"]["config"]
        profile_sha256 = sha256_file(profile_path)
        expected_profile = record["identity"]["target"]["profile_sha256"]
        if profile_sha256 != expected_profile:
            raise ValueError(
                f"{candidate_id}: stale profile {profile_sha256} != {expected_profile}"
            )
        artifact_path = ROOT / implementation["build"]["value"]["artifact"]
        if not artifact_path.is_file():
            raise ValueError(f"{candidate_id}: payload {artifact_path} is missing")
        payload_sha256 = sha256_file(artifact_path)
        expected_payload = record["identity"]["implementation"]["artifact_sha256"]
        if payload_sha256 != expected_payload:
            raise ValueError(
                f"{candidate_id}: stale payload {payload_sha256} != {expected_payload}"
            )

        bundle = er.bundle_for(plan, workload, record, plan_path)
        short = candidate_id.split(".")[-1]
        bundle_name = f"{short}.json"
        encoded = (json.dumps(bundle, indent=2) + "\n").encode()
        if len(encoded) > max_bundle_bytes:
            raise ValueError(
                f"{candidate_id}: bundle is {len(encoded)} bytes, over {max_bundle_bytes}"
            )
        (bundle_dir / bundle_name).write_bytes(encoded)
        # The browser exporter changes only this namespaced extension. Prove
        # here, with the native AX-03 validator itself, that the resulting
        # document remains the bundle that experiment-reproduce accepts.
        browser_export = copy.deepcopy(bundle)
        browser_export["extensions"]["org.atomix.browser-run"] = {
            "schema": {"id": "org.atomix.browser-run", "major": 1, "minor": 0}
        }
        er.validate_bundle(bundle_dir / bundle_name, browser_export)
        outcomes = record["extensions"].get("org.atomix.case-outcomes", {})
        if len(outcomes) != 1:
            raise ValueError(f"{candidate_id}: browser fixture needs exactly one case")
        case, outcome = next(iter(outcomes.items()))
        staged.append({
            "candidate": candidate_id,
            "bundle": f"experiments/{bundle_name}",
            "machine": profile_name,
            "module": f"machines/{machine['module']}",
            "export": machine["export"],
            "target": target["id"],
            "profile": target["profile"]["value"]["config"],
            "profile_sha256": profile_sha256,
            "payload": f"machines/{machines['payload']}",
            "payload_sha256": payload_sha256,
            "case": case,
            "outputs": outcome["outputs"],
            "output_sha256": record["correctness"]["output_sha256"],
            "execute_cycles": measured(record, EXECUTE_CYCLES),
            "total_cycles": measured(record, TOTAL_CYCLES),
            "max_cycles": target["profile"]["value"]["max_cycles"],
        })

    manifest = {
        "schema": SCHEMA,
        "limits": {"max_bundle_bytes": max_bundle_bytes},
        "default": staged[0]["bundle"],
        "experiments": staged,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "handoff.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"browser experiments: {len(staged)} bundles in {output}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--machines", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-bundle-bytes", type=int, required=True)
    args = parser.parse_args()
    try:
        stage(args.plan, args.records, args.machines, args.output,
              args.max_bundle_bytes)
    except (OSError, ValueError, pc.ContractError) as exc:
        print(f"browser experiment stage: FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
