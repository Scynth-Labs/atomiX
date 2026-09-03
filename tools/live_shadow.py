#!/usr/bin/env python3
"""Live FPGA L2 shadow evaluation: simulate, gate, and request a volatile test.

L2 sits between L1 reviewed selection and any physical trial.  It takes a
candidate accelerator program, proves bounded and confined behaviour *before*
running it, simulates the survivors against their oracle, and emits a
signed-off volatile test request.  It never deploys: every record it writes
carries `actuation: org.atomix.not-authorized`, and a withheld request is the
only possible output for a candidate that fails any gate.

The static gates are re-derivable from the recorded program words, so `check`
recomputes every verdict rather than trusting the record.  Only the simulator
observations (output digest and cycle counts) come from `evaluate`.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import struct
import sys
from pathlib import Path
from typing import Any

import personality_contract as pc
import candidate_registry as cr

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHADOW = ROOT / "research/live-fpga/shadow/l2-polynomial-horner.json"
DEFAULT_REGISTRY = ROOT / "research/live-fpga/registry/reviewed-gpu.json"
U32 = (1 << 32) - 1

# role.gpu-compute limits as instantiated by the Primer runtime profile.
# PROG_WORDS/opcode set come from components/role/gpu-compute/gpu_engine.sv;
# DATA_WORDS and lane count come from configs/sim-primer-runtime-gpu.json.
PROG_WORDS = 64
DATA_WORDS = 256
NLANES = 1
NREGS = 8

OP_HALT, OP_TID, OP_LI, OP_MOV, OP_LDX, OP_STX = 0, 1, 2, 3, 4, 5
OP_ADD, OP_SUB, OP_MUL, OP_AND, OP_OR, OP_XOR = 6, 7, 8, 9, 10, 11
OP_SLL, OP_SRL, OP_SRA, OP_MIN, OP_MAX = 12, 13, 14, 15, 16
OP_ADDI, OP_MULI = 17, 18
ALLOWED_OPS = frozenset(range(OP_HALT, OP_MULI + 1))

CHECK_LENGTH = "org.atomix.check.program-length"
CHECK_OPCODES = "org.atomix.check.opcode-allow-list"
CHECK_HALT = "org.atomix.check.halt-terminated"
CHECK_DEFINED = "org.atomix.check.define-before-use"
CHECK_CONFINED = "org.atomix.check.address-confinement"
CHECK_ORACLE = "org.atomix.check.oracle-exact-output"
STATIC_CHECKS = (CHECK_LENGTH, CHECK_OPCODES, CHECK_HALT, CHECK_DEFINED,
                 CHECK_CONFINED)
ALL_CHECKS = STATIC_CHECKS + (CHECK_ORACLE,)

PASS, FAIL, SKIPPED = "org.atomix.pass", "org.atomix.fail", "org.atomix.skipped"


def decode(word: int) -> tuple[int, int, int, int, int]:
    """Split a program word exactly as gpu_engine.sv decodes it."""
    op = (word >> 26) & 0x3F
    rd = (word >> 23) & 0x7
    ra = (word >> 20) & 0x7
    rb = (word >> 17) & 0x7
    imm = word & 0x1FFFF
    if imm & 0x10000:
        imm -= 0x20000
    return op, rd, ra, rb, imm


def artifact_hash(words: list[int]) -> str:
    payload = b"".join(struct.pack("<I", word & U32) for word in words)
    return hashlib.sha256(payload).hexdigest()


# --------------------------------------------------------------------------
# Static gates.  Each returns (status, reason) and never raises on candidate
# input: a malformed candidate is a rejection, not a tool error.
# --------------------------------------------------------------------------

def check_length(words: list[int]) -> tuple[str, str | None]:
    if not words:
        return FAIL, "org.atomix.reason.empty-program"
    if len(words) > PROG_WORDS:
        return FAIL, "org.atomix.reason.program-exceeds-role-window"
    return PASS, None


def check_opcodes(words: list[int]) -> tuple[str, str | None]:
    for word in words:
        if decode(word)[0] not in ALLOWED_OPS:
            return FAIL, "org.atomix.reason.opcode-not-implemented-by-role"
    return PASS, None


def check_halt(words: list[int]) -> tuple[str, str | None]:
    """The engine stops at HALT or at the clamped length; require an explicit
    HALT so termination does not depend on the host's ninsn field."""
    if not any(decode(word)[0] == OP_HALT for word in words):
        return FAIL, "org.atomix.reason.no-halt-instruction"
    return PASS, None


