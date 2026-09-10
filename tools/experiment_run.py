#!/usr/bin/env python3
"""Run an experiment plan through its execution adapters and record what happened.

Every candidate produces a record, including the ones that never ran.  A plan
whose FPGA leg is blocked for want of a board and whose native leg passed is a
more useful result than a report that silently contains one row, so blocked,
timed-out, and failed outcomes are written with the same care as passing ones.

Correctness is judged here, not by the adapters: each case's outputs are
compared against the workload's own oracle, so a target cannot mark its own
homework.  A candidate that fails the oracle keeps its measurements in the
record and is excluded from ranking.

Nothing in this file converts between measurement domains.  It aggregates
model cycles as cycles and host elapsed time as nanoseconds, labels each with
the method that produced it, and leaves them separate.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import shutil
import statistics
import subprocess
import time
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import execution
import experiment_contract as ec
import personality_contract as pc
from execution.contract import sha256_json

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECORDS = ROOT / "research" / "experiments" / "records"
DEFAULT_WORK = ROOT / "build" / "experiments"

EVIDENCE_LEVELS = {
    "org.atomix.native-host": "org.atomix.native-execution",
    "org.atomix.rtl-simulator": "org.atomix.simulation",
}


def git(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def source_identity() -> dict[str, Any]:
    commit = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain"))
    diff = git("diff", "HEAD") if dirty else ""
    return {
        "commit": commit or None,
        "dirty": dirty,
        "diff_sha256": execution.contract.sha256_bytes(diff.encode()) if dirty else None,
    }


def logical_work(workload: dict[str, Any], cases: list[dict[str, Any]]) -> int | None:
    """The architecture-neutral work the selected cases represent.

    Each workload defines its own unit; nothing here invents one for a workload
    it does not recognise, because a made-up denominator would silently change
    every derived per-item figure.
    """
    operation = workload["operation"]
    if operation == "org.atomix.workload.saxpy-i32":
        return sum(int(case["parameters"]["items"]) for case in cases)
    if operation == "org.atomix.workload.cpu-perf-mix":
        return sum(
            int({**workload["parameters"], **case["parameters"]}["array_words"])
            for case in cases
        )
    return None


def oracle(workload: dict[str, Any], case: dict[str, Any],
           path: Path) -> tuple[dict[str, Any], str]:
    """What this case must produce, and how strong that claim is."""
    kind = workload["oracle"]["kind"]
    if kind == "org.atomix.builtin-exact":
        parameters = {**workload["parameters"], **case["parameters"]}
        expected = pc.builtin_result(path, workload["operation"], parameters,
                                     case["inputs"])
        return expected, (
            "independent built-in oracle recomputed from the case inputs "
            "(tools/personality_contract.py)"
        )
    if kind == "org.atomix.recorded-exact":
        return case["expected"], (
            "recorded exact value pinned by the workload document; it proves "
            "agreement between implementations, not independent derivation"
        )
    raise ec.pc.ContractError(f"no runner support for oracle kind {kind!r}")


def compare(expected: dict[str, Any], actual: dict[str, list[int]]) -> list[str]:
    problems = []
    for name, wanted in expected.items():
        got = actual.get(name)
        if got is None:
            problems.append(f"{name}: absent")
        elif list(got) != list(wanted):
            problems.append(f"{name}: {got} != {wanted}")
    return problems


def measurement(value: Any, unit: str, status: str, method: str) -> dict[str, Any]:
    return {"value": value, "unit": unit, "status": status, "method": method}


def measurements_for(plan: dict[str, Any], candidate: dict[str, Any],
                     target: dict[str, Any], numbers: dict[str, tuple[float, str]],
                     ) -> dict[str, Any]:
    """Fill the plan's metric set for one candidate, honestly.

    Three outcomes are distinct and stay distinct: a number, a metric nobody
    measured, and a metric this class of target does not have.
    """
    result: dict[str, Any] = {}
    for metric in plan["metrics"]:
        rule = metric["applicability"][target["class"]]
        if rule == "org.atomix.inapplicable":
            result[metric["id"]] = measurement(
                None, metric["unit"], "org.atomix.inapplicable",
                f"{target['class']} has no such quantity",
            )
            continue
        if metric["id"] in numbers:
            value, method = numbers[metric["id"]]
            result[metric["id"]] = measurement(
                value, metric["unit"], "org.atomix.measured", method
            )
        elif rule == "org.atomix.required":
            result[metric["id"]] = measurement(
                None, metric["unit"], "org.atomix.unavailable",
                "this run produced no such measurement",
            )
    return result


def numbers_from(outcomes: list[execution.CaseOutcome],
                 prepared: execution.Prepared,
                 work: int | None) -> dict[str, tuple[float, str]]:
    """Aggregate one run's raw outcomes into plan metrics, per domain."""
    numbers: dict[str, tuple[float, str]] = {}
    if work is not None:
        numbers["org.atomix.metric.work-items"] = (
            work, "logical work of the executed cases, from the workload definition"
        )
    numbers["org.atomix.metric.artifact-bytes"] = (
        prepared.artifact_bytes,
        f"{prepared.detail.get('artifact_kind', 'artifact')}, hashed as "
        f"{prepared.artifact_sha256[:12]}",
    )
    repetitions = {outcome.repetitions for outcome in outcomes}
    if len(repetitions) == 1:
        numbers["org.atomix.metric.repetitions"] = (
            repetitions.pop(), "timed executions of each case"
        )

    if all(outcome.cycles for outcome in outcomes):
        numbers["org.atomix.metric.execute-cycles"] = (
            sum(outcome.cycles["execute"] for outcome in outcomes),
            "model cycles from job start to observed completion, summed over cases",
        )
        numbers["org.atomix.metric.total-cycles"] = (
            sum(outcome.cycles["total"] for outcome in outcomes),
            "model cycles including staging and checked readback, summed over cases",
        )

    if all(outcome.elapsed_ns for outcome in outcomes):
        # Cases differ in size, so pooling their individual times would compare
        # unlike work. Each repetition's total across the case set is one
        # sample of the same quantity, and the distribution is reported rather
        # than reduced to a single number.
        per_repetition = [
            sum(outcome.elapsed_ns[index] for outcome in outcomes)
            for index in range(min(len(outcome.elapsed_ns) for outcome in outcomes))
        ]
        method = (
            f"CLOCK_MONOTONIC around the kernel only, {len(per_repetition)} "
            "repetitions of the whole case set"
        )
        numbers["org.atomix.metric.host-elapsed-median"] = (
            statistics.median(per_repetition), method)
        numbers["org.atomix.metric.host-elapsed-minimum"] = (min(per_repetition), method)
        numbers["org.atomix.metric.host-elapsed-maximum"] = (max(per_repetition), method)

    simulator = [
        outcome.detail["simulator_elapsed_ns"] for outcome in outcomes
        if "simulator_elapsed_ns" in outcome.detail
    ]
    if simulator:
        numbers["org.atomix.metric.simulator-host-elapsed-median"] = (
            statistics.median(simulator),
            "wall time of the simulator process per case; a cost of the tool, "
            "not of the design",
        )
    return numbers


