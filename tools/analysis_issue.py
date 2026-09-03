#!/usr/bin/env python3
"""Keep one GitHub issue in sync with the static-analysis report.

A findings list that is only in a workflow log is a findings list nobody reads.
This turns the report into the one place a finding is supposed to live: an open
issue, with the file and line, the command that reproduces it, and the commit it
was found on.

One issue, reused.  A new issue per run would bury the repository in duplicates
within a week, so the tool finds the open issue carrying its marker and edits it
in place.  It comments only when the set of findings actually changes -- a run
that finds the same things says nothing, because a notification that arrives
every night is a notification nobody opens.  When the report comes back clean
the issue is closed with the commit that cleared it.

  python3 tools/analysis_issue.py --report build/static-analysis/report.json \
                                 --report build/static-analysis/fuzz.json
  python3 tools/analysis_issue.py --report ... --dry-run    # print, change nothing

Needs `gh` authenticated with `issues: write`.  In Actions that is GITHUB_TOKEN
plus `permissions: issues: write`.
"""
import argparse
import collections
import hashlib
import json
import os
import pathlib
import subprocess
import sys

MARKER = "<!-- atomix-static-analysis -->"
# Findings from tools/fuzz_report.py rather than tools/static_analysis.py: they
# reproduce with a different command.
FUZZ_TOOLS = {"asan", "lsan", "ubsan", "libfuzzer"}
LABEL = "static-analysis"
TITLE = "Static analysis findings"
MAX_LISTED = 60
BODY_LIMIT = 60000


def gh(*args, check=True):
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)}: {result.stderr.strip()}")
    return result


def fingerprint(findings):
    """Identity of the finding *set*, so an unchanged report stays quiet.

    Deliberately excludes line and column: the same defect moving down a file
    because something was inserted above it is not news."""
    seed = sorted(f"{f['tool']}\t{f['file']}\t{f['rule']}\t{f['message']}"
                  for f in findings)
    return hashlib.sha256("\n".join(seed).encode()).hexdigest()[:16]


def body(report, sha, run_url):
    findings = report["findings"]
    by_tool = collections.Counter(f["tool"] for f in findings)
    by_file = collections.Counter(f["file"] for f in findings)

    out = [MARKER, f"<!-- fingerprint: {fingerprint(findings)} -->", ""]
    out.append(f"**{len(findings)} finding(s)** across {len(by_file)} file(s), "
               f"reported on `{sha[:12]}`.")
    out.append("")
    out.append("| Analyzer | Findings |")
    out.append("|---|---|")
    for tool, count in by_tool.most_common():
        out.append(f"| `{tool}` | {count} |")
    out.append("")

    skipped = [a for a in report.get("analyzers", []) if a["state"] == "SKIPPED"]
    if skipped:
        out.append("Analyzers that could not run, and so found nothing by "
                   "definition rather than by inspection:")
        out += [f"- `{a['name']}` — {a['note']}" for a in skipped] + [""]

    out.append("## Findings")
    out.append("")
    shown = sorted(findings, key=lambda f: (f["tool"], f["file"], f["line"]))
    for f in shown[:MAX_LISTED]:
        sealed = ("  \n  ⚠️ This file is content-addressed by a record under "
                  "`research/`. Fixing it changes a pinned SHA-256 and needs "
                  "the owning experiment re-sealed — see `ruff.toml`.") \
            if f.get("content_addressed") else ""
        out.append(f"- **`{f['file']}:{f['line']}`** — {f['message']} "
                   f"(`{f['rule']}`, {f['tool']}){sealed}")
    if len(shown) > MAX_LISTED:
        out.append(f"- …and {len(shown) - MAX_LISTED} more; the full list is in "
                   f"the `static-analysis-report` artifact.")

    # Name the command that reproduces *these* findings. A sanitizer report and
    # a lint finding come from different runs, and telling someone to run the
    # static analysis to reproduce a leak wastes the first thing they try.
    tools = set(by_tool)
    commands = []
    if tools - FUZZ_TOOLS:
        commands.append("make static-analysis")
    if tools & FUZZ_TOOLS:
        commands.append("make fuzz-loader          # ASan, LSan, UBSan")
        commands.append("make -C sim/fuzz explore  # unbounded, to reach it faster")
    out += ["", "## Reproduce", "", "```bash", *commands, "```", ""]
    if run_url:
        out.append(f"[Workflow run]({run_url})")
    out.append("")
    out.append("_Maintained by `tools/analysis_issue.py`; edited in place rather "
               "than reopened, and closed automatically when the report is "
               "clean._")

    text = "\n".join(out)
    return text[:BODY_LIMIT] if len(text) > BODY_LIMIT else text


