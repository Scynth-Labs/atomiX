# Tang Primer 25K benchmarks

This page separates physical measurements, fresh build measurements, and
historical results. Raw samples and exact hashes are in
[`research/benchmarks/tangprimer25k-2026-08-10.json`](../../research/benchmarks/tangprimer25k-2026-08-10.json).
No persistent flash write was used.

## Physical runtime — 2026-08-10

Ten independent trials each configured the same hashed image into SRAM, loaded
the 4,829-byte aXos host-link kernel, waited for `AXRD`, and checked SAXPY and
polynomial output exactly.

| Boundary | Min | Mean | Max |
|---|---:|---:|---:|
| 7,124,126-byte SRAM configuration | 3,548.127 ms | 3,604.614 ms | 3,693.355 ms |
| Kernel frame start to aXos ready | 65.971 ms | 68.152 ms | 72.395 ms |
| PING/INFO + two loads/executions/readbacks | 37.694 ms | 39.949 ms | 45.752 ms |

At 921600 baud the 4,841-byte framed kernel requires 52.528 ms on the wire.
Observed upload-to-ready throughput was 69.196 KiB/s; UART wire time accounts
for 77.075% of the mean. FPGA counts were stable: SAXPY loaded in 197–198 cycles
and executed in 1,354; polynomial loaded in 198 and executed in 1,022.

Kernel replacement is about 52.9x faster than full SRAM configuration. The
complete checked two-program switch is about 90.2x faster. The 38-byte program
frame alone is approximately 0.46 ms, about 7,836x below SRAM configuration.

## Build throughput

Place-and-route is where the time goes: across the seven-profile sweep,
nextpnr accounted for 4,480 s of 4,739 s — **95%** — with Yosys at 259 s. So
speeding up builds means speeding up nextpnr or overlapping profiles, and
nothing else is worth touching.

| Change | Effect | Adopted |
|---|---|---|
| `openFPGALoader --freq` 6 → 30 MHz | 3.42 s → 3.38 s | no — no effect |
| `nextpnr --threads 6` | 285 s → 253 s (−11%), identical routed result | **yes**, board manifest |
| `--jobs N` concurrent profiles | 861 s → 509 s for a pair (1.69x) | **yes**, opt-in |

**Programming is not JTAG-clock bound.** Raising the JTAG frequency five-fold
moves a 7,124,126-byte SRAM configuration by 40 ms, because the cost is USB/IP
round trips between Windows and WSL rather than the link rate. There is no win
available there from this side.

**Threading is safe for the lock.** `--threads 6` produced a bit-identical
result — same LUT4, same 35.73 MHz — so it speeds the build without changing
what is measured. That mattered enough to check before making it the default:
a faster build that quietly reroutes the design would invalidate every locked
number.

**Concurrency is the real win, and it is bounded by memory, not cores.** Each
place-and-route peaks over 1 GiB, so `--jobs` is an explicit argument rather
than defaulting to the core count; on a 6-core, 10 GiB WSL, 3 is comfortable.
Profiles already build in separate directories, so they contend only for CPU
and RAM. Expect the full sweep to drop from about 79 minutes to roughly 30 at
`--jobs 3`.

    python3 tools/tangprimer_synth_benchmark.py --jobs 3 --output <report.json>

## Fresh synthesis and place-and-route — 2026-08-10

One sweep, one toolchain, one process per profile, each built in an isolated
temporary tree from a hashed config. Raw report with per-profile bitstream and
log digests:
[`research/benchmarks/tangprimer25k-synth-2026-08-10.json`](../../research/benchmarks/tangprimer25k-synth-2026-08-10.json).
Yosys 0.67+102, nextpnr-himbaechel, gowin_pack from the same OSS CAD Suite.

| Profile | LUT4 | DFF | BSRAM | DSP | Fmax @25 MHz | Build | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| `cpu` | 14,326 / 23,040 (62%) | 3,138 | 36/56 | 0 | 33.81 MHz | 391 s | PASS |
| `ax2` | — | — | — | — | — | 243 s | **FAIL: no legal placement** |
| `gpu` | 17,782 / 23,040 (77%) | 3,015 | 40/56 | 12 | 34.12 MHz | 1,008 s | PASS |
| `tpu` | — | — | — | — | — | 529 s | **FAIL: no legal placement** |
| `runtime-gpu` | 18,417 / 23,040 (80%) | 3,853 | 44/56 | 3 | 29.30 MHz | 1,520 s | PASS |
| `morph-1pe` | 19,158 / 23,040 (83%) | 2,706 | 24/56 | 3 | 32.01 MHz | 698 s | PASS |
| `gpu-lane1` | 12,281 / 23,040 (53%) | 2,242 | 28/56 | 3 | 33.61 MHz | 350 s | PASS |

### The TPU failure was a regression, and it is fixed

`tpu` and `ax2` both failed the sweep above with *"Unable to find legal
placement"*. For the TPU this was a genuine regression against a profile that
had been physically verified on 2026-07-29, so it was bisected rather than
worked around. Rebuilding the profile at successive commits:

