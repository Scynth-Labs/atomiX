---
name: atomix-development
description: Implement, review, or verify atomiX architecture, RISC-V, aXos, FPGA, simulator, component/profile, kernel-evolve, Live FPGA, checklist, and research changes. Use for selecting the next project item, keeping designs reconfigurable, updating evidence, or running local verification without requiring physical hardware.
---

# atomiX development

## Orient

1. Read `AGENTS.md` and the relevant section of `docs/design-checklist.md` or
   `docs/research-checklist.md`.
2. Read `docs/workflow.md` for commands and the owning component/profile files
   before editing.
3. Check the worktree and preserve unrelated changes.

## Implement

- Express replaceable choices through `components/` manifests and `configs/`
  profiles. Keep protocol boundaries generic and capabilities discoverable.
- Keep the management plane independent from mutable accelerator logic.
- Treat L1 reviewed programs, L2 shadow evaluation, L3 bounded morph genomes,
  and L4 frame mutation as distinct authority levels. Do not skip their safety
  gates.
- Keep kernel evolution optional and tiered. Run the exact 32 KiB Primer fit
  checks whenever kernel composition or state budgets change.
- Prefer a hardware-free simulator test for every deterministic behavior,
  fault, watchdog, canary, and rollback case.

## Verify

Run focused targets for the changed area. Common gates are:

```bash
make runtime-primer
make -C sw/kernel check-uartboot
make registry-check
make live-sim-check
make verify-smoke
```

Use `python3 tools/verify.py list` to select broader suites. Run
`make nightly-integrated` for broad local reproduction when cost is justified;
otherwise leave it to scheduled CI.

## Record and hand off

- Update the relevant checklist and maintained design document in the same
  change. State whether evidence is simulation, P&R, or physical.
- Content-address candidates, evidence, and deployments; run
  `make registry-check` after changing any referenced file or digest.
- Record release SHA-256 identities instead of committing generated images.
- Commit/push only when asked, following the root direct-to-`main` policy.

