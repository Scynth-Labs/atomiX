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
2. **Delta measurement (complete; no live reconfig yet).**
   `make -C rtl/fpga pr-delta` builds `shell + role.none` and
   `shell + role.loopback` with explicit nextpnr seed 1.  Both route against
   the 25 MHz constraint (28.59 MHz and 26.74 MHz respectively); the loopback
   buffer consumes four `DP16KD` blocks rather than overflowing the device as
   flip-flops.  The result confirms the expected bad baseline decisively:
   **8,603 of 13,294 CRAM frames (64.7%) change**, spanning all 126
   106-frame groups.  At the tile level 4,816 configured tiles change, appear,
   or disappear (95.9% of the two builds' union), across all 125 interior
   columns and 94 rows.  The machine-readable result is
   `rtl/fpga/build/pr-delta-seed1/report.json`.

   The experiment also found a stricter tool blocker than the earlier
   documentation implied.  Current Project Trellis `ecppack --delta` computes
   the changed-frame set, then refuses to encode it for this device with
   `FIXME: partial bitstreams only supported for ECP5-45k`; its address map is
   hard-coded for 45F.  The target records that diagnostic in
   `ecppack-delta.log`.  `tools/pr_delta.py` obtains the 85F count by decoding
   both full compressed bitstreams with the same prefix-free frame format and
   comparing CRAM frames directly; it does not claim that the unavailable 85F
   partial stream can be loaded.
3. **85F address mapping and shell locking experiment**: first extend or
   validate Trellis's partial-frame address encoder for the 85F.  Then use the
   nextpnr Python API to pin every shell cell to the BELs of a reference run
   and confine role cells to reserved columns; iterate until the delta touches
   only role-region frames.  Routing divergence is the expected hard part;
   measure it, don't assume it.
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

## Stage 3 progress: first shell-lock experiment (2026-08-16)

The first attempt established a usable placement-lock apparatus and rejected
global hierarchy preservation as the production answer.  The compact record is
`research/partial-reconfig/ecp5-45f-shell-lock-probe.json`; generated JSON,
`.config`, reports, and logs remain under the build directory or `/tmp` and are
not source artifacts.

The experiment uses `synth_ecp5 -noflatten` for both profiles, routes the
`role.none` reference, packs `role.loopback`, and runs `tools/pr_lock.py` to
copy a placement only when packed cell name and type match.  A route is safe to
copy only when the named net has the exact same cell/port endpoint set.
`tools/pr_floorplan.py` then requests X1--X13 for the role and can be run again
as a pre-route check.  That second check matters: ECP5 cluster placement can
move macro members outside a rectangular-region hint without failing place.

Results at seed 1 with Yosys 0.67, nextpnr 0.10, and Trellis 1.4:

- 24,567 of 24,743 candidate shell cells reuse their reference BEL (99.29%);
- 1,491 of 1,494 role cells stay in X1--X13; three clustered LUTs escape to
  X15, X17, and X18, which the hardened pre-route check now rejects;
- 176 candidate shell cells remain unmatched and 156 reference shell cells
  disappear, mostly because carry packing still depends on whole-design order;
- 28,350 reference routes have identical endpoint sets and are eligible for a
  lock, but loading them into router1 reaches its
  `it->second.pip != PipId()` assertion;
- placement-only resume gets as far as routing, then the unmatched carry
  cells produce a fixed-wire conflict;
- the empty-role reference grows from 15,071 to 20,491 `TRELLIS_COMB` cells
  under global `-noflatten` (35.96%), and falls from 28.62 MHz to 21.91 MHz
  against the 25 MHz constraint.

The falsifiable criterion was strict shell identity plus a routed candidate at
25 MHz.  It failed on both counts, so no delta from this attempt is loadable or
described as partial-reconfiguration evidence.  The decision is to keep the
lock and floorplan tools, but replace global `-noflatten` with a separately
synthesised physical role boundary (or repair packed-netlist resume in
nextpnr).  Only after that produces zero unmatched shell cells is route locking
or frame-confinement measurement meaningful.

The low-level reproduction sequence is intentionally explicit:

```bash
# Keep the experiment netlists separate from normal profile artifacts.
make -C rtl/fpga synth COMPONENT_CONFIG=$PWD/configs/ulx3s-45f.json \
  SYNTH_ECP5_ARGS=-noflatten JSON=/tmp/reference-noflat.json
make -C rtl/fpga synth COMPONENT_CONFIG=$PWD/configs/ulx3s-45f-loopback.json \
  SYNTH_ECP5_ARGS=-noflatten JSON=/tmp/candidate-noflat.json

# Route/write the reference and --pack-only/write the candidate with seed 1,
# using the device arguments resolved by those profiles.
nextpnr-ecp5 --45k --package CABGA381 --speed 6 --freq 25 --seed 1 \
  --json /tmp/reference-noflat.json \
  --lpf components/board/ulx3s_85f/ulx3s_85f.lpf \
  --textcfg /tmp/reference.config --write /tmp/reference-routed.json \
  --report /tmp/reference-report.json
nextpnr-ecp5 --45k --package CABGA381 --speed 6 --freq 25 --seed 1 \
  --json /tmp/candidate-noflat.json \
  --lpf components/board/ulx3s_85f/ulx3s_85f.lpf \
  --pack-only --write /tmp/candidate-packed.json

# Transfer only proved-safe locks.
python3 tools/pr_lock.py /tmp/reference-routed.json /tmp/candidate-packed.json \
  --placements-only --output /tmp/candidate-locked.json \
  --report /tmp/lock-report.json

# Resume placement with the reference shell held.  Reuse the hook before
# routing so it turns the region hint into a checked invariant.
ATOMIX_PR_RESUME=1 ATOMIX_PR_ROLE_X0=1 ATOMIX_PR_ROLE_X1=13 \
  ATOMIX_PR_ROLE_Y0=1 ATOMIX_PR_ROLE_Y1=70 \
  nextpnr-ecp5 --45k --package CABGA381 --speed 6 --freq 25 --seed 1 \
  --no-pack --json /tmp/candidate-locked.json \
  --pre-place tools/pr_floorplan.py --pre-route tools/pr_floorplan.py \
  --textcfg /tmp/candidate-locked.config
```


## Stage 2 progress: compressed 85F frame decoding (2026-08-20)

The compression sub-blocker is closed.  `tools/pr_delta.py` had already grown
a prefix-free decoder to compare two compressed full-chip images, but the
dedicated frame/address tool still refused `LSC_PROG_INCR_CMP`; two independent
parsers therefore disagreed about which inputs the project supported.  The
decoder now lives once in `tools/ecp5_bitstream.py`, and both tools consume it.
It handles zero, dictionary, and literal tokens, per-frame padding, CRC/dummy
trailers, truncation, and wrong-geometry rejection.  The ordinary command
walker remains separate for uncompressed full and explicitly addressed partial
streams; a partial is valid when every emitted frame has its own
`LSC_WRITE_ADDRESS`, not when it equals the full device frame count.

Fresh evidence from seed 1 is in
`research/partial-reconfig/ecp5-85f-frame-decode.json`.  The current 85F
profile routes at 27.40 MHz against 25 MHz, and its compressed image decodes to
exactly 13,294 frames of 142 CRAM bytes (1,887,748 bytes after removing the
64-bit serialization padding).  Cross-validation on the maintained 45F pair
still finds 7,377 changed frames, while its delta contains 7,377 frames and
7,377 explicit addresses.  `make ecp5-frame-check` runs six synthetic
format/corruption cases and validates the source-hashed evidence record.

This removes no address-map uncertainty.  A full 85F stream still issues
`LSC_INIT_ADDRESS` once and contains zero explicit addresses, so decoding its
contents cannot reveal the index-to-`LSC_WRITE_ADDRESS` function.  This is
synthesis/P&R plus software-decoding evidence, not a partial image or a
physical-board result.

## Stage 2 progress: the 85F frame-address map (2026-08-10)

`ecppack --delta` refuses the 85F with `FIXME: partial bitstreams only
supported for ECP5-45k`.  That refusal is not caused by missing device data:
Trellis's `devices.json` already describes the part completely (13,294 frames
of 1,136 bits, `max_row` 95, `max_col` 126).  The gap is the encoding that turns
a frame index into an `LSC_WRITE_ADDRESS` value.

