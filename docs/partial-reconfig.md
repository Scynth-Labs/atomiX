# True partial reconfiguration of the role region (research track)

Goal: replace the **role** region of a running ULX3S bitstream — swap a TPU
role for a GPU role — without reloading the full bitstream and without
rebooting the shell (aXcore + aXos + peripherals keep running).

This is a research track, not a verified capability.  Nothing below is a
platform claim until the listed evidence exists.  The runtime-selection story
that already works — role discovery through the fixed `0x4000_0000` window and
role behavior driven by runtime-loaded descriptors/programs — does not depend
on this track.

**Why ECP5 and not the board that works.**  The verified hardware is the Tang
Primer 25K (Gowin GW5A-25A), but the open Gowin flow has no partial path at
all: `gowin_pack` offers no `--delta`, no `--background`, and no partial option,
where `ecppack` ships both `--delta` and `--background`.  Reaching stage 4 on
GW5A would mean first fuzzing that device's frame addressing and adding delta
support to apicula — a separate project, not a stage of this one.  So the track
runs on ULX3S/ECP5, and stage 4 is gated on having that board rather than on
the Primer already in hand.  Stages 2 and 3 need no board.

## Why this is plausible on ECP5 with open tools

Findings from the Project Trellis documentation and tools (checked July 2026):

- The ECP5 bitstream format documentation states that `LSC_WRITE_ADDRESS`
  "can be used to make partial bitstreams.  Combined with background
  reconfiguration and the ability to reload frames glitchlessly; partial
  reconfiguration is possible on ECP5."
- Loading a partial bitstream requires the `BACKGROUND_RECONFIG` sysCONFIG
  option in the resident design, then a JTAG preamble (instruction `0x79`
  with no data, then `0x74` followed by `0x00`) before the partial data.
- `ecppack --delta <reference.config>` already exists: it compares the
  configuration RAM frame-by-frame against a reference design and emits a
  bitstream containing only the differing frames.
- Configuration frames are column-shaped (106-frame groups per column), so a
  role region confined to whole columns has its own frames.
- nextpnr has per-cell placement constraints (a `Bel` attribute per cell) and
  a Python API that can apply them programmatically; it does **not** have a
  first-class region/pblock floorplanning flow.  This is the research gap.

Sources: the [Trellis bitstream-format
documentation](https://prjtrellis.readthedocs.io/en/latest/architecture/bitstream_format.html),
[`ecppack.cpp`](https://github.com/f4pga/prjtrellis/blob/master/libtrellis/tools/ecppack.cpp),
and the [nextpnr constraints
documentation](https://github.com/YosysHQ/nextpnr/blob/master/docs/constraints.md).
The Trellis documentation also notes frame addressing is fully documented
only for the 45k device; the ULX3S-85F may need fuzzing work.

## How the role contract already prepares for this

- **Fixed window, discovery by ID**: after a swap, software re-reads
  `ROLE_ID` at `0x4000_0000`; the new role identifies itself.  No shell
  address map change is ever part of a role swap.
- **Quiesce protocol**: `STATUS.BUSY` defines "safe to swap" — the driver
  waits for idle and stops issuing doorbells before reconfiguring.
- **Shell isolation register** (implemented): `axroleiso` sits between the
  address decoders and the role, with its control register at `0x1002_0000` in
  *shell* space — inside the role window it would be unreachable at exactly the
  moment it is needed.  Setting `ISO_CTRL.ISOLATE` holds `valid` low into the
  role, answers the bus with ready/zero/no-error, and masks the role's
  completion line so fabric in an unknown state cannot storm the PLIC.
  `ISO_CTRL.ROLE_RESET` holds the region in reset so rewritten fabric starts
  defined.  Isolation is immediate and unconditional rather than waiting for an
  in-flight transaction to retire: the role this protects against is the one
  that has stopped answering, so a fence that waits deadlocks on the failure it
  exists to contain.  Quiescing stays the driver's job (poll `STATUS.BUSY`
  before isolating); the fence is the backstop for when that does not work.
  Reads returning zero is deliberate — zero is already `ROLE_ID`'s "no role
  present" encoding, so an isolated role is indistinguishable from `role.none`
  and re-running discovery after a swap needs no new software path.
  Evidence: `make -C sim/unit run-axroleiso`, whose central case holds a role's
  `ready` low forever and requires the bus to complete anyway once fenced.

## Staged plan and evidence gates

1. **Baseline.** Build evidence is in: `make fpga CONFIG=configs/ulx3s-85f.json`
   runs ECP5 place-and-route to a `.bit`, routing at **28.42 MHz against the
   25 MHz constraint** with 13,782/83,640 LUT4 (16%), 3,096 FF, 0 BRAM, 0 DSP.
   Until 2026-08-01 this had never been run: the board's `.lpf` wrapped its
   `SYSCONFIG` line with a backslash continuation, which nextpnr's LPF parser
   does not accept, so the flow failed at parse before reaching placement.
   Board evidence — program SRAM, boot aXos, keep a UART session up — still
   requires a physical ULX3S and remains the gate for stage 4.
2. **Delta measurement (no live reconfig yet)**: build `shell + role.none`
   and `shell + role.loopback` with identical seeds; run
   `ecppack --delta` between them; count differing frames and check how many
   fall outside any plausible role region.  Expectation: without placement
   locking the delta touches shell frames all over the die.
3. **Shell locking experiment**: use the nextpnr Python API to pin every
   shell cell to the BELs of a reference run and confine role cells to
   reserved columns; iterate until the delta touches only role-region
   frames.  Routing divergence is the expected hard part; measure it, don't
   assume it.
4. **Live-load experiment (SRAM only, board at hand)**: set
   `BACKGROUND_RECONFIG`, quiesce the role, send the JTAG preamble plus the
   delta bitstream, re-run discovery.  Success = aXos never stops running
   (UART session survives) and the new `ROLE_ID` appears.  Failure modes
   (device reinit, bus wedge) are recoverable by full SRAM reprogram.
5. **Only then** promote the capability into
   [design-checklist.md](design-checklist.md) with the recorded commands.

Until stage 4 passes on hardware, "swap without reflashing" on the physical
board means full-bitstream SRAM reload (~1 s, no flash wear); in simulation
it means selecting a different role component and rebuilding the model.