def blank_record(plan: dict[str, Any], candidate: dict[str, Any],
                 target: dict[str, Any], implementation: dict[str, Any],
                 ) -> dict[str, Any]:
    return {
        "schema": {"id": "org.atomix.experiment-record", "major": 1, "minor": 0},
        "kind": "experiment-record",
        "id": f"org.atomix.experiment-record.{candidate['id'].split('.')[-1]}",
        "revision": 1,
        "summary": "",
        "claim": "org.atomix.observation",
        "plan": {"id": plan["id"], "revision": plan["revision"]},
        "candidate": candidate["id"],
        "status": "org.atomix.not-run",
        "identity": {
            "implementation": {
                "id": implementation["id"], "artifact_sha256": None,
                "build_sha256": None, "tools": {},
            },
            "target": {
                "id": target["id"], "class": target["class"],
                "adapter": target["adapter"], "build_sha256": None,
                "profile_sha256": None,
            },
        },
        "execution": {
            "repetitions": 0, "limit_seconds": 0, "elapsed_seconds": 0,
            "termination": "org.atomix.not-run",
        },
        "source": source_identity(),
        "environment": {
            "evidence_level": EVIDENCE_LEVELS.get(target["class"], "org.atomix.simulation"),
            "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(
                timespec="seconds").replace("+00:00", "Z"),
            "board": None,
            "device": platform.machine() if target["class"] == "org.atomix.native-host"
            else None,
            "tools": {},
        },
        "correctness": {
            "status": "org.atomix.not-run", "oracle_cases": 0,
            "output_sha256": None, "method": "not reached",
        },
        "measurements": {},
        "extensions": {},
    }


