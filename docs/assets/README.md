# Brand assets

Four SVGs, no external dependency and no embedded raster.  One of them is
written by hand; the other three are cut from it by `make brand`.

| File | Use |
|---|---|
| [atomix-logo-cloud.svg](atomix-logo-cloud.svg) | **Master.** Horizontal lockup, animated: mark plus wordmark. README headers, slides, talks. |
| [atomix-logo-cloud-static.svg](atomix-logo-cloud-static.svg) | The same lockup with no animation and no script. Print, PDF export, and pipelines that flatten SVG. |
| [atomix-mark-cloud.svg](atomix-mark-cloud.svg) | Square mark, animated. Avatars, app icons, anywhere square. |
| [atomix-mark-cloud-static.svg](atomix-mark-cloud-static.svg) | The square mark frozen on the state that is the X. Favicons, print, reduced motion, and any renderer without SMIL. |

## What it means

The mark is not decorative.  It is an atom drawn from its own physics, and the
two halves of it are the two halves of the project.

- **The nucleus is the reference machine.**  Five protons and five neutrons —
  boron-10, the atom the name starts from.  It is what does not change: the
  shell, aXcore, aXos, the bus, the UART loader, the isolation fence, identical
  in every bitstream.
- **The cloud is what you replace.**  It cycles 1s → 2pₓ → 3d_xy, three states
  of the same atom, because a role is a thing you swap rather than a thing you
  have.  The same nucleus, a different shape around it.
- **The 3d_xy state is the X.**  Its four lobes fall in the diagonal quadrants
  with nodal planes along x and y, so the orbital arrives already shaped like
  the last letter of the name.  Nothing was drawn to make that happen, which is
  why it is the static mark: the one frame that has to stand alone is the one
  that reads as the word.

## The clouds are sampled, not drawn

Each cloud is a few thousand equal-weight draws from a real hydrogen-like
probability density, one dot per sample, so the shape you see is |ψ|² and the
density gradient is the actual one.

All three states are node-free (n = l + 1), which makes the radial density
r²|R_nl|² exactly a Gamma distribution — 1s is Gamma(3, 1/2Z), 2pₓ is
Gamma(5, 1/Z), 3d_xy is Gamma(7, 3/2Z), at Z = 5 for B⁴⁺.  The angular part is
rejection-sampled against |Y_lm|²: `n.x²` for 2pₓ, `4·n.x²·n.y²` for 3d_xy.
Samples are z-sorted before they are written so nearer dots land on top.

Each cloud is normalised to a fixed display radius on the way to pixels.  That
is worth knowing before editing: **Z changes the physics on paper and not one
pixel of the output**, so a wrong Z is invisible rather than obvious.

## Regenerating

```bash
make brand           # rewrite the three derived files from the master
make brand-check     # fail if they no longer match it (runs in CI's reach)
```

Only `atomix-logo-cloud.svg` carries sample data.  Every other file's dots are
copied out of it by [`tools/brand_cloud.py`](../../tools/brand_cloud.py), so the
square mark and the print fallback show literally the same samples as the
animation and the family cannot drift apart.  Edit the master, then run
`make brand`; never hand-edit a derived file.

## Notes for whoever maintains these

**The master carries a sampler in a `<script>`, and almost nothing runs it.**
Opened directly in a browser, the logo re-rolls a fresh set of samples every
12 seconds.  Embedded with `<img>` — which is every use in this repo, GitHub
included — the script never executes and the baked dots are what you see.  So
the baked dots are the asset, and the script is a bonus; if you change the
sampler, re-bake by opening the file, copying the generated paths back in, and
running `make brand`.  The derived files carry no script by design.

**The animated files need SMIL** for the state cycle and the caret.  A renderer
that ignores SMIL shows the first state (1s) and a solid caret, which is why the
`-static` files exist rather than being a nicety.  Use them anywhere you are not
certain: print, PDF export, some documentation pipelines, and favicons.

**The panel is deliberately dark in both themes.**  These files draw their own
`#0d1117` card rather than carrying a `prefers-color-scheme` block, so one asset
works on a light and a dark page without a second copy or a `#gh-dark-mode-only`
trick.  The trade is that the logo always reads as a card; that is intended.

**The wordmark uses a monospace system stack** and pins its width with
`textLength` + `lengthAdjust="spacingAndGlyphs"`.  Without that, a machine
falling back to a wider face runs the text past the viewBox and clips the X.  If
you ever need pixel-identical output across machines — print, or a trademark
filing — convert the text to outlines and keep that as a fifth file rather than
changing this one.

**The static mark draws its samples harder than the master does.**  One frozen
state has to carry a whole mark, so `tools/brand_cloud.py` appends a small style
override for stroke width and opacity, and shrinks the nucleus: at lockup scale
the nucleus fills exactly the nodal cross that makes 3d_xy an X.

## Rendering a preview

```bash
pip install cairosvg
python3 -c "import cairosvg; cairosvg.svg2png(
  url='docs/assets/atomix-logo-cloud.svg', write_to='/tmp/logo.png',
  output_width=860)"
```

`cairosvg` runs neither SMIL nor script, so it shows exactly what a static
renderer shows — useful for checking the fallback frame, not the animation.

## Using the name

The files here are covered by the repository's [MIT
Licence](../../LICENSE); the **name and the mark** are not.  See
[TRADEMARKS.md](../../TRADEMARKS.md) before putting either on something.
