#!/usr/bin/env python3
"""Render, export, and reproduce an experiment's result.

This is the part a user reads, so its job is to be honest before it is
convenient.  Three rules shape everything below.

A table is per measurement domain, never across them.  Model cycles, host
elapsed time, and simulator wall time are separate quantities, and there is no
arithmetic anywhere in this file that turns one into another.  Candidates that
cannot appear in a domain's table are listed under it with the reason -- the
metric does not apply to that class of target, or nobody measured it.

Correctness is a gate, not a column.  A candidate that failed its oracle keeps
its measurements in the record and never enters a table.  A candidate that was
never attempted is named as never attempted, because a report that shows only
what ran cannot be read as a survey of what was tried.

A missing measurement never satisfies a constraint.  Asking for "under 300
cycles" returns the candidates known to be under 300 cycles, and separately the
candidates about which there is no evidence.  Silence is not a pass.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import experiment_contract as ec
import personality_contract as pc
from execution.contract import ROOT, sha256_file, sha256_json

DEFAULT_RECORDS = ROOT / "research" / "experiments" / "records"
CONSTRAINT = re.compile(r"^([A-Za-z0-9_.\-]+)\s*(<=|>=|<|>)\s*([0-9.]+)$")

DOMAIN_TITLES = {
    "org.atomix.domain.model-cycles":
        "Model cycles (deterministic; a property of the design, not of any clock)",
    "org.atomix.domain.host-elapsed":
        "Host elapsed time (this machine, this run; a measured distribution)",
    "org.atomix.domain.simulator-host-elapsed":
        "Simulator wall time (a cost of Verilator, not of the design)",
    "org.atomix.domain.device-resources":
        "Device resources (only at an evidence level that can contain them)",
}
CLAIM_TITLES = {
    "org.atomix.same-artifact":
        "Same artifact, different machines: what the machine choice was worth",
    "org.atomix.same-workload":
        "Same workload, different implementations: what the implementation "
        "choice was worth",
}


def load_plan(path: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    plan = pc.load_document(path)
    ec.validate_plan(path, plan)
    workloads = ec.workload_documents(ec.DEFAULT_PERSONALITIES)
    ec.validate_plan_workload(path, plan, workloads)
    workload = workloads[(plan["workload"]["id"], plan["workload"]["revision"])]
    selected = set(plan["workload"]["cases"])
    cases = [case for case in workload["cases"] if case["name"] in selected]
    return plan, workload, cases


def load_records(records: Path, plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    if not records.is_dir():
        return found
    for path in sorted(records.glob("*.json")):
        document = pc.load_document(path)
        if document.get("kind") != "experiment-record":
            continue
        if (document["plan"]["id"], document["plan"]["revision"]) != \
                (plan["id"], plan["revision"]):
            continue
        ec.validate_record(path, document)
        ec.validate_record_against_plan(path, document, plan)
        found[document["candidate"]] = document
    return found


def load_state(records: Path, plan: dict[str, Any]) -> dict[str, Any]:
    path = records / f"run-state-{plan['id'].split('.')[-1]}.json"
    if not path.is_file():
        return {}
    state = pc.load_document(path)
    ec.validate_run_state(path, state)
    return state.get("candidates", {})


def short(name: str) -> str:
    return name.split(".")[-1]


def measured(record: dict[str, Any], metric_id: str) -> float | None:
    value = record["measurements"].get(metric_id)
    if not value or value["status"] != "org.atomix.measured":
        return None
    return value["value"]


def rankable_metrics(plan: dict[str, Any], domain: str) -> list[dict[str, Any]]:
    """Metrics in this domain that express a preference.

    Context metrics are excluded from domination on purpose: an artifact that
    is smaller is not thereby better, and a work count is not a score.
    """
    return [
        metric for metric in plan["metrics"]
        if metric["domain"] == domain and
        metric["direction"] in {"org.atomix.lower-is-better", "org.atomix.higher-is-better"}
    ]


def dominates(first: dict[str, float], second: dict[str, float],
              metrics: list[dict[str, Any]]) -> bool:
    """True when `first` is at least as good everywhere and better somewhere."""
    better_somewhere = False
    for metric in metrics:
        a, b = first[metric["id"]], second[metric["id"]]
        lower_is_better = metric["direction"] == "org.atomix.lower-is-better"
        if (a > b) if lower_is_better else (a < b):
            return False
        if a != b:
            better_somewhere = True
    return better_somewhere


def pareto(rows: dict[str, dict[str, float]],
           metrics: list[dict[str, Any]]) -> set[str]:
    return {
        name for name, values in rows.items()
        if not any(dominates(other, values, metrics)
                   for key, other in rows.items() if key != name)
    }


def parse_constraint(text: str) -> tuple[str, str, float]:
    match = CONSTRAINT.fullmatch(text.strip())
    if not match:
        raise pc.ContractError(
            f"constraint {text!r} must look like org.atomix.metric.execute-cycles<=300"
        )
    return match.group(1), match.group(2), float(match.group(3))


def satisfies(value: float, operator: str, bound: float) -> bool:
    return {
        "<=": value <= bound, "<": value < bound,
        ">=": value >= bound, ">": value > bound,
    }[operator]


def eligibility(plan: dict[str, Any], records: dict[str, dict[str, Any]],
                state: dict[str, Any]) -> tuple[list[str], list[tuple[str, str]]]:
    """Split the plan's candidates into rankable ones and everything else."""
    eligible, excluded = [], []
    for candidate in ec.plan_candidates(plan):
        name = candidate["id"]
        record = records.get(name)
        if record is None:
            entry = state.get(name, {})
            if entry.get("disposition") == "org.atomix.not-attempted":
                excluded.append((name, "never attempted; bounded out or not reached"))
            else:
                excluded.append((name, "no record"))
            continue
        if ec.is_rankable(record):
            eligible.append(name)
            continue
        if record["status"] == "org.atomix.fail":
            mismatches = record["extensions"].get("org.atomix.oracle-mismatches", [])
            detail = mismatches[0] if mismatches else "oracle failed"
            excluded.append((name, f"failed the oracle: {detail}"))
        else:
            reason = record["extensions"].get("org.atomix.blocked-reason", {})
            excluded.append((
                name,
                f"{short(record['status'])}: {reason.get('kind', 'no reason recorded')}"
                + (f" -- {reason['detail']}" if reason.get("detail") else ""),
            ))
    return eligible, excluded