def blocked_record(record: dict[str, Any], plan: dict[str, Any],
                   target: dict[str, Any], reason: str, kind: str,
                   limit_seconds: int, elapsed: float = 0.0,
                   termination: str = "org.atomix.blocked",
                   ) -> tuple[dict[str, Any], str]:
    record["status"] = ("org.atomix.timeout" if termination == "org.atomix.timeout"
                        else "org.atomix.blocked")
    record["summary"] = f"{target['id']}: {kind}"
    record["execution"].update({
        "limit_seconds": limit_seconds,
        "elapsed_seconds": round(elapsed, 6),
        "termination": termination,
    })
    record["correctness"]["method"] = "the run never reached the oracle"
    record["measurements"] = measurements_for(plan, {"id": record["candidate"]},
                                              target, {})
    record["extensions"]["org.atomix.blocked-reason"] = {"kind": kind, "detail": reason}
    record["extensions"]["org.atomix.reuse"] = {"fingerprint": None, "key": None}
    return record, "org.atomix.attempted"


def reuse_key(plan: dict[str, Any], candidate: dict[str, Any],
              cases: list[dict[str, Any]], repetitions: int,
              identity: dict[str, Any]) -> str:
    """What must be identical for a previous result to still be this result.

    Source commit is deliberately absent and artifact hashes are deliberately
    present: an unrelated commit does not invalidate a binary that hashes the
    same, and a dirty tree that changed the source produces a different hash
    whether or not anyone committed it. Payload identity and machine identity
    are separate entries, so a rebuilt model with an unchanged payload
    invalidates only what it should.
    """
    return sha256_json({
        "plan": [plan["id"], plan["revision"]],
        "candidate": candidate["id"],
        "workload": [
            plan["workload"]["id"], plan["workload"]["revision"],
            [case["name"] for case in cases], plan["workload"]["parameters"],
        ],
        "repetitions": repetitions,
        "implementation": {
            "artifact_sha256": identity["implementation"]["artifact_sha256"],
            "build_sha256": identity["implementation"]["build_sha256"],
            "tools": identity["implementation"]["tools"],
        },
        "target": {
            "id": identity["target"]["id"],
            "adapter": identity["target"]["adapter"],
            "build_sha256": identity["target"]["build_sha256"],
            "profile_sha256": identity["target"]["profile_sha256"],
        },
    })


def reusable(previous: dict[str, Any] | None, field: str, value: Any) -> bool:
    if previous is None or value is None:
        return False
    if previous["status"] not in ec.EXECUTED_STATUS:
        return False
    stored = previous.get("extensions", {}).get("org.atomix.reuse", {})
    return stored.get(field) is not None and stored[field] == value


