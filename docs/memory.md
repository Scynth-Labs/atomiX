# Reference memory and storage architecture

Status: the reference RTL path is complete and regressed. It provides the delayed
32 MiB model plus split I/D caches, a ULX3S-targeted MT48LC16M16 SDRAM
controller boundary, SPI SDHC CMD17/CMD24, writable AXFS v1, and ROM SD boot.
The physical-board proof remains the final hardware gate; no simulation result
is presented as electrical SDRAM validation.

## Boundary and configuration

`soc_top` keeps aXcore's two physical aXbus ports unchanged. With caches
enabled, each port passes through an `axcache` before the existing `axbus_mux`:

```text
aXcore ibus ── I$ ── aXbus mux ── RAM / ROM / MMIO
aXcore dbus ── D$ ── aXbus mux ── RAM / ROM / MMIO
                                 └─ axdram_model (simulation configuration)
```

The cache boundary only claims the RAM range `0x8000_0000 .. RAM_BYTES-1`.
ROM, CLINT, UART, test finisher, and decode errors bypass it exactly once; no
device register is cached. The default SoC remains the 128 KiB dual-port BRAM
configuration. Select the delayed-memory/cache simulation configuration
explicitly:

```bash
make -C sw/kernel run-rtl \
  UART_INPUT_FILE="$PWD/sw/kernel/shell_input.txt" \
  RAM_BYTES=33554432 EXTERNAL_MEMORY=1 CACHES=1
```

`MAX_CYCLES` defaults to 100000 in the SoC runner. The cache/Sv32 fork demo is
intentionally more demanding, so the regression sets it to 500000; it may be
overridden for interactive experiments.

## Delayed backing store

`components/memory/reference/axdram_model.sv` implements the future controller-facing shape today:
one independently stalled aXbus port for instruction traffic and one for data
traffic. Each request is latched, responds after a fixed three-cycle delay,
honors byte writes, and reports malformed/out-of-range requests at completion.
The backing array is `$readmemh` initialized, so the normal RAM image loader
continues to work.

This is a timing and protocol model, not a claim that an FPGA has 32 MiB of
BRAM. The board path replaces it with `components/memory/reference/axsdram.sv`, preserving the
same two aXbus RAM ports while arbitrating them onto one x16 SDR channel.

## ULX3S SDRAM controller

`axsdram` targets the ULX3S's 32 MiB MT48LC16M16-compatible SDR SDRAM at
25 MHz. It performs the power-up delay, precharge-all, two refreshes, CAS-2
mode set, periodic refresh, activate/read-or-write/precharge sequence, and
DQM byte masking. Each 32-bit aXbus access becomes two 16-bit transfers. It
intentionally uses no bursts or open-row policy; the small I/D caches absorb
most traffic and keep the first hardware design auditable.

The controller emits separate DQ input/output/enable signals. The board top
owns the ECP5 `BB` bidirectional pads, avoiding an internal tri-state loop.
`run-axsdram` checks CAS-2 timing; `check-sdboot` boots the full shell through
the same physical-controller path.

## Cache contract

`components/cache/direct-mapped/axcache.sv` is a small direct-mapped cache: 16 lines of four 32-bit
words (16-byte lines). It is intentionally small so the control path is easy
to audit before choosing board RAM resources.

- Reads allocate and refill one complete line.
- Writes are write-through, never allocate, and invalidate their local line.
- A fetch-side Sv32 A-bit write invalidates the D-cache on the following
  cycle, preventing a stale PTE data view.
- `FENCE.I` retires serially in aXcore. `soc_top` recognizes that committed
  instruction from the existing trace, registers a one-cycle I-cache flush,
  and the already-serialized refetch cannot complete from a stale line.
- Plain `FENCE` has no extra hardware action: writes are ordered and
  write-through on this single-hart implementation.

The caches see physical addresses after Sv32 translation. There is no DMA or
second hart yet; when either arrives, this deliberately simple invalidation
scheme must be replaced or extended by a defined coherency policy.

## Regression commands

No additional host dependency is needed beyond the documented RISC-V GCC and
Verilator setup.

