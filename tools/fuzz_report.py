#!/usr/bin/env python3
"""Run the fuzzers and turn what the sanitizers say into findings.

A sanitizer report is the most actionable thing this repository produces: it
names a defect, the line it happened on, and the stack that got there.  Left in
a workflow log it is also the most easily lost, because nobody reads a green
job and a red one scrolls.  This runs the fuzz targets, parses AddressSanitizer,
LeakSanitizer, UndefinedBehaviorSanitizer and libFuzzer's own diagnostics into
the same `org.atomix.static-analysis.v1` schema `tools/static_analysis.py`
emits, and writes it where `tools/analysis_issue.py` can put it in the issue
alongside the lint.

The two halves answer different questions and belong in one place: static
analysis says what is wrong with code nobody ran, a sanitizer says what went
wrong while it ran.

  python3 tools/fuzz_report.py --json build/static-analysis/fuzz.json
  python3 tools/fuzz_report.py --timeout 600        # a longer nightly budget
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# A crashing input is saved next to the harness; the corpus README explains how
# one gets promoted to a checked-in regression seed.
ARTIFACT_GLOBS = ("crash-*", "leak-*", "timeout-*", "oom-*")

TARGETS = [
    {"name": "loader", "dir": "sim/fuzz", "target": "run-loader",
     "what": "loader.elf32 against malformed ELF images"},
    {"name": "axfs", "dir": "sim/fuzz", "target": "run-axfs",
     "what": "filesystem.axfs against malformed on-disk metadata"},
    {"name": "axk1-upload-format", "dir": "sim/fuzz", "target": "run-axk1-format",
     "what": "immutable UART loader against malformed AXK1 envelopes"},
]

# ==12345==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x...
SANITIZER = re.compile(
    r"==\d+==ERROR: (?P<san>AddressSanitizer|LeakSanitizer|"
    r"UndefinedBehaviorSanitizer): (?P<kind>[^\s]+)(?P<rest>.*)")
# libFuzzer's own verdicts, which are not sanitizer reports.
LIBFUZZER = re.compile(r"ERROR: libFuzzer: (?P<kind>deadly signal|timeout|"
                       r"out-of-memory|fuzz target exited)")
# `file.c:12:34: runtime error: <what>` -- UBSan's non-fatal form.
UBSAN_INLINE = re.compile(
    r"^(?P<file>[^\s:]+):(?P<line>\d+):(?P<col>\d+): runtime error: (?P<msg>.+)$")
# A stack frame: `    #3 0x... in fn /abs/path/file.c:120:7`
FRAME = re.compile(r"^\s*#(?P<depth>\d+) 0x[0-9a-f]+ in (?P<fn>\S+) "
                   r"(?P<file>[^\s:]+):(?P<line>\d+)(?::(?P<col>\d+))?")
LEAK_HEADER = re.compile(
    r"(?P<kind>Direct|Indirect) leak of (?P<bytes>\d+) byte\(s\) in "
    r"(?P<objects>\d+) object\(s\)")


def relative(path):
    try:
        return str(pathlib.Path(path).resolve().relative_to(ROOT))
    except (ValueError, OSError):
        return path


def first_repo_frame(lines, start):
    """The first stack frame inside this repository.

    Sanitizer stacks begin in the sanitizer's own interceptors and in libFuzzer;
    the frame worth putting on an issue is the first one in code we wrote."""
    for line in lines[start:start + 40]:
        m = FRAME.match(line)
        if not m:
            if line.strip() == "" and start:
                break
            continue
        rel = relative(m.group("file"))
        if not rel.startswith("/") and "sanitizer" not in rel:
            return rel, int(m.group("line")), int(m.group("col") or 1), m.group("fn")
    return None, 1, 1, None


def finding(tool, rule, path, line, col, message):
    return {"tool": tool, "rule": rule, "file": path, "line": line,
            "column": col, "severity": "error", "message": message}


def parse(output, target):
    """Sanitizer and libFuzzer diagnostics, as findings."""
    lines = output.splitlines()
    findings = []
    for i, line in enumerate(lines):
        m = UBSAN_INLINE.match(line.strip())
        if m:
            findings.append(finding(
                "ubsan", "runtime-error", relative(m.group("file")),
                int(m.group("line")), int(m.group("col")),
                f"{m.group('msg')} [{target}]"))
            continue

        m = LEAK_HEADER.search(line)
        if m:
            path, ln, col, fn = first_repo_frame(lines, i + 1)
            findings.append(finding(
                "lsan", f"{m.group('kind').lower()}-leak", path or f"{target}",
                ln, col,
                f"{m.group('kind').lower()} leak of {m.group('bytes')} byte(s) "
                f"in {m.group('objects')} object(s)"
                + (f", allocated in {fn}" if fn else "")
                + f" [{target}]"))
            continue

        m = SANITIZER.match(line.strip())
        if m and m.group("san") != "LeakSanitizer":
            path, ln, col, fn = first_repo_frame(lines, i + 1)
            tool = {"AddressSanitizer": "asan",
                    "UndefinedBehaviorSanitizer": "ubsan"}[m.group("san")]
            findings.append(finding(
                tool, m.group("kind"), path or f"{target}", ln, col,
                f"{m.group('san')}: {m.group('kind')}"
                + (f" in {fn}" if fn else "") + f" [{target}]"))
            continue

        m = LIBFUZZER.search(line)
        if m:
            path, ln, col, fn = first_repo_frame(lines, i + 1)
            findings.append(finding(
                "libfuzzer", m.group("kind").replace(" ", "-"),
                path or f"{target}", ln, col,
                f"libFuzzer: {m.group('kind')}"
                + (f" in {fn}" if fn else "") + f" [{target}]"))
    return findings


def saved_artifacts(directory):
    out = []
    for pattern in ARTIFACT_GLOBS:
        out += sorted(str(p.relative_to(ROOT)) for p in directory.glob(pattern))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", type=pathlib.Path,
                    default=ROOT / "build/static-analysis/fuzz.json")
    ap.add_argument("--timeout", type=int, default=120,
                    help="seconds per target (the nightly budget is larger)")
    ap.add_argument("--runs", type=int, default=200000)
    args = ap.parse_args(argv)

    findings, reports = [], []
    for target in TARGETS:
        directory = ROOT / target["dir"]
        result = subprocess.run(
            ["make", "-C", target["dir"], target["target"],
             f"TIMEOUT={args.timeout}", f"RUNS={args.runs}"],
            cwd=ROOT, capture_output=True, text=True)
        output = result.stdout + result.stderr
        found = parse(output, target["name"])

        # A non-zero exit with nothing parsed is still a failure, and saying
        # "clean" because the output did not match a regex would be the worst
        # possible outcome for a tool whose whole job is not losing findings.
        if result.returncode != 0 and not found:
            found = [finding("libfuzzer", "nonzero-exit", target["dir"], 1, 1,
                             f"fuzz target '{target['name']}' exited "
                             f"{result.returncode} with no diagnostic this tool "
                             f"recognised; see the run log")]
        findings += found
        reports.append({"name": target["name"], "what": target["what"],
                        "state": "FAIL" if found else "PASS",
                        "note": f"{len(found)} finding(s)" if found else
                                f"clean over {args.runs} runs / {args.timeout}s",
                        "artifacts": saved_artifacts(directory)})
        print(f"  {'FAIL' if found else 'PASS':<8} {target['what']}")
        for f in found:
            print(f"    {f['file']}:{f['line']}: {f['message']} [{f['rule']}]")
        for artifact in reports[-1]["artifacts"]:
            print(f"    saved input: {artifact}")

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(
        {"schema": "org.atomix.static-analysis.v1",
         "findings": findings,
         "analyzers": [{"name": f"fuzz-{r['name']}", "state": r["state"],
                        "note": r["note"]} for r in reports]}, indent=2) + "\n")

    print()
    if findings:
        print(f"fuzzing: {len(findings)} finding(s)")
        return 1
    print("fuzzing: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