def _straight_line(words: list[int]):
    """Yield decoded instructions up to and including the first HALT."""
    for word in words:
        fields = decode(word)
        yield fields
        if fields[0] == OP_HALT:
            return


def check_defined(words: list[int]) -> tuple[str, str | None]:
    """Registers survive jobs and waves — gpu_engine.sv never resets `regs`.
    Reading one before writing it in this job would expose the previous
    program's per-lane state, so an undefined read is an isolation failure."""
    defined: set[int] = set()
    for op, rd, ra, rb, _ in _straight_line(words):
        if op == OP_HALT:
            break
        reads: list[int] = []
        if op in (OP_MOV, OP_LDX, OP_ADDI, OP_MULI):
            reads = [ra]
        elif op == OP_STX:
            reads = [ra, rb]
        elif op in (OP_ADD, OP_SUB, OP_MUL, OP_AND, OP_OR, OP_XOR, OP_SLL,
                    OP_SRL, OP_SRA, OP_MIN, OP_MAX):
            reads = [ra, rb]
        if any(register not in defined for register in reads):
            return FAIL, "org.atomix.reason.reads-undefined-register"
        if op not in (OP_HALT, OP_STX):
            defined.add(rd)
    return PASS, None


def check_confinement(words: list[int], nthreads: int) -> tuple[str, str | None]:
    """Interval analysis proving every LDX/STX address stays inside the role's
    data window.  The hardware truncates the address to DATA_ADDR_BITS, so an
    out-of-window access cannot escape the role — but it silently aliases other
    live data, which this gate rejects instead of tolerating."""
    top = (-(1 << 31), (1 << 31) - 1)
    intervals: dict[int, tuple[int, int]] = {}

    def get(register: int) -> tuple[int, int]:
        return intervals.get(register, top)

    def widen(lo: int, hi: int) -> tuple[int, int]:
        if lo < -(1 << 31) or hi > (1 << 31) - 1:
            return top
        return lo, hi

    for op, rd, ra, rb, imm in _straight_line(words):
        if op == OP_HALT:
            break
        if op in (OP_LDX, OP_STX):
            lo, hi = get(ra)
            if lo < 0 or hi >= DATA_WORDS:
                return FAIL, "org.atomix.reason.address-outside-role-window"
            if op == OP_STX:
                continue
            intervals[rd] = top
            continue
        if op == OP_TID:
            intervals[rd] = (0, max(nthreads - 1, 0))
        elif op == OP_LI:
            intervals[rd] = (imm, imm)
        elif op == OP_MOV:
            intervals[rd] = get(ra)
        elif op == OP_ADDI:
            lo, hi = get(ra)
            intervals[rd] = widen(lo + imm, hi + imm)
        elif op == OP_ADD:
            (alo, ahi), (blo, bhi) = get(ra), get(rb)
            intervals[rd] = widen(alo + blo, ahi + bhi)
        elif op == OP_SUB:
            (alo, ahi), (blo, bhi) = get(ra), get(rb)
            intervals[rd] = widen(alo - bhi, ahi - blo)
        elif op in (OP_MUL, OP_MULI):
            (alo, ahi) = get(ra)
            (blo, bhi) = (imm, imm) if op == OP_MULI else get(rb)
            corners = [alo * blo, alo * bhi, ahi * blo, ahi * bhi]
            intervals[rd] = widen(min(corners), max(corners))
        elif op == OP_MIN:
            (alo, ahi), (blo, bhi) = get(ra), get(rb)
            intervals[rd] = (min(alo, blo), min(ahi, bhi))
        elif op == OP_MAX:
            (alo, ahi), (blo, bhi) = get(ra), get(rb)
            intervals[rd] = (max(alo, blo), max(ahi, bhi))
        else:
            intervals[rd] = top
    return PASS, None


