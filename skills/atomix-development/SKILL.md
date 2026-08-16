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
  per board profile, proved once. On a Gowin profile `reset_pc: "0x00001000"`
  is the *only* thing that declares this: `rtl/fpga/Makefile` derives blank RAM
  and a correctly sized UART ROM from it, so a loader profile names no payload
  anywhere. Build one with `make fpga-loader LOADER_CONFIG=<profile>`, which
  refuses a profile that does not reset into the ROM. Every baked Primer
  profile has a loader counterpart differing in exactly that one setting; add
  one rather than inventing a second way to decouple.
- Ship programs as **runtime payloads** over that loader
  (`sw/host/axhost.py --upload-kernel <bin>`, `make load PROGRAM=<name>`). The
  loader is payload-agnostic: it copies bytes to `0x8000_0000` and jumps, so a
  bare-metal game and an aXos kernel are the same kind of thing to it.
- A payload's budget is **size** (`RAM_BYTES - 4096`), not LUTs. Check size;
  do not re-run P&R.

The loader must not be the expensive option, or a tight profile gets a reason
to refuse it. `axrom` therefore carries `SYNC_READ` exactly as `axram` does, so
the boot ROM infers BSRAM instead of becoming a LUT ROM. Anything that
reintroduces a combinational read on a synthesised memory undoes that — see the
same rule for main memory. Gates that stand behind a *board* claim run at the
board's timing (`SYNC_READ=1`), not the simulator default;
`check_payload_boot.py` defaults to it for that reason.

**Scope a component's cost to the profiles that use it.** `soc_top` enables the
registered ROM only when `RESET_PC == ROM_BASE`, because a profile resetting
into RAM never fetches a ROM word. Enabling it everywhere made two locked
profiles stop placing: with no `ROM_INIT_FILE` the async ROM optimises away
entirely, while the registered one leaves a handshake that re-rolls packing by
−252 LUT4 on one profile and +427 on another, and `role.tpu-lite` and
`role.morph` were sitting at 78–87% utilisation with no room for either sign.
Prefer deriving such a condition in RTL from a parameter the profile already
sets, rather than adding a Makefile flag that can drift from the config.

More generally: on a part near its limit, **placement is not a function of size**.
A change that removes logic can still break a design whose BSRAM and DSP
placement pins it. Never infer "smaller, so it still fits" — run P&R, and when a
locked row moves, A/B it against HEAD before attributing the cause.

And one level below that: **equivalent RTL is not equivalent.** When a profile
opts out of a feature, the opted-out build must be the *original text*, not code
that means the same thing. Handing `axlivemon` a locally declared
`wire live_reject_event = 1'b0` instead of the tied-off `role_reject_event` port
the shell had always passed — the same constant, preprocessed sources differing
in nothing else — synthesised 1,989 more LUT4 (20,649 against 18,660) with
identical `ALU` and `DFF` counts, and cost `role.morph` its placement at five
seeds. Restoring the original text as the `` `else `` arm returned the netlist to
HEAD cell for cell.

So when adding a feature that a tight profile must be able to decline:

- gate it with `` `ifdef ``, not with a parameter compared to zero — a declared
  parameter always emits its define, so a value test can remove logic but never
  a port or a port connection;
- declare the opt-out in `components/*/component.json` with
  `"omit_when_zero": true` so `configure.py` omits the define entirely;
- make the declined arm the pre-existing text verbatim, and prove it by
  comparing Yosys cell counts against HEAD, not by reasoning about equivalence;
- bisect by copying individual files into a HEAD worktree when the counts move —
  it is far cheaper than a P&R per hypothesis, and it is how the above was found.

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
