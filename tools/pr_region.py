#!/usr/bin/env python3
"""Measure the frame-address footprint of a declared role rectangle.

`pr_verify_delta.py` enforces a region; this tool is where that region comes
from.  Declaring one by hand would make the gate circular -- the allow-list
would encode the same assumption the gate is supposed to test -- so the region
is *measured* on the real device geometry instead.

The method exploits the one thing `ecppack --delta` gives away for free on the
45F: a partial bitstream names every frame it writes.  Empty every tile inside
the rectangle in a copy of a routed `.config`, pack that copy as a delta
against the original, and the emitted `LSC_WRITE_ADDRESS` values are exactly
the frames those tiles can reach.  No frame-address *function* has to be
derived; the packer states the addresses and this reads them back.

Two probes run, not one.  The complement probe empties every tile *outside*
the rectangle, and the two address sets are then intersected.  That is the
measurement that matters: if a frame carries bits from both sides of the
boundary, then no partial image can rewrite the role without also rewriting
shell state, and whole-frame confinement is impossible on this geometry
regardless of how good the floorplan is.  A region manifest therefore records
whether it is separable, and `separable: false` is a result rather than an
error.

Under-measuring is the safe direction.  A tile contributes an address only
where emptying it actually changes a bit, so a frame the role could reach but
never sets is absent from the allow-list; that makes a legitimate delta fail
the gate, never a hostile one pass it.

    tools/pr_region.py measure reference.config \
        --reference-bit reference.bit --device LFE5U-45F \
        --idcode 0x41112043 --columns 1:13 --rows 1:70 \
        --output research/partial-reconfig/ulx3s-45f-role-window.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    from .ecp5_frames import geometry
    from .pr_verify_delta import DeltaRejected, unpack, sha256_file
except ImportError:
    from ecp5_frames import geometry
    from pr_verify_delta import DeltaRejected, unpack, sha256_file

ROOT = Path(__file__).resolve().parents[1]
REGION_SCHEMA = "org.atomix.pr-region.v1"

TILE_RE = re.compile(r"^\.tile\s+(\S+)")
POS_RE = re.compile(r"R(\d+)C(\d+)")
# `.config` sections that are not tile bodies.  Emptying a tile must not
# disturb these, or the probe would attribute their frames to the rectangle.
SECTION_RE = re.compile(r"^\.")


def parse_span(text: str, name: str) -> tuple[int, int]:
    try:
        lo, _, hi = text.partition(":")
        span = (int(lo), int(hi if hi else lo))
    except ValueError:
        raise SystemExit(f"--{name}: expected LO:HI, got {text!r}") from None
    if span[0] > span[1]:
        raise SystemExit(f"--{name}: {span[0]} > {span[1]}")
    return span


def tile_position(name: str) -> tuple[int, int] | None:
    match = POS_RE.search(name)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def split_tiles(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Return the file's leading directives and every (tile, body) pair.

    Bodies are kept as raw lines so a rewritten config differs from the input
    in exactly the tiles this tool chose to empty and nowhere else."""
    header: list[str] = []
    tiles: list[tuple[str, list[str]]] = []
    current: str | None = None
    body: list[str] = []
    for line in text.splitlines(True):
        match = TILE_RE.match(line)
        if match:
            if current is not None:
                tiles.append((current, body))
            current, body = match.group(1), []
            continue
        if SECTION_RE.match(line) and not line.startswith(".tile"):
            # A non-tile section ends the tile it follows; keep it verbatim in
            # whichever bucket is currently open so round-tripping is exact.
            if current is not None:
                tiles.append((current, body))
                current, body = None, []
            header.append(line)
            continue
        if current is None:
            header.append(line)
        else:
            body.append(line)
    if current is not None:
        tiles.append((current, body))
    return header, tiles


def render_config(header: list[str], tiles: list[tuple[str, list[str]]],
                  emptied: set[str]) -> str:
    out = list(header)
    for name, body in tiles:
        out.append(f".tile {name}\n")
        if name not in emptied:
            out.extend(body)
        out.append("\n")
    return "".join(out)


def probe(reference: Path, header: list[str],
          tiles: list[tuple[str, list[str]]], emptied: set[str],
          idcode: str, frame_bytes: int, workdir: Path,
          label: str) -> dict[str, Any]:
    """Empty the named tiles, pack a delta, and read back the addresses."""
    candidate = workdir / f"{label}.config"
    delta = workdir / f"{label}.delta.bit"
    candidate.write_text(render_config(header, tiles, emptied))
    result = subprocess.run(
        ["ecppack", "--idcode", idcode, str(candidate), str(delta),
         "--delta", str(reference)],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"{label}: ecppack failed\n{result.stderr}")
    try:
        walked = unpack(delta.read_bytes(), frame_bytes)
    except DeltaRejected as exc:
        raise SystemExit(f"{label}: probe delta did not parse: {exc.reason}")
    if walked["unaddressed_frames"]:
        raise SystemExit(f"{label}: probe delta contains unaddressed frames")
    return {
        "addresses": sorted(set(walked["addresses"])),
        "frames": len(walked["frames"]),
        "delta_bytes": delta.stat().st_size,
        "tiles_emptied": len(emptied),
    }


