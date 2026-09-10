#!/usr/bin/env python3
"""Every command the documentation advertises, and where its failures surface.

A README that lists forty commands is making forty claims.  Some are checked by
a verification suite on every push, some only by a nightly campaign, some
cannot run without hardware nobody in this project has, and some print
information and cannot fail.  Those are four very different promises, and until
they are written down the difference is invisible: an advertised command with
no home looks exactly like one CI has been running all along.

So this is an inventory with teeth.  It reads the commands out of the
documentation, requires each to be accounted for in tests/coverage-map.json,
and checks the accounting rather than trusting it -- a stage that claims to run
a target must actually run it, and an aggregate that claims to cover one must
actually list it.  A newly advertised command with no entry fails, which is the
only way the inventory stays true.

Five ways a command can be accounted for, and the first three are verified
rather than believed:

  suite          a verification stage runs it; checked against the manifest
  via            an aggregate a stage runs lists it; checked by reading the rule
  workflow       a GitHub workflow runs it; checked by reading the workflow
  manual         nothing runs it -- needs a board, a tool, or a person; must
                 say what it needs and where its failure would surface
  informational  produces information or artifacts rather than a verdict

  make coverage-map          check the inventory
  make coverage-map REPORT=1 print it
"""
import argparse
import json
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "tests/coverage-map.json"
SUITES = ROOT / "tests/verification-suites.json"
SCHEMA = "org.atomix.coverage-map.v1"

# Where the project advertises commands to a reader who will run them.
ADVERTISING = ["docs/workflow.md"]

FENCE = re.compile(r"^```(\w*)\s*$")


def advertised_commands(doc: Path) -> list[list[str]]:
    """Every `make` invocation inside a shell block of one document."""
    commands: list[list[str]] = []
    # Two pieces of state, not one: whether a fence is open, and whether the
    # fence that opened it was a shell block.  Collapsing them made the close
    # of a ```json block read as the open of a shell one.
    in_block = False
    shell = False
    pending = ""
    for raw in doc.read_text(encoding="utf-8").splitlines():
        fence = FENCE.match(raw)
        if fence:
            if in_block:
                in_block, shell = False, False
            else:
                in_block = True
                shell = fence.group(1) in {"bash", "sh", "console", ""}
            pending = ""
            continue
        if not (in_block and shell):
            continue
        line = raw.split(" #", 1)[0].rstrip()
        if line.endswith("\\"):          # a continued command line
            pending += line[:-1] + " "
            continue
        line, pending = pending + line, ""
        line = line.strip()
        if not line.startswith("make"):
            continue
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            continue                      # unbalanced quotes: a prose example
        if words and words[0] == "make":
            commands.append(words)
    return commands


def make_targets(words: list[str]) -> list[tuple[str, str]]:
    """The (directory, target) pairs one `make` invocation would build.

    Assignments and flags are not targets; `-C` moves the directory for
    everything after it, which is how the project spells "in that tree".
    """
    pairs: list[tuple[str, str]] = []
    directory = "."
    index = 1
    while index < len(words):
        word = words[index]
        if word in {"-C", "--directory"} and index + 1 < len(words):
            directory = words[index + 1].replace("$PWD/", "").rstrip("/") or "."
            index += 2
            continue
        if word.startswith("-"):
            index += 1
            continue
        if "=" in word:
            index += 1
            continue
        pairs.append((directory, word))
        index += 1
    return pairs


def key(directory: str, target: str) -> str:
    return f"{directory}:{target}"


def suite_coverage() -> dict[str, list[str]]:
    """Which verification stages run which targets, read from the manifest."""
    document = json.loads(SUITES.read_text(encoding="utf-8"))
    coverage: dict[str, list[str]] = {}
    for stage_id, stage in document["stages"].items():
        command = stage["command"]
        if command[0] != "make":
            continue
        for directory, target in make_targets(command):
            coverage.setdefault(key(directory, target), []).append(stage_id)
    return coverage


def suites_containing(stage_id: str) -> list[str]:
    document = json.loads(SUITES.read_text(encoding="utf-8"))
    return sorted(name for name, stages in document["suites"].items()
                  if stage_id in stages)


