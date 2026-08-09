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
    tools/pr_delta.py reference.config candidate.config [--json out.json]

Exit status is 0 whatever the measurement shows: this reports a research
result, it does not assert one.
"""

import argparse
import json
import re
import sys
from collections import defaultdict

TILE_RE = re.compile(r"^\.tile\s+(\S+)")
# Tile names embed their die position, e.g. CIB_R10C3:PVT_COUNT2 -> row 10, col 3.
POS_RE = re.compile(r"R(\d+)C(\d+)")


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


def summarize(ref_path, cand_path):
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

    return {
        "reference": ref_path,
        "candidate": cand_path,
        "tiles_reference": len(ref),
        "tiles_candidate": len(cand),
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


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reference")
    parser.add_argument("candidate")
    parser.add_argument("--json", help="also write the result as JSON")
    parser.add_argument("--top", type=int, default=12,
                        help="how many busiest columns to print (default 12)")
    args = parser.parse_args()

    result = summarize(args.reference, args.candidate)

    total = result["tiles_shared"] or 1
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
          f"{result['tiles_touched']} ({pct:.1f}% of shared tiles)")
    print(f"spread over {result['touched_columns']} columns "
          f"and {result['touched_rows']} rows")
    if result["column_span"]:
        print(f"column span {result['column_span'][0]}..{result['column_span'][1]}, "
              f"row span {result['row_span'][0]}..{result['row_span'][1]}")

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
