#!/usr/bin/env python3
"""R1 stage-3 gate: prove a candidate partial bitstream before it is loaded.

`pr_delta.py` measures *where* two builds differ.  This tool answers the
question that has to be settled before a partial image is ever sent to a live
board: does this file write only frames the role is allowed to own, on the
exact shell it was generated against, and is it a complete stream rather than a
truncated one?

The gates run on the file itself, not on the flow that produced it.  A delta is
accepted or rejected on what it contains, so a packer bug, a mismatched
reference, a copy interrupted mid-transfer, and a deliberately hostile image
are all caught by the same path.  That is the point: on a live device the
recovery channel is the thing being risked, and the shell's UART loader cannot
un-write a frame that has already landed outside the role window.

Following L2 (`live_shadow.py`), a malformed candidate is a *rejection* and not
a tool error: every gate returns a status and a reason, none of them raise on
candidate input, and the only output for a candidate that fails any gate is a
withheld load authorisation.  This tool never programs anything; the record it
writes always carries `actuation: org.atomix.not-authorized`.

    tools/pr_verify_delta.py verify candidate.delta.bit \
        --region research/partial-reconfig/ulx3s-45f-role-window.json \
        --reference reference.bit --json report.json
    tools/pr_verify_delta.py self-test
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

try:
    from .ecp5_frames import geometry
except ImportError:
    from ecp5_frames import geometry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGION = ROOT / "research/partial-reconfig/ulx3s-45f-role-window.json"

REGION_SCHEMA = "org.atomix.pr-region.v1"
REPORT_SCHEMA = "org.atomix.pr-delta-verdict.v1"

PASS, FAIL, SKIPPED = "org.atomix.pass", "org.atomix.fail", "org.atomix.skipped"
ACCEPTED, REJECTED = "org.atomix.accepted", "org.atomix.rejected"
NOT_AUTHORIZED = "org.atomix.not-authorized"

CHECK_STRUCTURE = "org.atomix.check.delta-structure"
CHECK_DEVICE = "org.atomix.check.device-identity"
CHECK_GEOMETRY = "org.atomix.check.frame-geometry"
CHECK_ADDRESSED = "org.atomix.check.every-frame-addressed"
CHECK_SHELL = "org.atomix.check.shell-identity"
CHECK_CONFINED = "org.atomix.check.region-confinement"
CHECK_BUDGET = "org.atomix.check.frame-budget"
ALL_CHECKS = (CHECK_STRUCTURE, CHECK_DEVICE, CHECK_GEOMETRY, CHECK_ADDRESSED,
              CHECK_SHELL, CHECK_CONFINED, CHECK_BUDGET)

# ECP5 configuration command opcodes.  Widths are the fixed command sizes the
# configuration engine consumes; anything not listed here makes the stream
# unaccountable and is rejected rather than skipped.
DUMMY = 0xFF
JUMP = 0x7E
LSC_WRITE_COMP_DIC = 0x02
LSC_PROG_CNTRL0 = 0x22
LSC_RESET_CRC = 0x3B
LSC_INIT_ADDRESS = 0x46
ISC_PROGRAM_DONE = 0x5E
LSC_PROG_INCR_RTI = 0x82
LSC_PROG_INCR_CMP = 0xB8
LSC_WRITE_ADDRESS = 0xB4
LSC_PROG_SED_CRC = 0xA2
VERIFY_ID = 0xE2
ISC_PROGRAM_USERCODE = 0xC2

FIXED_WIDTH = {
    LSC_RESET_CRC: 4,
    LSC_INIT_ADDRESS: 4,
    ISC_PROGRAM_DONE: 4,
    LSC_PROG_SED_CRC: 4,
    VERIFY_ID: 8,
    LSC_PROG_CNTRL0: 8,
    ISC_PROGRAM_USERCODE: 8,
    LSC_WRITE_COMP_DIC: 12,
}

SYNC = b"\xbd\xb3"


class DeltaRejected(Exception):
    """A candidate stream that cannot be accounted for.  Carries the reason id
    the failing gate reports; it is never raised for a *tool* problem."""

    def __init__(self, reason: str, detail: str | None = None):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


# --------------------------------------------------------------------------
# Strict command-stream walker.
#
# `ecp5_frames.parse` is deliberately lenient -- it advances one byte past an
# opcode it does not know, which is right for walking research bitstreams and
# wrong here.  A gate that skips what it cannot parse cannot claim the frames
# it did parse are all the frames the file writes, which is exactly the claim
# region confinement rests on.
# --------------------------------------------------------------------------

def unpack(data: bytes, frame_bytes: int) -> dict[str, Any]:
    """Return every frame the stream writes, with the address it lands on."""
    start = data.find(SYNC)
    if start < 0:
        raise DeltaRejected("org.atomix.reason.no-sync-word")

    offset = start + 2
    frames: list[tuple[int | None, bytes]] = []
    addresses: list[int] = []
    idcode: int | None = None
    unaddressed = 0
    address: int | None = None
    terminated = False
    blocks = 0

    while offset < len(data):
        op = data[offset]
        if op == DUMMY:
            offset += 1
            continue
        if op == JUMP:
            terminated = True
            offset += 4
            break
        if op == ISC_PROGRAM_DONE:
            # Trellis terminates both full and partial streams here and pads
            # with 0xFF; JUMP is accepted too but ecppack does not emit it.
            terminated = True
            offset += 4
            continue
        if op == LSC_PROG_INCR_CMP:
            # A compressed block covers the whole device by construction, so it
            # is a full image regardless of what the caller believes it is.
            raise DeltaRejected("org.atomix.reason.compressed-full-image")
        if op == LSC_WRITE_ADDRESS:
            if offset + 8 > len(data):
                raise DeltaRejected("org.atomix.reason.truncated-command")
            address = int.from_bytes(data[offset + 4:offset + 8], "big")
            addresses.append(address)
            offset += 8
            continue
        if op == LSC_INIT_ADDRESS:
            address = 0
            offset += 4
            continue
        if op == LSC_PROG_INCR_RTI:
            if offset + 4 > len(data):
                raise DeltaRejected("org.atomix.reason.truncated-command")
            flags = data[offset + 1]
            count = int.from_bytes(data[offset + 2:offset + 4], "big")
            # Same trailer encoding the compressed decoder uses: bit 7 enables
            # the CRC, bit 6 moves it from after every frame to once at the end
            # of the block, and the low nibble is a per-frame dummy-byte count.
            # Ignoring the nibble desynchronises the walk one byte per frame,
            # which resynchronises on stray 0xB4 payload bytes and silently
            # invents addresses -- the exact failure this gate must not have.
            check_crc = bool(flags & 0x80)
            crc_each = 2 if check_crc and not flags & 0x40 else 0
            crc_last = 2 if check_crc and flags & 0x40 else 0
            dummy = flags & 0x0F
            offset += 4
            need = count * (frame_bytes + crc_each + dummy) + crc_last
            if offset + need > len(data):
                raise DeltaRejected(
                    "org.atomix.reason.truncated-frame-block",
                    f"block claims {count} frames ({need} bytes) but only "
                    f"{len(data) - offset} bytes remain")
            blocks += 1
            for _ in range(count):
                frames.append((address, data[offset:offset + frame_bytes]))
                if address is None:
                    unaddressed += 1
                else:
                    address += 1
                offset += frame_bytes + crc_each + dummy
            offset += crc_last
            continue
        width = FIXED_WIDTH.get(op)
        if width is None:
            raise DeltaRejected("org.atomix.reason.unknown-command",
                                f"opcode 0x{op:02X} at byte {offset}")
        if offset + width > len(data):
            raise DeltaRejected("org.atomix.reason.truncated-command")
        if op == VERIFY_ID:
            idcode = int.from_bytes(data[offset + 4:offset + 8], "big")
        offset += width

    if not terminated:
        raise DeltaRejected("org.atomix.reason.stream-does-not-terminate")
    return {
        "frames": frames,
        "addresses": addresses,
        "idcode": idcode,
        "unaddressed_frames": unaddressed,
        "blocks": blocks,
        # Padding to a device-word boundary is normal after JUMP; anything
        # else is a second stream, or a first one that was appended to.
        "trailing_bytes": sum(1 for byte in data[offset:] if byte != DUMMY),
    }


# --------------------------------------------------------------------------
# Region manifest.
# --------------------------------------------------------------------------

def load_region(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{path}: {exc}") from exc
    if manifest.get("schema") != REGION_SCHEMA:
        raise SystemExit(f"{path}: expected schema {REGION_SCHEMA}")
    region = manifest.get("region", {})
    ranges = region.get("allowed_frame_addresses")
    if not isinstance(ranges, list) or not ranges:
        raise SystemExit(f"{path}: region.allowed_frame_addresses is required")
    for entry in ranges:
        if not (isinstance(entry, list) and len(entry) == 2
                and all(isinstance(value, int) for value in entry)
                and entry[0] <= entry[1]):
            raise SystemExit(f"{path}: malformed address range {entry!r}")
    if not isinstance(region.get("max_frames"), int):
        raise SystemExit(f"{path}: region.max_frames is required")
    return manifest


def allowed_addresses(manifest: dict[str, Any]) -> list[tuple[int, int]]:
    return [(lo, hi) for lo, hi
            in manifest["region"]["allowed_frame_addresses"]]


def in_region(address: int, ranges: list[tuple[int, int]]) -> bool:
    return any(lo <= address <= hi for lo, hi in ranges)


# --------------------------------------------------------------------------
# Gates.  Each returns (status, reason, detail) and never raises on candidate
# input.  Order matters: a stream that did not parse has no frames to confine.
# --------------------------------------------------------------------------

def gate_device(walked: dict[str, Any], geo: dict[str, int],
                idcode: int) -> tuple[str, str | None, Any]:
    observed = walked["idcode"]
    if observed is None:
        return FAIL, "org.atomix.reason.no-device-id", None
    if observed != idcode:
        return (FAIL, "org.atomix.reason.device-id-mismatch",
                {"expected": f"0x{idcode:08X}", "observed": f"0x{observed:08X}"})
    return PASS, None, {"idcode": f"0x{observed:08X}"}


def gate_geometry(walked: dict[str, Any],
                  geo: dict[str, int]) -> tuple[str, str | None, Any]:
    frames = walked["frames"]
    if not frames:
        return FAIL, "org.atomix.reason.no-frames-written", None
    if len(frames) >= geo["frames"]:
        # A stream that writes the whole device is a full image; calling it a
        # partial one would let a full reload pass a confinement gate.
        return (FAIL, "org.atomix.reason.writes-whole-device",
                {"frames": len(frames), "device_frames": geo["frames"]})
    widths = {len(payload) for _, payload in frames}
    if widths != {geo["frame_bytes"]}:
        return (FAIL, "org.atomix.reason.frame-width-mismatch",
                {"expected": geo["frame_bytes"], "observed": sorted(widths)})
    if walked["trailing_bytes"] > 0:
        return (FAIL, "org.atomix.reason.trailing-bytes-after-jump",
                {"bytes": walked["trailing_bytes"]})
    return PASS, None, {"frames": len(frames),
                        "frame_bytes": geo["frame_bytes"],
                        "blocks": walked["blocks"]}


def gate_addressed(walked: dict[str, Any]) -> tuple[str, str | None, Any]:
    """A partial image must name every frame it writes.

    `LSC_INIT_ADDRESS` plus a sequential run is how a *full* image streams the
    device without ever naming an address.  Accepting that shape here would
    mean confinement was checked against addresses the file never stated."""
    if walked["unaddressed_frames"]:
        return (FAIL, "org.atomix.reason.frames-without-explicit-address",
                {"frames": walked["unaddressed_frames"]})
    if not walked["addresses"]:
        return FAIL, "org.atomix.reason.no-write-address-command", None
    return PASS, None, {"write_address_commands": len(walked["addresses"])}


def gate_shell(manifest: dict[str, Any],
               reference_digest: str | None) -> tuple[str, str | None, Any]:
    """A delta is only meaningful against the exact image it was cut from.

    Every frame it omits is a frame it assumes is already correct on the
    device, so loading it over a different shell leaves the fabric in a state
    neither image describes."""
    expected = manifest["reference"]["bitstream_sha256"]
    if reference_digest is None:
        return FAIL, "org.atomix.reason.reference-not-supplied", None
    if reference_digest != expected:
        return (FAIL, "org.atomix.reason.reference-shell-mismatch",
                {"expected": expected, "observed": reference_digest})
    return PASS, None, {"reference_sha256": expected}


def gate_confined(walked: dict[str, Any],
                  ranges: list[tuple[int, int]]) -> tuple[str, str | None, Any]:
    written = sorted({address for address, _ in walked["frames"]
                      if address is not None})
    outside = [address for address in written if not in_region(address, ranges)]
    detail = {
        "frames_written": len(written),
        "frames_outside_region": len(outside),
        "address_span": [written[0], written[-1]] if written else None,
        "outside_sample": outside[:16],
    }
    if outside:
        return FAIL, "org.atomix.reason.frame-outside-role-region", detail
    return PASS, None, detail


def gate_budget(walked: dict[str, Any], manifest: dict[str, Any],
                full_image_bytes: int | None,
                delta_bytes: int) -> tuple[str, str | None, Any]:
    """Two independent budgets, both load-bearing.

    The frame budget is the region's own size: a delta cannot write more
    distinct frames than the region contains.  The size budget is the reason
    partial reconfiguration exists at all -- an image larger than simply
    reloading the whole device is slower *and* riskier than the thing it
    replaces, so it is refused rather than reported."""
    written = {address for address, _ in walked["frames"] if address is not None}
    budget = manifest["region"]["max_frames"]
    detail: dict[str, Any] = {"frames_written": len(written),
                              "max_frames": budget,
                              "delta_bytes": delta_bytes,
                              "full_image_bytes": full_image_bytes}
    if len(written) > budget:
        return FAIL, "org.atomix.reason.frame-budget-exceeded", detail
    if full_image_bytes is not None and delta_bytes >= full_image_bytes:
        return FAIL, "org.atomix.reason.larger-than-full-reload", detail
    return PASS, None, detail


def evaluate(delta: bytes, manifest: dict[str, Any],
             reference_digest: str | None = None,
             full_image_bytes: int | None = None) -> dict[str, Any]:
    """Run every gate in a fixed order and derive the load authorisation."""
    geo = geometry(manifest["device"])
    idcode = int(manifest["idcode"], 0)
    checks: list[dict[str, Any]] = []

    def record(check_id: str, outcome: tuple[str, str | None, Any]) -> str:
        status, reason, detail = outcome
        checks.append({"id": check_id, "status": status, "reason": reason,
                       "detail": detail})
        return status

    try:
        walked = unpack(delta, geo["frame_bytes"])
    except DeltaRejected as exc:
        checks.append({"id": CHECK_STRUCTURE, "status": FAIL,
                       "reason": exc.reason, "detail": exc.detail})
        for check_id in ALL_CHECKS[1:]:
            checks.append({"id": check_id, "status": SKIPPED,
                           "reason": "org.atomix.reason.earlier-gate-failed",
                           "detail": None})
        walked = None
    else:
        checks.append({"id": CHECK_STRUCTURE, "status": PASS, "reason": None,
                       "detail": {"blocks": walked["blocks"]}})
        record(CHECK_DEVICE, gate_device(walked, geo, idcode))
        record(CHECK_GEOMETRY, gate_geometry(walked, geo))
        record(CHECK_ADDRESSED, gate_addressed(walked))
        record(CHECK_SHELL, gate_shell(manifest, reference_digest))
        record(CHECK_CONFINED, gate_confined(walked, allowed_addresses(manifest)))
        record(CHECK_BUDGET, gate_budget(walked, manifest, full_image_bytes,
                                         len(delta)))

    accepted = all(item["status"] == PASS for item in checks)
    return {
        "schema": REPORT_SCHEMA,
        "kind": "pr-delta-verdict",
        "region_manifest": manifest["name"],
        "device": manifest["device"],
        "delta_sha256": sha256_bytes(delta),
        "delta_bytes": len(delta),
        "checks": checks,
        "verdict": ACCEPTED if accepted else REJECTED,
        "load_authorization": {
            "status": ("org.atomix.permitted" if accepted
                       else "org.atomix.withheld"),
            "actuation": NOT_AUTHORIZED,
            "reasons": ([ "org.atomix.reason.all-gates-passed"] if accepted
                        else [item["reason"] for item in checks
                              if item["status"] == FAIL]),
        },
    }


# --------------------------------------------------------------------------
# Reporting.
# --------------------------------------------------------------------------

def render(report: dict[str, Any]) -> None:
    print(f"device         {report['device']}")
    print(f"region         {report['region_manifest']}")
    print(f"delta          {report['delta_bytes']} bytes")
    print(f"               {report['delta_sha256']}")
    print()
    for item in report["checks"]:
        mark = {PASS: "PASS", FAIL: "FAIL", SKIPPED: "skip"}[item["status"]]
        name = item["id"].rsplit(".", 1)[-1]
        print(f"  [{mark}] {name}")
        if item["reason"]:
            print(f"         {item['reason']}")
        detail = item["detail"]
        if isinstance(detail, dict):
            for key, value in detail.items():
                if value not in (None, [], {}):
                    print(f"         {key}: {value}")
    print()
    verdict = report["verdict"].rsplit(".", 1)[-1].upper()
    authorization = report["load_authorization"]["status"].rsplit(".", 1)[-1]
    print(f"verdict: {verdict}   load authorization: {authorization}")


def cmd_verify(args) -> int:
    manifest = load_region(args.region)
    try:
        delta = args.delta.read_bytes()
    except OSError as exc:
        raise SystemExit(f"{args.delta}: {exc}") from exc

    reference_digest = None
    if args.reference is not None:
        try:
            reference_digest = sha256_file(args.reference)
        except OSError as exc:
            raise SystemExit(f"{args.reference}: {exc}") from exc

    full_image_bytes = None
    if args.full_image is not None:
        try:
            full_image_bytes = args.full_image.stat().st_size
        except OSError as exc:
            raise SystemExit(f"{args.full_image}: {exc}") from exc

    report = evaluate(delta, manifest, reference_digest, full_image_bytes)
    render(report)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n")
        print(f"\nwrote {args.json}")
    return 0 if report["verdict"] == ACCEPTED else 1


# --------------------------------------------------------------------------
# Self-test: the rejections are the deliverable, so they are exercised against
# a synthetic stream this file builds itself.  It depends on no build output,
# so the gate keeps its coverage on a machine with no FPGA toolchain.
# --------------------------------------------------------------------------

def build_delta(addresses: list[int], frame_bytes: int, idcode: int,
                *, terminator: str | None = "done", crc: bool = False,
                init_address: bool = False, fill: int = 0,
                dummy: int = 0) -> bytes:
    """Assemble a minimal but structurally faithful ECP5 partial stream."""
    out = bytearray(b"\xff" * 8 + SYNC)
    out += bytes([VERIFY_ID, 0, 0, 0]) + idcode.to_bytes(4, "big")
    out += bytes([LSC_RESET_CRC, 0, 0, 0])
    flags = (0x80 if crc else 0x00) | (dummy & 0x0F)
    if init_address:
        out += bytes([LSC_INIT_ADDRESS, 0, 0, 0])
    for index, address in enumerate(addresses):
        if not init_address:
            out += bytes([LSC_WRITE_ADDRESS, 0, 0, 0]) + address.to_bytes(4, "big")
        out += bytes([LSC_PROG_INCR_RTI, flags, 0, 1])
        out += bytes([(index + 1 + fill) & 0xFF]) * frame_bytes
        if crc:
            out += b"\x00\x00"
        out += b"\x00" * dummy
    if terminator == "done":
        out += bytes([ISC_PROGRAM_DONE, 0, 0, 0]) + b"\xff" * 4
    elif terminator == "jump":
        out += bytes([JUMP, 0, 0, 0])
    return bytes(out)


def self_test() -> int:
    device = "LFE5U-45F"
    geo = geometry(device)
    idcode = 0x41112043
    inside = list(range(1000, 1016))
    manifest = {
        "schema": REGION_SCHEMA,
        "name": "<self-test>",
        "device": device,
        "idcode": "0x41112043",
        "reference": {"bitstream_sha256": "sha256:" + "a" * 64},
        "region": {
            "derivation": "org.atomix.derivation.self-test",
            "allowed_frame_addresses": [[1000, 1099]],
            "max_frames": 100,
        },
    }
    good_reference = manifest["reference"]["bitstream_sha256"]
    frame_bytes = geo["frame_bytes"]

    def verdict(delta: bytes, *, reference=good_reference,
                full_image=None, region=manifest) -> dict[str, Any]:
        return evaluate(delta, region, reference, full_image)

    def failing(report: dict[str, Any]) -> set[str]:
        return {item["id"] for item in report["checks"] if item["status"] == FAIL}

    # Accepted shapes.  `crc=True, dummy=1` is what ecppack actually emits
    # (flags 0x91); a walker that ignores the dummy nibble drifts one byte per
    # frame and then resynchronises on payload bytes that happen to be 0xB4,
    # so this case is the one that keeps confinement honest on real files.
    for label, delta in (
            ("plain", build_delta(inside, frame_bytes, idcode)),
            ("jump-terminated",
             build_delta(inside, frame_bytes, idcode, terminator="jump")),
            ("ecppack-flags",
             build_delta(inside, frame_bytes, idcode, crc=True, dummy=1)),
    ):
        accepted = verdict(delta)
        if accepted["verdict"] != ACCEPTED:
            raise SystemExit(
                f"a confined delta ({label}) was rejected: {failing(accepted)}")
        if accepted["load_authorization"]["status"] != "org.atomix.permitted":
            raise SystemExit(f"a confined delta ({label}) was not authorized")

    cases: list[tuple[str, bytes, str, dict[str, Any]]] = []

    # Truncation, in both places a transfer can stop: mid-command and mid-frame.
    whole = build_delta(inside, frame_bytes, idcode)
    cases.append(("truncated-mid-frame", whole[:len(whole) - frame_bytes // 2],
                  CHECK_STRUCTURE, {}))
    cases.append(("truncated-no-terminator",
                  build_delta(inside, frame_bytes, idcode, terminator=None),
                  CHECK_STRUCTURE, {}))
    cases.append(("empty", b"", CHECK_STRUCTURE, {}))

    # Out of region, at both edges and interleaved with legitimate frames.
    cases.append(("out-of-region-above",
                  build_delta(inside + [1100], frame_bytes, idcode),
                  CHECK_CONFINED, {}))
    cases.append(("out-of-region-below",
                  build_delta([999] + inside, frame_bytes, idcode),
                  CHECK_CONFINED, {}))
    cases.append(("out-of-region-interleaved",
                  build_delta(inside[:8] + [4096] + inside[8:], frame_bytes,
                              idcode),
                  CHECK_CONFINED, {}))

    # Wrong shell, wrong device, and a full image wearing a delta's name.
    cases.append(("wrong-shell", whole, CHECK_SHELL,
                  {"reference": "sha256:" + "b" * 64}))
    cases.append(("reference-missing", whole, CHECK_SHELL, {"reference": None}))
    cases.append(("wrong-device",
                  build_delta(inside, frame_bytes, 0x41113043), CHECK_DEVICE, {}))
    cases.append(("unaddressed-full-stream",
                  build_delta(inside, frame_bytes, idcode, init_address=True),
                  CHECK_ADDRESSED, {}))
    cases.append(("larger-than-full-reload", whole, CHECK_BUDGET,
                  {"full_image": 16}))

    over_budget = dict(manifest)
    over_budget["region"] = dict(manifest["region"], max_frames=4)
    cases.append(("frame-budget-exceeded", whole, CHECK_BUDGET,
                  {"region": over_budget}))

    for label, delta, expected_gate, kwargs in cases:
        report = verdict(delta, **kwargs)
        failed = failing(report)
        if report["verdict"] != REJECTED:
            raise SystemExit(f"{label}: accepted a delta that must be rejected")
        if expected_gate not in failed:
            raise SystemExit(
                f"{label}: expected {expected_gate} to fail, got {sorted(failed)}")
        if report["load_authorization"]["status"] != "org.atomix.withheld":
            raise SystemExit(f"{label}: rejected delta still authorized a load")

    # A rejected candidate must never carry an actuation claim, whatever the
    # reason for its rejection.
    for label, delta, _, kwargs in cases:
        report = verdict(delta, **kwargs)
        if report["load_authorization"]["actuation"] != NOT_AUTHORIZED:
            raise SystemExit(f"{label}: record claims actuation authority")

    # What a frame *contains* is the role's business; where it lands is the
    # shell's.  Two deltas differing only in payload must gate identically, or
    # confinement would be reading the wrong thing.
    other = build_delta(inside, frame_bytes, idcode, fill=0x40)
    if other == whole:
        raise SystemExit("the payload-independence case did not change payloads")
    statuses = [(item["id"], item["status"]) for item in verdict(other)["checks"]]
    if statuses != [(item["id"], item["status"])
                    for item in verdict(whole)["checks"]]:
        raise SystemExit("payload contents changed a gate verdict")

    print(f"PR delta gate self-test: PASS "
          f"({len(cases)} rejection cases, {len(ALL_CHECKS)} gates)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("verify", help="gate one candidate partial bitstream")
    p.add_argument("delta", type=Path)
    p.add_argument("--region", type=Path, default=DEFAULT_REGION,
                   help="region manifest naming the allowed frame addresses")
    p.add_argument("--reference", type=Path,
                   help="the full bitstream this delta was cut against")
    p.add_argument("--full-image", type=Path,
                   help="full candidate bitstream, to check the delta is smaller")
    p.add_argument("--json", type=Path, help="write the verdict record")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("self-test", help="exercise every rejection path")
    p.set_defaults(func=lambda args: self_test())

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
