#!/usr/bin/env python3
"""What this project needs, what it has been run on, and what this host has.

Version requirements were previously implicit: spread across prose in three
documents, a `?=` in four Makefiles, and a preference buried in a shell script.
That is how a claim like "Verilator 5 fails to elaborate role.loopback" outlives
the release it was true for -- nothing looks at it, so nothing contradicts it.

So `tools/requirements.json` is the single source of truth, and this reads it
three ways:

  report   what this host has, classified against the requirement
  check    non-zero when a required tool is missing or known-bad
  docs     regenerate (or verify) the tables in docs/dependencies.md

The distinction the whole file turns on is between *supported* and *tested*.
Supported is the range accepted without comment; tested is what was actually
run, with the evidence beside it. A version in the first and not the second is
reported as exactly that -- accepted, untested -- rather than being quietly
blessed or quietly refused.

No third-party packages, no network: this has to run on a host that has nothing
installed yet, because that is the host most likely to need it.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "tools" / "requirements.json"
DEPENDENCIES = ROOT / "docs" / "dependencies.md"

BEGIN = "<!-- BEGIN GENERATED: tools/requirements.py docs -->"
END = "<!-- END GENERATED -->"

VERSION = re.compile(r"(\d+(?:\.\d+)+)")

# Statuses, worst first: `check` fails on the first two.
MISSING, UNSUPPORTED, UNTESTED, TESTED, PRESENT = (
    "missing", "unsupported", "untested", "tested", "present")


def parts(version):
    """A comparable tuple. Verilator's 4.038 is (4, 38), which orders correctly
    against 5.050 -> (5, 50); the zero padding is display, not magnitude."""
    return tuple(int(p) for p in version.split("."))


def satisfies(version, constraint):
    """`constraint` is "*", or comma-separated clauses like ">=4.0,<5.0"."""
    if constraint in ("*", "", None):
        return True
    for clause in constraint.split(","):
        clause = clause.strip()
        match = re.match(r"(>=|<=|==|>|<)\s*(\d[\d.]*)", clause)
        if not match:
            raise SystemExit(f"requirements.json: unreadable constraint {clause!r}")
        op, want = match.group(1), parts(match.group(2))
        # Compare on the shorter length so ">=10" accepts 10.2.0 and "<7.0"
        # rejects 6.2.0 without either side needing padding.
        width = min(len(parts(version)), len(want))
        have, want = parts(version)[:width], want[:width]
        if not {">=": have >= want, "<=": have <= want, "==": have == want,
                ">": have > want, "<": have < want}[op]:
            return False
    return True


def substitute(text):
    """Expand {VERILATOR}, {PYTHON} and friends from the environment, so a probe
    honours the same override the build does."""
    defaults = {"VERILATOR": "verilator", "PYTHON": sys.executable or "python3",
                "NODE": "node", "RISCV_PREFIX": "riscv64-unknown-elf-",
                "AX_BROWSER": ""}
    def one(match):
        name = match.group(1)
        return os.environ.get(name) or defaults.get(name, "")
    return re.sub(r"\{([A-Z_]+)\}", one, text)


def probe(tool):
    """Return (status, version_or_None, detail)."""
    spec = tool.get("probe", {})

    if spec.get("mode") == "presence":
        for candidate in spec.get("candidates", []):
            if candidate.startswith("$"):
                candidate = os.environ.get(candidate[1:], "")
            if not candidate:
                continue
            found = candidate if "/" in candidate else shutil.which(candidate)
            if found and Path(found).exists():
                return PRESENT, None, found
        return MISSING, None, ""

    command = substitute(spec.get("command", ""))
    if not command or not shutil.which(command):
        return MISSING, None, command
    try:
        result = subprocess.run([command] + spec.get("args", []), capture_output=True,
                                text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        return MISSING, None, f"{command}: {error}"
    output = (result.stdout or "") + (result.stderr or "")
    first = output.strip().splitlines()[0] if output.strip() else ""
    found = VERSION.search(first)
    if not found:
        # Present, but it does not say what it is. `sby` is the example.
        return PRESENT, None, first
    version = found.group(1)

    for bad in tool.get("unsupported", []):
        if satisfies(version, bad["range"]):
            return UNSUPPORTED, version, bad["reason"]
    if not satisfies(version, tool.get("supported", "*")):
        return UNSUPPORTED, version, f"outside the supported range {tool['supported']}"
    for entry in tool.get("tested", []):
        if entry["version"] == version:
            return TESTED, version, entry["evidence"]
    if tool.get("supported", "*") == "*" and not tool.get("tested"):
        # Nothing is claimed about this tool's versions, so there is nothing to
        # be untested against. Saying "untested" here would train a reader to
        # skim past the word where it does mean something.
        return PRESENT, version, ""
    return UNTESTED, version, ""


def load():
    return json.loads(SPEC.read_text())


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------
MARK = {TESTED: "ok", PRESENT: "ok", UNTESTED: "untested",
        UNSUPPORTED: "UNSUPPORTED", MISSING: "missing"}


def report(spec, tiers=None):
    results = {}
    for tier_id, tier in spec["tiers"].items():
        if tiers and tier_id not in tiers:
            continue
        print(f"\n{tier['label']}")
        for tool_id, tool in spec["tools"].items():
            if tool["tier"] != tier_id:
                continue
            status, version, detail = probe(tool)
            results[tool_id] = (status, version, detail)
            shown = version or ("found" if status == PRESENT else "-")
            print(f"  {tool['name']:<26} {shown:<12} {MARK[status]}")
            if status == UNSUPPORTED:
                print(f"  {'':<26} {detail}")
                if tool.get("override"):
                    print(f"  {'':<26} override with {tool['override']}")
            elif status == UNTESTED:
                versions = ", ".join(e["version"] for e in tool.get("tested", [])) or "none"
                print(f"  {'':<26} accepted ({tool['supported']}) but not tested "
                      f"here; tested: {versions}")
            elif status == MISSING:
                if tool.get("missing_hint"):
                    print(f"  {'':<26} {tool['missing_hint']}")
                elif tool["required"]:
                    print(f"  {'':<26} required for this tier -- see docs/dependencies.md")
    return results


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------
def check(spec, tiers=None):
    problems = []
    for tool_id, tool in spec["tools"].items():
        if tiers and tool["tier"] not in tiers:
            continue
        status, version, detail = probe(tool)
        if status == UNSUPPORTED:
            problems.append(f"{tool['name']} {version}: {detail}")
        elif status == MISSING and tool["required"]:
            problems.append(f"{tool['name']} is required and was not found "
                            f"({tool['use'].lower()})")
    for problem in problems:
        print(f"requirements: FAIL {problem}", file=sys.stderr)
    if problems:
        print("requirements: see docs/dependencies.md; tools/requirements.json is "
              "the source of truth", file=sys.stderr)
        return 1
    scope = ", ".join(tiers) if tiers else "every tier"
    print(f"requirements: OK ({scope})")
    return 0


# --------------------------------------------------------------------------
# docs
# --------------------------------------------------------------------------
def table(spec):
    """The generated block for docs/dependencies.md.

    Generated rather than maintained beside the JSON, because a version table
    that is edited by hand is a version table that disagrees with the build the
    first time either one moves.
    """
    lines = [BEGIN, "", "<!-- Generated from tools/requirements.json by",
             "     `make requirements`. Do not edit this block by hand;",
             "     `make requirements-check` fails when it drifts. -->", ""]
    for tier_id, tier in spec["tiers"].items():
        tools = [t for t in spec["tools"].values() if t["tier"] == tier_id]
        if not tools:
            continue
        lines += [f"### {tier['label']}", "", tier["doc"], "",
                  "| Tool | Required | Supported | Tested here | Use |",
                  "|---|---|---|---|---|"]
        for tool in tools:
            tested = "<br>".join(e["version"] for e in tool.get("tested", [])) or "—"
            supported = tool.get("supported", "*")
            supported = "any" if supported == "*" else f"`{supported}`"
            bad = "".join(f"<br>**not** `{b['range']}` — {b['reason']}"
                          for b in tool.get("unsupported", []))
            lines.append(f"| {tool['name']} | {'yes' if tool['required'] else 'optional'} "
                         f"| {supported}{bad} | {tested} | {tool['use']} |")
        lines.append("")
    lines.append(END)
    return "\n".join(lines)


def write_docs(spec, verify):
    text = DEPENDENCIES.read_text()
    if BEGIN not in text or END not in text:
        raise SystemExit(f"{DEPENDENCIES}: no generated block; add {BEGIN} / {END}")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    updated = head + table(spec) + tail
    if verify:
        if updated != text:
            print("requirements: FAIL docs/dependencies.md is out of date with "
                  "tools/requirements.json -- run `make requirements`", file=sys.stderr)
            return 1
        print("requirements: docs/dependencies.md matches tools/requirements.json")
        return 0
    DEPENDENCIES.write_text(updated)
    print(f"requirements: wrote the generated block in {DEPENDENCIES}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["report", "check", "docs", "docs-check"])
    parser.add_argument("--tier", action="append", dest="tiers",
                        help="limit to a tier; repeatable")
    args = parser.parse_args()
    spec = load()
    if args.action == "report":
        report(spec, args.tiers)
        return 0
    if args.action == "check":
        return check(spec, args.tiers)
    return write_docs(spec, verify=args.action == "docs-check")


if __name__ == "__main__":
    sys.exit(main())
