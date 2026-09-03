#!/usr/bin/env python3
"""Derive the atomiX brand assets from the one master lockup.

`docs/assets/atomix-logo-cloud.svg` is the master: a boron-10 nucleus inside a
hydrogen-like probability cloud that cycles 1s -> 2p_x -> 3d_xy, beside the
wordmark.  Every other cloud asset is cut from it here rather than drawn again,
so the square mark and the print fallback show literally the same samples as
the animation -- a brand family cannot drift out of sync if only one file has
sample data in it.

Three things are derived:

  atomix-mark-cloud.svg         square, animated -- avatars, app icons
  atomix-mark-cloud-static.svg  square, the 3d_xy state only -- favicons, print
  atomix-logo-cloud-static.svg  the lockup with no SMIL and no script

The static files are the 3d_xy state on purpose.  Its four lobes sit in the
diagonal quadrants with nodal planes along x and y, so the orbital that the
physics hands us is already the letter the name ends in.  The other two states
are round (1s) and two-lobed (2p_x) and read as neither.

Usage:
  python3 tools/brand_cloud.py            # write the derived assets
  python3 tools/brand_cloud.py --check    # fail if they are stale
"""
import pathlib
import re
import sys

ASSETS = pathlib.Path(__file__).resolve().parent.parent / "docs" / "assets"
MASTER = ASSETS / "atomix-logo-cloud.svg"

# One sample is `M<x> <y>h0`: a zero-length subpath, drawn as a round cap.  It
# is the cheapest dot SVG has -- a <circle> per sample would triple the file.
POINT = re.compile(r"M(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)h0")
SMIL = re.compile(r"[ \t]*<animate(?:Transform|Motion)?\b[^>]*/>\n?")
GLOW = re.compile(r"[ \t]*<path class=\"cloud-glow\" d=\"[^\"]*\"/>\n?")
EMPTY = re.compile(r"(<(?:circle|rect)\b[^>]*[^/])>\s*</(?:circle|rect)>")

# The lockup's nucleus is sized against a wordmark; a square mark has none.
NUCLEUS_IN_SQUARE = ".62"

# One frozen state has to do the work the cycle does in the animated files, so
# the static mark's samples are drawn harder than the master's.
CLOUD_CARRIES_THE_MARK = """
  <style>
    .cloud-sample { stroke: #b3dcff; stroke-width: 1.22; opacity: .8 }
    .cloud-glow   { stroke-width: 2.4; opacity: .22 }
  </style>"""


def span(text, start, end):
    """The master's chunk from `start` through the first `end` after it."""
    i = text.index(start)
    j = text.index(end, i) + len(end)
    return text[i:j]


def indent(chunk, spaces):
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else line for line in chunk.splitlines())


def still(chunk):
    """Drop SMIL, then close the elements that only had animation inside."""
    return EMPTY.sub(r"\1/>", SMIL.sub("", chunk))


def thin(chunk, step=1, glow=True):
    """Keep every `step`-th sample, at one decimal place.

    A cloud is a density, not a drawing: dropping every other sample thins it
    evenly and costs shape, not structure.  One decimal is 0.1 of a 128-unit
    viewBox -- a fifth of a pixel at the 640px this is ever rendered at, and
    a fifth of the file size of two."""
    def rewrite(match):
        kept = POINT.findall(match.group(2))[::step]
        return match.group(1) + " ".join(
            "M{:.1f} {:.1f}h0".format(float(x), float(y)) for x, y in kept
        ) + '"'

    out = re.sub(r"(<path class=\"cloud-(?:glow|sample)\" d=\")([^\"]*)\"", rewrite, chunk)
    return GLOW.sub("", out) if not glow else out


def shrink(chunk, factor):
    """Scale the nucleus about the glow's centre.

    In the lockup the atom is a third of the width and sits beside a wordmark,
    so a nucleus this size reads as the subject.  In a square mark the atom is
    the whole logo, and a nucleus at lockup scale fills exactly the nodal cross
    that makes 3d_xy an X.  Shrinking it puts the cross back and leaves the
    nucleus reading as what it is: the part that does not change."""
    open_g = '<g transform="translate(64 61) scale({}) translate(-64 -61)">'.format(factor)
    return open_g + "\n" + indent(chunk, 2) + "\n</g>"


def visible(chunk, opacity):
    return re.sub(r'(<g id="orbital-.") opacity="[01]"', r'\1 opacity="%s"' % opacity, chunk, count=1)


