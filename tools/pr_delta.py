#!/usr/bin/env python3
"""Measure the configuration delta between two ECP5 builds of the same shell.

Stage 2 of the partial-reconfiguration research track (docs/partial-reconfig.md):
before any live-reconfiguration experiment is worth attempting, we need to know
*where on the die* two builds that differ only in their role component actually
differ.  If swapping a role perturbs shell tiles scattered across the chip --
which is what an unconstrained place-and-route is expected to do -- then a
partial bitstream covering only the role region cannot exist yet, and the
placement-locking work in stage 3 is the prerequisite rather than an
optimisation.

The measurement is taken on Trellis `.config` files rather than on packed
bitstreams because `.config` is tile-addressed: every line belongs to a named
tile like `R14C125`, so a difference carries coordinates.  A frame count alone
says how much differs; coordinates say whether the difference is confined, and
confinement is the entire question at this stage.

Usage:
    tools/pr_delta.py reference.config candidate.config \
        --reference-bit reference.bit --candidate-bit candidate.bit \
        [--json out.json]

Exit status is 0 whatever the measurement shows: this reports a research
result, it does not assert one.
"""

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

TILE_RE = re.compile(r"^\.tile\s+(\S+)")
# Tile names embed their die position, e.g. CIB_R10C3:PVT_COUNT2 -> row 10, col 3.
POS_RE = re.compile(r"R(\d+)C(\d+)")

# Project Trellis writes ECP5 configuration frames in reverse order.  The
# frame count identifies the ECP5 geometry, and the frame width is part of the
# public Trellis device database.  Keeping the three supported ECP5 sizes here
# makes the report independent of pytrellis (which OSS CAD Suite does not ship
# as an importable Python module).
ECP5_FRAME_BYTES = {
    7562: 74,   # 12F / 25F: 592 bits
    9470: 106,  # 45F: 846 bits plus two pad bits
    13294: 142,  # 85F: 1136 bits
}
ECP5_PREAMBLE = b"\xff\xff\xbd\xb3"
LSC_WRITE_COMP_DIC = b"\x02\x00\x00\x00"
LSC_PROG_INCR_CMP = 0xB8


def parse_config(path):
    """Return {tile_name: frozenset(config lines)} plus the file's preamble.

    Line order within a tile is not semantically meaningful -- two builds can
    emit the same arcs in a different order -- so tiles are compared as sets.
    Comparing them as ordered lists would report cosmetic reordering as a
    hardware difference and inflate every number below.
    """
    tiles = {}
    preamble = []
    current = None
    body = []
    with open(path) as handle:
        for line in handle:
            line = line.rstrip("\n")
            match = TILE_RE.match(line)
            if match:
                if current is not None:
                    tiles[current] = frozenset(body)
                current = match.group(1)
                body = []
                continue
            # `.bram_init` is payload data rather than CRAM, and
            # `.tile_group` is a separate multi-tile section.  Neither belongs
            # to the preceding tile; stop collecting so it cannot create a
            # false change at that tile's coordinates.
            if line.startswith("."):
                if current is not None:
                    tiles[current] = frozenset(body)
                    current = None
                    body = []
                continue
            if current is None:
                if line.strip():
                    preamble.append(line)
            elif line.strip():
                body.append(line)
    if current is not None:
        tiles[current] = frozenset(body)
    return tiles, preamble


