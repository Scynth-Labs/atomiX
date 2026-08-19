"""nextpnr place/verify hook for the ECP5 role-confinement experiment.

The role is kept in a whole-column rectangle.  Placement locking of the
management shell is applied separately from a routed reference JSON.  Use this
hook at both pre-place and pre-route: it supplies the region constraint that
nextpnr's command line cannot express, then verifies every resulting BEL.
"""

import os


def setting(name, default):
    return int(os.environ.get(name, default), 0)


x0 = setting("ATOMIX_PR_ROLE_X0", "1")
x1 = setting("ATOMIX_PR_ROLE_X1", "13")
y0 = setting("ATOMIX_PR_ROLE_Y0", "1")
y1 = setting("ATOMIX_PR_ROLE_Y1", "70")
prefix = os.environ.get("ATOMIX_PR_ROLE_PREFIX", "u_soc.u_role.")
role_cells = [(name, cell) for name, cell in ctx.cells
              if str(name).startswith(prefix)]

# The same hook can be passed to --pre-route.  At that point every role cell
# is bound, so turn the requested rectangle into a checked invariant instead
# of trusting the placer-region hint.  ECP5 macro clustering can otherwise
# pull a member outside the rectangle without making placement fail.
if role_cells and all(cell.bel is not None for _, cell in role_cells):
    escaped = []
    locations = []
    for name, cell in role_cells:
        location = ctx.getBelLocation(cell.bel)
        locations.append((location.x, location.y))
        if not (x0 <= location.x <= x1 and y0 <= location.y <= y1):
            escaped.append((str(name), "X%d/Y%d" %
                            (location.x, location.y)))
    if escaped:
        sample = ", ".join("%s=%s" % item for item in escaped[:3])
        raise RuntimeError(
            "atomiX PR: %d/%d role cells escaped X%d..X%d/Y%d..Y%d: %s" %
            (len(escaped), len(role_cells), x0, x1, y0, y1, sample))
    print("atomiX PR: verified %d role BELs inside X%d..X%d/Y%d..Y%d "
          "(actual grid X%d..X%d/Y%d..Y%d)" %
          (len(role_cells), x0, x1, y0, y1,
           min(x for x, _ in locations), max(x for x, _ in locations),
           min(y for _, y in locations), max(y for _, y in locations)))
else:

# A nextpnr JSON written after placement restores BEL bindings while loading.
# The default placer then tries to apply the same NEXTPNR_BEL constraints a
# second time.  When resuming a locked design, keep those bindings in place but
# remove their serialization attributes before the placer scans constraints.
# The placer then treats them as occupied BELs and places only the new cells.
    if os.environ.get("ATOMIX_PR_RESUME"):
        restored = 0
        for _, cell in ctx.cells:
            if cell.bel is not None:
                cell.unsetAttr("BEL")
                cell.unsetAttr("NEXTPNR_BEL")
                cell.unsetAttr("BEL_STRENGTH")
                restored += 1
        print("atomiX PR: preserved %d restored BEL bindings" % restored)

    ctx.createRectangularRegion("atomix_role", x0, y0, x1, y1)
    for name, cell in role_cells:
        ctx.constrainCellToRegion(name, "atomix_role")

    print("atomiX PR: constrained %d packed role cells to X%d..X%d/Y%d..Y%d" %
          (len(role_cells), x0, x1, y0, y1))
