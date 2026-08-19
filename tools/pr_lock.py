#!/usr/bin/env python3
"""Transfer a routed nextpnr shell into a packed role candidate.

The ECP5 research flow synthesizes with hierarchy preserved, routes a
``role.none`` reference, and packs a candidate separately.  This tool copies
only placements whose packed cell name and type are identical, and only routes
whose named net has the exact same cell/port endpoint set.  Anything else is
left for nextpnr to place or route and is reported explicitly; silently
assuming that two same-named objects are the same shell would invalidate the
experiment.
"""

import argparse
import hashlib
import json
from pathlib import Path


PLACEMENT_ATTRIBUTES = ("NEXTPNR_BEL", "BEL_STRENGTH")


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def top_module(design, path):
    modules = design.get("modules", {})
    if "top" in modules:
        return modules["top"]
    tops = [module for module in modules.values()
            if module.get("attributes", {}).get("top") in (1, "1")]
    if len(tops) == 1:
        return tops[0]
    raise ValueError(f"{path}: cannot identify one top module")


def endpoints(module):
    """Return physical bit id -> {(cell, port, index), ...}."""
    result = {}
    for cell_name, cell in module.get("cells", {}).items():
        for port_name, bits in cell.get("connections", {}).items():
            for index, bit in enumerate(bits):
                if isinstance(bit, int):
                    result.setdefault(bit, set()).add(
                        (cell_name, port_name, index))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", help="routed nextpnr JSON for role.none")
    parser.add_argument("candidate", help="packed nextpnr JSON for candidate role")
    parser.add_argument("--output", required=True,
                        help="candidate JSON with safe shell locks copied")
    parser.add_argument("--report", required=True,
                        help="machine-readable lock coverage report")
    parser.add_argument("--role-prefix", default="u_soc.u_role.")
    parser.add_argument("--placements-only", action="store_true",
                        help="copy BEL locks but leave every route to nextpnr")
    args = parser.parse_args()

    reference_path = Path(args.reference)
    candidate_path = Path(args.candidate)
    reference = json.loads(reference_path.read_text())
    candidate = json.loads(candidate_path.read_text())
    ref_top = top_module(reference, reference_path)
    cand_top = top_module(candidate, candidate_path)
    ref_cells = ref_top.get("cells", {})
    cand_cells = cand_top.get("cells", {})

    ref_names = set(ref_cells)
    cand_names = set(cand_cells)
    common_names = ref_names & cand_names
    role_cells = {name for name in cand_names
                  if name.startswith(args.role_prefix)}
    type_mismatches = {name for name in common_names
                       if ref_cells[name].get("type") !=
                       cand_cells[name].get("type")}
    placed = set()
    for name in common_names - type_mismatches:
        ref_attrs = ref_cells[name].get("attributes", {})
        if "NEXTPNR_BEL" not in ref_attrs:
            continue
        cand_attrs = cand_cells[name].setdefault("attributes", {})
        for attribute in PLACEMENT_ATTRIBUTES:
            if attribute in ref_attrs:
                cand_attrs[attribute] = ref_attrs[attribute]
        placed.add(name)

    ref_endpoints = endpoints(ref_top)
    cand_endpoints = endpoints(cand_top)
    ref_nets = ref_top.get("netnames", {})
    cand_nets = cand_top.get("netnames", {})
    routed = set()
    endpoint_mismatches = set()
    for name in (() if args.placements_only else set(ref_nets) & set(cand_nets)):
        ref_bits = ref_nets[name].get("bits", [])
        cand_bits = cand_nets[name].get("bits", [])
        if len(ref_bits) != 1 or len(cand_bits) != 1:
            continue
        ref_bit, cand_bit = ref_bits[0], cand_bits[0]
        if not isinstance(ref_bit, int) or not isinstance(cand_bit, int):
            continue
        if ref_endpoints.get(ref_bit, set()) != cand_endpoints.get(cand_bit, set()):
            endpoint_mismatches.add(name)
            continue
        route = ref_nets[name].get("attributes", {}).get("ROUTING", "")
        if not route.strip():
            continue
        cand_nets[name].setdefault("attributes", {})["ROUTING"] = route
        routed.add(name)

    ref_only = ref_names - cand_names
    cand_only_shell = cand_names - ref_names - role_cells
    unlocked_shell = (cand_names - role_cells) - placed
    report = {
        "schema": 1,
        "experiment": "org.atomix.research.ecp5-shell-lock",
        "reference": str(reference_path),
        "reference_sha256": sha256(reference_path),
        "candidate": str(candidate_path),
        "candidate_sha256": sha256(candidate_path),
        "role_prefix": args.role_prefix,
        "placements_only": args.placements_only,
        "cells": {
            "reference": len(ref_names),
            "candidate": len(cand_names),
            "role_candidate": len(role_cells),
            "common": len(common_names),
            "placements_locked": len(placed),
            "type_mismatches": len(type_mismatches),
            "reference_only_shell": len(ref_only),
            "candidate_only_shell": len(cand_only_shell),
            "candidate_shell_unlocked": len(unlocked_shell),
        },
        "nets": {
            "reference": len(ref_nets),
            "candidate": len(cand_nets),
            "routes_locked": len(routed),
            "same_name_endpoint_mismatches": len(endpoint_mismatches),
        },
        "strict_shell_identity": not (type_mismatches or ref_only or
                                      cand_only_shell or unlocked_shell),
        "samples": {
            "reference_only_shell": sorted(ref_only)[:20],
            "candidate_only_shell": sorted(cand_only_shell)[:20],
            "candidate_shell_unlocked": sorted(unlocked_shell)[:20],
            "type_mismatches": sorted(type_mismatches)[:20],
            "net_endpoint_mismatches": sorted(endpoint_mismatches)[:20],
        },
    }

    Path(args.output).write_text(json.dumps(candidate, indent=2) + "\n")
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
    print(f"locked {len(placed)}/{len(cand_names - role_cells)} shell placements")
    print(f"locked {len(routed)}/{len(cand_nets)} candidate routes")
    print(f"unlocked shell cells: {len(unlocked_shell)}")
    print(f"strict shell identity: {report['strict_shell_identity']}")


if __name__ == "__main__":
    main()