def static_gates(words: list[int], nthreads: int) -> list[dict[str, Any]]:
    """Run every static gate in a fixed order; later gates assume a decodable
    program, so a length or opcode failure short-circuits the rest."""
    results: list[dict[str, Any]] = []

    def record(check_id: str, outcome: tuple[str, str | None]) -> str:
        status, reason = outcome
        results.append({"id": check_id, "status": status, "reason": reason})
        return status

    if record(CHECK_LENGTH, check_length(words)) != PASS or \
            record(CHECK_OPCODES, check_opcodes(words)) != PASS:
        for check_id in (CHECK_HALT, CHECK_DEFINED, CHECK_CONFINED):
            results.append({"id": check_id, "status": SKIPPED,
                            "reason": "org.atomix.reason.earlier-gate-failed"})
        return results
    record(CHECK_HALT, check_halt(words))
    record(CHECK_DEFINED, check_defined(words))
    record(CHECK_CONFINED, check_confinement(words, nthreads))
    return results


def oracle_gate(observed: str | None, expected: str) -> dict[str, Any]:
    if observed is None:
        return {"id": CHECK_ORACLE, "status": SKIPPED,
                "reason": "org.atomix.reason.not-simulated"}
    if observed != expected:
        return {"id": CHECK_ORACLE, "status": FAIL,
                "reason": "org.atomix.reason.output-differs-from-oracle"}
    return {"id": CHECK_ORACLE, "status": PASS, "reason": None}


# --------------------------------------------------------------------------
# Deterministic derivation of verdicts and the volatile test request.
# --------------------------------------------------------------------------

def derive_evaluation(evaluation: dict[str, Any]) -> dict[str, Any]:
    program = evaluation["program"]
    words = program["words"]
    checks = static_gates(words, program["nthreads"])
    static_ok = all(item["status"] == PASS for item in checks)
    observation = evaluation["observation"]
    checks.append(oracle_gate(
        observation["output_sha256"] if static_ok else None,
        program["oracle_sha256"]))
    failed = [item["id"] for item in checks if item["status"] == FAIL]
    if failed:
        verdict, reason = "org.atomix.rejected", failed
    elif any(item["status"] == SKIPPED for item in checks):
        verdict, reason = "org.atomix.rejected", \
            ["org.atomix.reason.incomplete-evaluation"]
    else:
        verdict, reason = "org.atomix.accepted", \
            ["org.atomix.reason.all-gates-passed"]
    return {"checks": checks, "verdict": verdict, "reason": reason}