| Commit | Date | Change | LUT4 | Result |
|---|---|---|---:|---|
| `f038b65` | 07-30 | last commit before the window | 17,345 (75%) | PASS, 32.65 MHz |
| `1373b58` | 07-31 | PLIC for role completion | 17,759 (77%) | PASS |
| `6ad8a4c` | 08-09 | `axroleiso` isolation fence | 18,193 (78%) | PASS |
| `77553a2` | 08-09 | **Live FPGA telemetry** | 20,254 (87%) | **FAIL** |

`f038b65` reproduces the July record exactly — 17,345 LUT4, 24 MULT12X12,
32.65 MHz — which confirms the TPU design itself never changed. `77553a2` added
the `axlivemon` counters *and* their register decode inside `axroleiso`,
costing about 2,100 LUT4 in **every** profile, and that pushed the TPU past the
point where nextpnr could legalise a placement.

Gating only the counter instance was not enough: it saved 1,511 LUT4 and left
18,743, still above the 18,193 that placed at `6ad8a4c`, because the decode,
the command validation and the 64-bit read muxes were still being synthesised.
`soc.reference` now exposes a `live_monitor` parameter whose `LIVE_MON`
localparam folds out all of it together.

**Result: 18,134 LUT4 (78%), 48/56 BSRAM, 24 MULT12X12, 31.74 MHz, PASS** — the
full 8x8 tile and every DSP retained, with no RTL change to the role.

An earlier note here blamed the TPU's DSP mapping. That was wrong and has been
removed: the multipliers were always reaching the DSP blocks.

### `ax2` remains an explicit known failure

25,569 LUT4, **110%**, in its original configuration. It overflows rather than
failing to place, so it is not the same problem. Making it fit needed
`icache_kb` 1 and branch prediction dropped entirely, which reaches 18,825 LUT4
at 26.39 MHz but changes what the profile measures. That trade was rejected;
`ax2` is recorded as failing rather than silently redefined.

### Reading these against the per-profile numbers elsewhere

The sweep builds each profile in an isolated tree, which is what makes the rows
comparable to each other. It is not always identical to an in-tree
`make fpga` of the same profile: `morph-1pe` measures 19,158 LUT4 at 32.01 MHz
here against 20,176 at 29.20 MHz for the in-tree build that was loaded onto the
board, because the two builds differ in payload contents and build directory
keying. Where a number is attached to a physical run, the in-tree build that
was actually configured into SRAM is the authority; this table is the
cross-profile comparison.

## Physical role benchmarks — 2026-08-10

Every row below was measured on the Dock in volatile SRAM, no flash write. All
role jobs are timed the same way — doorbell to observed completion, including
the status polling the management CPU actually performs — so the columns are
comparable across roles. "Reconfigure" is the whole personality change as seen
by the CPU, excluding any host transfer.

The on-core reference reads its operands from `volatile` RAM arrays. Without
that, `-O2` folds these loops toward a closed form and the baseline stops
measuring the work the accelerator does; the cross-check is that the scalar
reference measures 3,732 cycles in both the morph and gpu-compute payloads.

### role.morph, 1 PE, 256 data words

| Personality | Reconfigure | Fabric job | On-core | Items | Speedup |
|---|---:|---:|---:|---:|---:|
| scalar recurrence, 64 steps | 358 cyc (14.3 us) | 440 | 3,732 | 64 | 8.5x |
| SIMT SAXPY, 50 elements | 358 cyc (14.3 us) | 462 | 3,170 | 50 | not claimed |
| systolic GEMM, 12x8x8 | 361 cyc (14.4 us) | 4,950 | 59,168 | 96 | 12.0x |

No SIMT speedup is claimed: that reference loop compiles differently in the two
payloads that measure it (3,170 against 1,717 cycles), so the baselines are not
comparable. Only fabric-against-fabric rows below share a measurement path.

### role.gpu-compute, 1 lane, 256 data words

| Workload | Reconfigure | Job | Jobs needed | Notes |
|---|---:|---:|---:|---|
| SIMT SAXPY, 50 elements | 206 cyc | 1,479 | 1 | |
| scalar recurrence, 64 steps | 209 cyc | 4,955 | 64 | one dependent step per doorbell |
| systolic GEMM, 12x8x8 | — | — | — | not expressible in this window |

### Head-to-head on identical workloads

Same buffer layout, same measurement, one lane against one PE.

| | morph, 1 PE | gpu-compute, 1 lane | Ratio |
|---|---:|---:|---:|
| LUT4 | 20,176 (87%) | **13,426 (58%)** | 1.50x |
| Routed fmax | 29.20 MHz | **33.15 MHz** | 0.88x |
| Reconfigure | 358 cyc | **206 cyc** | 1.74x |
| SIMT SAXPY job | **462 cyc** | 1,479 cyc | 0.31x |
| Scalar recurrence | **440 cyc, 1 job** | 4,955 cyc, 64 jobs | 0.09x |
| Systolic GEMM | **4,950 cyc** | not expressible | — |

