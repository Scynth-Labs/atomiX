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