`tools/ecp5_frames.py` is the apparatus for closing that gap.  It parses the
ECP5 command stream, and is validated against the 45F, where it recovers
exactly the 9,470 frames of 106 bytes the database predicts.

Two structural facts shape the work:

**A full bitstream cannot teach us the map.**  It issues `LSC_INIT_ADDRESS`
once and then streams every frame in order, so no address is ever named.  Only
a partial bitstream emits `LSC_WRITE_ADDRESS` per frame, and only the 45F can
produce one — so the 45F is the sole ground truth available.

**Single-tile probes give exact pairs.**  Deleting one arc from one tile of a
`.config` changes a handful of frames.  Packing that config both fully and as a
delta yields exactly as many emitted addresses as changed frames, so the lists
pair one-to-one with no inference.  Probes whose counts disagree are discarded
rather than guessed at.  26 exact pairs from 55 sites are recorded in
`research/partial-reconfig/ecp5-45f-frame-address-probe.json`.

What the pairs show, and why the map is not yet closed:

- addresses are emitted strictly descending, so the packer walks frames in
  descending index order;
- the address space is roughly twice the frame count — 19,106 observed against
  9,470 frames — so an address is not a frame index;
- the offset between them is piecewise constant over at least nine segments and
  is not monotonic: between frame indices 5,545 and 5,561 the address advances
  by 15 while the index advances by 16.

A second obstacle surfaced here: `ecppack` compresses 85F frame data even when
`--compress` is not requested because the profile enables `COMPRESS_CONFIG`.
Before the decoder was shared on 2026-08-20, a fixed-stride walk had reported a
confident and entirely false "13,294 frames, 27.1% changed", because the frame
count came from the block header rather than from traversing the frames.  The
new decoder closes that obstacle and rejects truncated or malformed streams;
the address map above remains the open problem.
