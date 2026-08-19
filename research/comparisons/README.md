# Cross-implementation comparison research

`r2-morph-vs-hard.json` is the versioned comparison plan for the first scalar,
SIMT, and systolic morph-fabric experiments.  `r2-composite-hard.json` is the
companion plan for resident hard GPU+TPU candidates, and
`gpu-tpu-primer-pnr.json` records the first candidate's simulation-correctness
and Tang Primer place-and-route result; it is not physical-board evidence.
`evidence-template.json` is an explicitly non-evidentiary record showing every
field an observation must fill.

Validate them with:

```bash
make comparison-check
```

Copy the template only when an experiment is ready to run.  Change `claim` to
`org.atomix.observation`, use a new namespaced ID and revision, pin source and
environment identity, record oracle output, and replace unavailable metrics
only with measurements or clearly labelled projections/derivations.