def render(plan: dict[str, Any], workload: dict[str, Any], cases: list[dict[str, Any]],
           records: dict[str, dict[str, Any]], state: dict[str, Any],
           constraints: list[str]) -> int:
    print(f"\n{plan['id']} revision {plan['revision']}")
    print(f"  {plan['summary']}")
    print(f"\n  {CLAIM_TITLES.get(plan['claim'], plan['claim'])}")
    print(f"  Workload {workload['id']} revision {workload['revision']}, "
          f"{len(cases)} case(s): {', '.join(case['name'] for case in cases)}")
    print(f"  Oracle: {workload['oracle']['kind']}")

    eligible, excluded = eligibility(plan, records, state)
    print(f"\n  Eligible for comparison: {len(eligible)} of "
          f"{len(ec.plan_candidates(plan))} candidates")
    for name, reason in excluded:
        print(f"    excluded  {short(name):<24} {reason}")

    if not eligible:
        print("\n  Nothing passed its oracle, so there is nothing to compare.\n")
        return 1

    print("\n  Identity")
    print(f"    {'candidate':<24} {'payload':<14} {'machine':<14} {'profile':<14} tools")
    for name in eligible:
        identity = records[name]["identity"]
        tools = ", ".join(sorted(records[name]["environment"]["tools"].values()))
        print(f"    {short(name):<24} "
              f"{(identity['implementation']['artifact_sha256'] or 'none')[:12]:<14} "
              f"{(identity['target']['build_sha256'] or 'none')[:12]:<14} "
              f"{(identity['target']['profile_sha256'] or 'none')[:12]:<14} "
              f"{tools[:60]}")

    domains = sorted({metric["domain"] for metric in plan["metrics"]})
    compared: set[str] = set()
    for domain in domains:
        metrics = rankable_metrics(plan, domain)
        if not metrics:
            continue
        rows: dict[str, dict[str, float]] = {}
        absent: list[tuple[str, str]] = []
        for name in eligible:
            values = {metric["id"]: measured(records[name], metric["id"])
                      for metric in metrics}
            if all(value is not None for value in values.values()):
                rows[name] = values
                continue
            missing = next(metric for metric in metrics if values[metric["id"]] is None)
            entry = records[name]["measurements"].get(missing["id"])
            absent.append((
                name,
                "does not apply to this target"
                if entry and entry["status"] == "org.atomix.inapplicable"
                else f"no measurement of {short(missing['id'])}",
            ))
        print(f"\n  {DOMAIN_TITLES.get(domain, domain)}")
        if not rows and len(absent) == len(eligible) and all(
                reason.startswith("does not apply") for _, reason in absent):
            # Every candidate lacks the quantity by nature, not by omission.
            # Naming them one by one would bury the single fact that matters.
            print("    no candidate in this plan is a target that has such a "
                  "quantity")
            continue
        if not rows:
            print("    no candidate has evidence in this domain")
        else:
            front = pareto(rows, metrics)
            header = "    " + f"{'candidate':<24}" + "".join(
                f"{short(metric['id']):>22}" for metric in metrics
            ) + "   pareto"
            print(header)
            for name in sorted(rows, key=lambda key: [rows[key][m["id"]] for m in metrics]):
                cells = "".join(f"{rows[name][metric['id']]:>22,.0f}" for metric in metrics)
                print(f"    {short(name):<24}{cells}   "
                      f"{'*' if name in front else ''}")
            compared.update(rows)
        for name, reason in absent:
            print(f"    {short(name):<24} -- {reason}")

    ranked_domains = [
        domain for domain in domains
        if rankable_metrics(plan, domain) and any(
            all(measured(records[name], metric["id"]) is not None
                for metric in rankable_metrics(plan, domain))
            for name in eligible
        )
    ]
    if len(ranked_domains) > 1:
        print("\n  Not comparable across the tables above:")
        for domain in ranked_domains:
            print(f"    {short(domain)}")
        print("    These are different quantities measured by different means. No\n"
              "    row above may be divided by a row under another heading, and this\n"
              "    report deliberately computes no combined score.")

    context = [
        metric for metric in plan["metrics"]
        if metric["direction"] == "org.atomix.context-only" and any(
            measured(records[name], metric["id"]) is not None for name in eligible
        )
    ]
    if context:
        print("\n  Context, which is recorded but not ranked")
        print("    " + f"{'candidate':<24}" + "".join(
            f"{short(metric['id']):>30}" for metric in context
        ))
        for name in eligible:
            cells = ""
            for metric in context:
                value = measured(records[name], metric["id"])
                cells += f"{'--' if value is None else format(value, ',.0f'):>30}"
            print(f"    {short(name):<24}{cells}")

    print("\n  How each number was measured")
    seen: dict[str, str] = {}
    for name in eligible:
        for metric_id, value in records[name]["measurements"].items():
            if value["status"] == "org.atomix.measured":
                seen.setdefault(metric_id, value["method"])
    for metric_id, method in sorted(seen.items()):
        print(f"    {short(metric_id):<32} {method}")

    status = 0
    if constraints:
        print("\n  Constraints")
        for text in constraints:
            metric_id, operator, bound = parse_constraint(text)
            metric = next(
                (item for item in plan["metrics"] if item["id"] == metric_id), None
            )
            if metric is None:
                print(f"    {text}: the plan declares no such metric")
                status = 1
                continue
            qualifying, failing, unknown = [], [], []
            for name in eligible:
                value = measured(records[name], metric_id)
                if value is None:
                    entry = records[name]["measurements"].get(metric_id)
                    unknown.append((
                        name,
                        "inapplicable to this target"
                        if entry and entry["status"] == "org.atomix.inapplicable"
                        else "unmeasured",
                    ))
                elif satisfies(value, operator, bound):
                    qualifying.append((name, value))
                else:
                    failing.append((name, value))
            print(f"    {text}")
            for name, value in sorted(qualifying, key=lambda row: row[1]):
                print(f"      qualifies   {short(name):<24} {value:,.0f}")
            for name, value in sorted(failing, key=lambda row: row[1]):
                print(f"      outside     {short(name):<24} {value:,.0f}")
            for name, reason in unknown:
                print(f"      no evidence {short(name):<24} {reason}")
            if not qualifying:
                print("      nothing qualifies on the evidence available")
                status = 1
    print()
    return status


