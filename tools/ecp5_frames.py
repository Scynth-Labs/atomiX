#!/usr/bin/env python3
"""Parse ECP5 bitstreams and recover the frame-address map empirically.

Stage 2 of the partial-reconfiguration track needs something Trellis does not
provide: the frame-address encoding for the ECP5-85F.  `ecppack --delta` refuses
the part outright with `FIXME: partial bitstreams only supported for ECP5-45k`,
and that refusal is not caused by missing device data — `devices.json` already
carries the 85F geometry (13,294 frames of 1,136 bits).  What is missing is the
mapping from a frame index to the value written by `LSC_WRITE_ADDRESS`.

That mapping cannot be read off a full bitstream.  A full bitstream issues
`LSC_INIT_ADDRESS` once and then streams every frame sequentially, so it never
names an address.  Only a *partial* bitstream emits one `LSC_WRITE_ADDRESS` per
frame — which is available for the 45F and is therefore the only ground truth
the open tools expose.

The `probe-map` subcommand exploits that.  Perturbing a single tile in a
`.config` changes a handful of frames; packing that perturbed config both fully
and as a delta yields exactly as many emitted addresses as there are changed
frames, so the two lists pair unambiguously and each probe yields exact
(frame index, address) pairs.  Aggregating probes across the die samples the
map without needing to guess its algebraic form first.

    tools/ecp5_frames.py frames design.bit --device LFE5U-45F
    tools/ecp5_frames.py diff a.bit b.bit --device LFE5U-85F
    tools/ecp5_frames.py probe-map base.bit probe.bit probe.delta.bit \
        --device LFE5U-45F
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

DB = Path(os.environ.get(
    "TRELLIS_DB",
    Path.home() / "opt/oss-cad-suite/share/trellis/database")) / "devices.json"

SYNC = b"\xbd\xb3"
DUMMY = 0xFF
LSC_RESET_CRC, VERIFY_ID, LSC_WRITE_COMP_DIC = 0x3B, 0xE2, 0x02
LSC_PROG_CNTRL0, LSC_INIT_ADDRESS = 0x22, 0x46
LSC_WRITE_ADDRESS = 0xB4
LSC_PROG_INCR_RTI, LSC_PROG_INCR_CMP = 0x82, 0xB8
LSC_PROG_SED_CRC, ISC_PROGRAM_USERCODE = 0xA2, 0xC2
ISC_PROGRAM_DONE, JUMP = 0x5E, 0x7E


def geometry(device: str) -> dict[str, int]:
    families = json.loads(DB.read_text())["families"]
    for family in families.values():
        if device in family.get("devices", {}):
            info = family["devices"][device]
            bits = info["bits_per_frame"] + info["pad_bits_before_frame"] + \
                info["pad_bits_after_frame"]
            if bits % 8:
                raise SystemExit(f"{device}: frame is not byte-aligned ({bits} bits)")
            return {"frames": info["frames"], "frame_bytes": bits // 8,
                    "bits_per_frame": info["bits_per_frame"],
                    "max_row": info["max_row"], "max_col": info["max_col"]}
    raise SystemExit(f"unknown device {device!r}")


def parse(path: Path, frame_bytes: int) -> dict[str, Any]:
    """Walk the command stream and return every frame with the address the
    configuration engine would have written it to."""
    data = path.read_bytes()
    start = data.find(SYNC)
    if start < 0:
        raise SystemExit(f"{path}: no 0xBDB3 sync word")
    i = start + 2
    frames: list[tuple[int | None, bytes]] = []
    explicit: list[int] = []
    address: int | None = None
    compressed = False
    while i < len(data):
        op = data[i]
        if op == DUMMY:
            i += 1
        elif op == LSC_WRITE_ADDRESS:
            address = int.from_bytes(data[i + 4:i + 8], "big")
            explicit.append(address)
            i += 8
        elif op == LSC_INIT_ADDRESS:
            address = 0
            i += 4
        elif op in (LSC_PROG_INCR_RTI, LSC_PROG_INCR_CMP):
            # A compressed stream stores frames at variable length against a
            # dictionary, so the fixed stride below would silently produce
            # `count` garbage slices and a frame total that looks correct
            # because it was read from the header.  Refuse instead.
            if op == LSC_PROG_INCR_CMP:
                raise SystemExit(
                    f"{path}: compressed bitstream (LSC_PROG_INCR_CMP); "
                    "repack without --compress before frame analysis")
            flags = data[i + 1]
            count = int.from_bytes(data[i + 2:i + 4], "big")
            crc = 2 if flags & 0x80 else 0
            i += 4
            need = count * (frame_bytes + crc)
            if i + need > len(data):
                raise SystemExit(
                    f"{path}: frame block claims {count} frames but only "
                    f"{(len(data) - i) // (frame_bytes + crc)} fit in the file")
            for _ in range(count):
                frames.append((address, data[i:i + frame_bytes]))
                i += frame_bytes + crc
                if address is not None:
                    address += 1
        elif op in (LSC_RESET_CRC, ISC_PROGRAM_DONE, LSC_PROG_SED_CRC):
            i += 4
        elif op in (VERIFY_ID, LSC_PROG_CNTRL0, ISC_PROGRAM_USERCODE,
                    LSC_WRITE_COMP_DIC):
            i += 8
        elif op == JUMP:
            break
        else:
            i += 1
    return {"frames": frames, "explicit_addresses": explicit,
            "compressed": compressed}


def load_frames(path: Path, frame_bytes: int) -> list[bytes]:
    return [payload for _, payload in parse(path, frame_bytes)["frames"]]


def cmd_frames(args) -> int:
    geo = geometry(args.device)
    parsed = parse(args.path, geo["frame_bytes"])
    count = len(parsed["frames"])
    print(f"{args.path.name}: {count} frames of {geo['frame_bytes']} bytes "
          f"({'compressed' if parsed['compressed'] else 'uncompressed'})")
    print(f"expected {geo['frames']} for {args.device}: "
          f"{'MATCH' if count == geo['frames'] else 'MISMATCH'}")
    print(f"explicit LSC_WRITE_ADDRESS commands: "
          f"{len(parsed['explicit_addresses'])}")
    return 0 if count == geo["frames"] else 1


def cmd_diff(args) -> int:
    geo = geometry(args.device)
    a = load_frames(args.reference, geo["frame_bytes"])
    b = load_frames(args.candidate, geo["frame_bytes"])
    changed = [k for k in range(min(len(a), len(b))) if a[k] != b[k]]
    print(f"frames: {len(a)} vs {len(b)}")
    print(f"changed frames: {len(changed)}")
    if changed:
        span = changed[-1] - changed[0] + 1
        print(f"span: {changed[0]}..{changed[-1]} = {span} frames "
              f"({span / len(a):.1%} of the device)")
    return 0


def cmd_probe_map(args) -> int:
    """Pair changed frames with emitted addresses for one small perturbation.

    Only usable when the two counts agree; a probe that perturbs a tile whose
    frames the packer coalesces differently is reported and skipped rather than
    guessed at."""
    geo = geometry(args.device)
    base = load_frames(args.base, geo["frame_bytes"])
    probe = load_frames(args.probe, geo["frame_bytes"])
    addresses = parse(args.delta, geo["frame_bytes"])["explicit_addresses"]
    changed = sorted((k for k in range(min(len(base), len(probe)))
                      if base[k] != probe[k]), reverse=True)
    if len(changed) != len(addresses):
        print(f"unpairable: {len(changed)} changed frames but "
              f"{len(addresses)} emitted addresses", file=sys.stderr)
        return 1
    pairs = sorted(zip(changed, addresses))
    for index, address in pairs:
        print(f"{index}\t{address}\t{address - index:+d}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="LFE5U-85F")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("frames", help="parse a bitstream and check frame count")
    p.add_argument("path", type=Path)
    p.set_defaults(func=cmd_frames)
    p = sub.add_parser("diff", help="frame-level difference between bitstreams")
    p.add_argument("reference", type=Path)
    p.add_argument("candidate", type=Path)
    p.set_defaults(func=cmd_diff)
    p = sub.add_parser("probe-map", help="recover (frame, address) pairs")
    p.add_argument("base", type=Path)
    p.add_argument("probe", type=Path)
    p.add_argument("delta", type=Path)
    p.set_defaults(func=cmd_probe_map)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
