#!/usr/bin/env python3
"""Hold the Tang Primer synthesis results to their locked baseline.

On 2026-08-10 a shell change added about 2,100 LUT4 to every profile and broke
`role.tpu-lite`'s placement.  Nothing caught it: the profile had been
physically verified three weeks earlier and simply stopped building, and that
only surfaced when someone re-ran the sweep by hand.  This gate exists so the
next such change fails on the day it lands.

The baseline in `research/benchmarks/tangprimer25k-baseline.json` pins, per
profile, the resource counts, the routed frequency, and — importantly — whether
the build is expected to *pass at all*.  `ax2` is locked as a known failure, so
a build that starts passing is also a violation: it means the profile was
silently redefined, which is exactly the drift this guards against.

Logic counts may move a few percent with synthesis changes, so LUT4 and DFF
carry a tolerance band.  BSRAM and DSP counts are structural — a change there
means memories or multipliers stopped mapping, which is never incidental — so
they must match exactly.

    tools/synth_baseline.py check <sweep-report.json>
    tools/synth_baseline.py show
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "research/benchmarks/tangprimer25k-baseline.json"


def load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"synth baseline: cannot read {path}: {exc}")


def observed(result: dict[str, Any]) -> dict[str, Any]:
    """Reduce one sweep result to the fields the baseline pins."""
    util = result.get("utilization") or {}

    def used(name: str) -> int | None:
        entry = util.get(name)
        return entry.get("used") if entry else None

    dsp = 0
    for name in ("MULT12X12", "MULTALU27X18", "MULTADDALU12X12"):
        dsp += used(name) or 0
    clocks = (result.get("clocks_mhz") or {}).get("clk_25mhz") or {}
    return {
        "status": result.get("place_route_status"),
        "lut4": used("LUT4"),
        "dff": used("DFF"),
        "bsram": used("BSRAM"),
        "dsp": dsp if used("LUT4") is not None else None,
        "fmax_mhz": clocks.get("achieved"),
    }


def compare(name: str, want: dict[str, Any], got: dict[str, Any],
            tol: dict[str, Any]) -> list[str]:
    expect_pass = want["expect"] == "pass"
    passed = got["status"] == "PASS"
    if passed != expect_pass:
        state = "passed" if passed else "failed"
        return [f"{name}: expected to {want['expect']}, but the build {state}"]
    if not expect_pass:
        return []  # A known failure that still fails is exactly correct.

    problems: list[str] = []
    for field, limit in (("lut4", tol["lut4_percent"]), ("dff", tol["dff_percent"])):
        reference, actual = want[field], got[field]
        if actual is None:
            problems.append(f"{name}: {field} missing from the report")
            continue
        drift = 100.0 * (actual - reference) / reference
        if abs(drift) > limit:
            problems.append(
                f"{name}: {field} {actual} vs locked {reference} "
                f"({drift:+.1f}%, tolerance +/-{limit}%)")
    for field in ("bsram", "dsp"):
        if got[field] != want[field]:
            problems.append(
                f"{name}: {field} {got[field]} vs locked {want[field]} "
                f"(structural, must match exactly)")
    reference, actual = want["fmax_mhz"], got["fmax_mhz"]
    if actual is None:
        problems.append(f"{name}: fmax missing from the report")
    else:
        if actual < reference * tol["fmax_min_ratio"]:
            problems.append(
                f"{name}: fmax {actual:.2f} MHz vs locked {reference:.2f} "
                f"(below {tol['fmax_min_ratio']:.0%})")
        if actual < 25.0:
            problems.append(f"{name}: fmax {actual:.2f} MHz misses the 25 MHz "
                            "board constraint")
    return problems


def cmd_check(args) -> int:
    baseline = load(BASELINE)
    report = load(args.report)
    results = {r["profile"]: r for r in report.get("results", [])}
    tol = baseline["tolerance"]

    problems: list[str] = []
    checked = 0
    for name, want in baseline["profiles"].items():
        result = results.get(name)
        if result is None:
            if args.partial:
                continue
            problems.append(f"{name}: absent from the report")
            continue
        checked += 1
        problems.extend(compare(name, want, observed(result), tol))

    if problems:
        print(f"synth baseline: FAIL ({len(problems)} deviation(s) "
              f"across {checked} profiles)", file=sys.stderr)
        for item in problems:
            print(f"  {item}", file=sys.stderr)
        print("\nIf a change is intended, re-lock deliberately by updating "
              f"{BASELINE.relative_to(ROOT)} in the same commit.",
              file=sys.stderr)
        return 1
    print(f"synth baseline: PASS ({checked} profiles match the "
          f"{baseline['locked_on']} lock)")
    return 0


def cmd_relock(args) -> int:
    """Rewrite the lock from a sweep, printing every field it moves.

    Transcribing eight profiles by hand into the one file whose entire job is
    precision is how a lock quietly stops describing the hardware.  This does
    the copy mechanically and prints old -> new for each field, so the human
    part of a deliberate re-lock is reading the deltas rather than retyping
    them.
    """
    baseline = load(BASELINE)
    report = load(args.report)
    if report.get("status") != "COMPLETE":
        raise SystemExit(
            f"synth baseline: {args.report} has status "
            f"{report.get('status')!r}, not COMPLETE; re-lock only from a "
            "sweep that finished")
    results = {r["profile"]: r for r in report.get("results", [])}
    names = args.profile or sorted(results)
    missing = [n for n in names if n not in results]
    if missing:
        raise SystemExit(f"synth baseline: not in the report: {', '.join(missing)}")

    profiles = baseline["profiles"]
    unlocked = baseline.get("unlocked_rows", {})
    stale = baseline.get("known_stale_rows", {})
    for name in names:
        got = observed(results[name])
        passed = got["status"] == "PASS"
        row = profiles.get(name)
        if row is None:
            # A profile that had no locked row: take its config and any note
            # from unlocked_rows so the reason it exists survives the move.
            pending = unlocked.get(name, {})
            row = {
                "config": pending.get("config", results[name]["config"]["path"]),
                "program": None,
                "expect": "pass" if passed else "fail",
                "lut4": None, "dff": None, "bsram": None, "dsp": None,
                "fmax_mhz": None,
            }
            if pending.get("note"):
                row["note"] = pending["note"]
            profiles[name] = row
            print(f"{name}: NEW row, expect={row['expect']}")
        expect = "pass" if passed else "fail"
        if row["expect"] != expect:
            print(f"{name}: expect {row['expect']} -> {expect}")
            row["expect"] = expect
        for field in ("lut4", "dff", "bsram", "dsp", "fmax_mhz"):
            after = got[field]
            # A build that failed to place reports no utilisation at all. Do not
            # let that null out the row: `ax2` is locked as a known failure and
            # its last known counts are what tell a reader *how far* over the
            # device it is. Only fields the sweep actually measured are written.
            if after is None:
                continue
            if field == "fmax_mhz":
                after = round(after, 2)
            before = row.get(field)
            if before != after:
                print(f"{name}: {field} {before} -> {after}")
            row[field] = after
        baseline.setdefault("provenance", {})[name] = f"sweep {args.report.name}"
        stale.pop(name, None)
        unlocked.pop(name, None)

    baseline["locked_on"] = dt.date.today().isoformat()
    baseline["locked_at_commit"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, text=True,
        stdout=subprocess.PIPE).stdout.strip()
    # Keep the baseline's own tool vocabulary; the report's raw strings go in
    # one place rather than merging keys that mean different things. (The sweep
    # records gowin_pack's "version" as whatever it prints, which can be a
    # warning line, so it is evidence rather than a version field.)
    tools = dict(baseline.get("tools", {}))
    reported = report.get("tools", {})
    if reported.get("yosys"):
        tools["yosys"] = reported["yosys"]
    tools["sweep_tool_versions"] = reported
    baseline["tools"] = tools
    # An empty note-only container is noise; drop it once nothing is pending.
    for key in ("known_stale_rows", "unlocked_rows"):
        container = baseline.get(key, {})
        if set(container) <= {"note"}:
            baseline.pop(key, None)

    BASELINE.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    print(f"\nre-locked {len(names)} profile(s) on {baseline['locked_on']} at "
          f"{baseline['locked_at_commit'][:12]} from {args.report.name}")
    print(f"Review the deltas above, then commit {BASELINE.relative_to(ROOT)} "
          "with the change that caused them.")
    return 0


def cmd_show(args) -> int:
    baseline = load(BASELINE)
    stale = {k: v for k, v in baseline.get("known_stale_rows", {}).items()
             if k != "note"}
    unlocked = {k: v for k, v in baseline.get("unlocked_rows", {}).items()
                if k != "note"}
    print(f"locked {baseline['locked_on']} at {baseline['locked_at_commit'][:12]}")
    header = (f"{'profile':<13}{'expect':<8}{'LUT4':>7}{'DFF':>7}{'BSRAM':>7}"
              f"{'DSP':>5}{'fmax':>9}  ")
    print(header)
    print("-" * len(header))
    for name, want in baseline["profiles"].items():
        # A row for a build that has never placed carries no counts at all, so
        # every column has to survive a missing value.
        cell = lambda f: "-" if want.get(f) is None else str(want[f])  # noqa: E731
        fmax = f"{want['fmax_mhz']:.2f}" if want.get("fmax_mhz") else "-"
        print(f"{name:<13}{want['expect']:<8}{cell('lut4'):>7}{cell('dff'):>7}"
              f"{cell('bsram'):>7}{cell('dsp'):>5}{fmax:>9}  "
              f"{'STALE' if name in stale else ''}")
    # A locked number that is already known to be wrong is worse than no number
    # if the reader cannot tell which is which, so say so here rather than only
    # in the file.
    if stale:
        print(f"\n{len(stale)} row(s) known stale; they will flag on the next "
              "sweep and must be re-locked deliberately:")
        for name in stale:
            print(f"  {name}: {stale[name]}")
    if unlocked:
        print(f"\n{len(unlocked)} profile(s) with no locked row yet "
              "(add them from the first sweep that includes them):")
        for name, entry in unlocked.items():
            # No `expect` here on purpose: whether one of these places is what
            # the sweep is for, not something to declare in advance.
            print(f"  {name} -> decouples {entry.get('decouples', '?')} "
                  f"({entry['config']})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", help="compare a sweep report against the lock")
    p.add_argument("report", type=Path)
    p.add_argument("--partial", action="store_true",
                   help="only check profiles present in the report")
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("relock", help="rewrite the lock from a finished sweep")
    p.add_argument("report", type=Path)
    p.add_argument("--profile", action="append",
                   help="re-lock only this profile (repeatable; default: every "
                        "profile in the report)")
    p.set_defaults(func=cmd_relock)
    p = sub.add_parser("show", help="print the locked baseline")
    p.set_defaults(func=cmd_show)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