def derive_request(document: dict[str, Any]) -> dict[str, Any]:
    """A signed-off request needs an accepted candidate, an accepted baseline
    to roll back to, and a strict cycle improvement on the same workload."""
    baseline_id = document["baseline"]
    evaluations = {item["candidate"]: item for item in document["evaluations"]}
    derived = {key: derive_evaluation(value) for key, value in evaluations.items()}
    withheld = {
        "status": "org.atomix.withheld",
        "candidate": None,
        "baseline": baseline_id,
        "authority": "org.atomix.volatile-test-only",
        "actuation": "org.atomix.not-authorized",
        "rollback_to": None,
        "requires": [],
        "reason": [],
    }
    baseline = evaluations.get(baseline_id)
    if baseline is None or derived[baseline_id]["verdict"] != "org.atomix.accepted":
        withheld["reason"] = ["org.atomix.reason.baseline-not-established"]
        return withheld

    baseline_cycles = baseline["observation"]["execute_cycles"]
    eligible = []
    for candidate_id, evaluation in evaluations.items():
        if candidate_id == baseline_id:
            continue
        if derived[candidate_id]["verdict"] != "org.atomix.accepted":
            continue
        if evaluation["program"]["workload"] != baseline["program"]["workload"]:
            continue
        cycles = evaluation["observation"]["execute_cycles"]
        if cycles < baseline_cycles:
            eligible.append((cycles, candidate_id))
    if not eligible:
        withheld["reason"] = ["org.atomix.reason.no-accepted-improvement"]
        return withheld

    eligible.sort()
    chosen = eligible[0][1]
    return {
        "status": "org.atomix.signed-off",
        "candidate": chosen,
        "baseline": baseline_id,
        "authority": "org.atomix.volatile-test-only",
        "actuation": "org.atomix.not-authorized",
        "rollback_to": baseline_id,
        "requires": [
            "org.atomix.gate.manager-approval",
            "org.atomix.gate.canary-workload",
            "org.atomix.gate.rollback-target-resident",
        ],
        "reason": [
            "org.atomix.reason.all-gates-passed",
            "org.atomix.reason.strict-cycle-improvement",
        ],
    }


# --------------------------------------------------------------------------
# Record validation.
# --------------------------------------------------------------------------

def validate_program(path: Path, value: Any, name: str) -> dict[str, Any]:
    program = pc.object_value(path, value, name)
    pc.exact_keys(path, program, name,
                  {"words", "nthreads", "workload", "program_sha256",
                   "oracle_sha256"})
    words = pc.list_value(path, program["words"], f"{name}.words")
    for index, word in enumerate(words):
        if not isinstance(word, int) or isinstance(word, bool) or \
                not 0 <= word <= U32:
            raise pc.error(path, f"{name}.words[{index}] must be a uint32")
    pc.nonnegative_int(path, program["nthreads"], f"{name}.nthreads")
    pc.namespaced(path, program["workload"], f"{name}.workload")
    for field in ("program_sha256", "oracle_sha256"):
        cr.sha256_value(path, program[field], f"{name}.{field}")
    if artifact_hash(words) != program["program_sha256"]:
        raise pc.error(path, f"{name}.program_sha256 does not match its words")
    return program


def validate_observation(path: Path, value: Any, name: str) -> dict[str, Any]:
    observation = pc.object_value(path, value, name)
    pc.exact_keys(path, observation, name,
                  {"simulated", "output_sha256", "load_cycles", "execute_cycles"})
    if not isinstance(observation["simulated"], bool):
        raise pc.error(path, f"{name}.simulated must be boolean")
    if observation["simulated"]:
        cr.sha256_value(path, observation["output_sha256"], f"{name}.output_sha256")
        for field in ("load_cycles", "execute_cycles"):
            cr.u32(path, observation[field], f"{name}.{field}", positive=True)
    else:
        for field in ("output_sha256", "load_cycles", "execute_cycles"):
            if observation[field] is not None:
                raise pc.error(
                    path, f"{name}.{field} must be null when not simulated")
    return observation


