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

## Pending and failed

- [ ] Physically test the AX2 CPU profile; it currently has synthesis and RTL
  evidence only.
- [ ] Decide whether persistent configuration is useful only after the runtime
  SRAM regression is repeatable. Until then, do not use `make flash`.
- [ ] Optional expansion work: SDRAM module, USB host, PMODs, and removable
  storage, each behind its own profile and evidence item.

The board currently holds the physically passing runtime image in volatile
SRAM. A power cycle restores whatever configuration was already in flash.
