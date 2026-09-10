#!/usr/bin/env python3
"""Validate atomiX experiment plans and the records their runs produce.

An experiment plan is the versioned input a user brings to the platform.  It
names one workload and oracle, the cases to run, the implementations that
claim to satisfy it, the execution targets that can host them, the metrics
each target class is able to produce, and the budget a run may spend.

It is a sibling of the R2 comparison plan rather than a replacement for it.
The comparison contract answers "what did this FPGA implementation cost for
this workload" and requires the full FPGA metric matrix from every record.
That matrix is wrong for a host process: a native executable has no LUT count,
no configuration transfer, and no role transition.  Rather than let a host
candidate invent those fields to satisfy a schema, this contract makes metric
applicability explicit per target class, and keeps measurements in separate
domains so that host elapsed time is never ranked against simulator wall time
or modelled cycles.  Existing comparison documents keep their own validator
unchanged; `tools/comparison_contract.py` still owns them.
"""

from __future__ import annotations

import argparse
import copy
import itertools
import sys
from pathlib import Path
from typing import Any

import comparison_contract as cc
import personality_contract as pc


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "research" / "experiments"
DEFAULT_PERSONALITIES = ROOT / "research" / "personalities"

# A plan states which of two claims it makes.  They are not interchangeable:
# the first isolates the machine, the second isolates the implementation, and
# a report that mixes them is answering neither question.
CLAIM_SAME_ARTIFACT = "org.atomix.same-artifact"
CLAIM_SAME_WORKLOAD = "org.atomix.same-workload"
PLAN_CLAIMS = {CLAIM_SAME_ARTIFACT, CLAIM_SAME_WORKLOAD}

# Metric applicability per target class.  `inapplicable` is not a polite way of
# saying zero or unknown: it says the target has no such quantity at all.
APPLICABILITY = {
    "org.atomix.required", "org.atomix.optional", "org.atomix.inapplicable",
}
MEASUREMENT_STATUS = {
    "org.atomix.measured", "org.atomix.unavailable", "org.atomix.inapplicable",
}

# Context metrics describe a run without belonging to a comparable quantity, so
# any target may be asked for them.  Every other metric belongs to exactly one
# measurement domain, and a target may only be required to produce metrics in
# its own domain.
CONTEXT_DOMAIN = "org.atomix.domain.context"

RECORD_STATUS = {
    "org.atomix.pass", "org.atomix.fail", "org.atomix.blocked",
    "org.atomix.timeout", "org.atomix.not-run",
}
EXECUTED_STATUS = {"org.atomix.pass", "org.atomix.fail"}
TERMINATION = {
    "org.atomix.completed", "org.atomix.timeout", "org.atomix.cancelled",
    "org.atomix.blocked", "org.atomix.not-run",
}

# A resource claim needs evidence that could contain one.  Simulation cannot
# report a LUT count, so a record that carries one at simulation level is
# rejected rather than believed.
RESOURCE_METRICS = {
    "org.atomix.metric.lut-used", "org.atomix.metric.lut-available",
    "org.atomix.metric.flip-flop-used", "org.atomix.metric.flip-flop-available",
    "org.atomix.metric.block-ram-bits-used",
    "org.atomix.metric.block-ram-bits-available",
    "org.atomix.metric.dsp-used", "org.atomix.metric.dsp-available",
    "org.atomix.metric.maximum-frequency",
}
RESOURCE_EVIDENCE_LEVELS = {
    "org.atomix.synthesis", "org.atomix.place-and-route",
}
RESOURCE_EVIDENCE_PREFIX = "org.atomix.physical"

WORK_ITEMS = "org.atomix.metric.work-items"
EXECUTE_CYCLES = "org.atomix.metric.execute-cycles"
TOTAL_CYCLES = "org.atomix.metric.total-cycles"


