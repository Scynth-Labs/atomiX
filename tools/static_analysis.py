#!/usr/bin/env python3
"""Static analysis across every language in the repository.

The build already refuses bad code in two of the three: Verilator runs -Wall on
whatever a profile elaborates, and every C target compiles -Wall -Wextra
-Werror.  Two gaps remain, and this closes them.

  RTL      A component is only linted if some profile that gets *built* selects
           it.  Here every profile in configs/ is resolved and linted, so a
           component nothing currently simulates is still checked.
  C        -Werror catches what the front end sees on one translation unit.
           -fanalyzer explores paths: leaks, double frees, null dereferences,
           use-after-free.  It is not on in the build because it is slow.
  C again  clang's analyzer over the same files at the same target. A second
           engine disagrees with the first often enough to be worth the seconds
           it costs; TOOLCHAIN=llvm is what makes the target build valid clang
           input at all.
  Python   ~9,000 lines across 29 tools with no linter at all -- the one place
           in this repository where nothing was checking anything.
  Shell    shellcheck, when it is installed.

Every analyzer names the binary it needs.  A missing binary is reported as
SKIPPED with the reason, never as a pass -- the same rule the Trellis geometry
check follows, because a check that silently stops checking is worse than one
that is absent.

  python3 tools/static_analysis.py                 # human summary, non-zero on findings
  python3 tools/static_analysis.py --json out.json # machine-readable
  python3 tools/static_analysis.py --sarif out.sarif
  python3 tools/static_analysis.py --only ruff
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "static-analysis"

# The kernel links these component sources; analysing them with the kernel's own
# flags is the only way -fanalyzer sees the calls across the seam.
KERNEL_COMPONENTS = [
    "components/syscall/linux-compat/syscall.c",
    "components/loader/elf32/loader.c",
    "components/allocator/free-list/page.c",
    "components/shell/axsh/shell.c",
    "components/filesystem/axfs/fs.c",
    "components/block/spi-sd/sd.c",
    "components/scheduler/round-robin/scheduler.c",
    "components/vm/sv32/vm.c",
    "components/evolution/core/evolution.c",
    "components/fitness/core/fitness.c",
]

FREESTANDING = [
    "-march=rv32im", "-mabi=ilp32", "-mcmodel=medany", "-mno-relax",
    "-msmall-data-limit=0", "-ffreestanding", "-fno-builtin", "-fno-pic",
    "-fno-stack-protector",
]

KERNEL_DEFINES = [
    "-DAXOS_SD=0", "-DAXFS_BLOCK=0", "-DAX_EVOLUTION_TIER=0",
    "-DAX_EVOLUTION_CAPABILITIES=0", "-DAX_EVOLUTION_CAPACITY=0",
    "-DAX_EVOLUTION_STATE_BUDGET=0", "-DAX_FITNESS_ENABLED=0",
    "-DAX_FITNESS_OBJECTIVE=0",
]

# Compile units, each mirroring the flags its Makefile uses.  If these drift
# from the real build the analyzer stops finding a header and reports it, which
# is the intended failure: a unit that no longer matches its build is not
# analysing the thing that ships.
C_UNITS = [
    {
        "name": "kernel",
        "includes": ["-Isw/kernel/include", "-Isw/kernel/build",
                     "-Icomponents/libc/axlibc/include"],
        "defines": KERNEL_DEFINES,
        "sources": sorted(str(p.relative_to(ROOT))
                          for p in (ROOT / "sw/kernel").glob("*.c")
                          if not p.name.startswith("check_")) + KERNEL_COMPONENTS,
    },
    {
        "name": "baremetal",
        "includes": ["-Isw/baremetal/include", "-Isw/baremetal/build"],
        "defines": [],
        "sources": sorted(str(p.relative_to(ROOT))
                          for p in (ROOT / "sw/baremetal").rglob("*.c")),
    },
    {
        "name": "axlibc",
        "includes": ["-Icomponents/libc/axlibc/include"],
        "defines": [],
        "sources": sorted(str(p.relative_to(ROOT))
                          for p in (ROOT / "components/libc/axlibc").glob("*.c")),
    },
]

GCC_LINE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+): (?P<sev>warning|error|note): "
    r"(?P<msg>.*?)(?: \[(?P<rule>-W[\w=-]+|CWE-\d+.*)\])?$")


def finding(tool, path, line, col, sev, rule, message):
    try:
        path = str(pathlib.Path(path).resolve().relative_to(ROOT))
    except ValueError:
        path = str(path)
    return {"tool": tool, "file": path, "line": int(line or 1),
            "column": int(col or 1), "severity": sev, "rule": rule or tool,
            "message": message}


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, **kw)


def parse_gcc(tool, text):
    out = []
    for raw in text.splitlines():
        m = GCC_LINE.match(raw.strip())
        if not m or m.group("sev") == "note":
            continue
        out.append(finding(tool, m.group("file"), m.group("line"), m.group("col"),
                           "error" if m.group("sev") == "error" else "warning",
                           m.group("rule"), m.group("msg")))
    return out


# --------------------------------------------------------------------------
# analyzers.  Each returns (findings, note) and may raise Skip.

class Skip(Exception):
    pass


def need(binary):
    path = shutil.which(binary)
    if path is None:
        raise Skip(f"{binary} is not installed")
    return path


def analyse_rtl():
    """Lint every profile's resolved design, not just the ones that get built."""
    verilator = need("verilator")
    BUILD.mkdir(parents=True, exist_ok=True)
    version = run([verilator, "--version"]).stdout.split()
    major = int(version[1].split(".")[0]) if len(version) > 1 else 0
    flags = ["--lint-only", "-Wall"]
    if major >= 5:
        # Shared component packages declare operation constants that a given
        # selection does not consume; that is component API, not dead hardware.
        flags.append("-Wno-UNUSEDPARAM")

    findings, linted = [], 0
    for profile in sorted((ROOT / "configs").glob("*.json")):
        mk = BUILD / f"{profile.stem}.mk"
        resolved = run([sys.executable, "tools/configure.py", "resolve",
                        "--config", str(profile), "--output", str(mk)])
        if resolved.returncode != 0:
            findings.append(finding("verilator", profile, 1, 1, "error",
                                    "profile-unresolvable",
                                    resolved.stderr.strip().splitlines()[-1:] or
                                    ["profile does not resolve"]))
            continue
        text = mk.read_text()
        top = re.search(r"^COMPONENT_SIM_TOP := (\S+)", text, re.M)
        if not top:
            continue                      # synthesis-only profile: no elaboration top
        svs, seen = [], set()
        for line in re.findall(r"^COMPONENT_\w+_SOURCES := (.*)$", text, re.M):
            for src in line.split():
                if src.endswith(".sv") and src not in seen:
                    seen.add(src)
                    svs.append(src)
        defines = re.search(r"^COMPONENT_DEFINES := (.*)$", text, re.M)
        cmd = [verilator, *flags, "--top-module", top.group(1)]
        if defines:
            cmd += defines.group(1).split()
        result = run(cmd + svs)
        linted += 1
        findings += parse_verilator(result.stdout + result.stderr, profile.name)
    return findings, f"{linted} profile(s) elaborated and linted"