def position(tile_name):
    match = POS_RE.search(tile_name)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _read_bits(data, bit_offset, count):
    value = 0
    for _ in range(count):
        if bit_offset // 8 >= len(data):
            raise ValueError("compressed frame data ends unexpectedly")
        value = (value << 1) | ((data[bit_offset // 8] >>
                                (7 - bit_offset % 8)) & 1)
        bit_offset += 1
    return value, bit_offset


def extract_ecp5_frames(path):
    """Extract CRAM frame bytes from an ecppack-compressed ECP5 `.bit`.

    This mirrors Project Trellis's prefix-free frame decoder.  It deliberately
    reads only the full-chip compressed payload; BRAM initialisation and the
    device-specific partial-frame address encoding are outside the measurement.
    """
    data = Path(path).read_bytes()
    try:
        preamble = data.index(ECP5_PREAMBLE)
        dict_cmd = data.index(LSC_WRITE_COMP_DIC,
                              preamble + len(ECP5_PREAMBLE))
    except ValueError as exc:
        raise ValueError(f"{path}: not a compressed ECP5 ecppack bitstream") from exc

    dict_start = dict_cmd + len(LSC_WRITE_COMP_DIC)
    patterns = data[dict_start:dict_start + 8]
    if len(patterns) != 8:
        raise ValueError(f"{path}: truncated compression dictionary")
    dictionary = [1 << i for i in range(8)] + [0] * 8
    # Patterns are stored pattern7 first, matching Trellis's reader.
    for pattern, index in zip(patterns, range(15, 7, -1)):
        dictionary[index] = pattern

    payload = dict_start + 8
    if payload + 4 > len(data) or data[payload] != LSC_PROG_INCR_CMP:
        raise ValueError(f"{path}: compressed frame command does not follow dictionary")
    flags = data[payload + 1]
    frame_count = (data[payload + 2] << 8) | data[payload + 3]
    if frame_count not in ECP5_FRAME_BYTES:
        raise ValueError(f"{path}: unsupported ECP5 frame count {frame_count}")

    frame_bytes = ECP5_FRAME_BYTES[frame_count]
    # Compression pads each decoded frame to a whole 64-bit unit.
    decoded_bytes = frame_bytes + (7 - ((frame_bytes - 1) % 8))
    check_crc = bool(flags & 0x80)
    crc_after_each = check_crc and not bool(flags & 0x40)
    dummy_bytes = flags & 0x0F
    byte_offset = payload + 4
    frames = []

    for frame_number in range(frame_count):
        bit_offset = byte_offset * 8
        decoded = bytearray()
        for _ in range(decoded_bytes):
            first, bit_offset = _read_bits(data, bit_offset, 1)
            if not first:
                decoded.append(0)
                continue
            second, bit_offset = _read_bits(data, bit_offset, 1)
            if second:
                literal, bit_offset = _read_bits(data, bit_offset, 8)
                decoded.append(literal)
            else:
                index, bit_offset = _read_bits(data, bit_offset, 4)
                decoded.append(dictionary[index])

        byte_offset = (bit_offset + 7) // 8
        if crc_after_each or (check_crc and frame_number == frame_count - 1):
            byte_offset += 2
        byte_offset += dummy_bytes
        frames.append(bytes(decoded[:frame_bytes]))

    # The payload is serialized highest frame first; expose native CRAM order,
    # which is the order ecppack uses when deciding which frames differ.
    frames.reverse()
    return frames


def summarize(ref_path, cand_path, ref_bit=None, cand_bit=None):
    ref, _ = parse_config(ref_path)
    cand, _ = parse_config(cand_path)

    ref_names = set(ref)
    cand_names = set(cand)
    only_ref = ref_names - cand_names
    only_cand = cand_names - ref_names
    shared = ref_names & cand_names
    changed = {name for name in shared if ref[name] != cand[name]}

    # Every tile that a partial bitstream would have to rewrite.
    touched = changed | only_ref | only_cand

    columns = defaultdict(int)
    rows = defaultdict(int)
    unplaced = 0
    for name in touched:
        pos = position(name)
        if pos is None:
            unplaced += 1
            continue
        rows[pos[0]] += 1
        columns[pos[1]] += 1

    result = {
        "reference": ref_path,
        "candidate": cand_path,
        "tiles_reference": len(ref),
        "tiles_candidate": len(cand),
        "tiles_union": len(ref_names | cand_names),
        "tiles_shared": len(shared),
        "tiles_identical": len(shared) - len(changed),
        "tiles_changed": len(changed),
        "tiles_only_in_reference": len(only_ref),
        "tiles_only_in_candidate": len(only_cand),
        "tiles_touched": len(touched),
        "touched_columns": len(columns),
        "touched_rows": len(rows),
        "tiles_without_position": unplaced,
        "column_span": [min(columns), max(columns)] if columns else None,
        "row_span": [min(rows), max(rows)] if rows else None,
        "column_histogram": dict(sorted(columns.items())),
    }

    if ref_bit is not None or cand_bit is not None:
        if ref_bit is None or cand_bit is None:
            raise ValueError("both reference and candidate bitstreams are required")
        ref_frames = extract_ecp5_frames(ref_bit)
        cand_frames = extract_ecp5_frames(cand_bit)
        if len(ref_frames) != len(cand_frames):
            raise ValueError("bitstreams use different ECP5 frame geometries")
        changed_frames = [
            index
            for index, (ref_frame, cand_frame) in
            enumerate(zip(ref_frames, cand_frames))
            if ref_frame != cand_frame
        ]
        groups = sorted({frame // 106 for frame in changed_frames})
        group_total = math.ceil(len(ref_frames) / 106)
        result.update({
            "reference_bitstream": ref_bit,
            "candidate_bitstream": cand_bit,
            "frames_total": len(ref_frames),
            "frames_changed": len(changed_frames),
            "frame_span": ([min(changed_frames), max(changed_frames)]
                           if changed_frames else None),
            "frame_groups_touched": len(groups),
            "frame_groups_total": group_total,
            "frame_group_span": ([min(groups), max(groups)] if groups else None),
        })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reference")
    parser.add_argument("candidate")
    parser.add_argument("--reference-bit",
                        help="full reference .bit for exact CRAM-frame counting")
    parser.add_argument("--candidate-bit",
                        help="full candidate .bit for exact CRAM-frame counting")
    parser.add_argument("--json", help="also write the result as JSON")
    parser.add_argument("--top", type=int, default=12,
                        help="how many busiest columns to print (default 12)")
    args = parser.parse_args()

    try:
        result = summarize(args.reference, args.candidate,
                           args.reference_bit, args.candidate_bit)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    total = result["tiles_union"] or 1
    print(f"reference   {result['reference']}")
    print(f"candidate   {result['candidate']}")
    print()
    print(f"tiles in reference        {result['tiles_reference']:>7}")
    print(f"tiles in candidate        {result['tiles_candidate']:>7}")
    print(f"tiles present in both     {result['tiles_shared']:>7}")
    print(f"  identical               {result['tiles_identical']:>7}")
    print(f"  changed                 {result['tiles_changed']:>7}")
    print(f"tiles only in reference   {result['tiles_only_in_reference']:>7}")
    print(f"tiles only in candidate   {result['tiles_only_in_candidate']:>7}")
    print()
    pct = 100.0 * result["tiles_touched"] / total
    print(f"tiles a partial bitstream must rewrite: "
          f"{result['tiles_touched']} ({pct:.1f}% of configured-tile union)")
    print(f"spread over {result['touched_columns']} columns "
          f"and {result['touched_rows']} rows")
    if result["column_span"]:
        print(f"column span {result['column_span'][0]}..{result['column_span'][1]}, "
              f"row span {result['row_span'][0]}..{result['row_span'][1]}")

    if "frames_changed" in result:
        frame_pct = 100.0 * result["frames_changed"] / result["frames_total"]
        print()
        print(f"configuration frames changed: {result['frames_changed']} "
              f"of {result['frames_total']} ({frame_pct:.1f}%)")
        if result["frame_span"]:
            print(f"frame span {result['frame_span'][0]}..{result['frame_span'][1]}; "
                  f"{result['frame_groups_touched']}/"
                  f"{result['frame_groups_total']} 106-frame groups touched")

    histogram = result["column_histogram"]
    if histogram:
        print()
        print(f"busiest columns (of {len(histogram)} touched):")
        busiest = sorted(histogram.items(), key=lambda kv: -kv[1])[: args.top]
        width = max(count for _, count in busiest)
        for column, count in busiest:
            bar = "#" * max(1, round(40 * count / width))
            print(f"  C{column:<4} {count:>5}  {bar}")

    if args.json:
        with open(args.json, "w") as handle:
            json.dump(result, handle, indent=2)
        print(f"\nwrote {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