def positive_int(path: Path, value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise pc.error(path, f"{name} must be a positive integer")
    return value


def nonnegative_number(path: Path, value: Any, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise pc.error(path, f"{name} must be a non-negative number")
    return value


def opaque_selector(path: Path, value: Any, name: str) -> None:
    """A build or profile selector belongs to its `kind`, not to this schema."""
    selector = pc.object_value(path, value, name)
    pc.exact_keys(path, selector, name, {"kind", "value"})
    pc.namespaced(path, selector["kind"], f"{name}.kind")


def validate_implementation(path: Path, value: Any, index: int) -> dict[str, Any]:
    name = f"implementations[{index}]"
    implementation = pc.object_value(path, value, name)
    pc.exact_keys(
        path, implementation, name,
        {"id", "class", "summary", "requires", "build"},
    )
    pc.namespaced(path, implementation["id"], f"{name}.id")
    pc.namespaced(path, implementation["class"], f"{name}.class")
    if not isinstance(implementation["summary"], str) or \
            not implementation["summary"].strip():
        raise pc.error(path, f"{name}.summary must be non-empty")
    pc.namespaced_list(path, implementation["requires"], f"{name}.requires")
    opaque_selector(path, implementation["build"], f"{name}.build")
    return implementation


def validate_target(path: Path, value: Any, index: int) -> dict[str, Any]:
    name = f"targets[{index}]"
    target = pc.object_value(path, value, name)
    pc.exact_keys(
        path, target, name,
        {"id", "class", "adapter", "summary", "capabilities",
         "measurement_domain", "profile"},
    )
    pc.namespaced(path, target["id"], f"{name}.id")
    pc.namespaced(path, target["class"], f"{name}.class")
    pc.namespaced(path, target["adapter"], f"{name}.adapter")
    if not isinstance(target["summary"], str) or not target["summary"].strip():
        raise pc.error(path, f"{name}.summary must be non-empty")
    pc.namespaced_list(path, target["capabilities"], f"{name}.capabilities")
    domain = pc.namespaced(
        path, target["measurement_domain"], f"{name}.measurement_domain"
    )
    if domain == CONTEXT_DOMAIN:
        raise pc.error(
            path, f"{name}.measurement_domain cannot be the context domain; a "
            "target must name the domain its own measurements belong to"
        )
    if target["profile"] is not None:
        opaque_selector(path, target["profile"], f"{name}.profile")
    return target


def validate_sweep(path: Path, value: Any, name: str) -> int:
    """A sweep is a finite, explicitly enumerated set of values.

    Ranges and step counts are deliberately absent. A plan that cannot say how
    many candidates it will produce cannot be bounded before it starts, and an
    unbounded sweep is how an experiment turns into an accident.
    """
    sweep = pc.object_value(path, value, name)
    pc.exact_keys(path, sweep, name, {"target_parameters"})
    parameters = pc.object_value(
        path, sweep["target_parameters"], f"{name}.target_parameters"
    )
    if not parameters:
        raise pc.error(path, f"{name}.target_parameters must name at least one parameter")
    combinations = 1
    for parameter, values in parameters.items():
        if not pc.LOCAL_NAME.fullmatch(parameter):
            raise pc.error(path, f"{name} key {parameter!r} is not a local identifier")
        listed = pc.list_value(path, values, f"{name}.target_parameters.{parameter}")
        if not listed:
            raise pc.error(path, f"{name}.target_parameters.{parameter} must not be empty")
        if not all(isinstance(item, int) and not isinstance(item, bool)
                   for item in listed):
            raise pc.error(
                path, f"{name}.target_parameters.{parameter} must be integers; a "
                "component parameter is a build-time integer"
            )
        if len(listed) != len(set(listed)):
            raise pc.error(
                path, f"{name}.target_parameters.{parameter} repeats a value"
            )
        combinations *= len(listed)
    return combinations


def sweep_candidates(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand one declared candidate into the candidates a run will attempt.

    A candidate without a sweep expands to itself, so every consumer works with
    one list and nothing has to remember which form it was handed.
    """
    sweep = candidate.get("sweep")
    if not sweep:
        derived = dict(candidate)
        derived["target_parameters"] = {}
        return [derived]
    names = sorted(sweep["target_parameters"])
    expanded = []
    for values in itertools.product(*(sweep["target_parameters"][n] for n in names)):
        assignment = dict(zip(names, values))
        suffix = "-".join(f"{name}-{value}" for name, value in assignment.items())
        derived = dict(candidate)
        derived.pop("sweep", None)
        derived["id"] = f"{candidate['id']}-{suffix}"
        derived["target_parameters"] = assignment
        expanded.append(derived)
    return expanded


def plan_candidates(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Every candidate a run of this plan will attempt, sweeps expanded."""
    result: list[dict[str, Any]] = []
    for candidate in plan["candidates"]:
        result.extend(sweep_candidates(candidate))
    return result


def validate_candidate(path: Path, value: Any, index: int) -> dict[str, Any]:
    name = f"candidates[{index}]"
    candidate = pc.object_value(path, value, name)
    pc.exact_keys(
        path, candidate, name,
        {"id", "implementation", "target", "parameters", "work"},
        {"sweep"},
    )
    pc.namespaced(path, candidate["id"], f"{name}.id")
    pc.namespaced(path, candidate["implementation"], f"{name}.implementation")
    pc.namespaced(path, candidate["target"], f"{name}.target")
    pc.validate_parameter_values(
        path, candidate["parameters"], f"{name}.parameters"
    )
    work = pc.object_value(path, candidate["work"], f"{name}.work")
    pc.exact_keys(path, work, f"{name}.work", {"unit", "count"})
    pc.namespaced(path, work["unit"], f"{name}.work.unit")
    positive_int(path, work["count"], f"{name}.work.count")
    if "sweep" in candidate:
        validate_sweep(path, candidate["sweep"], f"{name}.sweep")
    return candidate


def validate_metric(path: Path, value: Any, index: int) -> dict[str, Any]:
    name = f"metrics[{index}]"
    metric = pc.object_value(path, value, name)
    pc.exact_keys(
        path, metric, name, {"id", "unit", "direction", "domain", "applicability"}
    )
    pc.namespaced(path, metric["id"], f"{name}.id")
    pc.namespaced(path, metric["unit"], f"{name}.unit")
    pc.namespaced(path, metric["direction"], f"{name}.direction")
    pc.namespaced(path, metric["domain"], f"{name}.domain")
    applicability = pc.object_value(
        path, metric["applicability"], f"{name}.applicability"
    )
    if not applicability:
        raise pc.error(path, f"{name}.applicability must name at least one target class")
    for target_class, rule in applicability.items():
        pc.namespaced(path, target_class, f"{name}.applicability key")
        if pc.namespaced(path, rule, f"{name}.applicability.{target_class}") \
                not in APPLICABILITY:
            raise pc.error(
                path, f"{name}.applicability.{target_class} must be one of "
                f"{sorted(APPLICABILITY)}"
            )
    return metric


def validate_plan(path: Path, document: dict[str, Any]) -> None:
    required = {
        "schema", "kind", "id", "revision", "summary", "claim", "workload",
        "implementations", "targets", "candidates", "metrics", "budget",
        "policy", "extensions",
    }
    pc.exact_keys(path, document, "experiment plan", required)
    if document["kind"] != "experiment-plan":
        raise pc.error(path, "kind must be 'experiment-plan'")
    pc.common(path, document, "org.atomix.experiment-plan")

    claim = pc.namespaced(path, document["claim"], "claim")
    if claim not in PLAN_CLAIMS:
        raise pc.error(path, f"claim must be one of {sorted(PLAN_CLAIMS)}")

    workload = pc.object_value(path, document["workload"], "workload")
    pc.exact_keys(path, workload, "workload", {"id", "revision", "parameters", "cases"})
    pc.namespaced(path, workload["id"], "workload.id")
    positive_int(path, workload["revision"], "workload.revision")
    pc.validate_parameter_values(path, workload["parameters"], "workload.parameters")
    cases = pc.list_value(path, workload["cases"], "workload.cases")
    if not cases or not all(isinstance(case, str) and case for case in cases):
        raise pc.error(path, "workload.cases must be a non-empty list of case names")
    if len(cases) != len(set(cases)):
        raise pc.error(path, "workload.cases contains duplicates")

    implementations = pc.list_value(path, document["implementations"], "implementations")
    if not implementations:
        raise pc.error(path, "implementations must not be empty")
    by_implementation: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(implementations):
        implementation = validate_implementation(path, value, index)
        if implementation["id"] in by_implementation:
            raise pc.error(path, f"duplicate implementation {implementation['id']!r}")
        by_implementation[implementation["id"]] = implementation

    targets = pc.list_value(path, document["targets"], "targets")
    if not targets:
        raise pc.error(path, "targets must not be empty")
    by_target: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(targets):
        target = validate_target(path, value, index)
        if target["id"] in by_target:
            raise pc.error(path, f"duplicate target {target['id']!r}")
        by_target[target["id"]] = target

    metrics = pc.list_value(path, document["metrics"], "metrics")
    if not metrics:
        raise pc.error(path, "metrics must not be empty")
    by_metric: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(metrics):
        metric = validate_metric(path, value, index)
        if metric["id"] in by_metric:
            raise pc.error(path, f"duplicate metric {metric['id']!r}")
        by_metric[metric["id"]] = metric

    candidates = pc.list_value(path, document["candidates"], "candidates")
    if not candidates:
        raise pc.error(path, "candidates must not be empty")
    seen_candidates: set[str] = set()
    used_implementations: set[str] = set()
    used_targets: set[str] = set()
    used_classes: set[str] = set()
    for index, value in enumerate(candidates):
        candidate = validate_candidate(path, value, index)
        name = f"candidates[{index}]"
        if candidate["id"] in seen_candidates:
            raise pc.error(path, f"duplicate candidate {candidate['id']!r}")
        seen_candidates.add(candidate["id"])
        implementation = by_implementation.get(candidate["implementation"])
        target = by_target.get(candidate["target"])
        if implementation is None:
            raise pc.error(path, f"{name} names unknown implementation "
                                 f"{candidate['implementation']!r}")
        if target is None:
            raise pc.error(path, f"{name} names unknown target {candidate['target']!r}")
        used_implementations.add(implementation["id"])
        used_targets.add(target["id"])
        used_classes.add(target["class"])

        # A capability requirement is the plan's compatibility statement. An
        # implementation that needs something its target does not advertise is
        # rejected here rather than after a confusing run-time failure.
        missing = set(implementation["requires"]) - set(target["capabilities"])
        if missing:
            raise pc.error(
                path, f"{name}: target {target['id']!r} does not provide "
                f"{sorted(missing)!r} required by {implementation['id']!r}"
            )

        unknown_parameters = candidate["parameters"].keys() - workload["parameters"].keys()
        if unknown_parameters:
            raise pc.error(
                path, f"{name} sets workload parameters {sorted(unknown_parameters)!r} "
                "the plan's workload does not declare"
            )

        for metric_id, metric in by_metric.items():
            rule = metric["applicability"].get(target["class"])
            if rule is None:
                raise pc.error(
                    path, f"metric {metric_id!r} declares no applicability for "
                    f"target class {target['class']!r} used by {name}"
                )
            if rule == "org.atomix.required" and \
                    metric["domain"] not in (CONTEXT_DOMAIN, target["measurement_domain"]):
                raise pc.error(
                    path, f"metric {metric_id!r} is in domain {metric['domain']!r} but is "
                    f"required of target {target['id']!r}, which measures "
                    f"{target['measurement_domain']!r}; a target cannot be required to "
                    "produce a quantity from another measurement domain"
                )

    if claim == CLAIM_SAME_ARTIFACT:
        if len(used_implementations) != 1:
            raise pc.error(
                path, "a same-artifact claim compares one implementation across "
                f"targets, but {len(used_implementations)} are used"
            )
        if len(used_targets) < 2:
            raise pc.error(path, "a same-artifact claim needs at least two targets")
    else:
        if len(used_implementations) < 2:
            raise pc.error(
                path, "a same-workload claim needs at least two implementations"
            )

    budget = pc.object_value(path, document["budget"], "budget")
    pc.exact_keys(
        path, budget, "budget",
        {"max_candidates", "per_candidate_seconds", "repetitions"},
    )
    max_candidates = positive_int(path, budget["max_candidates"], "budget.max_candidates")
    positive_int(path, budget["per_candidate_seconds"], "budget.per_candidate_seconds")
    positive_int(path, budget["repetitions"], "budget.repetitions")
    # The bound is checked against the expanded space, not the written list: a
    # plan with one swept candidate and a budget of one is a plan that has not
    # decided what it is prepared to spend.
    attempted = sum(
        validate_sweep(path, candidate["sweep"], f"candidates[{index}].sweep")
        if "sweep" in candidate else 1
        for index, candidate in enumerate(candidates)
    )
    if max_candidates < attempted:
        raise pc.error(
            path, f"budget.max_candidates ({max_candidates}) is below the "
            f"{attempted} candidates the plan's sweeps expand to"
        )

    policy = pc.object_value(path, document["policy"], "policy")
    pc.exact_keys(
        path, policy, "policy",
        {"correctness", "missing_measurement", "ranking", "cross_domain_ranking",
         "resource_claims"},
    )
    for name, value in policy.items():
        pc.namespaced(path, value, f"policy.{name}")
    if policy["cross_domain_ranking"] != "org.atomix.forbidden":
        raise pc.error(
            path, "policy.cross_domain_ranking must be org.atomix.forbidden at "
            "schema major 1; host elapsed time, simulator wall time, and model "
            "cycles are not a common scale"
        )


def validate_identity(path: Path, value: Any, executed: bool) -> dict[str, Any]:
    identity = pc.object_value(path, value, "identity")
    pc.exact_keys(path, identity, "identity", {"implementation", "target"})

    implementation = pc.object_value(
        path, identity["implementation"], "identity.implementation"
    )
    pc.exact_keys(
        path, implementation, "identity.implementation",
        {"id", "artifact_sha256", "build_sha256", "tools"},
    )
    pc.namespaced(path, implementation["id"], "identity.implementation.id")
    for key in ("artifact_sha256", "build_sha256"):
        digest = implementation[key]
        if digest is not None and (not isinstance(digest, str) or
                                   not cc.SHA256.fullmatch(digest)):
            raise pc.error(
                path, f"identity.implementation.{key} must be null or lowercase SHA-256"
            )
    tools = pc.object_value(path, implementation["tools"], "identity.implementation.tools")
    if not all(isinstance(name, str) and isinstance(version, str)
               for name, version in tools.items()):
        raise pc.error(path, "identity.implementation.tools must map strings to strings")

    target = pc.object_value(path, identity["target"], "identity.target")
    pc.exact_keys(
        path, target, "identity.target",
        {"id", "class", "adapter", "build_sha256", "profile_sha256"},
    )
    for key in ("id", "class", "adapter"):
        pc.namespaced(path, target[key], f"identity.target.{key}")
    for key in ("build_sha256", "profile_sha256"):
        digest = target[key]
        if digest is not None and (not isinstance(digest, str) or
                                   not cc.SHA256.fullmatch(digest)):
            raise pc.error(path, f"identity.target.{key} must be null or lowercase SHA-256")

    if executed:
        if implementation["artifact_sha256"] is None:
            raise pc.error(
                path, "an executed record must hash the artifact it ran; replay "
                "cannot check an unidentified binary"
            )
        if not tools:
            raise pc.error(path, "an executed record must record its tool identities")
    return identity


def validate_execution(path: Path, value: Any, status: str) -> dict[str, Any]:
    execution = pc.object_value(path, value, "execution")
    pc.exact_keys(
        path, execution, "execution",
        {"repetitions", "limit_seconds", "elapsed_seconds", "termination"},
    )
    repetitions = pc.nonnegative_int(path, execution["repetitions"], "execution.repetitions")
    positive_int(path, execution["limit_seconds"], "execution.limit_seconds")
    elapsed = nonnegative_number(path, execution["elapsed_seconds"], "execution.elapsed_seconds")
    termination = pc.namespaced(path, execution["termination"], "execution.termination")
    if termination not in TERMINATION:
        raise pc.error(path, f"execution.termination must be one of {sorted(TERMINATION)}")
    if status in EXECUTED_STATUS:
        if repetitions < 1:
            raise pc.error(path, "an executed record needs at least one repetition")
        if termination != "org.atomix.completed":
            raise pc.error(path, "a pass/fail record must have completed")
    if status == "org.atomix.timeout" and termination != "org.atomix.timeout":
        raise pc.error(path, "a timed-out record must terminate as org.atomix.timeout")
    if status in {"org.atomix.blocked", "org.atomix.not-run"}:
        if repetitions:
            raise pc.error(path, f"a {status} record cannot report repetitions")
        if termination == "org.atomix.completed":
            raise pc.error(path, f"a {status} record cannot have completed")
    if termination != "org.atomix.timeout" and elapsed > execution["limit_seconds"]:
        raise pc.error(
            path, "execution.elapsed_seconds exceeds the limit without a timeout"
        )
    return execution


def validate_measurements(path: Path, value: Any) -> dict[str, Any]:
    measurements = pc.object_value(path, value, "measurements")
    for metric_id, measurement_value in measurements.items():
        pc.namespaced(path, metric_id, "measurement key")
        name = f"measurements.{metric_id}"
        measurement = pc.object_value(path, measurement_value, name)
        pc.exact_keys(path, measurement, name, {"value", "unit", "status", "method"})
        pc.namespaced(path, measurement["unit"], f"{name}.unit")
        status = pc.namespaced(path, measurement["status"], f"{name}.status")
        if status not in MEASUREMENT_STATUS:
            raise pc.error(path, f"{name}.status must be one of {sorted(MEASUREMENT_STATUS)}")
        if status == "org.atomix.measured":
            nonnegative_number(path, measurement["value"], f"{name}.value")
        elif measurement["value"] is not None:
            raise pc.error(path, f"{name} is {status} and must carry a null value")
        if not isinstance(measurement["method"], str) or not measurement["method"].strip():
            raise pc.error(path, f"{name}.method must be non-empty")
    return measurements


def validate_record(path: Path, document: dict[str, Any]) -> None:
    required = {
        "schema", "kind", "id", "revision", "summary", "claim", "plan",
        "candidate", "status", "identity", "execution", "source",
        "environment", "correctness", "measurements", "extensions",
    }
    pc.exact_keys(path, document, "experiment record", required)
    if document["kind"] != "experiment-record":
        raise pc.error(path, "kind must be 'experiment-record'")
    pc.common(path, document, "org.atomix.experiment-record")

    claim = pc.namespaced(path, document["claim"], "claim")
    if claim not in {"org.atomix.template", "org.atomix.observation"}:
        raise pc.error(path, "claim must be org.atomix.template or org.atomix.observation")
    template = claim == "org.atomix.template"

    status = pc.namespaced(path, document["status"], "status")
    if status not in RECORD_STATUS:
        raise pc.error(path, f"status must be one of {sorted(RECORD_STATUS)}")
    if template and status != "org.atomix.not-run":
        raise pc.error(path, "a template record must be not-run")
    executed = status in EXECUTED_STATUS

    cc.document_reference(path, document["plan"], "plan")
    pc.namespaced(path, document["candidate"], "candidate")
    validate_identity(path, document["identity"], executed)
    validate_execution(path, document["execution"], status)
    cc.validate_source(path, document["source"], template)
    cc.validate_environment(path, document["environment"], template)
    # Correctness follows execution, not the claim: a blocked or timed-out
    # observation genuinely has no oracle result, and must say so rather than
    # inherit a verdict it never earned.
    cc.validate_correctness(path, document["correctness"], not executed)
    if status == "org.atomix.pass" and \
            document["correctness"]["status"] != "org.atomix.pass":
        raise pc.error(path, "a passing record needs a passing oracle result")
    if status == "org.atomix.fail" and \
            document["correctness"]["status"] != "org.atomix.fail":
        raise pc.error(
            path, "a failing record must record the failing oracle result; a run "
            "that never reached the oracle is blocked or timed out, not failed"
        )
    measurements = validate_measurements(path, document["measurements"])
    if template and any(measurement["status"] == "org.atomix.measured"
                        for measurement in measurements.values()):
        raise pc.error(path, "a template record cannot carry a measured value")


DISPOSITION = {
    "org.atomix.attempted", "org.atomix.reused", "org.atomix.not-attempted",
}


def validate_run_state(path: Path, document: dict[str, Any]) -> None:
    """A run's own index: what was attempted, what was reused, what never ran.

    Without it, an interrupted sweep is indistinguishable from a complete one
    that happened to have fewer candidates -- and the difference between "this
    configuration lost" and "this configuration was never tried" is the whole
    point of keeping records.
    """
    required = {
        "schema", "kind", "id", "revision", "summary", "plan", "budget",
        "candidates", "extensions",
    }
    pc.exact_keys(path, document, "experiment run state", required)
    if document["kind"] != "experiment-run-state":
        raise pc.error(path, "kind must be 'experiment-run-state'")
    pc.common(path, document, "org.atomix.experiment-run-state")
    cc.document_reference(path, document["plan"], "plan")

    budget = pc.object_value(path, document["budget"], "budget")
    pc.exact_keys(
        path, budget, "budget",
        {"max_candidates", "per_candidate_seconds", "repetitions", "run_seconds"},
    )
    positive_int(path, budget["max_candidates"], "budget.max_candidates")
    positive_int(path, budget["per_candidate_seconds"], "budget.per_candidate_seconds")
    positive_int(path, budget["repetitions"], "budget.repetitions")
    if budget["run_seconds"] is not None:
        positive_int(path, budget["run_seconds"], "budget.run_seconds")

    candidates = pc.object_value(path, document["candidates"], "candidates")
    if not candidates:
        raise pc.error(path, "candidates must not be empty")
    for candidate_id, value in candidates.items():
        name = f"candidates.{candidate_id}"
        pc.namespaced(path, candidate_id, "candidate key")
        entry = pc.object_value(path, value, name)
        pc.exact_keys(
            path, entry, name, {"status", "disposition", "record", "timestamp_utc"}
        )
        status = pc.namespaced(path, entry["status"], f"{name}.status")
        if status not in RECORD_STATUS:
            raise pc.error(path, f"{name}.status must be one of {sorted(RECORD_STATUS)}")
        disposition = pc.namespaced(path, entry["disposition"], f"{name}.disposition")
        if disposition not in DISPOSITION:
            raise pc.error(path, f"{name}.disposition must be one of {sorted(DISPOSITION)}")
        if disposition == "org.atomix.not-attempted" and status != "org.atomix.not-run":
            raise pc.error(
                path, f"{name} was never attempted, so its status must be not-run"
            )
        if entry["record"] is not None and not isinstance(entry["record"], str):
            raise pc.error(path, f"{name}.record must be null or a file name")
        if entry["timestamp_utc"] is not None and \
                not isinstance(entry["timestamp_utc"], str):
            raise pc.error(path, f"{name}.timestamp_utc must be null or a string")


def validate_run_state_against_plan(path: Path, state: dict[str, Any],
                                    plan: dict[str, Any]) -> None:
    known = {candidate["id"] for candidate in plan_candidates(plan)}
    unknown = state["candidates"].keys() - known
    if unknown:
        raise pc.error(path, f"run state names candidates the plan has no room for: "
                             f"{sorted(unknown)!r}")


def validate_record_against_plan(path: Path, record: dict[str, Any],
                                 plan: dict[str, Any]) -> None:
    candidates = {candidate["id"]: candidate for candidate in plan_candidates(plan)}
    candidate = candidates.get(record["candidate"])
    if candidate is None:
        raise pc.error(path, f"unknown plan candidate {record['candidate']!r}")
    targets = {target["id"]: target for target in plan["targets"]}
    target = targets[candidate["target"]]

    identity = record["identity"]
    if identity["implementation"]["id"] != candidate["implementation"]:
        raise pc.error(
            path, "identity.implementation.id does not match the candidate's "
            f"implementation {candidate['implementation']!r}"
        )
    if identity["target"]["id"] != target["id"]:
        raise pc.error(path, f"identity.target.id must be {target['id']!r}")
    if identity["target"]["class"] != target["class"]:
        raise pc.error(path, f"identity.target.class must be {target['class']!r}")
    if identity["target"]["adapter"] != target["adapter"]:
        raise pc.error(path, f"identity.target.adapter must be {target['adapter']!r}")

    metrics = {metric["id"]: metric for metric in plan["metrics"]}
    measurements = record["measurements"]
    extra = measurements.keys() - metrics.keys()
    if extra:
        raise pc.error(path, f"measurements the plan does not declare: {sorted(extra)!r}")

    level = record["environment"]["evidence_level"]
    resource_capable = (
        level in RESOURCE_EVIDENCE_LEVELS or level.startswith(RESOURCE_EVIDENCE_PREFIX)
    )
    for metric_id, metric in metrics.items():
        rule = metric["applicability"][target["class"]]
        measurement = measurements.get(metric_id)
        if rule == "org.atomix.required" and measurement is None:
            raise pc.error(
                path, f"{metric_id} is required of target class {target['class']!r} "
                "and may be unavailable, but not silently omitted"
            )
        if measurement is None:
            continue
        if rule == "org.atomix.inapplicable" and \
                measurement["status"] != "org.atomix.inapplicable":
            raise pc.error(
                path, f"{metric_id} does not apply to target class "
                f"{target['class']!r}; it cannot carry a {measurement['status']} value"
            )
        if measurement["unit"] != metric["unit"]:
            raise pc.error(path, f"{metric_id} unit does not match the plan")
        if measurement["status"] == "org.atomix.measured" and \
                metric_id in RESOURCE_METRICS and not resource_capable:
            raise pc.error(
                path, f"{metric_id} is measured at evidence level {level!r}, which "
                "cannot produce a resource result"
            )

    work = measurements.get(WORK_ITEMS)
    if work is not None and work["value"] is not None and \
            work["value"] != candidate["work"]["count"]:
        raise pc.error(path, "work-items does not match the candidate's logical work count")
    execute = measurements.get(EXECUTE_CYCLES, {}).get("value")
    total = measurements.get(TOTAL_CYCLES, {}).get("value")
    if execute is not None and total is not None and total < execute:
        raise pc.error(path, "total-cycles cannot be less than execute-cycles")


def validate_plan_workload(path: Path, plan: dict[str, Any],
                           workloads: dict[tuple[str, int], dict[str, Any]]) -> None:
    reference = (plan["workload"]["id"], plan["workload"]["revision"])
    workload = workloads.get(reference)
    if workload is None:
        raise pc.error(
            path, f"unknown workload {reference[0]!r} revision {reference[1]}; a plan "
            "pins the revision it was written against"
        )
    unknown = plan["workload"]["parameters"].keys() - workload["parameters"].keys()
    if unknown:
        raise pc.error(path, f"plan sets unknown workload parameters {sorted(unknown)!r}")
    known_cases = {case["name"] for case in workload["cases"]}
    missing = set(plan["workload"]["cases"]) - known_cases
    if missing:
        raise pc.error(
            path, f"workload revision {reference[1]} has no cases {sorted(missing)!r}"
        )


def workload_documents(root: Path) -> dict[tuple[str, int], dict[str, Any]]:
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for path in pc.collect([root]):
        document = pc.load_document(path)
        pc.validate_document(path, document)
        if document["kind"] == "workload":
            result[(document["id"], document["revision"])] = document
    return result


def is_rankable(record: dict[str, Any]) -> bool:
    return (
        record["claim"] == "org.atomix.observation" and
        record["status"] == "org.atomix.pass" and
        record["correctness"]["status"] == "org.atomix.pass"
    )


def check(paths: list[Path], personalities: Path) -> int:
    files = pc.collect(paths)
    if not files:
        raise pc.ContractError("no experiment JSON documents found")
    plans: dict[tuple[str, int], tuple[Path, dict[str, Any]]] = {}
    records: list[tuple[Path, dict[str, Any]]] = []
    states: list[tuple[Path, dict[str, Any]]] = []
    identities: dict[tuple[str, int], Path] = {}
    for path in files:
        document = pc.load_document(path)
        schema = pc.object_value(path, document.get("schema"), "schema").get("id")
        if schema == "org.atomix.experiment-plan":
            validate_plan(path, document)
            plans[(document["id"], document["revision"])] = (path, document)
        elif schema == "org.atomix.experiment-record":
            validate_record(path, document)
            records.append((path, document))
        elif schema == "org.atomix.experiment-run-state":
            validate_run_state(path, document)
            states.append((path, document))
        else:
            raise pc.error(path, f"unsupported experiment schema {schema!r}")
        identity = (document["id"], document["revision"])
        if identity in identities:
            raise pc.error(path, f"duplicate identity also appears in {identities[identity]}")
        identities[identity] = path

    workloads = workload_documents(personalities)
    for path, plan in plans.values():
        validate_plan_workload(path, plan, workloads)
    observations = templates = 0
    for path, record in records:
        reference = (record["plan"]["id"], record["plan"]["revision"])
        if reference not in plans:
            raise pc.error(path, f"unknown experiment plan {reference!r}")
        validate_record_against_plan(path, record, plans[reference][1])
        if record["claim"] == "org.atomix.template":
            templates += 1
        else:
            observations += 1
    for path, state in states:
        reference = (state["plan"]["id"], state["plan"]["revision"])
        if reference not in plans:
            raise pc.error(path, f"unknown experiment plan {reference!r}")
        validate_run_state_against_plan(path, state, plans[reference][1])
    print(
        f"experiment contract: PASS ({len(plans)} plans, {templates} templates, "
        f"{observations} records, {len(states)} run states)"
    )
    return 0


def rejects(name: str, mutate) -> None:
    """Assert that a deliberate corruption of a shipped fixture is refused."""
    try:
        mutate()
    except pc.ContractError:
        return
    raise pc.ContractError(f"self-test accepted {name}")


def self_test() -> int:
    same_workload_path = DEFAULT_ROOT / "saxpy-native-vs-rtl.json"
    same_artifact_path = DEFAULT_ROOT / "same-binary-cores.json"
    template_path = DEFAULT_ROOT / "record-template.json"
    plan = pc.load_document(same_workload_path)
    artifact_plan = pc.load_document(same_artifact_path)
    template = pc.load_document(template_path)
    validate_plan(same_workload_path, plan)
    validate_plan(same_artifact_path, artifact_plan)
    validate_record(template_path, template)
    validate_record_against_plan(template_path, template, plan)
    workloads = workload_documents(DEFAULT_PERSONALITIES)
    validate_plan_workload(same_workload_path, plan, workloads)
    validate_plan_workload(same_artifact_path, artifact_plan, workloads)

    # An out-of-tree target class, adapter, and metric are accepted: the
    # contract is open to backends it has never heard of.
    external = copy.deepcopy(plan)
    external["targets"].append({
        "id": "dev.example.target.wavefront-board",
        "class": "dev.example.class.wavefront",
        "adapter": "dev.example.adapter.wavefront",
        "summary": "External backend supplied outside this source tree",
        "capabilities": list(plan["implementations"][0]["requires"]) +
                        ["dev.example.capability.wavefront-router"],
        "measurement_domain": "dev.example.domain.wavefront-cycles",
        "profile": {"kind": "dev.example.selector.remote", "value": ["opaque", 7]},
    })
    for metric in external["metrics"]:
        metric["applicability"]["dev.example.class.wavefront"] = "org.atomix.optional"
    external["metrics"].append({
        "id": "dev.example.metric.wavefront-steps",
        "unit": "dev.example.unit.step",
        "direction": "org.atomix.lower-is-better",
        "domain": "dev.example.domain.wavefront-cycles",
        "applicability": {
            target["class"]: "org.atomix.optional" for target in external["targets"]
        },
    })
    external["candidates"].append({
        "id": "dev.example.candidate.wavefront-saxpy",
        "implementation": external["implementations"][0]["id"],
        "target": "dev.example.target.wavefront-board",
        "parameters": {},
        "work": {"unit": "org.atomix.output-element", "count": 5},
    })
    external["budget"]["max_candidates"] += 1
    validate_plan(Path("<external-backend-self-test>"), external)

    # A requirement the target does not advertise is a compatibility failure,
    # not something to discover halfway through a sweep.
    incompatible = copy.deepcopy(plan)
    incompatible["implementations"][0]["requires"].append(
        "org.atomix.capability.riscv-rv32i"
    )
    rejects(
        "an implementation requiring a capability its target lacks",
        lambda: validate_plan(Path("<capability-self-test>"), incompatible),
    )

    # A plan pins the workload revision it was written against.
    stale = copy.deepcopy(plan)
    stale["workload"]["revision"] = 99
    rejects(
        "a plan pinned to a workload revision that does not exist",
        lambda: validate_plan_workload(Path("<revision-self-test>"), stale, workloads),
    )
    renamed_case = copy.deepcopy(plan)
    renamed_case["workload"]["cases"] = ["no-such-case"]
    rejects(
        "a plan naming a case its workload revision does not define",
        lambda: validate_plan_workload(Path("<case-self-test>"), renamed_case, workloads),
    )

    # A host process has no LUTs. The plan says so, and a record cannot claim
    # otherwise -- which is the whole reason this schema exists.
    native = next(
        candidate for candidate in plan["candidates"]
        if plan_target(plan, candidate)["class"] == "org.atomix.native-host"
    )
    fpga_claim = copy.deepcopy(template)
    fpga_claim["candidate"] = native["id"]
    fpga_claim["identity"]["implementation"]["id"] = native["implementation"]
    fpga_claim["identity"]["target"] = record_target_identity(plan, native)
    validate_record_against_plan(Path("<native-self-test>"), fpga_claim, plan)
    fpga_claim["measurements"]["org.atomix.metric.lut-used"] = {
        "value": 1989,
        "unit": "org.atomix.unit.count",
        "status": "org.atomix.measured",
        "method": "invented",
    }
    rejects(
        "a LUT count on a native host candidate",
        lambda: validate_record_against_plan(
            Path("<native-self-test>"), fpga_claim, plan
        ),
    )

    # Requiring model cycles from a host process is a plan-level mistake.
    crossed = copy.deepcopy(plan)
    for metric in crossed["metrics"]:
        if metric["id"] == EXECUTE_CYCLES:
            metric["applicability"]["org.atomix.native-host"] = "org.atomix.required"
    rejects(
        "a plan requiring model cycles from a host-elapsed target",
        lambda: validate_plan(Path("<domain-self-test>"), crossed),
    )
    bridged = copy.deepcopy(plan)
    bridged["policy"]["cross_domain_ranking"] = "org.atomix.permitted"
    rejects(
        "a plan permitting cross-domain ranking",
        lambda: validate_plan(Path("<policy-self-test>"), bridged),
    )

    # Measured zero is a result; measured null is not.
    zero = {WORK_ITEMS: {
        "value": 0, "unit": "org.atomix.unit.item",
        "status": "org.atomix.measured", "method": "declared work count",
    }}
    validate_measurements(Path("<zero-self-test>"), zero)
    null_value = copy.deepcopy(zero)
    null_value[WORK_ITEMS]["value"] = None
    rejects(
        "a measured metric with a null value",
        lambda: validate_measurements(Path("<null-self-test>"), null_value),
    )

    # Outcome integrity: a template is not evidence, and a run that never
    # reached the oracle cannot report a verdict.
    if is_rankable(template):
        raise pc.ContractError("self-test ranked a template record")
    lying = copy.deepcopy(template)
    lying["claim"] = "org.atomix.observation"
    lying["status"] = "org.atomix.pass"
    lying["source"] = {"commit": "0" * 40, "dirty": False, "diff_sha256": None}
    lying["environment"] = {
        "evidence_level": "org.atomix.native-execution",
        "timestamp_utc": "2026-09-10T00:00:00Z",
        "board": None, "device": None, "tools": {"cc": "self-test"},
    }
    lying["identity"]["implementation"]["artifact_sha256"] = "0" * 64
    lying["identity"]["implementation"]["tools"] = {"cc": "self-test"}
    lying["execution"] = {
        "repetitions": 1, "limit_seconds": 900, "elapsed_seconds": 1,
        "termination": "org.atomix.completed",
    }
    rejects(
        "a passing record whose oracle never ran",
        lambda: validate_record(Path("<status-self-test>"), lying),
    )
    timed_out = copy.deepcopy(lying)
    timed_out["status"] = "org.atomix.timeout"
    rejects(
        "a timed-out record that reports completion",
        lambda: validate_record(Path("<timeout-self-test>"), timed_out),
    )

    # A sweep is finite and its size is known before it runs.
    swept = copy.deepcopy(plan)
    expanded = plan_candidates(swept)
    if len(expanded) != swept["budget"]["max_candidates"]:
        raise pc.ContractError(
            f"self-test expected the shipped sweep to expand to "
            f"{swept['budget']['max_candidates']} candidates, got {len(expanded)}"
        )
    if len({candidate["id"] for candidate in expanded}) != len(expanded):
        raise pc.ContractError("self-test found duplicate ids in an expanded sweep")
    over_budget = copy.deepcopy(plan)
    over_budget["budget"]["max_candidates"] = 2
    rejects(
        "a sweep wider than the budget it declares",
        lambda: validate_plan(Path("<sweep-budget-self-test>"), over_budget),
    )
    repeated = copy.deepcopy(plan)
    for candidate in repeated["candidates"]:
        if "sweep" in candidate:
            candidate["sweep"]["target_parameters"]["lanes"] = [8, 8]
    rejects(
        "a sweep that repeats a value",
        lambda: validate_plan(Path("<sweep-repeat-self-test>"), repeated),
    )

    # The R2 comparison documents keep their own schema and validator. This
    # contract refuses them, and theirs still accepts them.
    r2_path = cc.DEFAULT_ROOT / "r2-morph-vs-hard.json"
    r2 = pc.load_document(r2_path)
    rejects(
        "an R2 comparison plan as an experiment plan",
        lambda: validate_plan(r2_path, r2),
    )
    cc.validate_plan(r2_path, r2)

    print(
        "experiment contract: SELF-TEST PASS (open backends, capability and "
        "revision gates, metric applicability, domain separation, finite bounded "
        "sweeps, outcome integrity)"
    )
    return 0


def plan_target(plan: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """The candidate's target, with any swept parameters applied to its profile.

    The sweep changes the machine, so it has to change the profile the adapter
    is handed -- and therefore the profile hash the record carries. A sweep
    that only changed a label would produce candidates that are identical on
    paper and different in fact.
    """
    target = next(
        item for item in plan["targets"] if item["id"] == candidate["target"]
    )
    overrides = candidate.get("target_parameters") or {}
    if not overrides:
        return target
    if not target.get("profile"):
        raise pc.error(
            Path(plan["id"]),
            f"{candidate['id']} sweeps target parameters, but {target['id']} has no "
            "profile to apply them to",
        )
    derived = copy.deepcopy(target)
    parameters = derived["profile"]["value"].setdefault("parameters", {})
    parameters.update(overrides)
    return derived


def record_target_identity(plan: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    target = plan_target(plan, candidate)
    return {
        "id": target["id"], "class": target["class"], "adapter": target["adapter"],
        "build_sha256": None, "profile_sha256": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check", help="validate plans and records")
    check_parser.add_argument("paths", nargs="*", type=Path, default=[DEFAULT_ROOT])
    check_parser.add_argument(
        "--personality-root", type=Path, default=DEFAULT_PERSONALITIES,
        help="workload documents the plans pin",
    )
    subparsers.add_parser("self-test", help="exercise the contract's own gates")
    args = parser.parse_args()
    try:
        if args.command == "self-test":
            return self_test()
        return check(args.paths, args.personality_root)
    except pc.ContractError as exc:
        print(f"experiment contract: FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
