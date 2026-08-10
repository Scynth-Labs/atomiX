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
import json
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


def cmd_show(args) -> int:
    baseline = load(BASELINE)
    print(f"locked {baseline['locked_on']} at {baseline['locked_at_commit'][:12]}")
    header = f"{'profile':<13}{'expect':<8}{'LUT4':>7}{'DFF':>7}{'BSRAM':>7}{'DSP':>5}{'fmax':>9}"
    print(header)
    print("-" * len(header))
    for name, want in baseline["profiles"].items():
        fmax = f"{want['fmax_mhz']:.2f}" if want["fmax_mhz"] else "-"
        print(f"{name:<13}{want['expect']:<8}{want['lut4']:>7}{want['dff']:>7}"
              f"{want['bsram']:>7}{want['dsp']:>5}{fmax:>9}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", help="compare a sweep report against the lock")
    p.add_argument("report", type=Path)
    p.add_argument("--partial", action="store_true",
                   help="only check profiles present in the report")
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("show", help="print the locked baseline")
    p.set_defaults(func=cmd_show)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