def input_files(implementation: dict[str, Any],
                target: dict[str, Any]) -> dict[str, str]:
    """The declared inputs a reader must have before rebuilding, and their hashes.

    Only files the plan itself names are listed. What the adapter reaches for
    on its own -- its harness, its build rules -- is covered by the target's
    build hash instead, which is why the bundle checks that too.
    """
    files: dict[str, str] = {}

    def add(name: Any) -> None:
        if isinstance(name, str) and (ROOT / name).is_file():
            files[name] = sha256_file(ROOT / name)

    build = implementation["build"]["value"]
    for key in ("source", "encoder", "artifact"):
        add(build.get(key))
    profile = target.get("profile")
    if profile:
        value = profile["value"]
        for key in ("component", "config"):
            add(value.get(key))
        manifest = value.get("component")
        if isinstance(manifest, str) and (ROOT / manifest).is_file():
            for source in json.loads((ROOT / manifest).read_text()).get("sources", []):
                add(source)
    return dict(sorted(files.items()))


def bundle_for(plan: dict[str, Any], workload: dict[str, Any],
               record: dict[str, Any], plan_path: Path) -> dict[str, Any]:
    """A self-contained description of one result, and how to get it again."""
    candidate = next(
        item for item in ec.plan_candidates(plan) if item["id"] == record["candidate"]
    )
    implementation = next(
        item for item in plan["implementations"]
        if item["id"] == candidate["implementation"]
    )
    deterministic = {
        metric["id"]: record["measurements"][metric["id"]]["value"]
        for metric in plan["metrics"]
        if metric["domain"] in {"org.atomix.domain.model-cycles",
                                "org.atomix.domain.context"} and
        record["measurements"].get(metric["id"], {}).get("status") ==
        "org.atomix.measured"
    }
    sources = input_files(implementation, ec.plan_target(plan, candidate))
    return {
        "schema": {"id": "org.atomix.experiment-bundle", "major": 1, "minor": 0},
        "kind": "experiment-bundle",
        "id": f"org.atomix.experiment-bundle.{short(record['candidate'])}",
        "revision": 1,
        "summary": f"One reproducible result: {record['summary']}",
        "exported_utc": dt.datetime.now(dt.timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z"),
        "plan": plan,
        "plan_path": relative_to_root(plan_path),
        "workload": workload,
        "record": record,
        "retrieval": {
            "repository_commit": record["source"]["commit"],
            "dirty": record["source"]["dirty"],
            "diff_sha256": record["source"]["diff_sha256"],
            "inputs": sources,
            "note": "Check out the commit, apply the recorded diff if the tree was "
                    "dirty, and confirm each input hash before rebuilding.",
        },
        "reproduce": {
            "command": [
                "python3", "tools/experiment_report.py", "reproduce", "<this file>",
            ],
            "expects": {
                "oracle_output_sha256": record["correctness"]["output_sha256"],
                "evidence_level": record["environment"]["evidence_level"],
                "identity": record["identity"],
                "deterministic": deterministic,
            },
        },
        "extensions": {},
    }


def relative_to_root(path: Path) -> str:
    resolved = path.resolve()
    return str(resolved.relative_to(ROOT)) if resolved.is_relative_to(ROOT) \
        else str(resolved)


def validate_bundle(path: Path, bundle: dict[str, Any]) -> None:
    required = {
        "schema", "kind", "id", "revision", "summary", "exported_utc", "plan",
        "plan_path", "workload", "record", "retrieval", "reproduce", "extensions",
    }
    pc.exact_keys(path, bundle, "experiment bundle", required)
    if bundle["kind"] != "experiment-bundle":
        raise pc.error(path, "kind must be 'experiment-bundle'")
    pc.common(path, bundle, "org.atomix.experiment-bundle")
    ec.validate_plan(path, bundle["plan"])
    pc.validate_document(path, bundle["workload"])
    ec.validate_record(path, bundle["record"])
    ec.validate_record_against_plan(path, bundle["record"], bundle["plan"])
    if bundle["record"]["status"] not in ec.EXECUTED_STATUS:
        raise pc.error(path, "a bundle exports a result, and this record has none")


def reproduce(path: Path, work: Path, records: Path) -> int:
    """Rebuild a bundle's candidate and check what must not have changed."""
    bundle = pc.load_document(path)
    validate_bundle(path, bundle)
    record = bundle["record"]
    expects = bundle["reproduce"]["expects"]
    print(f"\nreproducing {short(record['candidate'])} from {path.name}\n")

    problems = []
    for name, digest in bundle["retrieval"]["inputs"].items():
        source = ROOT / name
        if not source.is_file():
            problems.append(f"input {name} is missing from this checkout")
            print(f"  {name:<44} missing")
            continue
        actual = sha256_file(source)
        print(f"  {name:<44} {digest[:12]} "
              f"{'==' if actual == digest else '!='} {actual[:12]}")
        if actual != digest:
            problems.append(f"input {name} has changed since the bundle was exported")
    if problems:
        print("\nexperiment reproduce: FAIL")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    # The run itself goes through the ordinary runner, so a bundle cannot be
    # reproduced by a code path that only bundles use.
    command = [
        sys.executable, str(ROOT / "tools" / "experiment_run.py"),
        str(ROOT / bundle["plan_path"]), "--only", record["candidate"],
        "--records", str(records), "--work", str(work), "--no-reuse",
    ]
    import subprocess  # local: reproduction is the only place this file shells out
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    fresh_path = records / f"{short(record['candidate'])}.json"
    if not fresh_path.is_file():
        print(completed.stdout)
        print(completed.stderr, file=sys.stderr)
        print("\nexperiment reproduce: FAIL\n  - the run produced no record")
        return 1
    fresh = pc.load_document(fresh_path)

    if fresh["environment"]["evidence_level"] != expects["evidence_level"]:
        print(f"\nexperiment reproduce: FAIL\n  - this run is "
              f"{fresh['environment']['evidence_level']} evidence, and the bundle "
              f"records {expects['evidence_level']}; they are not interchangeable")
        return 1

    for section, field in (("implementation", "artifact_sha256"),
                           ("target", "build_sha256")):
        before = expects["identity"][section][field]
        after = fresh["identity"][section][field]
        print(f"  {section + '.' + field:<44} {short_digest(before)} "
              f"{'==' if before == after else '!='} {short_digest(after)}")
        if before != after:
            problems.append(f"{section} {field} differs from the bundle")

    before = expects["oracle_output_sha256"]
    after = fresh["correctness"]["output_sha256"]
    print(f"  {'oracle outputs':<44} {short_digest(before)} "
          f"{'==' if before == after else '!='} {short_digest(after)}")
    if before != after:
        problems.append("the oracle outputs differ from the bundle")
    if fresh["correctness"]["status"] != "org.atomix.pass":
        problems.append("the reproduced run did not pass its oracle")

    print()
    for metric_id, value in sorted(expects["deterministic"].items()):
        actual = measured(fresh, metric_id)
        print(f"  {short(metric_id):<44} {value:>12,.0f} "
              f"{'==' if actual == value else '!='} "
              f"{actual if actual is None else format(actual, ',.0f'):>12}")
        if actual != value:
            problems.append(f"{short(metric_id)} is deterministic but differs")

    if problems:
        print("\nexperiment reproduce: FAIL")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nexperiment reproduce: PASS (inputs, identity, oracle outputs, and "
          "deterministic values all reproduced)")
    return 0


def short_digest(value: Any) -> str:
    return value[:12] if isinstance(value, str) else "none"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    show = subparsers.add_parser("render", help="print the comparison")
    show.add_argument("plan", type=Path)
    show.add_argument("--records", type=Path, default=DEFAULT_RECORDS)
    show.add_argument("--constraint", action="append", default=[],
                      help="e.g. org.atomix.metric.execute-cycles<=300")

    export = subparsers.add_parser("export", help="write a self-contained result")
    export.add_argument("plan", type=Path)
    export.add_argument("candidate")
    export.add_argument("--records", type=Path, default=DEFAULT_RECORDS)
    export.add_argument("--output", type=Path, required=True)

    again = subparsers.add_parser("reproduce", help="rebuild and check a bundle")
    again.add_argument("bundle", type=Path)
    again.add_argument("--work", type=Path, default=ROOT / "build" / "experiments")
    again.add_argument("--records", type=Path,
                       default=ROOT / "build" / "experiments" / "reproduced")

    args = parser.parse_args()
    try:
        if args.command == "reproduce":
            return reproduce(args.bundle, args.work, args.records)
        plan, workload, cases = load_plan(args.plan)
        records = load_records(args.records, plan)
        if args.command == "render":
            return render(plan, workload, cases, records,
                          load_state(args.records, plan), args.constraint)
        candidate = next(
            (name for name in records
             if name == args.candidate or short(name) == args.candidate), None
        )
        if candidate is None:
            raise pc.ContractError(f"no record for candidate {args.candidate!r}")
        bundle = bundle_for(plan, workload, records[candidate], args.plan)
        validate_bundle(args.output, bundle)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(bundle, indent=2) + "\n")
        print(f"wrote {args.output} ({sha256_json(bundle)[:12]})")
        return 0
    except pc.ContractError as exc:
        print(f"experiment report: FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