def parts():
    master = MASTER.read_text()
    return {
        "style": span(master, "  <style>", "  </style>"),
        "defs": span(master, "  <defs>", "  </defs>"),
        "s": span(master, '  <g id="orbital-s"', "\n  </g>"),
        "p": span(master, '  <g id="orbital-p"', "\n  </g>"),
        "d": span(master, '  <g id="orbital-d"', "\n  </g>"),
        "glow": span(master, '  <circle class="nucleus-glow"', "</circle>"),
        "nucleus": span(master, '  <g id="nucleus"', "\n  </g>"),
        "state_d": span(master, '    <text id="state-d"', "</text>"),
        "word": span(master, '  <text class="word"', "</text>"),
        "cursor": span(master, '  <rect class="cursor"', "</rect>"),
        "tags": span(master, '  <text class="tag" x="142" y="100"', 'letter-spacing="1.25">BUILD · VERIFY · REPLACE</text>'),
    }


def mark_animated(m):
    """Square, animated: the lockup's atom with the wordmark cut away.

    The clouds sit at cy=60 in the lockup to leave room for the state caption
    underneath.  With no caption to leave room for, a translate re-centres the
    atom on 64 without touching a single sample coordinate -- and the rotate
    animations stay written about (64, 60), which is where they are inside
    this group."""
    body = "\n\n".join(
        indent(thin(chunk, step=2), 2) for chunk in (m["s"], m["p"], m["d"])
    )
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128" role="img" aria-labelledby="title desc">
  <title id="title">atomiX mark</title>
  <desc id="desc">A boron-10 nucleus of five green protons and five blue neutrons inside a
  probability cloud that cycles through the 1s, 2p x, and 3d xy states of a hydrogen-like
  B4+ ion. Derived from the atomiX lockup; the samples are the same ones.</desc>

{style}

{defs}

  <rect class="panel" x=".5" y=".5" width="127" height="127" rx="24"/>

  <g transform="translate(0 4)">
{body}

{nucleus}
  </g>
</svg>
""".format(
        style=m["style"],
        defs=m["defs"],
        body=indent(body, 2),
        nucleus=indent(shrink(m["glow"] + "\n" + m["nucleus"], NUCLEUS_IN_SQUARE), 2),
    )


def mark_static(m):
    """Square, static: the 3d_xy state, which is the X."""
    cloud = visible(still(thin(m["d"])), "1")
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128" role="img" aria-labelledby="title desc">
  <title id="title">atomiX mark, static</title>
  <desc id="desc">A boron-10 nucleus of five green protons and five blue neutrons inside the
  3d xy probability cloud of a hydrogen-like B4+ ion. The cloud's four lobes fall in the
  diagonal quadrants, so the orbital reads as the X the name ends in. No animation: for
  favicons, print, reduced motion, and renderers that do not run SMIL.</desc>

{style}

{defs}

  <rect class="panel" x=".5" y=".5" width="127" height="127" rx="24"/>

  <g transform="translate(0 4)">
{cloud}

{nucleus}
  </g>
</svg>
""".format(
        style=m["style"] + CLOUD_CARRIES_THE_MARK,
        defs=m["defs"],
        cloud=indent(cloud, 2),
        nucleus=indent(shrink(still(m["glow"]) + "\n" + m["nucleus"], NUCLEUS_IN_SQUARE), 2),
    )


def logo_static(m):
    """The lockup with no SMIL and no script, frozen on the 3d_xy state."""
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 430 128" width="430" height="128" role="img" aria-labelledby="title desc">
  <title id="title">atomiX logo, static</title>
  <desc id="desc">The atomiX wordmark beside a boron-10 nucleus of five green protons and
  five blue neutrons inside the 3d xy probability cloud of a hydrogen-like B4+ ion. The
  static form of the atomiX lockup: no animation and no script, for print, PDF export,
  and renderers that do not run SMIL.</desc>

{style}

{defs}

  <rect class="panel" x=".5" y=".5" width="429" height="127" rx="12"/>

{cloud}

{glow}
{nucleus}

  <g class="state" text-anchor="middle">
{state}
  </g>

{word}
{cursor}
{tags}
</svg>
""".format(
        style=m["style"],
        defs=m["defs"],
        cloud=visible(still(thin(m["d"])), "1"),
        glow=still(m["glow"]),
        nucleus=m["nucleus"],
        state=still(m["state_d"]).replace('opacity="0"', 'opacity="1"'),
        word=m["word"],
        cursor=still(m["cursor"]),
        tags=m["tags"],
    )


DERIVED = {
    "atomix-mark-cloud.svg": mark_animated,
    "atomix-mark-cloud-static.svg": mark_static,
    "atomix-logo-cloud-static.svg": logo_static,
}


def main(argv):
    check = "--check" in argv
    m = parts()
    stale = []
    for name, build in DERIVED.items():
        want = build(m)
        path = ASSETS / name
        if check:
            have = path.read_text() if path.exists() else None
            if have != want:
                stale.append(name)
            continue
        path.write_text(want)
        print("{:<30} {:>7,} bytes".format(name, len(want.encode())))
    if check:
        if stale:
            print("stale, rerun `make brand`: " + ", ".join(sorted(stale)), file=sys.stderr)
            return 1
        print("brand: {} derived assets match {}".format(len(DERIVED), MASTER.name))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