VERILATOR_LINE = re.compile(
    r"^%(?P<sev>Warning|Error)(?:-(?P<rule>[A-Z0-9_]+))?: "
    r"(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+): (?P<msg>.*)$")


def parse_verilator(text, profile):
    out = []
    for raw in text.splitlines():
        m = VERILATOR_LINE.match(raw.strip())
        if not m:
            continue
        out.append(finding("verilator", m.group("file"), m.group("line"),
                           m.group("col"),
                           "error" if m.group("sev") == "Error" else "warning",
                           m.group("rule") or "LINT",
                           f"{m.group('msg')} [profile {profile}]"))
    return out


def analyse_c():
    """-fanalyzer over the freestanding C, with each unit's own build flags."""
    cc = need("riscv64-unknown-elf-gcc")
    findings, files = [], 0
    for unit in C_UNITS:
        for src in unit["sources"]:
            if not (ROOT / src).exists():
                continue
            files += 1
            result = run([cc, "-fsyntax-only", "-fanalyzer", "-Wall", "-Wextra",
                          *FREESTANDING, *unit["includes"], *unit["defines"], src])
            findings += parse_gcc("gcc-analyzer", result.stderr)
    return findings, f"{files} translation unit(s) analysed"


def analyse_clang():
    """clang's analyzer over the same freestanding C, at the same target.

    Not redundant with -fanalyzer: GCC's is a path exploration over its own IR
    and clang's is symbolic execution over a different one, and in practice they
    disagree about what they find.  Running it against
    `--target=riscv32-unknown-elf` rather than the host matters -- pointer width
    and ABI change which paths are even reachable, so a host-targeted run is
    analysing a program the machine never executes.

    This is only possible because TOOLCHAIN=llvm made clang able to compile the
    target in the first place; before that there was no set of flags under which
    these files were valid clang input."""
    clang = need("clang")
    findings, files = [], 0
    for unit in C_UNITS:
        for src in unit["sources"]:
            if not (ROOT / src).exists():
                continue
            files += 1
            result = run([clang, "--analyze", "--analyzer-output", "text",
                          "--target=riscv32-unknown-elf", "-Wall", "-Wextra",
                          *FREESTANDING, *unit["includes"], *unit["defines"],
                          "-o", os.devnull, src])
            findings += parse_gcc("clang-analyzer", result.stderr)
    return findings, f"{files} translation unit(s) analysed at riscv32"


