#!/usr/bin/env python3
"""Prove the comparison report refuses to flatter its own results.

The failure mode a report has is not crashing; it is being persuasive about
something it does not know.  These checks hold it to the rules that make it
worth reading:

- a candidate that failed its oracle appears as excluded and in no table;
- a candidate that was never attempted is named as never attempted, not
  omitted;
- a constraint on a metric nobody measured qualifies nobody;
- host time and model cycles are rendered as separate tables with an explicit
  statement that no ratio between them means anything;
- the same-artifact and same-workload claims are labelled differently;
- an exported result reproduces from a clean run, and is refused when an input
  has changed or the evidence level does not match.

Run with `make experiment-report-check`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import personality_contract as pc

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "tools" / "experiment_report.py"
RUNNER = ROOT / "tools" / "experiment_run.py"
SAXPY = ROOT / "research" / "experiments" / "saxpy-native-vs-rtl.json"
SAME_BINARY = ROOT / "research" / "experiments" / "same-binary-cores.json"
RECORDS = ROOT / "research" / "experiments" / "records"

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f": {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def report(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(REPORT), *arguments],
                          capture_output=True, text=True, check=False)


def check_claims_are_labelled_differently() -> None:
    workload = report("render", str(SAXPY), "--records", str(RECORDS))
    artifact = report("render", str(SAME_BINARY), "--records", str(RECORDS))
    check("the same-workload claim is labelled as one",
          "Same workload, different implementations" in workload.stdout)
    check("the same-artifact claim is labelled as another",
          "Same artifact, different machines" in artifact.stdout)


def check_domains_stay_apart() -> None:
    rendered = report("render", str(SAXPY), "--records", str(RECORDS)).stdout
    separate = (
        "Model cycles (deterministic" in rendered and
        "Host elapsed time" in rendered and
        "Not comparable across the tables above" in rendered and
        "deliberately computes no combined score" in rendered
    )
    check("host time and model cycles are separate tables with a stated warning",
          separate)
    check("a host candidate is absent from the model-cycles table with a reason",
          "saxpy-native             -- does not apply to this target" in rendered)


def check_missing_evidence_never_qualifies() -> None:
    rendered = report("render", str(SAXPY), "--records", str(RECORDS),
                      "--constraint", "org.atomix.metric.lut-used<=5000")
    refused = (
        "nothing qualifies on the evidence available" in rendered.stdout and
        "qualifies  " not in rendered.stdout.split("org.atomix.metric.lut-used")[-1] and
        rendered.returncode != 0
    )
    check("an area bound with no evidence qualifies nobody", refused,
          "" if refused else rendered.stdout.strip().splitlines()[-1:] or "")


def check_failed_and_untried_are_named(root: Path) -> None:
    """A wrong candidate and an untried one both appear, and neither is ranked."""
    staged = ROOT / "build" / "experiments" / "report-check" / "saxpy_off_by_one.c"
    staged.parent.mkdir(parents=True, exist_ok=True)
    original = (ROOT / "sw" / "native" / "saxpy_i32.c").read_text()
    staged.write_text(original.replace(
        "out[i] = wrap_i32(factor * (uint32_t)x[i] + (uint32_t)y[i]);",
        "out[i] = wrap_i32(factor * (uint32_t)x[i] + (uint32_t)y[i] + 1u);",
    ))
    plan = pc.load_document(SAXPY)
    for implementation in plan["implementations"]:
        if implementation["build"]["kind"] == "org.atomix.host-cc":
            implementation["build"]["value"]["source"] = str(staged.relative_to(ROOT))
    plan_path = root / "wrong-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2) + "\n")

    records, work = root / "records", root / "work"
    # Two candidates only: one wrong, one never attempted, so the report has to
    # distinguish "lost" from "not tried".
    subprocess.run(
        [sys.executable, str(RUNNER), str(plan_path), "--records", str(records),
         "--work", str(work), "--only", "saxpy-native",
         "--only", "saxpy-simt-rtl-lanes-1", "--max-candidates", "1"],
        capture_output=True, text=True, check=False,
    )
    rendered = report("render", str(plan_path), "--records", str(records)).stdout
    check("a candidate that failed its oracle is named as excluded",
          "excluded  saxpy-native" in rendered and "failed the oracle" in rendered,
          "" if "failed the oracle" in rendered else rendered[:200])
    check("a candidate that was never attempted is named as such",
          "never attempted" in rendered)
    check("neither appears in a comparison table",
          "Nothing passed its oracle" in rendered or
          "saxpy-native " not in rendered.split("Eligible for comparison")[-1]
          .split("Constraints")[0].split("Identity")[-1])


def check_export_and_reproduce(root: Path) -> None:
    bundle = root / "bundle.json"
    exported = report("export", str(SAXPY), "saxpy-simt-rtl-lanes-4",
                      "--records", str(RECORDS), "--output", str(bundle))
    if exported.returncode != 0 or not bundle.is_file():
        check("a result exports as a self-contained bundle", False,
              exported.stderr.strip())
        return
    document = pc.load_document(bundle)
    complete = (
        document["retrieval"]["inputs"] and
        document["reproduce"]["expects"]["oracle_output_sha256"] and
        document["reproduce"]["expects"]["deterministic"]
    )
    check("a result exports as a self-contained bundle", bool(complete),
          f"{len(document['retrieval']['inputs'])} declared inputs")

    again = report("reproduce", str(bundle), "--work", str(root / "repro"),
                   "--records", str(root / "repro" / "records"))
    check("the bundle reproduces from a clean run", again.returncode == 0,
          again.stdout.strip().splitlines()[-1] if again.stdout else "")

    changed = root / "changed.json"
    mutated = pc.load_document(bundle)
    first = sorted(mutated["retrieval"]["inputs"])[0]
    mutated["retrieval"]["inputs"][first] = "0" * 64
    changed.write_text(json.dumps(mutated, indent=2) + "\n")
    stale = report("reproduce", str(changed), "--work", str(root / "repro"),
                   "--records", str(root / "repro" / "records"))
    check("a changed input is refused before anything is rebuilt",
          stale.returncode != 0 and "has changed" in stale.stdout,
          stale.stdout.strip().splitlines()[-1] if stale.stdout else "")

    # A different toolchain making different bytes from identical source is a
    # successful reproduction with a fact worth reporting, not a rejection.
    other_toolchain = root / "other-toolchain.json"
    mutated = pc.load_document(bundle)
    mutated["reproduce"]["expects"]["identity"]["implementation"]["artifact_sha256"] = \
        "1" * 64
    mutated["reproduce"]["expects"]["identity"]["implementation"]["tools"] = \
        {"verilator": "Verilator 4.038 2020-07-11 (a different host)"}
    other_toolchain.write_text(json.dumps(mutated, indent=2) + "\n")
    elsewhere = report("reproduce", str(other_toolchain), "--work", str(root / "repro"),
                       "--records", str(root / "repro" / "records"))
    check("a rebuild on another toolchain reproduces and says the bytes differ",
          elsewhere.returncode == 0 and "Rebuilt to different bytes" in elsewhere.stdout,
          elsewhere.stdout.strip().splitlines()[-1] if elsewhere.stdout else "")

    # The same toolchain producing different bytes is not explained by anything
    # the bundle declares, so it is refused.
    unexplained = root / "unexplained.json"
    mutated = pc.load_document(bundle)
    mutated["reproduce"]["expects"]["identity"]["implementation"]["artifact_sha256"] = \
        "1" * 64
    unexplained.write_text(json.dumps(mutated, indent=2) + "\n")
    refused = report("reproduce", str(unexplained), "--work", str(root / "repro"),
                     "--records", str(root / "repro" / "records"))
    check("the same toolchain producing different bytes is refused",
          refused.returncode != 0 and "tool identities are identical" in refused.stdout,
          refused.stdout.strip().splitlines()[-1] if refused.stdout else "")

    wrong_level = root / "wrong-level.json"
    mutated = pc.load_document(bundle)
    mutated["reproduce"]["expects"]["evidence_level"] = \
        "org.atomix.physical-tang-primer-25k"
    wrong_level.write_text(json.dumps(mutated, indent=2) + "\n")
    mismatched = report("reproduce", str(wrong_level), "--work", str(root / "repro"),
                        "--records", str(root / "repro" / "records"))
    check("a bundle claiming another evidence level is refused",
          mismatched.returncode != 0 and "not interchangeable" in mismatched.stdout,
          mismatched.stdout.strip().splitlines()[-1] if mismatched.stdout else "")


def check_documented_numbers_match_the_records() -> None:
    """Prose that quotes a measurement must quote the one on disk.

    Numbers in documentation rot silently: the records get regenerated, the
    sentence stays, and nothing fails. These are the figures the walkthrough
    and the README use to describe what a reader will see, so they are checked
    against the records they came from.
    """
    quoted = {}
    for path in sorted(RECORDS.glob("*.json")):
        record = pc.load_document(path)
        if record.get("kind") != "experiment-record":
            continue
        value = record["measurements"].get("org.atomix.metric.execute-cycles", {})
        if value.get("status") == "org.atomix.measured":
            quoted[record["candidate"].split(".")[-1]] = f"{value['value']:,.0f}"

    documents = {
        name: (ROOT / name).read_text()
        for name in ("docs/experiment-alpha.md", "README.md",
                     "docs/design-checklist.md")
    }
    missing = []
    for candidate, number in sorted(quoted.items()):
        for name, text in documents.items():
            if candidate.split("-lanes-")[0] in text and number not in text:
                missing.append(f"{name} discusses {candidate} but not {number}")
    check("documented cycle counts match the records", not missing,
          "; ".join(missing[:2]))


def main() -> int:
    print("experiment report:")
    root = Path(tempfile.mkdtemp(prefix="ax-report-"))
    try:
        check_claims_are_labelled_differently()
        check_domains_stay_apart()
        check_missing_evidence_never_qualifies()
        check_failed_and_untried_are_named(root)
        check_export_and_reproduce(root)
        check_documented_numbers_match_the_records()
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(ROOT / "build" / "experiments" / "report-check",
                      ignore_errors=True)
    if failures:
        print(f"experiment report: FAIL ({len(failures)} of the gates did not hold)")
        return 1
    print("experiment report: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
