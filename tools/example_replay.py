#!/usr/bin/env python3
"""Boot every documentation example, and require it to still say what it says.

A code block that shows a command and its output is a claim, and it is the kind
that rots quietly: the machine changes, the program changes, the transcript in
the document does not, and nothing fails.  Prose drifts silently; this is the
part of the documentation that does not have to.

Each example is a record naming the profile, the payload and its identity, the
entry the payload is loaded under, the console script, the exact transcript,
and a cycle bound.  Replaying one is booting the machine the document describes
and comparing every byte.  Two further things are checked because they are the
ways such a record goes stale without the transcript changing:

  * the command must still appear in the document that shows it, so an example
    cannot outlive the text around it; and
  * the entry the record names must be the reset PC the profile resolves to,
    because a payload loaded somewhere the machine does not start from would
    run whatever *was* at the entry and could still print the right thing.

A missing asset or a missing tool is refused in the open rather than skipped:
the run reports what it could not do and fails, because an example nobody could
replay is not an example that passed.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORDS = ROOT / "tests/examples.json"
SCHEMA = "org.atomix.doc-examples.v1"
OUTPUT = ROOT / "build/examples/replay.json"


def run(command, **kwargs):
    return subprocess.run([str(arg) for arg in command], cwd=ROOT, text=True,
                          capture_output=True, timeout=1800, **kwargs)


def resolved_profile(profile: str) -> dict[str, str]:
    result = run([sys.executable, "tools/configure.py", "resolve",
                  "--config", ROOT / profile])
    if result.returncode:
        raise SystemExit(f"[example] {profile} does not resolve:\n{result.stderr}")
    values = {}
    for line in result.stdout.splitlines():
        if ":=" in line:
            key, _, value = line.partition(":=")
            values[key.strip()] = value.strip()
    return values


def replay(name: str, example: dict) -> dict:
    document = ROOT / example["document"]
    if not document.is_file():
        raise SystemExit(f"[example] {name}: {example['document']} is missing")
    if example["command"] not in document.read_text(encoding="utf-8"):
        raise SystemExit(
            f"[example] {name}: {example['document']} no longer contains\n"
            f"    {example['command']}\n"
            "  The example and the document it illustrates have parted "
            "company; update one of them.")

    build = run(example["payload_build"])
    if build.returncode:
        raise SystemExit(f"[example] {name}: cannot build its payload:\n"
                         f"{build.stdout}{build.stderr}")
    payload = ROOT / example["payload"]
    if not payload.is_file():
        raise SystemExit(f"[example] {name}: {example['payload']} was not built")
    payload_sha = hashlib.sha256(payload.read_bytes()).hexdigest()

    resolved = resolved_profile(example["profile"])
    entry = int(example["entry"], 0)
    reset_pc = int(resolved["COMPONENT_RESET_PC"], 0)
    if entry != reset_pc:
        raise SystemExit(
            f"[example] {name}: the record loads its payload at "
            f"{entry:#010x}, but {example['profile']} resets at "
            f"{reset_pc:#010x}: the machine would not start in the payload")

    model = run(["make", "-s", "--no-print-directory", "-C", "sim/soc",
                 "model-path", f"COMPONENT_CONFIG={ROOT / example['profile']}",
                 "RAM_INIT_FILE=", "ROM_INIT_FILE=", "BUILD_ID=example-replay"])
    if model.returncode:
        raise SystemExit(f"[example] {name}: the model did not build:\n"
                         f"{model.stdout}{model.stderr}")
    binary = Path(model.stdout.strip().splitlines()[-1])

    command = [binary, "--ram-image", payload,
               "--max-cycles", str(example["max_cycles"])]
    if example["input"]:
        script = ROOT / example["input"]
        if not script.is_file():
            raise SystemExit(f"[example] {name}: {example['input']} is missing")
        command += ["--uart-input-file", script]
    result = run(command)
    cycles = re.search(r"\[soc\] exit 0 \(cycles=(\d+)\)", result.stderr)
    if result.returncode or not cycles:
        raise SystemExit(
            f"[example] {name}: the machine did not finish within "
            f"{example['max_cycles']} cycles\n{result.stderr}")
    if result.stdout != example["expected_output"]:
        raise SystemExit(
            f"[example] {name}: the document says the machine prints\n"
            f"  {example['expected_output']!r}\nit printed\n"
            f"  {result.stdout!r}")

    print(f"[example] {name}: PASS -- {example['title']} "
          f"({resolved['COMPONENT_CONFIG_NAME']}, {cycles.group(1)} cycles of "
          f"{example['max_cycles']})")
    return {
        "example": name,
        "document": example["document"],
        "section": example["section"],
        "command": example["command"],
        "profile": example["profile"],
        "profile_name": resolved["COMPONENT_CONFIG_NAME"],
        "core": resolved.get("COMPONENT_CORE_ID"),
        "memory": resolved.get("COMPONENT_MEMORY_ID"),
        "harness": resolved.get("COMPONENT_HARNESS_ID"),
        "payload": example["payload"],
        "payload_sha256": payload_sha,
        "entry": example["entry"],
        "input": example["input"],
        "cycles": int(cycles.group(1)),
        "max_cycles": example["max_cycles"],
        "output": result.stdout,
        "status": "passed",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="replay one example by name")
    args = parser.parse_args()

    document = json.loads(RECORDS.read_text(encoding="utf-8"))
    if document.get("schema") != SCHEMA:
        print(f"{RECORDS}: unsupported schema {document.get('schema')!r}",
              file=sys.stderr)
        return 2
    examples = document["examples"]
    if args.only:
        if args.only not in examples:
            print(f"[example] unknown example {args.only!r}; known: "
                  f"{', '.join(sorted(examples))}", file=sys.stderr)
            return 2
        examples = {args.only: examples[args.only]}

    # Refused in the open.  A capability this cannot supply makes every example
    # unreplayable, and an unreplayable example is not a passing one.
    missing = [tool for tool in ("verilator", "make") if shutil.which(tool) is None]
    if missing:
        print(f"[example] REFUSED: {', '.join(missing)} not installed, so no "
              "example can be replayed. This is a refusal, not a skip: an "
              "example nobody can run is not an example that passed.",
              file=sys.stderr)
        return 1

    results = [replay(name, examples[name]) for name in sorted(examples)]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "schema": "org.atomix.doc-example-replay.v1",
        "evidence_kind": "simulation",
        "command": "make example-replay",
        "verilator": run([os.environ.get("VERILATOR", "verilator"),
                          "--version"]).stdout.strip(),
        "examples": results,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"[example] PASS: {len(results)} documentation examples booted the "
          f"machine they describe and printed exactly what they claim; "
          f"{OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