def validate_document(path: Path, document: dict[str, Any],
                      registry: dict[str, dict[str, Any]] | None = None) -> None:
    pc.exact_keys(path, document, "L2 shadow record",
                  {"schema", "kind", "id", "revision", "summary", "content_id",
                   "baseline", "environment", "evaluations", "request",
                   "extensions"})
    if document["kind"] != "live-l2-shadow":
        raise pc.error(path, "kind must be 'live-l2-shadow'")
    pc.common(path, document, "org.atomix.live-l2-shadow")
    declared = cr.content_id_value(path, document["content_id"], "content_id")
    if declared != cr.document_content_id(document):
        raise pc.error(path, "content_id does not match canonical document")

    environment = pc.object_value(path, document["environment"], "environment")
    pc.exact_keys(path, environment, "environment",
                  {"level", "timestamp_utc", "role", "limits", "tools"})
    pc.namespaced(path, environment["level"], "environment.level")
    cr.validate_timestamp(path, environment["timestamp_utc"],
                          "environment.timestamp_utc")
    pc.namespaced(path, environment["role"], "environment.role")
    limits = pc.object_value(path, environment["limits"], "environment.limits")
    pc.exact_keys(path, limits, "environment.limits",
                  {"program_words", "data_words", "lanes", "registers"})
    expected_limits = {"program_words": PROG_WORDS, "data_words": DATA_WORDS,
                       "lanes": NLANES, "registers": NREGS}
    if limits != expected_limits:
        raise pc.error(path, "environment.limits disagree with the analysed role")
    cr.validate_tools(path, environment["tools"], "environment.tools")

    evaluations = pc.list_value(path, document["evaluations"], "evaluations")
    if not evaluations:
        raise pc.error(path, "evaluations must not be empty")
    seen: set[str] = set()
    for index, item in enumerate(evaluations):
        name = f"evaluations[{index}]"
        evaluation = pc.object_value(path, item, name)
        pc.exact_keys(path, evaluation, name,
                      {"candidate", "identity", "label", "program",
                       "observation", "checks", "verdict", "reason"})
        candidate = cr.content_id_value(path, evaluation["candidate"],
                                        f"{name}.candidate")
        if candidate in seen:
            raise pc.error(path, "evaluations contains duplicate candidates")
        seen.add(candidate)
        # Rejected candidates never reach the registry, so the shadow record is
        # their provenance: bind each one to a full canonical identity.
        identity = cr.validate_identity(path, evaluation["identity"],
                                        f"{name}.identity")
        if cr.content_id(identity) != candidate:
            raise pc.error(path, f"{name}.candidate does not address its identity")
        if identity["artifact"]["sha256"] != evaluation["program"]["program_sha256"]:
            raise pc.error(path, f"{name}.identity disagrees with the analysed words")
        if not isinstance(evaluation["label"], str) or not evaluation["label"].strip():
            raise pc.error(path, f"{name}.label must be non-empty")
        validate_program(path, evaluation["program"], f"{name}.program")
        validate_observation(path, evaluation["observation"], f"{name}.observation")
        expected = derive_evaluation(evaluation)
        if evaluation["checks"] != expected["checks"]:
            raise pc.error(path, f"{name}.checks are not the derived static/oracle gates")
        if evaluation["verdict"] != expected["verdict"] or \
                evaluation["reason"] != expected["reason"]:
            raise pc.error(path, f"{name} verdict does not follow from its gates")
        if evaluation["verdict"] == "org.atomix.rejected" and \
                evaluation["observation"]["simulated"] and \
                any(item["status"] == FAIL
                    for item in evaluation["checks"]
                    if item["id"] in STATIC_CHECKS):
            raise pc.error(
                path, f"{name} was simulated despite failing a static gate")

    baseline = cr.content_id_value(path, document["baseline"], "baseline")
    if baseline not in seen:
        raise pc.error(path, "baseline is not among the evaluated candidates")
    if registry is not None and baseline not in registry:
        raise pc.error(path, "baseline is not a registered candidate")

    expected_request = derive_request(document)
    if pc.object_value(path, document["request"], "request") != expected_request:
        raise pc.error(path, "request does not match the deterministic derivation")
    if document["request"]["actuation"] != "org.atomix.not-authorized":
        raise pc.error(path, "L2 must never claim actuation authority")
    pc.extensions(path, document["extensions"])


# --------------------------------------------------------------------------
# Simulation driver.
# --------------------------------------------------------------------------

