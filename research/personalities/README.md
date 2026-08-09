# Compute-personality research artifacts

These machine-readable descriptors and workloads implement the contract in
[`docs/personality-contract.md`](../../docs/personality-contract.md).

`personalities/` says what a resident fabric must support and supplies
replaceable encodings.  `workloads/` fixes the semantic oracles used to compare
the morph fabric with the existing CPU, GPU, and TPU roles.  A workload can be
bound by many personalities; it is intentionally not nested under one engine.

Validate everything with:

```bash
make personality-check
```

External experiments may keep descriptors outside this tree and run
`python3 tools/personality_contract.py check /path/to/descriptors`.  New
capabilities, encodings, models, and optional metadata use namespaced IDs and
do not require changes to the base validator.

