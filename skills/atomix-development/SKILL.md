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

### Software must never be part of a bitstream's identity

**A new example, game, benchmark, or kernel change must never require
re-synthesis and must never invalidate an existing board claim.** The Gowin
bring-up flow bakes the payload into synthesis (`chparam -set RAM_INIT_FILE` in
`rtl/fpga/Makefile`) because block RAM contents are set at configuration time,
so under it every program is a different bitstream with different placement,
different timing, and a different hash — "the board works" becomes a claim
about one *program*, not about the hardware.

This has already cost the project repeatedly. `role.tpu-lite` stopped placing
after software-side additions, and
`research/benchmarks/tangprimer25k-baseline.json` still carries two rows marked
stale in `known_stale_rows` for exactly this reason: `gpu-lane1` and
`morph-1pe` drifted because `gpu_lane1.c` and `morph.c` changed. Editing a C
file moved a locked *hardware* number. That is the coupling, and it is the
thing to refuse.

The decoupled path already exists and is the default for anything shipped:

- Synthesize a **loader** bitstream — `rom.axrom` plus `sw/bootrom` (`AXK1`
  frame: magic, length, CRC-32), blank RAM, `reset_pc=0x1000`. One bitstream
  per board profile, proved once.
- Ship programs as **runtime payloads** over that loader
  (`sw/host/axhost.py --upload-kernel <bin>`, `make load PROGRAM=<name>`). The
  loader is payload-agnostic: it copies bytes to `0x8000_0000` and jumps, so a
  bare-metal game and an aXos kernel are the same kind of thing to it.
- A payload's budget is **size** (`RAM_BYTES - 4096`), not LUTs. Check size;
  do not re-run P&R.

Baking a payload is legitimate for exactly two cases, and both must say so:
first bring-up of a board that has no loader image yet, and a part too small to
carry the ROM. Never for a shipped example, and never as the flow a reader is
told to run.

When a change does move hardware, re-lock deliberately: `make synth-baseline`
against a fresh sweep, and keep the board evidence, the profile, and the
payload identity separate in the record.

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
- Commit or push only after an explicit request in the current turn, following
  the root direct-to-`main` policy. Never reuse earlier authorization.