def analyse_python():
    ruff = need("ruff")
    result = run([ruff, "check", "--output-format", "json", "."])
    findings = []
    for item in json.loads(result.stdout or "[]"):
        findings.append(finding("ruff", item["filename"],
                                (item.get("location") or {}).get("row", 1),
                                (item.get("location") or {}).get("column", 1),
                                "warning", item.get("code") or "ruff",
                                item["message"]))
    files = len(list(ROOT.glob("**/*.py")))
    return findings, f"{files} file(s) checked against ruff.toml"


def analyse_shell():
    sc = need("shellcheck")
    scripts = sorted(str(p.relative_to(ROOT)) for p in ROOT.glob("**/*.sh")
                     if ".git" not in p.parts and "build" not in p.parts)
    if not scripts:
        return [], "no shell scripts"
    result = run([sc, "--format", "json1", *scripts])
    payload = json.loads(result.stdout or '{"comments":[]}')
    findings = [finding("shellcheck", c["file"], c["line"], c["column"],
                        c["level"], f"SC{c['code']}", c["message"])
                for c in payload.get("comments", [])]
    return findings, f"{len(scripts)} script(s) checked"


def analyse_cpp():
    """The host-side C++ -- ISS and Verilator harness -- via cppcheck."""
    cppcheck = need("cppcheck")
    sources = sorted(str(p.relative_to(ROOT))
                     for p in (ROOT / "sim").rglob("*.cpp")
                     if "obj_dir" not in str(p) and "build" not in p.parts)
    sources += sorted(str(p.relative_to(ROOT))
                      for p in (ROOT / "components/harness").rglob("*.cpp"))
    if not sources:
        return [], "no host C++ sources"
    result = run([cppcheck, "--enable=warning,performance,portability",
                  "--inline-suppr", "--quiet",
                  "--template={file}:{line}:{column}: {severity}: {message} [{id}]",
                  *sources])
    findings = []
    for raw in result.stderr.splitlines():
        m = re.match(r"^(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+): "
                     r"(?P<sev>\w+): (?P<msg>.*?) \[(?P<rule>[\w-]+)\]$", raw)
        if m:
            findings.append(finding("cppcheck", m.group("file"), m.group("line"),
                                    m.group("col"), "warning", m.group("rule"),
                                    m.group("msg")))
    return findings, f"{len(sources)} host C++ file(s) checked"


ANALYZERS = [
    ("rtl", "SystemVerilog lint, every profile", analyse_rtl),
    ("c", "Freestanding C, GCC path analyzer", analyse_c),
    ("clang", "Freestanding C, clang analyzer", analyse_clang),
    ("cpp", "Host C++", analyse_cpp),
    ("ruff", "Python", analyse_python),
    ("shell", "Shell", analyse_shell),
]


