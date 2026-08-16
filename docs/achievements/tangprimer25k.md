# Tang Primer 25K Dock achievements

## Hardware status

- **Available:** yes; this is the only FPGA board currently owned.
- **FPGA:** Gowin GW5A-25A on the Sipeed Tang Primer 25K Dock.
- **Debugger:** Sipeed FT2232C USB Debugger, VID:PID `0403:6010`.
- **WSL interfaces:** channel A/JTAG at `/dev/ttyUSB0`, channel B/UART at
  `/dev/ttyUSB1` on the verified attachment.
- **Programming policy:** volatile SRAM only. No atomiX image has been written
  to configuration flash.

The safe procedure remains in
[tangprimer25k-bringup.md](../tangprimer25k-bringup.md). Generated binaries and
reports are intentionally not tracked; the release identities below are pinned
by profile and SHA-256 instead.

## Successfully completed

### Windows/WSL and board access — 2026-08-10

- Windows `usbipd` shared and attached BUSID `2-1`; the BUSID is specific to
  that USB port/session and must be rediscovered after moving the cable.
- WSL kernel `5.15.167.4-microsoft-standard-WSL2` loaded `ftdi_sio` and exposed
  both FT2232C channels.
- `openFPGALoader --detect` read JTAG ID `0x1281b` and identified Gowin family
  GW5A, model GW5A-25.
- The known-good CPU image loaded into SRAM and UART returned
  `hello from atomiX`.

### CPU, GPU, and TPU images — 2026-07-29

All three were loaded into volatile SRAM and verified through the Dock UART:

| Image | Routed resources | Routed fmax | Physical verdict |
|---|---|---:|---|
| CPU `hello` | 12,179 LUT4, 2,699 FF, 36 BSRAM | 32.23 MHz | hello output observed; S1 reset confirmed |
| GPU `gpu_perf` | 18,280 LUT4, 2,446 FF, 40 BSRAM, 12 DSP | 38.47 MHz | `gpu-perf: PASS`; N=256 checksums `0xf515cdf9` / `0xbe878696` |
| TPU `tpu` | 17,345 LUT4, 3,696 FF, 48 BSRAM, 24 DSP | 32.65 MHz | `role tpu-lite: PASS`; checksum `0x8acb4a08` |

**These three no longer all rebuild.** A fresh sweep on 2026-08-10 found that
`tpu` and `ax2` fail place-and-route at the utilisation limit. The TPU's DSP
mapping is intact — it still uses 24 MULT12X12 — but it now needs 20,254 LUT4
(87%) against the 17,345 recorded here, because the shell grew by roughly 2,100
LUT4 when `axroleiso` and the `axlivemon` counters were added to `soc_top`. The
`cpu` profile shows the same shift, 12,179 to 14,326. The physical runs below
happened and their transcripts stand, but they measured a smaller shell. See
[benchmarks](../benchmarks/tangprimer25k.md).

**Update, 2026-08-12: `tpu` places again; `ax2` still does not.** The 11-profile
sweep of that date routes `tpu` at 17,637 LUT4 / 3,720 DFF / 48 BSRAM / 24 DSP
and 31.74 MHz, so the row is locked `expect: pass` again. That is a
place-and-route result only — the image has not been programmed into the Dock,
so the physical verdict in the table above remains the 2026-07-29 run against a
smaller shell, and nothing here is a new hardware claim. `ax2` still overflows
the device at 110% LUT4 and stays locked as a known failure.

**The loader reaches every profile here except `ax2`.** Each now has a variant
that boots any payload over UART instead of baking one in, and on the plain CPU
profile that image is *smaller* than a baked one (13,387 against 13,844 LUT4).
The TPU's loader form routes at 18,403 LUT4 / 50 BSRAM / 32.75 MHz — +766 LUT4
and the ROM's two block RAMs over the baked build, and 1.01 MHz faster — but it
is placement-fragile, legalising on one seed in five, so
`tangprimer25k-runtime-tpu.json` pins `pnr_seed: 2`. `ax2` is out of reach in
either form — it needs 25,569 LUT4 against the device's 23,040 — because
`core.ax2` targets a larger part than this one; that is a statement about the
board, not about the loader.

None of this is a physical claim. These are place-and-route results; no loader
image for a role-carrying profile has been programmed into the Dock.