def simulate(programs: list[dict[str, Any]], config: Path, boot_rom: Path,
             kernel: Path, max_cycles: int) -> list[tuple[str, int, int]]:
    """Run candidates through the same RTL host-link path the board uses."""
    sys.path.insert(0, str(ROOT / "sw/host"))
    import axhost

    requests = [axhost.request(axhost.OP_PING), axhost.request(axhost.OP_INFO)]
    for program in programs:
        requests.append(axhost.request(
            axhost.OP_GPU_LOAD, axhost.gpu_load_payload(program["words"])))
        requests.append(axhost.request(
            axhost.OP_GPU_EXEC,
            axhost.gpu_exec_payload(program["nthreads"], program["data"])))
    requests.append(axhost.request(axhost.OP_BYE))
    pipe = axhost.SimPipe(None, str(config), max_cycles, boot_rom=str(boot_rom),
                          kernel_binary=str(kernel))
    frames = axhost.parse_responses(pipe.exchange(b"".join(requests)))
    expected_frames = 2 + 2 * len(programs)
    if len(frames) < expected_frames:
        raise pc.ContractError(
            f"shadow simulation returned {len(frames)} of {expected_frames} frames")

    results = []
    for index, program in enumerate(programs):
        load_frame = frames[2 + index * 2]
        exec_frame = frames[3 + index * 2]
        if load_frame[0] != axhost.ST_OK or len(load_frame[1]) != 4:
            raise pc.ContractError(f"{program['label']}: LOAD rejected by the shell")
        load_cycles = struct.unpack("<I", load_frame[1])[0]
        if exec_frame[0] != axhost.ST_OK or len(exec_frame[1]) < 4:
            raise pc.ContractError(f"{program['label']}: EXEC rejected by the shell")
        execute_cycles = struct.unpack("<I", exec_frame[1][:4])[0]
        payload = exec_frame[1][4:]
        words = list(struct.unpack(f"<{len(payload) // 4}I", payload))
        results.append((artifact_hash(words), load_cycles, execute_cycles))
    return results


def load(path: Path) -> dict[str, Any]:
    return cr.load_json(path)


def check(path: Path, registry_path: Path = DEFAULT_REGISTRY) -> int:
    document = load(path)
    registry = cr.check(registry_path)
    validate_document(path, document, registry)
    accepted = sum(1 for item in document["evaluations"]
                   if item["verdict"] == "org.atomix.accepted")
    print(f"live L2 shadow: PASS ({len(document['evaluations'])} candidates, "
          f"{accepted} accepted, request={document['request']['status']}, "
          f"actuation={document['request']['actuation']})")
    return 0