def content_addressed():
    """Files whose SHA-256 a record under research/ pins.

    A finding in one of these cannot simply be fixed: the edit breaks a digest
    and makes an evidence record describe code that no longer exists.  Saying so
    next to the finding is the difference between a five-minute fix and the
    twenty minutes I spent discovering it twice."""
    pinned = set()

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("path"), str) and "sha256" in node:
                pinned.add(node["path"])
            if isinstance(node.get("sources"), dict):
                pinned.update(node["sources"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for record in (ROOT / "research").rglob("*.json"):
        try:
            walk(json.loads(record.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return pinned


def sarif(findings):
    levels = {"error": "error", "warning": "warning", "info": "note",
              "style": "note", "note": "note"}
    by_tool = {}
    for f in findings:
        by_tool.setdefault(f["tool"], []).append(f)
    runs = []
    for tool, group in sorted(by_tool.items()):
        rules = sorted({f["rule"] for f in group})
        runs.append({
            "tool": {"driver": {
                "name": f"atomiX/{tool}",
                "informationUri": "https://github.com/ShubhendraGautam/atomiX",
                "rules": [{"id": r, "shortDescription": {"text": r}} for r in rules],
            }},
            "results": [{
                "ruleId": f["rule"],
                "ruleIndex": rules.index(f["rule"]),
                "level": levels.get(f["severity"], "warning"),
                "message": {"text": f["message"]},
                "locations": [{"physicalLocation": {
                    "artifactLocation": {"uri": f["file"]},
                    "region": {"startLine": f["line"], "startColumn": f["column"]},
                }}],
            } for f in group],
        })
    return {"version": "2.1.0",
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "runs": runs or [{"tool": {"driver": {"name": "atomiX/static-analysis"}},
                              "results": []}]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", action="append", metavar="NAME",
                    help="run just this analyzer (repeatable)")
    ap.add_argument("--json", type=pathlib.Path, help="write the findings as JSON")
    ap.add_argument("--sarif", type=pathlib.Path, help="write SARIF for code scanning")
    ap.add_argument("--allow-skips", action="store_true",
                    help="exit 0 when an analyzer's tool is absent (CI installs them)")
    args = ap.parse_args(argv)

    selected = [a for a in ANALYZERS if not args.only or a[0] in args.only]
    if args.only and not selected:
        ap.error(f"no such analyzer: {', '.join(args.only)}")

    findings, skipped, report = [], [], []
    for name, label, fn in selected:
        try:
            found, note = fn()
        except Skip as exc:
            skipped.append(name)
            report.append((name, label, "SKIPPED", str(exc)))
            continue
        findings += found
        report.append((name, label, "FAIL" if found else "PASS",
                       f"{len(found)} finding(s); {note}" if found else note))

    width = max(len(r[1]) for r in report) if report else 0
    print("static analysis")
    for name, label, state, note in report:
        print(f"  {state:<8} {label:<{width}}  {note}")

    if findings:
        pinned = content_addressed()
        print()
        for f in sorted(findings, key=lambda f: (f["tool"], f["file"], f["line"])):
            sealed = "  (content-addressed: fixing this needs a re-seal)" \
                if f["file"] in pinned else ""
            f["content_addressed"] = f["file"] in pinned
            print(f"  {f['file']}:{f['line']}:{f['column']}: "
                  f"{f['severity']}: {f['message']} [{f['rule']}]{sealed}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(
            {"schema": "org.atomix.static-analysis.v1",
             "findings": findings,
             "analyzers": [{"name": n, "state": s, "note": t}
                           for n, _, s, t in report]}, indent=2) + "\n")
    if args.sarif:
        args.sarif.parent.mkdir(parents=True, exist_ok=True)
        args.sarif.write_text(json.dumps(sarif(findings), indent=2) + "\n")

    print()
    if findings:
        print(f"static analysis: {len(findings)} finding(s)")
        return 1
    if skipped and not args.allow_skips:
        print(f"static analysis: clean, but {len(skipped)} analyzer(s) could not "
              f"run ({', '.join(skipped)})")
        return 2
    print("static analysis: clean" +
          (f" ({len(skipped)} analyzer(s) skipped)" if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