Release `.fs` SHA-256 values are CPU
`bb9ab409ec8f0c0da834672b0c4a6116fb6e18471dba22e285476b78b2065e55`,
GPU `a361173ba4a5a82fc98ce4b8445620e9463c891686e33ac508135e2d84a9500b`,
and TPU `8cbc19c6902f5daf3fb896bc689058c4ae1dff225cdc735ece08912804a70104`.

The GPU transcript covered SAXPY and polynomial kernels at 32, 64, 128, and
256 threads and checked every result against the on-core reference. At N=256,
the GPU measured 2,457 compute cycles for SAXPY and 3,029 for polynomial. The
folded TPU measured 189 compute cycles versus 42,995 CPU cycles for the same
GEMM.

The physical capacity experiments also established that the shipped GPU width
is four lanes: eight lanes overflowed at 109% LUT use, six lanes packed to 96%
but could not be legally placed, and four lanes placed, routed, met timing, and
passed its workloads.

### Role-window fence and Live FPGA telemetry (R1/R3) — 2026-08-10

The R1 exit gate requires that role isolation is asserted while a role is being
loaded. `axroleiso` implements that fence and had been proven in Verilator, but
never on hardware — and a fence is exactly the logic whose simulation model can
be right while the routed design is not, since its whole job is keeping a bus
alive when the thing behind it has stopped answering.

The board ran the swap protocol without the bitstream write, which is the only
part the Gowin toolchain cannot express, and printed:

```
roleiso: PASS (fence contained the role, shell stayed live, role rediscovered
and reconfigured)
roleiso: telemetry seq=1 cycles=947599 rejects=0 watchdogs=0 generation=0
```

What that covers, all on silicon:

- with `ISO_CTRL.ISOLATE` set, every read of the role window **completed** —
  the property under test, since a broken fence stalls the CPU forever and the
  board goes silent — and returned zero, so a fenced role is indistinguishable
  from `role.none` and discovery needs no new software path;
- the role stayed invisible while additionally held in `ROLE_RESET`, standing
  in for fabric mid-rewrite;
- the management CPU's cycle counter advanced across the whole swap window and
  the Live FPGA monitor stayed reachable while the role was fenced;
- releasing the fence restored `ROLE_ID` and cleared the configuration
  generation, after which the morph fabric was reconfigured and its SAXPY
  verified element-by-element;
- the shell-owned telemetry snapshot works on hardware: the sequence advanced
  0 to 1 and the counters read back.

| Item | Value |
|---|---|
| Profile | `tangprimer25k-morph.json`, payload `roleiso` |
| Routed resources | 20,176/23,040 LUT4, 29.20 MHz |
| Bitstream SHA-256 | `76179771acb1c4f8d1828faf21fe5b3df7cb6dab8258d4489e429f71aeac6ff1` |
| Payload SHA-256 | `248ca084884434fe06a030eed176a6730cb614b0271f181ce97c64b78563303a` |
| Flash written | no |

`rejects=0 watchdogs=0 generation=0` is not a pass — it confirms from the
telemetry side that `soc_top.sv` tied `role_reject_event`, `watchdog_event` and
the activation event to zero at the time of this run, so those counters could
not advance in a real SoC.

Rejection and the watchdog were wired on 2026-08-13 (see
[live-fpga.md](../live-fpga.md)), which does not retroactively change what this
board run observed: it read the tied-off zeros, and a board result for the
wired counters needs a new run. The activation event is still explicit and
host-driven by design.

### Morph fabric (R2) — 2026-08-10

The coarse-grained reconfigurable role ran on the Dock in volatile SRAM and
printed `role morph: PASS (scalar, SIMT, systolic on one fabric; descriptors
confined)`. One resident configuration computed a 64-step scalar recurrence, a
50-element SIMT SAXPY, and a 12x8x8 systolic GEMM, each checked
element-by-element against an on-core reference, with only the 13-word genome
changing between them. Four descriptor classes — output stream and input stream
leaving the window, unknown mode, and a short genome — were refused before BUSY
rose, did not advance the configuration generation, and did not disturb the
previous result; the fabric then ran the last good genome correctly.