def to_ranges(addresses: list[int]) -> list[list[int]]:
    ranges: list[list[int]] = []
    for address in addresses:
        if ranges and address == ranges[-1][1] + 1:
            ranges[-1][1] = address
        else:
            ranges.append([address, address])
    return ranges


def tool_versions() -> dict[str, str]:
    versions = {}
    for tool, args in (("ecppack", ["--version"]),):
        try:
            done = subprocess.run([tool] + args, capture_output=True, text=True)
            versions[tool] = (done.stdout or done.stderr).strip().splitlines()[0]
        except OSError:
            versions[tool] = "not found"
    return versions


def cmd_measure(args) -> int:
    geo = geometry(args.device)
    rows = parse_span(args.rows, "rows")
    columns = parse_span(args.columns, "columns")
    text = args.reference.read_text()
    header, tiles = split_tiles(text)

    inside: set[str] = set()
    outside: set[str] = set()
    unplaced = 0
    for name, body in tiles:
        if not body:
            continue
        position = tile_position(name)
        if position is None:
            unplaced += 1
            continue
        row, column = position
        if rows[0] <= row <= rows[1] and columns[0] <= column <= columns[1]:
            inside.add(name)
        else:
            outside.add(name)

    if not inside:
        raise SystemExit("no configured tiles fall inside the rectangle")

    print(f"rectangle R{rows[0]}..R{rows[1]} C{columns[0]}..C{columns[1]}: "
          f"{len(inside)} tiles inside, {len(outside)} outside, "
          f"{unplaced} without coordinates")

    with tempfile.TemporaryDirectory(prefix="atomix-pr-region-") as tmp:
        workdir = Path(tmp)
        # Probe zero: re-render the parsed config changing nothing.  If that
        # is not a byte-for-byte equivalent design, the parser is dropping or
        # reordering something and every address below would be attributed to
        # the rectangle when it actually came from the rewrite.
        print("checking the config round-trips ...")
        identity = probe(args.reference, header, tiles, set(), args.idcode,
                         geo["frame_bytes"], workdir, "identity")
        if identity["frames"]:
            raise SystemExit(
                f"config round-trip is not lossless: an unmodified rewrite "
                f"still changes {identity['frames']} frames")
        print("  0 frames — the rewrite is lossless")
        print("probing the role rectangle ...")
        role = probe(args.reference, header, tiles, inside, args.idcode,
                     geo["frame_bytes"], workdir, "role")
        print(f"  {len(role['addresses'])} distinct frame addresses")
        print("probing the complement (shell) ...")
        shell = probe(args.reference, header, tiles, outside, args.idcode,
                      geo["frame_bytes"], workdir, "shell")
        print(f"  {len(shell['addresses'])} distinct frame addresses")

    role_set = set(role["addresses"])
    shell_set = set(shell["addresses"])
    shared = sorted(role_set & shell_set)
    separable = not shared

    print()
    print(f"role frames    {len(role_set)}")
    print(f"shell frames   {len(shell_set)}")
    print(f"shared frames  {len(shared)}")
    print(f"device frames  {geo['frames']}")
    print(f"separable: {separable}")

    manifest = {
        "schema": REGION_SCHEMA,
        "kind": "pr-region-manifest",
        "name": args.name,
        "device": args.device,
        "idcode": args.idcode,
        "reference": {
            "config": str(args.reference),
            "config_sha256": sha256_file(args.reference),
            "bitstream_sha256": (sha256_file(args.reference_bit)
                                 if args.reference_bit else None),
        },
        "rectangle": {"rows": list(rows), "columns": list(columns),
                      "note": args.note},
        "region": {
            "derivation": "org.atomix.derivation.measured-address-probe",
            "separable": separable,
            "allowed_frame_addresses": to_ranges(sorted(role_set)),
            "max_frames": len(role_set),
        },
        "measurement": {
            "device_frames": geo["frames"],
            "frame_bytes": geo["frame_bytes"],
            "tiles_inside": len(inside),
            "tiles_outside": len(outside),
            "tiles_without_position": unplaced,
            "role_probe": role,
            "shell_probe": shell,
            "shared_frames": len(shared),
            "shared_sample": shared[:16],
            "roundtrip_frames": identity["frames"],
            "tools": tool_versions(),
        },
    }
    if not separable:
        manifest["region"]["note"] = (
            "role and shell tiles share configuration frames, so no partial "
            "image can rewrite this rectangle without rewriting shell state")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\nwrote {args.output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("measure", help="measure a rectangle's frame footprint")
    p.add_argument("reference", type=Path, help="routed reference .config")
    p.add_argument("--reference-bit", type=Path,
                   help="the full bitstream built from that config")
    p.add_argument("--device", default="LFE5U-45F")
    p.add_argument("--idcode", default="0x41112043")
    p.add_argument("--rows", default="1:70")
    p.add_argument("--columns", default="1:13")
    p.add_argument("--name", default="ulx3s-45f-role-window")
    p.add_argument("--note", default=None,
                   help="why this rectangle, recorded with the measurement")
    p.add_argument("--output", type=Path, required=True)
    p.set_defaults(func=cmd_measure)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