```bash
make -C sim/unit run-axdram-model  # delayed-memory timing/data/error contract
make -C sim/unit run-axcache       # fills, hits, write-through, flush, bypass
make -C sim/unit run-axsdram       # init, refresh, x16 DQ, masks, bank mapping
make -C sw/baremetal check-fencei QEMU="$HOME/.local/bin/qemu-system-riscv32"
make -C sw/kernel check-memory     # cached delayed-memory shell + fork/wait
make -C sw/kernel check-sdboot     # SD boot through physical-SDRAM RTL path
```

The `check-fencei` image fetches an instruction, patches it through the data
port, executes `fence.i`, and verifies the second call observes the patched
instruction on ISS, QEMU, and cached delayed RTL.

## SPI SD-card foundation

`components/spi/polling_mode0/axspi.sv` is a polling SPI mode-0 controller at `0x1001_0000`.
`DATA` (`+0`) holds the transmit/received byte, `CTRL` (`+4`) controls
`GO` bit 0 and `CS_N` bit 1, `STATUS` (`+8`) reports `BUSY` bit 0 and
`RX_VALID` bit 1, and `CLKDIV` (`+12`) sets the half-cycle divider. The three
pin-level signals (`spi_sclk`, `spi_mosi`, `spi_cs_n`) and `spi_miso` are on
`soc_top` for the later board top.

The SoC runner has an SPI-mode SDHC simulation card; pass `SD_IMAGE=path` to
load a binary sector image. It implements CMD0, CMD8, CMD55/ACMD41, CMD16,
CMD17, CMD24, and CMD58, with 512-byte SDHC block addressing. It is a
simulation device; the synthesizable SPI controller drives the same physical
card protocol.

```bash
make -C sim/unit run-axspi        # controller register/waveform contract
make -C sw/baremetal check-spi    # SoC decode plus idle-MISO smoke image
make -C sw/baremetal check-sd     # SDHC init + CMD17 sector read on RTL
```

AXFS v1 and the kernel block driver provide a mounted SD filesystem path:

```bash
make -C sw/kernel check-storage
```

This builds a deterministic SD image containing `motd`, `readme`, and the
`hello.elf` user program, mounts it through the kernel SPI driver, and runs the
normal shell plus fork/wait plus filesystem-backed `exec` script on cached
delayed RTL.

When no card answers, the same filesystem component mounts a built-in read-only
root carrying those two files, so a diskless profile still has a namespace to
`ls`, `cat`, and `openat`. That fallback used to be a private table inside the
shell; moving it behind the filesystem seam is what lets the shell's `cat` and
the `read` syscall share one lookup and one read path (docs/abi.md).

## Writable AXFS v1

AXFS deliberately remains small: up to eight named files. Image builders may
package a read-only file as a contiguous multi-sector extent, which is how the
kernel loads `hello.elf` without embedding its padded ELF image in the boot
kernel. Runtime `write NAME TEXT` remains limited to one 512-byte sector and
creates or replaces a file by issuing CMD24 for the data sector and then CMD24
for the directory sector. AXFS has no extent allocation for runtime writes,
reclamation, checksums, or crash-safe journalling; those are explicit future
filesystem work.

```bash
make -C sw/kernel check-storage-write
```

This proves write → directory update → readback in a cached RTL session.

## SD boot path

`sw/bootrom/` contains a less-than-4-KiB ROM-resident M-mode loader. It brings
up SPI SDHC, validates the `AXBT` boot header, copies the raw kernel sectors to
`0x8000_0000`, and jumps to the kernel's existing reset entry. The boot disk
places AXFS at sector 96 so the persistent-process kernel and future service
growth fit before the filesystem while the loaded kernel mounts the same image.

```bash
make -C sw/kernel check-sdboot
```

This is a true SD-to-SDRAM boot through `axsdram`; the test requires the
`aXboot` banner, shell, fork/wait, and filesystem-backed ELF-exec transcripts.
It takes several million simulation cycles because the deliberately simple SPI
controller transfers one byte at a time, including the multi-sector ELF, and
SDRAM accesses are conservative.
