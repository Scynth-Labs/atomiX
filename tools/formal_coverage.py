#!/usr/bin/env python3
"""What the bounded proofs actually prove, derived from what actually runs.

A formal badge is the strongest claim this project makes, and the easiest to
overstate: `make -C formal check-all` exists, so it is tempting to read it as
"the cores are proved".  It is not.  What runs is a named list of checks, over
a named ISA subset, at a named depth, against a wrapper that ties off the M
extension -- and every one of those four is a place where the claim and the
configuration could drift apart without anybody noticing.

So this derives the coverage from the configuration itself: the check lists in
formal/Makefile, the ISA, mode and depths in each core's .cfg, the defines, and
the ENABLE_M each RVFI wrapper elaborates.  Then it compares that against the
committed record in research/formal-coverage.json and fails on any difference,
so widening or narrowing a proof has to be a deliberate edit to the record
rather than a silent change of meaning.

The uncovered set is derived the same way and is part of the record: the RV32I
instructions no check names, and the feature sets the wrappers exclude.

  make formal-coverage           check the record against the configuration
  make formal-coverage REPORT=1  print it
  make formal-coverage WRITE=1   rewrite it after a deliberate change
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "formal"
RECORD = ROOT / "research/formal-coverage.json"
SCHEMA = "org.atomix.formal-coverage.v1"

# The base integer set, so "which instructions are not proved" is a subtraction
# rather than an opinion.  riscv-formal names a check after the instruction it
# proves, which is what makes this comparable at all.
RV32I = [
    "lui", "auipc", "jal", "jalr",
    "beq", "bne", "blt", "bge", "bltu", "bgeu",
    "lb", "lh", "lw", "lbu", "lhu", "sb", "sh", "sw",
    "addi", "slti", "sltiu", "xori", "ori", "andi", "slli", "srli", "srai",
    "add", "sub", "sll", "slt", "sltu", "xor", "srl", "sra", "or", "and",
]

CORES = {
    "pipeline5": {"config": "checks.cfg", "variable": "CHECKS",
                  "wrapper": "components/core/pipeline5/axcore_rvfi_wrapper.sv",
                  "target": "check"},
    "minimal": {"config": "checks-minimal.cfg", "variable": "CHECKS",
                "wrapper": "components/core/minimal/axcore_rvfi_wrapper.sv",
                "target": "check-minimal"},
    "ax2": {"config": "checks-ax2.cfg", "variable": "AX2_CHECKS",
            "wrapper": "components/core/ax2/ax2_rvfi_wrapper.sv",
            "target": "check-ax2"},
}


def makefile_list(name: str) -> list[str]:
    # Join continuations first: a greedy `.*` eats the trailing backslash, so
    # matching them in one pattern silently truncates a wrapped list -- which
    # is exactly the kind of quiet under-reporting this file exists to stop.
    text = (FORMAL / "Makefile").read_text(encoding="utf-8").replace("\\\n", " ")
    match = re.search(rf"^{name} :?= (.*)$", text, re.M)
    if match is None:
        raise SystemExit(f"formal/Makefile does not define {name}")
    return match.group(1).split()


def config_facts(config: Path) -> dict:
    """ISA, proof mode, retire channels, depths and defines, from the .cfg."""
    facts: dict = {"defines": [], "depths": {}}
    section = None
    for raw in config.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section == "options":
            key, _, value = line.partition(" ")
            facts[key] = value.strip()
        elif section == "depth":
            key, _, value = line.partition(" ")
            facts["depths"][key] = [int(item) for item in value.split()]
        elif section == "defines":
            facts["defines"].append(line.strip("`").replace("define ", ""))
    return facts


def wrapper_facts(path: Path) -> dict:
    text = (ROOT / path).read_text(encoding="utf-8")
    enable_m = re.search(r"ENABLE_M\((1'b[01])\)", text)
    return {
        "enable_m": enable_m.group(1) if enable_m else "unknown",
        "memory_ready": "always" if "ibus_ready(1'b1)" in text else "modelled",
        "interrupts": "tied low" if re.search(r"irq_\w+\(1'b0\)", text)
                      else "driven",
    }


def derive() -> dict:
    cores = {}
    for name, spec in CORES.items():
        checks = makefile_list(spec["variable"])
        instructions, channels = set(), set()
        for check in checks:
            match = re.fullmatch(r"insn_(\w+)_ch(\d+)", check)
            if match:
                instructions.add(match.group(1))
                channels.add(int(match.group(2)))
        facts = config_facts(FORMAL / spec["config"])
        wrapper = wrapper_facts(Path(spec["wrapper"]))
        declared_channels = int(facts.get("nret", "1"))
        cores[name] = {
            "target": f"make -C formal {spec['target']}",
            "config": f"formal/{spec['config']}",
            "isa": facts.get("isa", "?"),
            "mode": facts.get("mode", "?"),
            "retire_channels_declared": declared_channels,
            "retire_channels_proved": sorted(channels),
            "instructions_proved": sorted(instructions),
            "instructions_unproved": sorted(set(RV32I) - instructions),
            "depths": facts["depths"],
            "defines": facts["defines"],
            "wrapper": spec["wrapper"],
            **wrapper,
        }
    return {
        "schema": SCHEMA,
        "note": (
            "Derived from formal/Makefile, the per-core .cfg files, and each "
            "RVFI wrapper by tools/formal_coverage.py, which fails if this "
            "record and the configuration disagree. `make -C formal check-all` "
            "is not itself evidence: it is the three targets below, and only "
            "what has been run proves anything."),
        "excluded": {
            "rv32m": "every wrapper elaborates ENABLE_M(1'b0), so multiply and "
                     "divide are not in the proved design at all",
            "unaligned_memory": "RISCV_FORMAL_ALIGNED_MEM is defined, so "
                                "misaligned accesses are excluded from the "
                                "memory model",
            "csr_and_privilege": "no check in the lists below proves a CSR "
                                 "access, a trap, or a privilege transition; "
                                 "those are covered by simulation and cosim "
                                 "only",
            "liveness": "mode is bmc, so these are bounded safety proofs: they "
                        "show no counterexample within the depth, not that one "
                        "does not exist beyond it",
        },
        "cores": cores,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    derived = derive()
    if args.report:
        for name, core in sorted(derived["cores"].items()):
            proved = ", ".join(core["instructions_proved"])
            print(f"{name}: {core['isa']} {core['mode']}, "
                  f"channels {core['retire_channels_proved']} of "
                  f"{core['retire_channels_declared']}, "
                  f"ENABLE_M={core['enable_m']}")
            print(f"  proved:   {proved}")
            print(f"  unproved: {len(core['instructions_unproved'])} of "
                  f"{len(RV32I)} RV32I instructions")
            print(f"  command:  {core['target']}")
        print()
        for name, reason in sorted(derived["excluded"].items()):
            print(f"excluded {name}: {reason}")
        print()

    if args.write:
        RECORD.parent.mkdir(parents=True, exist_ok=True)
        RECORD.write_text(json.dumps(derived, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
        print(f"formal coverage: wrote {RECORD.relative_to(ROOT)}")
        return 0

    if not RECORD.is_file():
        print(f"formal coverage: {RECORD.relative_to(ROOT)} does not exist; "
              "run make formal-coverage WRITE=1", file=sys.stderr)
        return 1
    recorded = json.loads(RECORD.read_text(encoding="utf-8"))
    if recorded != json.loads(json.dumps(derived, sort_keys=True)):
        print("formal coverage: FAIL the record and the configuration "
              "disagree.", file=sys.stderr)
        for name in sorted(set(derived["cores"]) | set(recorded.get("cores", {}))):
            was = recorded.get("cores", {}).get(name)
            now = derived["cores"].get(name)
            if was != now:
                for field in sorted(set(was or {}) | set(now or {})):
                    if (was or {}).get(field) != (now or {}).get(field):
                        print(f"  {name}.{field}: recorded "
                              f"{(was or {}).get(field)!r}, configuration says "
                              f"{(now or {}).get(field)!r}", file=sys.stderr)
        print("  A proof that got wider or narrower is a change of claim: "
              "rewrite the record with make formal-coverage WRITE=1 and say "
              "why in the commit.", file=sys.stderr)
        return 1
    totals = {name: len(core["instructions_proved"])
              for name, core in sorted(derived["cores"].items())}
    print("formal coverage: PASS (" +
          ", ".join(f"{name} proves {count} of {len(RV32I)} RV32I instructions"
                    for name, count in totals.items()) + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