def run_candidate(plan: dict[str, Any], candidate: dict[str, Any],
                  workload: dict[str, Any], cases: list[dict[str, Any]],
                  workroot: Path, *, limit_seconds: int, repetitions: int,
                  cancel_after: float | None, workload_path: Path,
                  previous: dict[str, Any] | None = None,
                  reuse: bool = False) -> tuple[dict[str, Any], str]:
    target = ec.plan_target(plan, candidate)
    implementation = next(
        item for item in plan["implementations"]
        if item["id"] == candidate["implementation"]
    )
    record = blank_record(plan, candidate, target, implementation)
    adapters = execution.registry()
    adapter = adapters.get(target["adapter"])
    if adapter is None:
        return blocked_record(
            record, plan, target,
            f"no adapter implements {target['adapter']}; refusing to substitute "
            f"another target", "unknown-adapter", limit_seconds,
        )

    workdir = workroot / candidate["id"].split(".")[-1]
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)

    try:
        description = adapter.describe(target)
        adapter.check_compatibility(description, implementation, target)
    except execution.Blocked as exc:
        return blocked_record(record, plan, target, str(exc), "prerequisite",
                              limit_seconds)
    except execution.Unsupported as exc:
        return blocked_record(record, plan, target, str(exc), "unsupported",
                              limit_seconds)
    record["environment"]["tools"].update(description.tools)
    record["extensions"]["org.atomix.target-limits"] = description.limits
    if candidate.get("target_parameters"):
        record["extensions"]["org.atomix.target-parameters"] = \
            dict(candidate["target_parameters"])

    # Cheap reuse first: if the adapter can identify its build inputs without
    # building them, an unchanged sweep point costs nothing at all.
    #
    # The adapter's fingerprint covers what it would build, which is only half
    # of what a result depends on. The run inputs are folded in here, because a
    # binary that hashes the same executed over a different case set or a
    # different repetition count is a different result -- and reusing across
    # that was a real bug, not a hypothetical one.
    fingerprint = adapter.fingerprint(implementation, target, cases)
    digest = sha256_json({
        "build": fingerprint,
        "run": {
            "workload": [plan["workload"]["id"], plan["workload"]["revision"],
                         plan["workload"]["parameters"]],
            "cases": [case["name"] for case in cases],
            "repetitions": repetitions,
            "candidate": candidate["id"],
        },
    }) if fingerprint is not None else None
    if reuse and reusable(previous, "fingerprint", digest):
        return previous, "org.atomix.reused"

    try:
        prepared = adapter.prepare(
            implementation, target, workdir, cases,
            limit_seconds=limit_seconds, cancel_after=cancel_after,
        )
    except execution.Timeout as exc:
        return blocked_record(record, plan, target, str(exc), "build-timeout",
                              limit_seconds, limit_seconds, "org.atomix.timeout")
    except execution.Cancelled as exc:
        return blocked_record(record, plan, target, str(exc), "build-cancelled",
                              limit_seconds, termination="org.atomix.cancelled")
    except execution.Blocked as exc:
        return blocked_record(record, plan, target, str(exc), "prerequisite",
                              limit_seconds)
    except execution.Unsupported as exc:
        return blocked_record(record, plan, target, str(exc), "unsupported",
                              limit_seconds)
    except execution.AdapterError as exc:
        return blocked_record(record, plan, target, str(exc), "build-failed",
                              limit_seconds)

    record["identity"]["implementation"].update({
        "artifact_sha256": prepared.artifact_sha256,
        "build_sha256": prepared.build_sha256,
        "tools": dict(prepared.tools),
    })
    record["identity"]["target"].update({
        "build_sha256": prepared.target_build_sha256,
        "profile_sha256": prepared.profile_sha256,
    })
    record["environment"]["tools"].update(prepared.tools)
    key = reuse_key(plan, candidate, cases, repetitions, record["identity"])
    record["extensions"]["org.atomix.reuse"] = {"fingerprint": digest, "key": key}
    # Second chance: the artifact this run built hashes the same as the one the
    # previous record ran, so the previous result is still this result.
    if reuse and reusable(previous, "key", key):
        return previous, "org.atomix.reused"

    try:
        result = adapter.execute(
            prepared, target, cases, workdir,
            limit_seconds=limit_seconds, repetitions=repetitions,
            cancel_after=cancel_after,
        )
    except execution.Timeout as exc:
        return blocked_record(record, plan, target, str(exc), "execution-timeout",
                              limit_seconds, limit_seconds, "org.atomix.timeout")
    except execution.Cancelled as exc:
        return blocked_record(record, plan, target, str(exc), "execution-cancelled",
                              limit_seconds, termination="org.atomix.cancelled")
    except execution.Blocked as exc:
        return blocked_record(record, plan, target, str(exc), "prerequisite",
                              limit_seconds)
    except execution.Unsupported as exc:
        return blocked_record(record, plan, target, str(exc), "unsupported",
                              limit_seconds)
    except execution.AdapterError as exc:
        return blocked_record(record, plan, target, str(exc), "execution-failed",
                              limit_seconds)

    outcomes = {outcome.name: outcome for outcome in result.cases}
    problems: list[str] = []
    for case in cases:
        outcome = outcomes.get(case["name"])
        if outcome is None:
            problems.append(f"{case['name']}: not executed")
            continue
        expected, method = oracle(workload, case, workload_path)
        problems.extend(
            f"{case['name']} {problem}" for problem in compare(expected, outcome.outputs)
        )
    _, method = oracle(workload, cases[0], workload_path)

    work = logical_work(workload, cases)
    if work is not None and work != candidate["work"]["count"]:
        raise ec.pc.ContractError(
            f"{candidate['id']}: the selected cases are {work} units of work, but the "
            f"candidate declares {candidate['work']['count']}"
        )
    numbers = numbers_from(result.cases, prepared, work)

    record["status"] = "org.atomix.pass" if not problems else "org.atomix.fail"
    record["summary"] = (
        f"{candidate['id'].split('.')[-1]}: {len(cases)} oracle cases "
        f"{'passed' if not problems else 'failed'} on {target['id']}"
    )
    record["execution"].update({
        "repetitions": max(outcome.repetitions for outcome in result.cases),
        "limit_seconds": limit_seconds,
        "elapsed_seconds": round(result.elapsed_seconds, 6),
        "termination": result.termination,
    })
    record["correctness"] = {
        "status": "org.atomix.pass" if not problems else "org.atomix.fail",
        "oracle_cases": len(cases),
        "output_sha256": sha256_json({
            outcome.name: outcome.outputs for outcome in result.cases
        }),
        "method": method,
    }
    record["measurements"] = measurements_for(plan, candidate, target, numbers)
    record["extensions"]["org.atomix.case-outcomes"] = {
        outcome.name: {
            "outputs": outcome.outputs,
            "cycles": outcome.cycles,
            "detail": outcome.detail,
        }
        for outcome in result.cases
    }
    if problems:
        record["extensions"]["org.atomix.oracle-mismatches"] = problems
    return record, "org.atomix.attempted"


