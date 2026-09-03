# atomiX agent instructions

## Mission and constraints

- Keep atomiX a replaceable, component/profile-driven RISC-V and FPGA platform.
  Do not hard-wire a vendor flow, board, accelerator, or evolution policy into a
  generic interface when a manifest/profile boundary can express it.
- Preserve the immutable management shell, UART loader, isolation, watchdog,
  oracle, provenance, and rollback boundaries for Live FPGA work.
- The only physically available board is the Tang Primer 25K Dock. Never turn
  simulation or synthesis results for another board into a physical claim.
- Tang Primer main RAM is 32 KiB. Keep `kernel-evolve-small`, `-mid`, and
  `-large` independently selectable and enforce their existing fit gates.
- A capacity, limit, or name that a build could reasonably want to change is a
  profile knob, not a literal. Put it where its owner is: a component's own
  `parameters` in its manifest (with a default and a `doc`) if a component owns
  it, a profile `setting` if the kernel does. Then make it *reach* the build,
  check its bounds where they are known, and test at a non-default value --
  `make -C sw/kernel check-abi-torture-small` is the pattern. A knob that is
  declared but not wired, or wired but never exercised, reads as configurable
  and is not; that is worse than an honest constant, because it fails silently.
  See `skills/atomix-development/SKILL.md`.
- Never make software part of a bitstream's identity. Adding an example, game,
  or kernel must not require re-synthesis or re-open a board claim: synthesize
  the loader bitstream once and ship programs as runtime payloads over it. The
  baked `RAM_INIT_FILE` path is for first bring-up only. See
  `skills/atomix-development/SKILL.md`.

## Start here

- Use `docs/workflow.md` as the command authority.
- Use `docs/design-checklist.md` and `docs/research-checklist.md` to select and
  update work. Adaptive reconfiguration details live in `docs/live-fpga.md`.
- Use `docs/tangprimer25k-bringup.md` for the lab procedure and
  `docs/achievements/tangprimer25k.md` for physical results.
- For normal implementation/research work, read and follow
  `skills/atomix-development/SKILL.md`. For physical Tang Primer work, also read
  `skills/tang-primer-lab/SKILL.md`.
- When more than one agent works this repository at the same time, read
  `gator-tools/skills/multi-agent-coordination/SKILL.md` and coordinate through
  it. It is a submodule: `git submodule update --init --recursive` if that
  directory is empty. Claims, reviews, and design questions live in
  `.git/multi-agent-coordination/` here, not in the submodule. Do not use it for
  ordinary single-agent work.

## Verification and evidence

- Run the narrowest relevant test first, then `make verify-smoke` before a
  completed change. `make nightly-integrated` is the broad scheduled suite.
- Keep simulator, synthesis/P&R, and physical-board evidence explicitly
  separate. Record failures as well as passes; never infer a hardware pass.
- Store reproducible inputs, commands, hashes, compact JSON evidence, and
  benchmark summaries. Do not commit generated build trees, bitstreams, logs,
  or an `artifacts/` directory.
- Keep content-addressed Live FPGA records valid with `make registry-check`.

## Hardware and Git safety

- Program FPGA SRAM only. Never run `make flash` or `openFPGALoader -f` without
  explicit user approval in the current turn.
- Preserve user changes. Avoid destructive Git operations.
- Work directly on `main`. Never commit or push unless the user explicitly asks
  in the current turn; earlier authorization is one-time only. Do not open PRs
  or create feature branches unless the user changes this policy.