def aggregate_lists(directory: str, aggregate: str, target: str) -> bool:
    """Does `aggregate`'s rule in that Makefile actually name `target`?

    A `via` claim is the easiest kind to get wrong -- an aggregate is renamed,
    or a target is dropped from it, and the inventory still says it is covered.
    Reading the rule is not a full make evaluation, but it does catch the
    aggregate that stopped listing what it claims to run.
    """
    makefile = ROOT / directory / "Makefile"
    if not makefile.is_file():
        return False
    text = makefile.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(aggregate)}:(?!=)(.*(?:\\\n.*)*)$",
                      text, re.M)
    if match is None:
        return False
    prerequisites = match.group(1)
    # The recipe lines that follow, too: an aggregate may run sub-makes rather
    # than depend on them.
    start = match.end()
    for line in text[start:].splitlines():
        if line.startswith("\t"):
            prerequisites += "\n" + line
        elif line.strip():
            break
    return re.search(rf"(?<![\w-]){re.escape(target)}(?![\w-])",
                     prerequisites) is not None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true",
                        help="print the inventory as well as checking it")
    args = parser.parse_args()

    document = json.loads(MAP.read_text(encoding="utf-8"))
    if document.get("schema") != SCHEMA:
        print(f"{MAP}: unsupported schema {document.get('schema')!r}",
              file=sys.stderr)
        return 2
    entries = document["entries"]

    advertised: dict[str, list[str]] = {}
    for name in ADVERTISING:
        doc = ROOT / name
        for words in advertised_commands(doc):
            for directory, target in make_targets(words):
                advertised.setdefault(key(directory, target), []).append(name)

    by_stage = suite_coverage()
    problems: list[str] = []
    rows: list[tuple[str, str, str]] = []

    for name in sorted(advertised):
        entry = entries.get(name)
        if entry is None:
            problems.append(
                f"{name} is advertised in {', '.join(sorted(set(advertised[name])))} "
                "but has no entry in tests/coverage-map.json: say where its "
                "failures surface, or that it is manual and why")
            continue
        kind = entry.get("kind")
        if kind == "suite":
            stage = entry.get("stage", "")
            stages = by_stage.get(name, [])
            if stage not in stages:
                problems.append(
                    f"{name} claims stage {stage!r}, but the stages that "
                    f"actually run it are {stages or 'none'}")
                continue
            where = ", ".join(suites_containing(stage)) or "no suite"
            rows.append((name, "suite", f"{stage} ({where})"))
        elif kind == "via":
            directory, _, target = name.partition(":")
            aggregate = entry.get("aggregate", "")
            aggregate_key = key(directory, aggregate)
            if aggregate_key not in by_stage:
                problems.append(
                    f"{name} says it runs under {aggregate_key}, which no "
                    "verification stage runs")
                continue
            if not aggregate_lists(directory, aggregate, target):
                problems.append(
                    f"{name} says it runs under {aggregate!r}, but that rule "
                    f"in {directory}/Makefile does not name it any more")
                continue
            stage = by_stage[aggregate_key][0]
            where = ", ".join(suites_containing(stage)) or "no suite"
            rows.append((name, "via", f"{aggregate} -> {stage} ({where})"))
        elif kind == "workflow":
            workflow = ROOT / ".github/workflows" / entry.get("workflow", "")
            if not workflow.is_file():
                problems.append(f"{name} names workflow "
                                f"{entry.get('workflow')!r}, which does not exist")
                continue
            directory, _, target = name.partition(":")
            spelling = (f"make -C {directory} {target}" if directory != "."
                        else f"make {target}")
            if spelling not in workflow.read_text(encoding="utf-8"):
                problems.append(
                    f"{name} says {entry['workflow']} runs it, but that file "
                    f"does not contain {spelling!r}")
                continue
            rows.append((name, "workflow", entry["workflow"]))
        elif kind == "manual":
            for field in ("prerequisite", "surfaces"):
                if not entry.get(field):
                    problems.append(
                        f"{name} is marked manual without a {field}: a command "
                        "nothing runs needs both what it needs and where its "
                        "failure would show up")
            rows.append((name, "manual", entry.get("prerequisite", "?")))
        elif kind == "informational":
            if not entry.get("note"):
                problems.append(f"{name} is marked informational without a note")
            rows.append((name, "informational", entry.get("note", "?")))
        else:
            problems.append(f"{name} has unknown kind {kind!r}")

    for name in sorted(set(entries) - set(advertised)):
        problems.append(
            f"{name} is in tests/coverage-map.json but is no longer advertised "
            "in the documentation: remove the entry or restore the command")

    if args.report:
        width = max((len(name) for name, _, _ in rows), default=10)
        for name, kind, detail in rows:
            print(f"{name:<{width}}  {kind:<14} {detail}")
        print()
    counts: dict[str, int] = {}
    for _, kind, _ in rows:
        counts[kind] = counts.get(kind, 0) + 1
    print("coverage map: " + ", ".join(f"{kind}={count}"
                                       for kind, count in sorted(counts.items())))
    for problem in problems:
        print(f"coverage map: FAIL {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"coverage map: PASS ({len(rows)} advertised commands, each with a "
          "place its failures surface)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
