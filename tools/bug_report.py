#!/usr/bin/env python3
"""Export a session as a record anyone can replay, and replay one.

"A bug report can be a URL that boots the machine that failed" is the goal; a
record that reconstructs the same machine, the same program, and the same
keystrokes from a clean session is the substance of it, and the half that does
not need a browser.

What a report has to carry is everything that would otherwise be supplied
silently by whoever replays it: which profile, which payload *and its hash*,
where it was loaded, what was typed, how long it was given, and what happened.
The hash is the part that matters most.  A report that named a path and let the
replay use whatever is at that path today would reproduce the current program's
behaviour and call it the reported one -- which is worse than not reproducing
at all, because it looks like an answer.  So a payload whose bytes have moved
on is refused by name, with both hashes, and the report says how it was built
so it can be rebuilt.

  export   run a session and write the record
  replay   rebuild the machine from a record and compare
  self-test  a passing case, a failing case, a stale payload, a missing one
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "tests/examples.json"
SCHEMA = "org.atomix.bug-report.v1"


def run(command, **kwargs):
    return subprocess.run([str(arg) for arg in command], cwd=ROOT, text=True,
                          capture_output=True, timeout=1800, **kwargs)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def resolve(profile: str) -> dict[str, str]:
    result = run([sys.executable, "tools/configure.py", "resolve",
                  "--config", ROOT / profile])
    if result.returncode:
        raise SystemExit(f"[report] {profile} does not resolve:\n{result.stderr}")
    values = {}
    for line in result.stdout.splitlines():
        if ":=" in line:
            key, _, value = line.partition(":=")
            values[key.strip()] = value.strip()
    return values


def identity(resolved: dict[str, str]) -> dict[str, str]:
    """The fields that decide whether this is the same machine."""
    return {field: resolved.get(key, "")
            for key, field in (("COMPONENT_CONFIG_NAME", "name"),
                               ("COMPONENT_CORE_ID", "core"),
                               ("COMPONENT_MEMORY_ID", "memory"),
                               ("COMPONENT_CACHE_ID", "cache"),
                               ("COMPONENT_ROLE_ID", "role"),
                               ("COMPONENT_HARNESS_ID", "harness"),
                               ("COMPONENT_RESET_PC", "reset_pc"),
                               ("COMPONENT_RAM_BYTES", "ram_bytes"),
                               ("COMPONENT_DEFINES", "defines"))}


def build_model(profile: str, build_id: str) -> Path:
    result = run(["make", "-s", "--no-print-directory", "-C", "sim/soc",
                  "model-path", f"COMPONENT_CONFIG={ROOT / profile}",
                  "RAM_INIT_FILE=", "ROM_INIT_FILE=", f"BUILD_ID={build_id}"])
    if result.returncode:
        raise SystemExit(f"[report] the model did not build:\n"
                         f"{result.stdout}{result.stderr}")
    return Path(result.stdout.strip().splitlines()[-1])


def session(model: Path, payload: Path, input_path: Path | None,
            max_cycles: int) -> dict:
    command = [model, "--ram-image", payload, "--max-cycles", str(max_cycles)]
    if input_path is not None:
        command += ["--uart-input-file", input_path]
    result = run(command)
    cycles = re.search(r"cycles=(\d+)", result.stderr)
    return {
        "output": result.stdout,
        "cycles": int(cycles.group(1)) if cycles else None,
        "exit_code": result.returncode,
        "status": "finished" if result.returncode == 0 else "did-not-finish",
        "runner_stderr": result.stderr.strip(),
    }


def git(*args: str) -> str | None:
    result = run(["git", *args])
    return result.stdout.strip() if result.returncode == 0 else None


def do_export(args) -> int:
    examples = json.loads(EXAMPLES.read_text(encoding="utf-8"))["examples"]
    if args.example not in examples:
        raise SystemExit(f"[report] unknown example {args.example!r}; known: "
                         f"{', '.join(sorted(examples))}")
    example = examples[args.example]
    build = run(example["payload_build"])
    if build.returncode:
        raise SystemExit(f"[report] cannot build the payload:\n{build.stderr}")
    payload = ROOT / example["payload"]
    max_cycles = args.max_cycles or example["max_cycles"]
    input_path = ROOT / example["input"] if example["input"] else None
    model = build_model(example["profile"], "bug-report")
    observed = session(model, payload, input_path, max_cycles)

    record = {
        "schema": SCHEMA,
        "created_at": utc_now(),
        "from_example": args.example,
        "machine": {
            "profile": example["profile"],
            "identity": identity(resolve(example["profile"])),
        },
        "software": {
            "payload": example["payload"],
            "payload_sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
            "build": example["payload_build"],
        },
        "session": {
            "entry": example["entry"],
            "input": example["input"],
            "input_text": input_path.read_text(encoding="utf-8")
                          if input_path else None,
            "max_cycles": max_cycles,
        },
        "observed": observed,
        "tools": {"verilator": run([os.environ.get("VERILATOR", "verilator"),
                                    "--version"]).stdout.strip()},
        "source": {"git_revision": git("rev-parse", "HEAD"),
                   "worktree_clean": git("status", "--porcelain") == ""},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"[report] wrote {args.output} -- {record['machine']['identity']['name']}, "
          f"{example['payload']}, {observed['status']}")
    return 0


def do_replay(args) -> int:
    record = json.loads(args.record.read_text(encoding="utf-8"))
    if record.get("schema") != SCHEMA:
        raise SystemExit(f"[report] {args.record}: unsupported schema "
                         f"{record.get('schema')!r}")
    profile = record["machine"]["profile"]
    print(f"[report] replaying {args.record.name}: "
          f"{record['machine']['identity']['name']}, "
          f"{record['software']['payload']}, reported "
          f"{record['observed']['status']}")

    now = identity(resolve(profile))
    drifted = {field: (was, now.get(field))
               for field, was in record["machine"]["identity"].items()
               if now.get(field) != was}
    for field, (was, is_now) in sorted(drifted.items()):
        print(f"[report] the machine has changed: {field} was {was!r}, "
              f"is now {is_now!r}")

    payload = ROOT / record["software"]["payload"]
    if not payload.is_file():
        print(f"[report] REFUSED: {record['software']['payload']} is not here. "
              f"The report says it was built by: "
              f"{' '.join(record['software']['build'])}", file=sys.stderr)
        return 1
    have = hashlib.sha256(payload.read_bytes()).hexdigest()
    if have != record["software"]["payload_sha256"]:
        print(f"[report] REFUSED: {record['software']['payload']} is not the "
              f"program this report is about.\n"
              f"  reported {record['software']['payload_sha256']}\n"
              f"  found    {have}\n"
              "  Replaying it would reproduce today's program and call the "
              "result the reported one. Rebuild the reported payload with: "
              f"{' '.join(record['software']['build'])}", file=sys.stderr)
        return 1

    input_path = None
    scratch = None
    if record["session"]["input_text"] is not None:
        # From the record, not from the tree: the point of an exported session
        # is that it carries its own keystrokes.
        scratch = tempfile.TemporaryDirectory(prefix="atomix-report-")
        input_path = Path(scratch.name) / "input.txt"
        input_path.write_text(record["session"]["input_text"], encoding="utf-8")

    model = build_model(profile, "bug-report-replay")
    observed = session(model, payload, input_path,
                       record["session"]["max_cycles"])
    if scratch:
        scratch.cleanup()

    same_output = observed["output"] == record["observed"]["output"]
    same_status = observed["status"] == record["observed"]["status"]
    if same_output and same_status:
        print(f"[report] REPRODUCED: {observed['status']}, "
              f"{observed['cycles']} cycles, identical transcript"
              + (" (on a machine that has since changed, see above)"
                 if drifted else ""))
        return 0
    print(f"[report] DID NOT REPRODUCE: reported {record['observed']['status']}"
          f" printing {record['observed']['output']!r}; this run "
          f"{observed['status']} printing {observed['output']!r}",
          file=sys.stderr)
    return 1


def do_self_test(args) -> int:
    """A passing case, a failing case, and the two ways a payload goes stale."""
    scratch = ROOT / "build/examples/report-selftest"
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    problems = []

    def export(name, example, max_cycles=None):
        path = scratch / f"{name}.json"
        code = do_export(argparse.Namespace(
            example=example, output=path, max_cycles=max_cycles))
        return path if code == 0 else None

    def replay(path):
        return do_replay(argparse.Namespace(record=path))

    # A session that finished, and one that did not: a report's real job is the
    # second, so the failing case is checked the same way as the passing one.
    print("\n[report-selftest] a session that finished")
    passing = export("passing", "baremetal-hello")
    if replay(passing) != 0:
        problems.append("a report of a finished session did not reproduce")

    print("\n[report-selftest] a session that ran out of cycles")
    failing = export("failing", "baremetal-timer", max_cycles=500)
    record = json.loads(failing.read_text())
    if record["observed"]["status"] != "did-not-finish":
        problems.append("the short-budget session finished after all, so this "
                        "is not the failing case it is meant to be")
    elif replay(failing) != 0:
        problems.append("a report of a failing session did not reproduce it")

    print("\n[report-selftest] a payload whose bytes have moved on")
    stale = json.loads(passing.read_text())
    stale["software"]["payload_sha256"] = "0" * 64
    stale_path = scratch / "stale.json"
    stale_path.write_text(json.dumps(stale, indent=2))
    if replay(stale_path) == 0:
        problems.append("a report whose payload no longer matches was replayed "
                        "against the current one instead of being refused")

    print("\n[report-selftest] a payload that is not here at all")
    missing = json.loads(passing.read_text())
    missing["software"]["payload"] = "sw/baremetal/build/not-here.hex"
    missing_path = scratch / "missing.json"
    missing_path.write_text(json.dumps(missing, indent=2))
    if replay(missing_path) == 0:
        problems.append("a report naming a payload that does not exist was "
                        "not refused")

    print()
    for problem in problems:
        print(f"[report-selftest] FAIL {problem}", file=sys.stderr)
    if problems:
        return 1
    print("[report-selftest] PASS: a finished session and a failed one both "
          "reproduce from their records, and a payload that is stale or "
          "missing is refused rather than substituted")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    exporter = sub.add_parser("export")
    exporter.add_argument("example")
    exporter.add_argument("--output", type=Path,
                          default=ROOT / "build/examples/report.json")
    exporter.add_argument("--max-cycles", type=int)
    replayer = sub.add_parser("replay")
    replayer.add_argument("record", type=Path)
    sub.add_parser("self-test")
    args = parser.parse_args()
    return {"export": do_export, "replay": do_replay,
            "self-test": do_self_test}[args.action](args)


if __name__ == "__main__":
    raise SystemExit(main())