Neither design dominates. The fabric buys capability and throughput; the hard
role buys area, frequency and reconfiguration cost.

### role.morph capacity on the GW5A-25A

| PEs | LUT4 | DSP | Placement |
|---:|---:|---:|---|
| 1 | 20,176 / 23,040 (87%) | 3 | placed, routed, 29.20 MHz |
| 2 | 21,275 / 23,040 (92%) | 6 | packs, no legal placement |
| 4 | 23,719 demanded (102%) | 12 | overflows; placement not reached |

### Resident runtime switching, role.gpu-compute

Ten consecutive verified switches in one resident aXos session.

| Program | Load cycles | Execute cycles |
|---|---:|---:|
| SAXPY | 197–198 | 1,354 |
| polynomial | 198 | 1,022 |
| Horner polynomial (L2 candidate) | 180 | 986 |
| clamped candidate (fault-injected) | 252 | 1,106 |

Host round trip for two loads and two executes: 37.52 / 37.94 / 38.30 ms
min/mean/max. The Horner candidate is 36 execute cycles faster than the
reviewed baseline with bit-identical output. The clamped candidate is correct
on the primary workload and is caught only by the canary.

## Physical kernel-evolve tiers

Five independent boot trials per tier all reached the Primer monitor banner:

| Tier | Binary | Resident | Pre-stack headroom | Upload-to-ready min/mean/max |
|---|---:|---:|---:|---:|
| none | 4,080 B | 8,220 B | 20,452 B | 57.880 / 58.522 / 59.103 ms |
| small | 5,478 B | 12,412 B | 16,260 B | 68.678 / 71.135 / 72.650 ms |
| mid | 5,476 B | 12,652 B | 16,020 B | 69.263 / 71.940 / 74.053 ms |
| large | 5,474 B | 13,612 B | 15,060 B | 69.918 / 72.373 / 74.079 ms |

The tiers remain comfortably inside the exact 32 KiB RAM gate. Their similar
binary sizes are expected: most tier capacity is zero-initialized resident
state rather than bytes transported over UART.

### Physical evolution-policy self-test

A subsequent one-pass test exercised the actual fitness/evolution path, not
only kernel boot. `small`, `mid`, and `large` each accepted two eligible
candidates, rejected invalid oracle evidence, selected candidate 2 at fitness
10,240, and rejected a mixed objective. The `none` profile was a negative
control and correctly reported `evolve` as an unknown command.

| Tier | Tested binary | Result | SRAM configuration | Upload-to-ready |
|---|---:|---|---:|---:|
| none | 4,080 B | predetermined control pass | 3,581.528 ms | 59.093 ms |
| small | 6,266 B | evolution self-test pass | 3,579.331 ms | 86.848 ms |
| mid | 6,264 B | evolution self-test pass | 3,569.807 ms | 83.331 ms |
| large | 6,262 B | evolution self-test pass | 3,562.187 ms | 82.352 ms |

All four simulator images also passed the exact 32 KiB gate at resident sizes
8,220/12,412/12,652/13,612 bytes for none/small/mid/large. Interactive commands
are transmitted with 1 ms byte pacing because the physical FPGA UART has a
one-byte receive buffer.

## Fresh synthesis/P&R sample

The run used isolated build roots on two WSL2 logical CPUs. It was stopped on
request during runtime P&R; completed outcomes remain useful and incomplete
ones are not promoted to passes.

| Profile | Synthesis | P&R/pack | Build wall | LUT4 | DFF | BSRAM | DSP | Fmax |
|---|---|---|---:|---:|---:|---:|---:|---:|
| CPU | pass | pass | 443.307 s | 14,326 | 3,138 | 36 | 0 | 33.815 MHz |
| AX2 | pass | fail | 299.657 s | — | — | — | — | — |
| GPU 4-lane | pass | pass | 890.594 s | 17,782 | 3,015 | 40 | 12 | 34.119 MHz |
| TPU 24-MAC | pass | fail | 427.576 s | — | — | — | 24 synthesized | — |
| runtime GPU | pass | interrupted | — | — | — | — | 3 synthesized | — |

The AX2 and TPU rows are current fresh placement failures, not claims that the
older physical images failed. CPU/GPU fresh images were not loaded onto the
board during this benchmark. Historical board-tested CPU/GPU/TPU results remain
in the [achievement ledger](../achievements/tangprimer25k.md).

## Still requiring equipment or a short follow-up

- Measure voltage/current/energy with a defined fixture; the board exposes no
  trustworthy software-only power measurement.
- Perform short focused reruns for AX2 and TPU; the runner now checkpoints each
  completed profile and preserves its nextpnr failure tail.
- Finish only the runtime P&R leg if a fresh replacement for the already
  physically verified seed-3 image is needed.