def existing_issue(repo):
    result = gh("issue", "list", "--repo", repo, "--label", LABEL,
                "--state", "open", "--json", "number,body", "--limit", "50")
    for issue in json.loads(result.stdout or "[]"):
        if MARKER in (issue.get("body") or ""):
            return issue
    return None


def ensure_label(repo):
    gh("label", "create", LABEL, "--repo", repo, "--color", "B60205",
       "--description", "Reported by tools/static_analysis.py", check=False)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", type=pathlib.Path, action="append", required=True,
                    metavar="PATH",
                    help="a findings report; repeat to merge several. Static "
                         "analysis and the sanitizer output belong in one "
                         "issue: they are two answers to the same question.")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    ap.add_argument("--sha", default=os.environ.get("GITHUB_SHA", "working tree"))
    ap.add_argument("--run-url", default=(
        f"{os.environ.get('GITHUB_SERVER_URL', '')}/"
        f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/"
        f"{os.environ.get('GITHUB_RUN_ID', '')}"
        if os.environ.get("GITHUB_RUN_ID") else ""))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not args.repo:
        ap.error("--repo is required outside Actions")
    report = {"findings": [], "analyzers": []}
    for path in args.report:
        if not path.exists():
            # A stage that never ran leaves no report. Say so in the issue
            # rather than silently reporting on less than was asked for.
            report["analyzers"].append(
                {"name": path.stem, "state": "SKIPPED",
                 "note": f"{path} was not written; that stage did not run"})
            continue
        loaded = json.loads(path.read_text())
        report["findings"] += loaded.get("findings", [])
        report["analyzers"] += loaded.get("analyzers", [])
    findings = report["findings"]
    text = body(report, args.sha, args.run_url)

    if args.dry_run:
        print(f"repo={args.repo} findings={len(findings)} "
              f"fingerprint={fingerprint(findings)}")
        print("-" * 60)
        print(text if findings else "(clean: would close any open issue)")
        return 0

    issue = existing_issue(args.repo)

    if not findings:
        if issue:
            gh("issue", "comment", str(issue["number"]), "--repo", args.repo,
               "--body", f"Static analysis is clean as of `{args.sha[:12]}`. "
                         f"Closing; it will reopen as a new issue if findings "
                         f"return.")
            gh("issue", "close", str(issue["number"]), "--repo", args.repo)
            print(f"closed #{issue['number']}: report is clean")
        else:
            print("clean, and no open issue to close")
        return 0

    ensure_label(args.repo)
    if issue is None:
        result = gh("issue", "create", "--repo", args.repo, "--title", TITLE,
                    "--label", LABEL, "--body", text)
        print(f"opened {result.stdout.strip()} with {len(findings)} finding(s)")
        return 0

    was = ""
    for line in (issue.get("body") or "").splitlines():
        if line.startswith("<!-- fingerprint:"):
            was = line.split(":", 1)[1].strip(" ->")
    gh("issue", "edit", str(issue["number"]), "--repo", args.repo, "--body", text)
    now = fingerprint(findings)
    if was != now:
        gh("issue", "comment", str(issue["number"]), "--repo", args.repo,
           "--body", f"The finding set changed on `{args.sha[:12]}`: "
                     f"{len(findings)} finding(s) now. The issue body above is "
                     f"the current list.")
        print(f"updated #{issue['number']} and commented: {was or 'none'} -> {now}")
    else:
        print(f"updated #{issue['number']}; finding set unchanged ({now}), "
              f"so no comment")
    return 0


if __name__ == "__main__":
    sys.exit(main())
