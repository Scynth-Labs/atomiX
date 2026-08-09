# Hardware capability matrix

What each supported FPGA target can actually run, per configuration, backed by
real synthesis and simulation runs on this repository — not projections.  Every
row here was produced by an `make`/`yosys` invocation listed at the bottom.

## Evidence levels

Each capability is marked with the strongest evidence that currently backs it:

- **SIM** — the configuration runs correctly in the Verilator RTL simulation
  (functional/architectural correctness).  This is board-independent: it proves
  the RTL, so any board the design *fits* inherits it.
- **SYNTH** — the configuration synthesises and **fits** the specific device
  (Yosys maps it and the resource totals are within the part's budget).
- **BOARD** — proven on physical silicon (place-and-route + bitstream + serial
  transcript).

> **Tang Primer 25K is now BOARD-verified.** CPU, GPU, and TPU were
> place-and-routed, programmed into volatile SRAM, and checked through the Dock
> UART on 2026-07-29. Tang Nano and ULX3S rows remain SIM + SYNTH until their
> physical-board gates are taken.

## The targets

| Board | FPGA | LUT4 | Flip-flops | Block RAM | DSP (18×18) | Clock |
|---|---|---|---|---|---|---|
| **Tang Nano 20K** | Gowin GW2AR-18C | 20,736 | 15,552 | ~46 × 18 Kb (828 Kb) | ~48 | 27 MHz |
| **Tang Primer 25K Dock** | Gowin GW5A-25A | 23,040 | 23,040 | 56 × 18 Kb (1,008 Kb) | 28 | 25 MHz |
| **ULX3S-85F** | Lattice ECP5 LFE5U-85F | ~83,640 | ~83,640 | 208 × 18 Kb (3.7 Mb) | 156 | 25 MHz |

The boards differ in main-memory strategy, which the board component fixes:
both Tang targets are **BRAM-only** (program and data in on-chip block RAM, so
RAM size competes with accelerators for BSRAM), while the ULX3S uses **external
SDRAM** with caches (fabric holds only a small ROM, leaving block RAM free).

## Tang Nano 20K (Gowin GW2AR-18C) — the small part

Three profiles, each peaked for the part — the CPU, the biggest GPU that fits,
and the TPU:

| Capability | Profile | Config | LUT4 | Block RAM | DSP | Verdict |
|---|---|---|---|---|---|---|
| **CPU** | `tangnano20k` | 5-stage RV32IM/Sv32 | 11.3k | 32 DPB | 0 | ✅ **SYNTH** + **SIM** (hello) |
| **GPU** | `tangnano20k-gpu` | minimal host + 6-lane SIMT | 20.2k (97%) | 32 DPB | 24 | ⚠️ **SYNTH** (fits, tight) + **SIM** (gpu, perf) |
| **TPU** | `tangnano20k-tpu` | folded 24-MAC int8 GEMM | 14.3k | 32 DPB + 8 DPX9B | 24 | ✅ **SYNTH** + **SIM** |

**Possible on the Tang Nano 20K:** a CPU plus **one** accelerator.  The
GPU is peaked at 6 lanes by pairing the wide engine with the minimal host core
(the 5-stage core + 8-lane GPU overflows at ~29k LUT4; the minimal core frees
enough to reach 6 lanes, at 97% — tight). The folded TPU now also fits: 14,300
LUT primitives, 3,239 FFs, and 24 `MULT9X9` cells; its old 64-MAC/flip-flop
overflow was removed by the same folding and single-port C-buffer work used for
the Primer. All-three is still out because the shell has one role window.
Other GPU lane counts are a config away. Deep analysis:
[tangnano-capacity.md](tangnano-capacity.md).

## Tang Primer 25K Dock (Gowin GW5A-25A)

Keep `tangprimer25k` as the first-UART profile. Each accelerator is an
alternative bitstream because the SoC has one role window:

| Capability | Profile | Configuration | LUT4 | FF | BSRAM | DSP | Strongest evidence |
|---|---|---|---:|---:|---:|---:|---|
| **CPU** | `tangprimer25k` | 5-stage RV32IM/Sv32 | 12,179 (52.9%) | 2,699 | 36 | 0 | ✅ **BOARD**: hello UART; 32.23 MHz |
| **CPU-max** | `tangprimer25k-ax2` | 2-wide AX2, 2 KiB I$, 64-entry BTB | 20,893 (90.7%) | 4,618 | 38 | 0 | **SYNTH** + **SIM**: 25,729 workload cycles |
| **GPU** | `tangprimer25k-gpu` | minimal host + 4-lane SIMT | 18,280 (79.3%) | 2,446 | 40 | 12 MULTALU27X18 | ✅ **BOARD**: two kernels × four sizes PASS; 38.47 MHz |
| **TPU** | `tangprimer25k-tpu` | 8 columns × 3 folded K MACs | 17,345 (75.3%) | 3,696 | 48 | 24 MULT12X12 | ✅ **BOARD**: GEMM/reference PASS; 32.65 MHz |

The verified GPU uses four lanes. A 32-bit low-word multiply is decomposed into
three unsigned 16-bit partial products and maps each lane to three GW5A
`MULTALU27X18` cells. Hardware bring-up also established the actual capacity
boundary: the former 8-lane profile packed to 25,325 LUT4 (109%), while six
lanes packed to 22,136 LUT4 (96%) but could not be legally placed. Four lanes
place, route, meet timing, and pass the physical workload with useful margin.
The generic RTL path is unchanged for simulation and other FPGA families.

The TPU was folded from 64 simultaneous multipliers to 24 physical int8 MACs.
It evaluates K=8 in three phases (3+3+2), while the C buffer now uses one
physical port so it infers BSRAM instead of tens of thousands of flip-flops.
Its programming interface and numerical result are unchanged.

The larger AX2 experiments establish the CPU boundary: 2 KiB/64 fits, while
2 KiB/128 jumps to 34,701 LUT primitives and 8 KiB/128 reaches 46,871. The
64-entry and 32-entry profiles are identical on the current workload-only
metric; this working set does not justify the extra predictor entries on
performance alone. The AX2 profile still needs its own physical-board run; the
baseline CPU and accelerator profiles have completed that gate.

### Tang Nano versus Primer in readable time

`python3 tools/bench.py tang` reproduces the RTL comparison with correctly
sized 16/32 KiB payloads. The table below uses those RTL figures for Tang Nano
and AX2, and the stronger physical UART measurements for the Primer
accelerators:

| Workload | Tang Nano 20K | Tang Primer 25K | Primer wall-time speedup |
|---|---:|---:|---:|
| CPU workload windows | 42,978 cycles / 1,591.8 us | 25,729 cycles / 1,029.2 us | 1.55× |
| GPU SAXPY N=256, complete | 23,097 cycles / 855.4 us | 29,887 cycles / 1,195.5 us | 0.72× |
| GPU polynomial N=256, complete | 23,520 cycles / 871.1 us | 30,513 cycles / 1,220.5 us | 0.71× |
| TPU 12x8x8 GEMM, complete | 5,257 cycles / 194.7 us | 6,893 cycles / 275.7 us | 0.71× |

“Complete” includes upload, doorbell-to-done execution, checked readback, and
checksum generation. The Primer accelerator figures are the physical UART
measurements; the Nano and Primer AX2 figures remain RTL measurements. The
verified Primer GPU uses four lanes rather than the Nano profile's six, and
transfer plus checked readback dominate the full offload boundary.

### Live-runtime repeatability

The seed-3 resident runtime was sampled for ten consecutive verified runs in
one aXos/FPGA session after a complete USB/IP detach and reattach. Every output
word matched the host oracle, with no FPGA reload or kernel reboot:

| Operation | FPGA cycles, min/max | Time at 25 MHz |
|---|---:|---:|
| SAXPY program load | 198 / 198 | 7.92 us |
| SAXPY execute | 1,354 / 1,354 | 54.16 us |
| Polynomial program load | 198 / 198 | 7.92 us |
| Polynomial execute | 1,022 / 1,022 | 40.88 us |

The complete host round trip—PING, INFO, two loads, two executions, checked
responses, and six paced USB serial exchanges—measured 36.86/38.62/42.16 ms
min/mean/max at 921600 baud. The 38-byte program switch frame itself occupies
about 0.46 ms on the UART wire; therefore host USB/serial transaction latency,
not FPGA reconfiguration or execution, dominates this control path.

A separate fresh-loader run rejected oversized and bad-CRC uploads, accepted a
valid retry, and then passed three more workload iterations. This is physical
recovery evidence, not simulator projection.

S1 recovery was also exercised twice. A normal S1 reset accepted a fresh
kernel and passed three runs. A second experiment stopped a correctly declared
4,829-byte upload after 2,048 payload bytes; the ROM correctly remained silent
and waited for the missing data. S1 restored the immutable loader, after which
the complete upload and three more checked runs passed. No FPGA SRAM reload was
needed for either reset recovery.

A full power cycle was also recovered without writing flash: WSL rediscovered
the FT2232/JTAG device, the hashed runtime image was reloaded into SRAM, loader
error retries and valid boot passed, and ten further exact-output switch rounds
completed. That post-cycle sample measured 41.60/46.01/57.19 ms host round-trip
min/mean/max. Across all recovery sessions, both reviewed candidates now have
30 verified physical executions recorded in the content-addressed registry.

## ULX3S-85F (Lattice ECP5) — the large part

| Configuration | Profile | LUT4 | Flip-flops | Block RAM | DSP | Verdict |
|---|---|---|---|---|---|---|
| CPU (5-stage RV32IM/Sv32) | `ulx3s-85f` | 10.6k (13%) | 3.1k | — | 0 | ✅ **SYNTH** + **SIM** |
| minimal host + GPU (**16-lane** SIMT) | `ulx3s-85f-gpu` | 35.4k (42%) | 5.9k | 18 EBR | 48 | ✅ **SYNTH** + **SIM** (suite) |
| CPU + TPU (folded 24-MAC int8 GEMM) | `ulx3s-85f-tpu` | 12.8k (15%) | 3.6k | 10 EBR | 24 | ✅ **SYNTH** + **SIM** |

**Possible on the ULX3S-85F:** the CPU plus a **wider GPU** — the profile takes
the engine to 16 lanes (42% LUT4, 48 of 156 DSP), using headroom the small Tang
Nano does not have — and the **TPU** as well. Folding the TPU and mapping its
accumulator to RAM cut this profile from 71.5k FFs/64 DSPs to 3.6k FFs/24 DSPs.
The part has headroom that the single-role shell does not yet exploit: hosting
GPU **and** TPU together needs a composite role, which does not exist yet.

### Lane scaling has diminishing returns (measured)

The engine has **one global-buffer port**, so the memory throughput — not the
lane count — sets the ceiling.  Two things follow.

**Optimisation delivered — pipelined loads.**  The block-RAM read is registered,
and `LDX` used to spend 2 cycles per lane (present address, then capture).  It is
now pipelined: address `p` is presented while lane `p-1`'s data is captured, so
N lane-loads cost N+1 cycles instead of 2N — single-port optimal.  This is a
flat ~1.35× at every lane count (16-lane `poly` 1827 → 1350 GPU cycles, `saxpy`
1683 → 1206 at N=256), verified against the on-core oracle at 6/8/16 lanes.

**Still bottlenecked past ~8 lanes.**  Even with pipelined loads the single port
services one lane per cycle, so doubling lanes doubles the per-wave memory time
over half as many waves — memory time is flat, only the parallel ALU/multiply
portion scales:

| kernel (N=256, GPU cycles, pipelined) | 8-lane | 16-lane | speedup |
|---|---|---|---|
| `poly` (compute-heavy) | 1901 | 1350 | 1.41× |
| `saxpy` (memory-heavy) | 1613 | 1206 | 1.34× |

## Scaling the accelerator: banked memory (measured)

The single-port ceiling above is what `role.gpu1-*` removes.  Its global buffer
is split into NBANKS interleaved block RAMs behind a lane→bank crossbar, so a
coalesced access — lane L touching `base+L`, the common SIMT pattern — hits
NBANKS distinct banks and retires in one round instead of one per lane.  Bank
conflicts serialise, lowest-lane-first, which is what preserves the
ascending-lane store order the oracle depends on.

saxpy, 50 threads, GPU cycles (lower is better).  Both engines are single
tunable components, so these are parameter settings:

| configuration | saxpy cycles | vs 8-lane single-port |
|---|---|---|
| `gpu-compute` 4 lanes, 1 port | 503 | 0.71× |
| `gpu-compute` 8 lanes, 1 port | 359 | 1.00× |
| `gpu-compute` 16 lanes, 1 port | 305 | 1.18× |
| `gpu1` 4 lanes / 4 banks | 347 | 1.03× |
| `gpu1` 8 lanes / 8 banks | 191 | 1.88× |
| `gpu1` 16 lanes / 4 banks | 140 | 2.56× |
| `gpu1` 16 lanes / 16 banks | 113 | 3.18× |
| `gpu1` 32 lanes / 32 banks | 62 | 5.79× |

The 16-lane rows separate the two effects: 16 lanes with only 4 banks reaches
2.56×, and widening the banks to match the lanes takes it to 3.18×.  Lanes and
banks are independent knobs and the memory side is the one that was missing.

Per doubling of lanes, the single-port engine gains 1.18× (8→16); gpu1 gains
1.69–1.82×.  That is the difference between a lane count that is worth raising
and one that is not, and it is why the gpu1 tiers go to 32 lanes while the
gpu-compute family stopped at 16.

`role.gpu1-*` also adds the control ISA the old engine lacked — structured
per-lane divergence (IF/ELSE/ENDIF), uniform and any-lane branches, compare-set,
integer divide, cross-lane shuffle, and displaced addressing — so kernels can
branch and loop instead of being straight-line only.

Reproduce: `python3 tools/bench.py gpu`.

## CPU scaling: dual issue (measured)

`core.ax2-*` fetches a two-instruction bundle through a block-RAM instruction
cache — the aXbus fetch port is 32 bits wide, so no bus-fed core can sustain
more than one instruction per cycle regardless of back-end width — and issues
both when they are independent.

Retired instructions per cycle, `sw/baremetal/examples/cpu_perf.c`.  `core.ax2`
is one component, so these rows are parameter settings, not variants:

| configuration | alu | chain | branch | memcpy | mixed | measured cycles | vs `core.minimal` |
|---|---|---|---|---|---|---|---|
| `core.minimal` | 0.50 | 0.50 | 0.50 | 0.43 | 0.45 | 70,650 | 1.00× |
| `core.pipeline5` | 0.77 | 0.83 | 0.66 | 0.77 | 0.84 | 42,978 | 1.64× |
| ax2 1-wide, 1K I$, no BTB | 0.77 | 0.83 | 0.66 | 0.77 | 0.84 | 43,078 | 1.64× |
| ax2 1-wide, 2K I$, BTB 32 | 0.99 | 0.99 | 0.99 | 0.99 | 0.99 | 33,432 | 2.11× |
| ax2 2-wide, 2K I$, no BTB | 1.15 | 0.90 | 0.79 | 0.99 | 0.99 | 35,375 | 2.00× |
| **ax2 defaults** (2-wide, 2K I$, BTB 32) | 1.72 | 1.10 | 1.32 | 1.38 | 1.21 | 25,729 | **2.75×** |
| **GW5A-25 max** (2-wide, 2K I$, BTB 64) | 1.72 | 1.10 | 1.32 | 1.38 | 1.21 | 25,729 | **2.75×** |
| ax2 2-wide, 8K I$, BTB 128 | 1.72 | 1.10 | 1.32 | 1.38 | 1.21 | 25,729 | 2.75× |

What each knob is actually worth, which a fixed set of tiers could not have
shown:

- **Stripped to 1-wide with a minimal cache and no predictor, ax2 lands exactly
  on `core.pipeline5`** (0.77/0.83, 1.59×).  That is the honest baseline: the
  dual-issue machinery contributes nothing until it is turned on.
- **The instruction cache is the single biggest knob.**  1K→2K plus a predictor
  takes a 1-wide core from 0.77 to 0.99 IPC — pipeline5 loses about a third of
  its cycles to fetch, and caching recovers nearly all of it before any second
  issue slot exists.
- **The predictor outranks the second issue slot on branchy code.**  2-wide
  without a BTB scores 0.79 on `branch`; 1-wide *with* one scores 0.99.  Issue
  width alone can lose to prediction alone.
- **`chain` never exceeds 1.10** — it is a serial dependency chain, so slot 1
  cannot fill.  It is in the suite to bound the claim.
- **8K I$ and a 128-entry BTB measure identically to 2K/32.**  This benchmark's
  working set fits in 2 KiB; the larger settings are for larger programs and
  this measurement is not evidence for them.

Reproduce: `python3 tools/bench.py cpu`.

## Memory system: what a real program actually hits (measured)

The IPC table above is measured on a working set that fits in cache, which
flatters any core with a good front end.  `render_perf.c` exists because that
number does not predict whether a real program runs well.  It is shaped like a
1993 software renderer -- texture-mapped column and span fills over a ~52 KiB
working set, a perspective divide per column, and a sequential framebuffer pass
-- run against delayed external memory.

Cycles per pixel (per divide for `fixdiv`), lower is better:

| configuration | column | span | fixdiv | blit | total | vs baseline |
|---|---|---|---|---|---|---|
| 256 B write-through $, div/32 | 24.93 | 31.03 | 37.01 | 25.00 | 9,323,481 | 1.00× |
| 16 KiB write-through $, div/32 | 16.83 | 21.63 | 37.01 | 25.00 | 7,119,856 | 1.31× |
| 16 KiB write-through $, div/16 | 16.58 | 21.63 | 21.01 | 25.00 | 7,035,384 | 1.33× |
| 16 KiB **write-back** $, div/16 | 13.13 | 18.51 | 21.01 | **3.92** | 4,515,432 | 2.06× |
| 32 KiB write-back $, div/16, 8-word line | 10.03 | 12.42 | 21.01 | **3.24** | 3,208,416 | **2.91×** |
| `core.minimal`, same memory | 26.22 | 33.47 | 30.01 | 12.24 | 8,756,530 | 1.06× |

Three things this measurement established, none of which were visible from the
IPC benchmark:

- **Cache policy dominated cache size.**  Growing the write-through cache 64×
  (256 B → 16 KiB) bought 1.31×.  Changing the policy at the *same* size bought
  another 1.55× on top.  `blit` is the tell: it sat at exactly 25.00 cycles per
  pixel at every write-through size, and got *worse* (41.00) with longer lines.
  The reference cache invalidates a line on write and does not write-allocate,
  so a sequential byte read-modify-write invalidates the line it just filled and
  every byte pays a full miss plus a full memory write.  `cache.writeback` takes
  that to 3.24 — a 7.7× swing on the workload a framebuffer generates.
- **Divider latency is worth about 1.02× overall** but 1.76× on the divide
  itself.  `muldiv.radix4` (16-cycle divide) matters to code that divides in an
  inner loop and is close to free elsewhere; it is not a general-purpose win.
- **A wide core does not rescue a bad memory system.**  `core.minimal` on the
  best memory configuration lands within 6% of the *baseline* — and dual-issue
  ax2 on the baseline memory is barely better than minimal.  The core and the
  memory system have to be sized together.

Reproduce: `python3 tools/bench.py render`.

> `cache.writeback` must not be paired with `core.pipeline5` using Sv32: that
> core's hardware page-table walker writes PTE A/D bits through the *fetch*
> port, and a drain could write a stale line back over such an update.  Cores
> with no fetch-port writes (`core.ax2-*`, `core.minimal`) are safe.  The
> constraint is stated on the component.

## Optimisation roadmap — maximising the large part

Ranked levers to push the ULX3S further, with what each unblocks.  Design-stage
notes (options, correctness constraints, open questions) for the open items are
in [optimization-design.md](optimization-design.md).

1. **Pipelined `LDX`** — ✅ done (~1.35× flat, above).
2. **Multi-bank global memory** — ✅ done, shipped as `role.gpu1-*` (above).
3. **TPU folding + `cbuf` block RAM** — ✅ done. The shared role now uses 24
   physical MACs and one mutually exclusive host/engine C-buffer port; ULX3S
   drops from 71.5k to 3.6k FFs.
4. **Composite GPU + TPU role** — the FF/DSP budget now allows both engines
   behind one role window on the large part (needs a composite role; the shell
   has one window today).

Catalog: cores `core.ax2` (tunable, above), `core.pipeline5` (reference — the
only one with Sv32/S-U and cosim/formal evidence), `core.minimal` (smallest).
Roles `role.gpu1` (banked, tunable) and `role.gpu-compute` (single-port,
tunable).  Sizes are parameters on these components, not separate components:
see [workflow.md](workflow.md) §3.4a.

> The scaling tables above are simulation cycle counts. AX2 now also has GW5A
> synthesis evidence for the Primer profile described above; other family/tier
> and board combinations still need their own synthesis and P&R evidence.

## Functional coverage (board-independent, SIM)

These prove the RTL that every fitting configuration above inherits:

- Bare-metal: `make -C sw/baremetal check-hello check-timer check-preempt
  check-fencei check-spi check-sd`
- Accelerator roles: `check-role` (loopback), `check-tpu`, `check-gpu`,
  `check-gpu-perf`
- Lean-component suite: `check-suite-minimal` — `core.minimal` (the accelerator
  host in the GPU profiles) driving the CPU, GPU, and TPU in one run
- Superscalar-core suite: `make -C sim/unit run-suite-ax2` (every `core.ax2-*`
  tier against the official rv32ui + rv32um binaries on the RTL, at three
  wait-state settings) and `make -C sw/baremetal check-suite-ax2` (SoC
  integration: interrupts, fence.i, IPC, and the gpu1 role)
- Scalable-role suite: `make -C sim/unit run-suite-gpu1` (all four `role.gpu1-*`
  tiers against a C++ interpreter of the ISA, including maximal-bank-conflict
  and worst-case-serialisation kernels) and `make -C sw/baremetal check-gpu1`
- Lock-step cosimulation vs the golden ISS: `make -C sim/cosim test`
- aXos kernel: `make -C sw/kernel check-role-driver check-hostlink`

## Reproducing the fit numbers

```bash
# Tang Nano (Gowin flow); BUILD=<dir> keeps trees separate.
make -C rtl/fpga synth COMPONENT_CONFIG=$PWD/configs/tangnano20k-gpu.json BUILD=build-tn
yosys -p "read_json build-tn/tangnano20k_top.json; stat -top tangnano20k_top"

# ULX3S (ECP5 flow).
make -C rtl/fpga synth COMPONENT_CONFIG=$PWD/configs/ulx3s-85f-gpu.json BUILD=build-u
yosys -p "read_json build-u/ulx3s_85f_top.json; stat"
```
On a memory-constrained host the final mapping passes can thrash; adding
`synth_gowin -run :map_luts; stat` (or `synth_ecp5 -run :map_luts`) reports the
block-RAM / DSP / flip-flop picture without the slowest pass.

_Maintained after each milestone that adds a board target, a role variant, or a
core that changes what fits._