def relative(path: Path) -> str:
    """Records may be written outside the tree; say where without pretending."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def write_record(record: dict[str, Any], directory: Path, plan: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record['candidate'].split('.')[-1]}.json"
    if path.is_file():
        existing = json.loads(path.read_text()).get("plan", {})
        if existing.get("id") not in (None, record["plan"]["id"]):
            raise pc.ContractError(
                f"{relative(path)} holds a record from {existing['id']}; two plans "
                "cannot write the same record file"
            )
    # Validate before writing: a record this runner could not itself accept is
    # a bug in the runner, and must not reach the evidence tree.
    ec.validate_record(path, record)
    ec.validate_record_against_plan(path, record, plan)
    path.write_text(json.dumps(record, indent=2, sort_keys=False) + "\n")
    return path


# What each domain is called in a one-line summary. The tag is not decoration:
# a reader who sees "model" and "host" on adjacent rows can tell at a glance
# that the two numbers are not a race, which a bare column of digits cannot.
DOMAIN_TAGS = {
    "org.atomix.domain.model-cycles": "model",
    "org.atomix.domain.host-elapsed": "host",
    "org.atomix.domain.simulator-host-elapsed": "sim-tool",
    "org.atomix.domain.device-resources": "device",
}
HEADLINE = {
    "org.atomix.domain.model-cycles": "org.atomix.metric.execute-cycles",
    "org.atomix.domain.host-elapsed": "org.atomix.metric.host-elapsed-median",
    "org.atomix.domain.simulator-host-elapsed":
        "org.atomix.metric.simulator-host-elapsed-median",
    "org.atomix.domain.device-resources": "org.atomix.metric.lut-used",
}
UNIT_NAMES = {
    "org.atomix.unit.cycle": "cycles",
    "org.atomix.unit.nanosecond": "ns",
    "org.atomix.unit.count": "",
    "org.atomix.unit.byte": "bytes",
    "org.atomix.unit.item": "items",
}


def headline_values(record: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    metrics = {metric["id"]: metric for metric in plan["metrics"]}
    shown = []
    for domain, metric_id in HEADLINE.items():
        metric = metrics.get(metric_id)
        if metric is None:
            continue
        value = record["measurements"].get(metric_id)
        if not value or value["status"] != "org.atomix.measured":
            continue
        unit = UNIT_NAMES.get(metric["unit"], metric["unit"].split(".")[-1])
        shown.append(f"{DOMAIN_TAGS.get(domain, domain)} {value['value']:,.0f} {unit}".strip())
    return shown


def summarise(records: list[dict[str, Any]], plan: dict[str, Any]) -> None:
    print(f"\n{plan['id']} revision {plan['revision']}  [{plan['claim'].split('.')[-1]}]")
    print(f"  {plan['summary']}\n")
    width = max(len(record["candidate"].split(".")[-1]) for record in records)
    domains = set()
    for record in records:
        name = record["candidate"].split(".")[-1]
        status = record["status"].split(".")[-1]
        detail = headline_values(record, plan)
        domains.update(part.split()[0] for part in detail)
        reason = record["extensions"].get("org.atomix.blocked-reason")
        if reason:
            detail.append(f"{reason['kind']}: {reason['detail']}")
        print(f"  {name:<{width}}  {status:<8}  {' | '.join(detail)}")
    if len(domains - {"sim-tool"}) > 1 or "sim-tool" in domains:
        print(
            "\n  These columns are different measurement domains and are not "
            "comparable:\n  model cycles describe the design, host time describes "
            "this machine, and\n  sim-tool time describes Verilator. No ratio "
            "between them means anything."
        )
    print()


def state_file(plan: dict[str, Any]) -> str:
    """One state file per plan, so two plans can share a records directory.

    They do share one in this repository, and a single run-state.json made the
    second plan look like a corrupted resume of the first.
    """
    return f"run-state-{plan['id'].split('.')[-1]}.json"


def new_state(plan: dict[str, Any], budget: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": {"id": "org.atomix.experiment-run-state", "major": 1, "minor": 0},
        "kind": "experiment-run-state",
        "id": f"org.atomix.experiment-run.{plan['id'].split('.')[-1]}",
        "revision": 1,
        "summary": f"What one run of {plan['id']} attempted, reused, and never tried",
        "plan": {"id": plan["id"], "revision": plan["revision"]},
        "budget": budget,
        "candidates": {},
        "extensions": {},
    }


def load_state(records: Path, plan: dict[str, Any]) -> dict[str, Any] | None:
    path = records / state_file(plan)
    if not path.is_file():
        return None
    state = pc.load_document(path)
    ec.validate_run_state(path, state)
    if (state["plan"]["id"], state["plan"]["revision"]) != \
            (plan["id"], plan["revision"]):
        raise pc.ContractError(
            f"{relative(path)} belongs to {state['plan']['id']} revision "
            f"{state['plan']['revision']}; resume the run it came from or choose "
            "another records directory"
        )
    return state


def write_state(records: Path, state: dict[str, Any], plan: dict[str, Any]) -> Path:
    records.mkdir(parents=True, exist_ok=True)
    path = records / state_file(plan)
    ec.validate_run_state(path, state)
    ec.validate_run_state_against_plan(path, state, plan)
    path.write_text(json.dumps(state, indent=2) + "\n")
    return path


def note(state: dict[str, Any], candidate_id: str, status: str, disposition: str,
         record_file: str | None) -> None:
    state["candidates"][candidate_id] = {
        "status": status,
        "disposition": disposition,
        "record": record_file,
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z"),
    }


def settled(entry: dict[str, Any] | None) -> bool:
    """A resumed run keeps every outcome it already has, including the bad ones.

    Re-running a blocked candidate on resume would quietly turn "we could not
    try this" into "we tried this once the tool appeared", which is a different
    experiment. --retry says so explicitly when that is what the user wants.
    """
    return bool(entry) and entry["disposition"] != "org.atomix.not-attempted"


def previous_record(records: Path, entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not entry or not entry.get("record"):
        return None
    path = records / entry["record"]
    if not path.is_file():
        return None
    try:
        record = pc.load_document(path)
        ec.validate_record(path, record)
    except pc.ContractError:
        return None
    return record


# Identities a replay must reproduce exactly before any number is compared.
# If the artifact changed, the two runs are not two observations of one thing.
REPLAY_IDENTITIES = (
    ("implementation", "artifact_sha256", "the implementation artifact"),
    ("implementation", "build_sha256", "the build inputs"),
    ("target", "build_sha256", "the target's built model"),
    ("target", "profile_sha256", "the target's resolved profile"),
)


def replay(plan: dict[str, Any], record_path: Path, args: argparse.Namespace,
           workload: dict[str, Any], cases: list[dict[str, Any]]) -> int:
    """Re-run a recorded candidate and say what did and did not reproduce.

    Deterministic quantities must match exactly: model cycles and oracle
    outputs are properties of the design and the inputs, so a difference is a
    real divergence rather than noise. Host timing is not asserted -- it is a
    distribution measured on a machine that was doing something else the first
    time -- so both runs' numbers are printed and left for the reader.
    """
    original = pc.load_document(record_path)
    ec.validate_record(record_path, original)
    if (original["plan"]["id"], original["plan"]["revision"]) != \
            (plan["id"], plan["revision"]):
        print(f"experiment replay: FAIL: {relative(record_path)} belongs to "
              f"{original['plan']['id']} revision {original['plan']['revision']}",
              file=sys.stderr)
        return 1
    candidate = next(
        (item for item in ec.plan_candidates(plan)
         if item["id"] == original["candidate"]),
        None,
    )
    if candidate is None:
        print(f"experiment replay: FAIL: the plan no longer has candidate "
              f"{original['candidate']}", file=sys.stderr)
        return 1
    if original["status"] not in ec.EXECUTED_STATUS:
        print(f"experiment replay: FAIL: {original['status']} records have nothing "
              "to reproduce", file=sys.stderr)
        return 1

    # A replay always executes: reusing the record it is checking would make
    # the comparison a tautology.
    fresh, _ = run_candidate(
        plan, candidate, workload, cases, args.work,
        limit_seconds=args.limit_seconds or original["execution"]["limit_seconds"],
        repetitions=args.repetitions or plan["budget"]["repetitions"],
        cancel_after=args.cancel_after, workload_path=args.plan,
        previous=None, reuse=False,
    )

    print(f"\nreplay of {original['candidate'].split('.')[-1]} "
          f"from {relative(record_path)}\n")
    problems = []
    for section, field, description in REPLAY_IDENTITIES:
        before = original["identity"][section][field]
        after = fresh["identity"][section][field]
        if before != after:
            problems.append(
                f"{description} changed: {short(before)} -> {short(after)}"
            )
        print(f"  {description:<32} {short(before)} {'==' if before == after else '!='}"
              f" {short(after)}")

    if fresh["status"] not in ec.EXECUTED_STATUS:
        reason = fresh["extensions"].get("org.atomix.blocked-reason", {})
        problems.append(f"the replay did not run: {reason.get('detail', fresh['status'])}")
    else:
        before = original["correctness"]["output_sha256"]
        after = fresh["correctness"]["output_sha256"]
        if before != after:
            problems.append(f"oracle outputs changed: {short(before)} -> {short(after)}")
        print(f"  {'oracle outputs':<32} {short(before)} "
              f"{'==' if before == after else '!='} {short(after)}")

        domains = {metric["id"]: metric["domain"] for metric in plan["metrics"]}
        print()
        for metric_id, domain in domains.items():
            first = original["measurements"].get(metric_id, {})
            second = fresh["measurements"].get(metric_id, {})
            if first.get("status") != "org.atomix.measured" or \
                    second.get("status") != "org.atomix.measured":
                continue
            name = metric_id.split("metric.")[-1]
            # Model cycles and context values are properties of the design
            # and the inputs, so they must reproduce. Elapsed times are
            # properties of a machine that had other work to do.
            deterministic = domain in {
                "org.atomix.domain.model-cycles", "org.atomix.domain.context",
            }
            if deterministic and first["value"] != second["value"]:
                problems.append(
                    f"{name} is deterministic but changed: "
                    f"{first['value']} -> {second['value']}"
                )
            marker = "==" if first["value"] == second["value"] else (
                "!=" if deterministic else "~"
            )
            print(f"  {name:<32} {first['value']:>14,.0f} {marker} "
                  f"{second['value']:>14,.0f}"
                  + ("" if deterministic else "   (elapsed time: reported, not asserted)"))

    if problems:
        print("\nexperiment replay: FAIL")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nexperiment replay: PASS (identity, oracle outputs, and deterministic "
          "cycles reproduced)")
    return 0


def short(digest: Any) -> str:
    if not isinstance(digest, str):
        return "none"
    return digest[:12]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path, help="experiment plan to run")
    parser.add_argument("--records", type=Path, default=DEFAULT_RECORDS)
    parser.add_argument("--work", type=Path, default=DEFAULT_WORK)
    parser.add_argument("--only", action="append", default=[],
                        help="run only these candidate IDs (or ID suffixes)")
    parser.add_argument("--limit-seconds", type=int, default=None,
                        help="override the plan's per-candidate time limit")
    parser.add_argument("--repetitions", type=int, default=None,
                        help="override the plan's repetition budget")
    parser.add_argument("--cancel-after", type=float, default=None,
                        help="cancel each adapter command after this many seconds")
    parser.add_argument("--personality-root", type=Path,
                        default=ec.DEFAULT_PERSONALITIES)
    parser.add_argument("--replay", type=Path, default=None,
                        help="re-run one recorded candidate and compare identities")
    parser.add_argument("--resume", action="store_true",
                        help="keep outcomes already recorded in the run state")
    parser.add_argument("--retry", action="append", default=[],
                        help="with --resume, attempt these candidates again")
    parser.add_argument("--max-candidates", type=int, default=None,
                        help="evaluate at most this many candidates this run")
    parser.add_argument("--budget-seconds", type=int, default=None,
                        help="stop starting candidates after this much wall time")
    parser.add_argument("--no-reuse", action="store_true",
                        help="re-execute even when a recorded result is still current")
    args = parser.parse_args()

    try:
        plan = pc.load_document(args.plan)
        ec.validate_plan(args.plan, plan)
        workloads = ec.workload_documents(args.personality_root)
        ec.validate_plan_workload(args.plan, plan, workloads)
    except pc.ContractError as exc:
        print(f"experiment run: FAIL: {exc}", file=sys.stderr)
        return 1

    reference = (plan["workload"]["id"], plan["workload"]["revision"])
    workload = workloads[reference]
    workload_path = args.plan
    selected = set(plan["workload"]["cases"])
    cases = [case for case in workload["cases"] if case["name"] in selected]

    limit_seconds = args.limit_seconds or plan["budget"]["per_candidate_seconds"]
    repetitions = args.repetitions or plan["budget"]["repetitions"]
    bound = min(args.max_candidates or plan["budget"]["max_candidates"],
                plan["budget"]["max_candidates"])
    candidates = ec.plan_candidates(plan)
    if args.only:
        candidates = [
            candidate for candidate in candidates
            if candidate["id"] in args.only or
            candidate["id"].split(".")[-1] in args.only or
            candidate["id"].rsplit(".", 1)[-1].startswith(tuple(args.only))
        ]
        if not candidates:
            print("experiment run: FAIL: no candidate matched --only", file=sys.stderr)
            return 1

    if args.replay:
        try:
            return replay(plan, args.replay, args, workload, cases)
        except pc.ContractError as exc:
            print(f"experiment replay: FAIL: {exc}", file=sys.stderr)
            return 1

    budget = {
        "max_candidates": bound,
        "per_candidate_seconds": limit_seconds,
        "repetitions": repetitions,
        "run_seconds": args.budget_seconds,
    }
    # The state on disk is read whether or not this run resumes: reuse and
    # resume are different questions. Reuse asks whether identical work was
    # already done; resume asks whether an outcome -- including a blocked or
    # failed one -- should stand without being attempted again.
    try:
        recorded = load_state(args.records, plan)
    except pc.ContractError as exc:
        print(f"experiment run: FAIL: {exc}", file=sys.stderr)
        return 1
    state = recorded if args.resume and recorded else new_state(plan, budget)
    state["budget"] = budget
    history = (recorded or {}).get("candidates", {})

    records: list[dict[str, Any]] = []
    attempted = 0
    interrupted = False
    deadline = (time.monotonic() + args.budget_seconds) if args.budget_seconds else None
    try:
        for candidate in candidates:
            entry = history.get(candidate["id"])
            retry = candidate["id"] in args.retry or \
                candidate["id"].split(".")[-1] in args.retry
            if args.resume and settled(entry) and not retry:
                kept = previous_record(args.records, entry)
                if kept is not None:
                    records.append(kept)
                print(f"  kept {candidate['id'].split('.')[-1]} "
                      f"({entry['status'].split('.')[-1]}, from the previous run)")
                continue
            if attempted >= bound:
                note(state, candidate["id"], "org.atomix.not-run",
                     "org.atomix.not-attempted", None)
                continue
            if deadline is not None and time.monotonic() >= deadline:
                note(state, candidate["id"], "org.atomix.not-run",
                     "org.atomix.not-attempted", None)
                continue

            record, disposition = run_candidate(
                plan, candidate, workload, cases, args.work,
                limit_seconds=limit_seconds, repetitions=repetitions,
                cancel_after=args.cancel_after, workload_path=workload_path,
                previous=previous_record(args.records, entry),
                reuse=not args.no_reuse,
            )
            attempted += 1
            path = write_record(record, args.records, plan)
            note(state, candidate["id"], record["status"], disposition, path.name)
            records.append(record)
            verb = "reused" if disposition == "org.atomix.reused" else "wrote"
            print(f"  {verb} {relative(path)}")
            write_state(args.records, state, plan)
    except KeyboardInterrupt:
        interrupted = True
        print("\n  interrupted; the candidates not yet attempted stay not-run")
    except pc.ContractError as exc:
        print(f"experiment run: FAIL: {exc}", file=sys.stderr)
        return 1

    for candidate in candidates:
        state["candidates"].setdefault(candidate["id"], {
            "status": "org.atomix.not-run",
            "disposition": "org.atomix.not-attempted",
            "record": None,
            "timestamp_utc": None,
        })
    try:
        state_file = write_state(args.records, state, plan)
    except pc.ContractError as exc:
        print(f"experiment run: FAIL: {exc}", file=sys.stderr)
        return 1

    if records:
        summarise(records, plan)
    not_attempted = [
        name for name, entry in state["candidates"].items()
        if entry["disposition"] == "org.atomix.not-attempted"
    ]
    if not_attempted:
        print(f"  {len(not_attempted)} candidate(s) never attempted; "
              f"{relative(state_file)} keeps them as not-run:")
        for name in not_attempted:
            print(f"    {name.split('.')[-1]}")
        print()
    if interrupted:
        return 130
    failed = [record for record in records if record["status"] == "org.atomix.fail"]
    if failed:
        print(f"experiment run: {len(failed)} candidate(s) failed the oracle")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