| Item | Value |
|---|---|
| Profile | `tangprimer25k-morph.json`, 1 PE, 256 data words |
| Routed resources | 20,176/23,040 LUT4, 2,706 FF, 24 BSRAM, 3 DSP |
| Routed fmax | 29.20 MHz (PASS at 25 MHz) |
| Bitstream SHA-256 | `8faf3d137e195d9280c75a5fc12b4d3a60622a5ddaca31c206e5c0363c455d5b` |
| Payload SHA-256 | `0314c572e3e2758a71902223f57f995a9d29a71d7293a20194424babdc5b7b7c` |
| Flash written | no |

Measured on the board in the same run, with an on-core reference reading the
same operands from volatile RAM arrays:

| Personality | Reconfigure | Fabric job | On-core | Items | Speedup |
|---|---:|---:|---:|---:|---:|
| scalar recurrence | 358 cyc (14.3 us) | 440 cyc | 3,732 cyc | 64 | 8.5x |
| SIMT SAXPY | 358 cyc (14.3 us) | 462 cyc | 3,170 cyc | 50 | see note |
| systolic GEMM | 361 cyc (14.4 us) | 4,950 cyc | 59,168 cyc | 96 | 12.0x |

"Reconfigure" is the whole personality change — thirteen genome words plus
NITEMS, written locally by the management CPU. It excludes host transfer; the
same 52-byte genome over the 921600-baud UART would add about 0.61 ms, still
inside R2's sub-millisecond target. "Fabric job" is doorbell to observed
completion including the status polling the CPU performs.

Note on the SIMT row: the on-core reference for SAXPY compiles differently in
the two payloads that measure it, so no SIMT speedup-against-core is claimed.
The scalar reference cross-validates exactly — 3,732 cycles in both the morph
and the GPU payload — which is what makes the other two rows trustworthy.

### Morph fabric versus the existing programmable role

The fabric was built assuming scalar, SIMT and systolic work needs a
reconfigurable datapath. `role.gpu-compute` is already programmable, so that
assumption was tested directly: one lane against one PE, identical 50-element
SAXPY, identical buffer layout, identical measurement.

| | morph, 1 PE | gpu-compute, 1 lane |
|---|---:|---:|
| LUT4 | 20,176 (87%) | **13,426 (58%)** |
| Routed fmax | 29.20 MHz | **33.15 MHz** |
| Reconfigure | 358 cyc | **206 cyc** |
| SIMT SAXPY job | **462 cyc** | 1,479 cyc |
| Scalar recurrence | **440 cyc, 1 job** | 4,955 cyc, 64 jobs |
| Systolic GEMM | **4,950 cyc** | not expressible in this window |

Neither dominates, and that is the result. The fabric is 3.2x faster on SIMT,
11.3x faster on the recurrence — which the GPU can only express as one
dependent step per doorbell, because its ISA is straight-line with 64 program
words — and it is the only one of the two that can run the GEMM at all. The
hard GPU is a third smaller, clocks 13% higher, and reconfigures in fewer
cycles. Flexibility here buys capability and throughput, not efficiency.

Capacity is the headline result: two PEs pack to 92% but find no legal
placement, and four PEs demand 102% of the LUT4 budget. One morph PE therefore
costs more area and less frequency than the hard four-lane GPU (18,280 LUT4,
38.47 MHz) or the hard TPU (17,345 LUT4, 32.65 MHz) that it would replace,
while returning 8-12x over the scalar core. For contrast the hard TPU
measured 189 compute cycles against 42,995 on-core cycles for its own GEMM.
That is a different matrix shape and not a like-for-like comparison, but the
order-of-magnitude gap is the cost of generality on this device.

### Live-runtime results — 2026-08-10

- The exact 32 KiB resident-kernel/one-lane-GPU profile passed the two-program
  simulator gate: two GPU programs loaded, executed, and produced independently
  verified results without FPGA resynthesis.
- Gowin placement seed 3 produced a legal route at 29.30 MHz against the
  25 MHz constraint. The evidence record reports 18,417/23,040 LUT4s,
  44/56 BSRAMs, 3 DSPs, and bitstream SHA-256
  `62ee2d6d2f833f3bbe29d7af0cac4b64f8a3914db9490d5cdb9b979ce7e329c6`.
- Placement is now deterministic: the runtime profile declares seed 3, the
  Gowin build passes it explicitly to nextpnr, seed-specific filenames prevent
  stale-route reuse, and the evidence manifest records the selected seed. A
  clean deterministic rebuild produced the exact board-tested `.fs` hash above.
