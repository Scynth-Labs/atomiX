#!/usr/bin/env python3
"""Measure the configuration delta between two GW5A builds of the same shell.

The Gowin arm of the R1 feasibility spike (docs/research-checklist.md).  The
ECP5 experiment in `pr_delta.py` could work on Trellis `.config` files, which
are tile-addressed, so a difference carried coordinates.  Apicula has no such
intermediate: `gowin_pack` emits a packed `.fs` and `gowin_unpack` reads one
back, with no frame-address, region, or partial-image option anywhere on either
command line.  So the strongest measurement available here is positional rather
than tile-named: an `.fs` is a sequence of fixed-width frames, and a frame's
index is its position in the configuration stream.

That is still enough to answer the question this stage asks.  If swapping only
the role component perturbs frames spread across the whole stream, then no
contiguous slice of the stream corresponds to the role, and a partial image
covering only the role cannot be cut out of these files -- regardless of what
the hardware might support.  Confinement is a necessary condition, and it is
one this measurement can refute.

Usage:
    tools/gowin_delta.py reference.fs candidate.fs [--json out.json]

Exit status is 0 whatever the measurement shows: this reports a research
result, it does not assert one.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def load_frames(path: Path) -> tuple[list[str], str]:
    text = path.read_bytes()
    digest = hashlib.sha256(text).hexdigest()
    frames = text.decode("ascii", "replace").split()
    return frames, digest


def classify(frames: list[str]) -> dict[int, int]:
    return dict(collections.Counter(len(frame) for frame in frames))


def measure(reference: Path, candidate: Path) -> dict[str, Any]:
    ref_frames, ref_digest = load_frames(reference)
    cand_frames, cand_digest = load_frames(candidate)

    widths_ref = classify(ref_frames)
    widths_cand = classify(cand_frames)
    comparable = len(ref_frames) == len(cand_frames) and widths_ref == widths_cand

    changed: list[int] = []
    changed_bits = 0
    by_width: dict[int, dict[str, int]] = {}
    limit = min(len(ref_frames), len(cand_frames))
    for index in range(limit):
        a, b = ref_frames[index], cand_frames[index]
        bucket = by_width.setdefault(len(a), {"total": 0, "changed": 0})
        bucket["total"] += 1
        if a != b:
            changed.append(index)
            bucket["changed"] += 1
            if len(a) == len(b):
                changed_bits += sum(1 for x, y in zip(a, b) if x != y)

    # Span and clustering: a role-confined delta would occupy a short, dense
    # run of frames.  A delta that spans nearly the whole stream cannot be cut
    # out as a region no matter how few frames it touches.
    span = (changed[-1] - changed[0] + 1) if changed else 0
    runs = 0
    previous = None
    for index in changed:
        if previous is None or index != previous + 1:
            runs += 1
        previous = index

    return {
        "reference": {"path": str(reference), "sha256": ref_digest,
                      "frames": len(ref_frames), "widths": widths_ref},
        "candidate": {"path": str(candidate), "sha256": cand_digest,
                      "frames": len(cand_frames), "widths": widths_cand},
        "comparable": comparable,
        "changed_frames": len(changed),
        "changed_bits": changed_bits,
        "first_changed_frame": changed[0] if changed else None,
        "last_changed_frame": changed[-1] if changed else None,
        "changed_span_frames": span,
        "span_fraction_of_stream": round(span / limit, 6) if limit else 0.0,
        "contiguous_runs": runs,
        "by_frame_width": by_width,
    }


def report(result: dict[str, Any]) -> None:
    print(f"reference {result['reference']['frames']} frames "
          f"({result['reference']['sha256'][:16]})")
    print(f"candidate {result['candidate']['frames']} frames "
          f"({result['candidate']['sha256'][:16]})")
    if not result["comparable"]:
        print("frame layouts differ; the two builds are not frame-comparable")
    print(f"changed frames: {result['changed_frames']} "
          f"({result['changed_bits']} bits)")
    for width, bucket in sorted(result["by_frame_width"].items()):
        share = bucket["changed"] / bucket["total"] if bucket["total"] else 0.0
        print(f"  width {width:>4}: {bucket['changed']:>6}/{bucket['total']:<6} "
              f"({share:6.1%})")
    if result["changed_frames"]:
        print(f"changed span: frames {result['first_changed_frame']}"
              f"..{result['last_changed_frame']} "
              f"= {result['changed_span_frames']} frames "
              f"({result['span_fraction_of_stream']:.1%} of the stream) "
              f"in {result['contiguous_runs']} contiguous run(s)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    for path in (args.reference, args.candidate):
        if not path.is_file():
            print(f"gowin-delta: missing {path}", file=sys.stderr)
            return 2
    result = measure(args.reference, args.candidate)
    report(result)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2) + "\n",
                             encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