def self_test() -> int:
    """Fault-inject candidates that must never reach a signed-off request."""
    document = load(DEFAULT_SHADOW)
    registry = cr.validate_registry(DEFAULT_REGISTRY,
                                    cr.load_json(DEFAULT_REGISTRY))
    validate_document(DEFAULT_SHADOW, document, registry)
    if document["request"]["status"] != "org.atomix.signed-off":
        raise pc.ContractError("the reference record must sign off one improvement")

    def reseal(mutated: dict[str, Any]) -> dict[str, Any]:
        for evaluation in mutated["evaluations"]:
            evaluation["identity"]["artifact"]["sha256"] = \
                evaluation["program"]["program_sha256"]
            evaluation["candidate"] = cr.content_id(evaluation["identity"])
            evaluation.update(derive_evaluation(evaluation))
        baseline_label = mutated["evaluations"][baseline_index]
        mutated["baseline"] = baseline_label["candidate"]
        mutated["request"] = derive_request(mutated)
        mutated["content_id"] = cr.document_content_id(mutated)
        return mutated

    sys.path.insert(0, str(ROOT / "sw/host"))
    from gpu_programs import gpu_insn

    accepted_index = next(
        index for index, item in enumerate(document["evaluations"])
        if item["candidate"] == document["request"]["candidate"])
    baseline_index = next(
        index for index, item in enumerate(document["evaluations"])
        if item["candidate"] == document["baseline"])

    faults = {
        "oversize": [gpu_insn(OP_ADDI, rd=1, ra=1, imm=1)] * (PROG_WORDS + 1),
        "bad-opcode": [gpu_insn(OP_TID, rd=0), gpu_insn(27, rd=1, ra=0),
                       gpu_insn(OP_HALT)],
        "no-halt": [gpu_insn(OP_TID, rd=0), gpu_insn(OP_ADDI, rd=1, ra=0, imm=1)],
        "undefined-register": [gpu_insn(OP_TID, rd=0),
                               gpu_insn(OP_ADD, rd=1, ra=0, rb=5),
                               gpu_insn(OP_HALT)],
        "escapes-window": [gpu_insn(OP_TID, rd=0),
                           gpu_insn(OP_ADDI, rd=1, ra=0, imm=DATA_WORDS),
                           gpu_insn(OP_LDX, rd=2, ra=1),
                           gpu_insn(OP_HALT)],
    }
    for label, words in faults.items():
        mutated = copy.deepcopy(document)
        evaluation = mutated["evaluations"][accepted_index]
        evaluation["program"]["words"] = words
        evaluation["program"]["program_sha256"] = artifact_hash(words)
        evaluation["observation"] = {"simulated": False, "output_sha256": None,
                                     "load_cycles": None, "execute_cycles": None}
        reseal(mutated)
        validate_document(Path(f"<{label}>"), mutated, registry)
        if evaluation["verdict"] != "org.atomix.rejected":
            raise pc.ContractError(f"{label} candidate was not rejected")
        if mutated["request"]["status"] != "org.atomix.withheld":
            raise pc.ContractError(f"{label} candidate still produced a request")

    wrong = copy.deepcopy(document)
    evaluation = wrong["evaluations"][accepted_index]
    evaluation["observation"]["output_sha256"] = "0" * 64
    reseal(wrong)
    validate_document(Path("<wrong-output>"), wrong, registry)
    if evaluation["verdict"] != "org.atomix.rejected":
        raise pc.ContractError("a candidate failing its oracle was accepted")
    if wrong["request"]["status"] != "org.atomix.withheld":
        raise pc.ContractError("a candidate failing its oracle produced a request")

    slower = copy.deepcopy(document)
    baseline = next(item for item in slower["evaluations"]
                    if item["candidate"] == slower["baseline"])
    slower["evaluations"][accepted_index]["observation"]["execute_cycles"] = \
        baseline["observation"]["execute_cycles"]
    reseal(slower)
    validate_document(Path("<no-improvement>"), slower, registry)
    if slower["request"]["status"] != "org.atomix.withheld":
        raise pc.ContractError("a candidate without improvement was signed off")

    unproven = copy.deepcopy(document)
    unproven_baseline = next(item for item in unproven["evaluations"]
                             if item["candidate"] == unproven["baseline"])
    unproven_baseline["observation"]["output_sha256"] = "0" * 64
    reseal(unproven)
    validate_document(Path("<broken-baseline>"), unproven, registry)
    if unproven["request"]["status"] != "org.atomix.withheld" or \
            unproven["request"]["reason"] != \
            ["org.atomix.reason.baseline-not-established"]:
        raise pc.ContractError("a request survived an unestablished baseline")

    forged = copy.deepcopy(document)
    forged["request"]["actuation"] = "org.atomix.authorized"
    try:
        validate_document(Path("<forged-authority>"), forged, registry)
    except pc.ContractError:
        pass
    else:
        raise pc.ContractError("an L2 record claimed actuation authority")

    print("live L2 shadow: SELF-TEST PASS "
          "(length, opcode, halt, register, window, oracle, improvement, authority)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check", help="validate a shadow record")
    check_parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_SHADOW)
    check_parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    subparsers.add_parser("self-test", help="exercise the L2 rejection gates")
    args = parser.parse_args()
    try:
        return self_test() if args.command == "self-test" else \
            check(args.path, args.registry)
    except pc.ContractError as exc:
        print(f"live L2 shadow: FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