- The loader-only image programmed into SRAM. Its immutable UART ROM accepted
  the compact kernel, verified its CRC, and transferred control.
- A prior resident-kernel diagnostic image answered paced `PING` and `INFO`
  requests with `aXHL`, GPU role `GPUC`, version 1. This verifies CPU execution,
  UART framing, the basic aXos host-link service, and role discovery.
- The failure was isolated to two transport assumptions: the ROM's `AXOK` was
  incorrectly treated as proof that aXos was ready, and periodic 2,000-cycle
  timer traps could overrun the one-byte UART while a long frame arrived. aXos
  now emits `AXRD` only after initialization, `axhost` waits for it and paces
  physical frames on one open descriptor, and the dedicated host-link kernel
  runs without the interactive monitor's periodic timer.
- The corrected 4,829-byte kernel completed the physical fast-switch gate:
  `saxpy` loaded in 197 FPGA cycles and executed in 1,354; `polynomial` loaded
  in 198 and executed in 1,022. Every returned word matched the independent
  host result. The two-load/two-execute host round trip measured 42.66 ms at
  921600 baud and ended in `FAST SWITCH PASS` without FPGA reload, aXos reboot,
  or resynthesis.
- The resident session survived a complete Windows USB/IP detach and reattach.
  Without FPGA programming or kernel upload, the first request rediscovered
  `GPUC` and both workloads passed with all outputs intact.
- Ten consecutive two-program runs in that same aXos session all passed. FPGA
  counts were invariant: SAXPY load/execute 198/1,354 cycles and polynomial
  198/1,022 cycles. Host round-trip latency was 36.86/38.62/42.16 ms
  min/mean/max at 921600 baud.
- After a fresh volatile SRAM load, the immutable ROM rejected an oversized
  image with `AXER/1`, rejected a full bad-CRC image with `AXER/2`, then accepted
  the untouched kernel and emitted `AXOK`/`AXRD`. Three subsequent workload
  runs passed; this proves bounded upload failures are retryable without FPGA
  reprogramming.
- S1 reset returned a running aXos session to the immutable loader without
  reloading FPGA SRAM. A new kernel upload produced `AXOK`/`AXRD`, followed by
  three passing two-program runs.
- An upload intentionally stopped after 2,048 of 4,829 declared payload bytes
  produced no false response and left the ROM waiting. S1 recovered it; the
  subsequent complete upload and three further workload runs passed. This
  closes the interrupted-upload recovery gate without an FPGA reload.
- A full USB power cycle returned the Dock as FT2232 serial `2025030317`; JTAG
  rediscovered GW5A-25 IDCODE `0x1281b`. The verified runtime image was then
  reloaded into volatile SRAM, both bounded loader errors were retried, and a
  valid kernel plus ten consecutive two-program runs passed. The post-cycle
  host round trip was 41.60/46.01/57.19 ms min/mean/max; no flash was written.
- A reproducible ten-run benchmark then measured SRAM configuration at
  3,548.127/3,604.614/3,693.355 ms, kernel upload-to-`AXRD` at
  65.971/68.152/72.395 ms, and checked live switching at
  37.694/39.949/45.752 ms (min/mean/max). Five physical boots each of the
  none/small/mid/large kernel-evolve tiers also passed. Full samples are in the
  [Tang Primer benchmark](../benchmarks/tangprimer25k.md).
- The live evolution policy then passed on physical hardware for the small,
  mid, and large tiers: each evaluated two eligible candidates plus invalid
  oracle evidence, deterministically selected candidate 2 at fitness 10,240,
  rejected a mixed objective, and reported one rejected record. The `none`
  tier remained a true predetermined negative control with no `evolve`
  command. Matching simulator runs passed all tier budgets inside 32 KiB RAM.

## Pending and failed

- [ ] Physically test the AX2 CPU profile; it currently has synthesis and RTL
  evidence only.
- [ ] Decide whether persistent configuration is useful only after the runtime
  SRAM regression is repeatable. Until then, do not use `make flash`.
- [ ] Optional expansion work: SDRAM module, USB host, PMODs, and removable
  storage, each behind its own profile and evidence item.

The board holds the physically passing runtime FPGA image and the last-tested
kernel-evolve-large monitor in volatile SRAM. A power cycle restores whatever
configuration was already in flash.
