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

### Supervisor role alias

The role remains physically decoded at `0x4000_0000`. User executables also
start at virtual `0x4000_0000`, so process page tables cannot identity-map the
device there: the user L0 table intentionally occupies that root entry. Sv32
therefore maps the role superpage at supervisor-only virtual `0x5000_0000`.
Kernel drivers use that alias under both the kernel and process page tables;
U-mode reaches the role only through the checked calls in [abi.md](abi.md).
ISS and QEMU do not decode the physical role range, so a recoverable boot-time
probe records absence before any process starts.

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
`run-axsdram` checks CAS-2 timing and that the commands reached the pins;
`check-sdboot` boots the shell and fork/wait through the same
physical-controller path, and reports the pin-command counts that show it did.

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
make -C sw/kernel check-sdboot     # SD boot: shell + fork on the SDRAM pin model
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
places AXFS at sector 128, leaving a 64 KiB kernel envelope so the role ABI and
future service growth fit before the filesystem while the loaded kernel mounts
the same image.

```bash
make -C sw/kernel check-sdboot        # shell + fork/wait + ELF exec
make -C sw/kernel check-sdboot-exec   # the same exec at the default quantum
```

`check-sdboot` is a true SD-to-SDRAM boot through `axsdram`: it selects
`configs/sim-sdram.json`, requires the `aXboot` banner and the shell,
fork/wait and ELF-exec transcripts, and requires the run to have driven the
SDRAM pins (263,360 activates and 37,335 refreshes for the shell run) before it
will call any of that physical-SDRAM evidence. It also proves the two refusals
first, against a real BRAM profile: `run-sdram` will not build a machine
without SDRAM pins, and a transcript from one is not accepted. Measured 7.54M,
8.47M and 9.86M cycles for the three stages; the bounds are 12M/12M/15M.

### The scheduling quantum on slow memory

Exec on this path used to make no progress at all, and the reason was not the
memory. The machine-timer shim armed the next deadline at trap *entry*, so the
shim, the delegated S-mode handler, the scheduler's page-table switch and its
`sfence` were all spent out of the interval the resumed task was supposed to
get. On on-chip RAM that overhead is small against a 2,000-cycle quantum; at
SDRAM latency it is most of it, and the resumed task was preempted before
retiring anything — 785 user instructions across 787 handler entries, with no
completion at 120M cycles.

The S-mode handler now arms the quantum on the way *out*, after the scheduler
has chosen who runs next, so the quantum measures the resumed task's own
execution and forward progress no longer depends on how expensive service is.
The shim's arming still stands for every path that does not reach the handler's
exit, so a missed rearm cannot stop the tick. Exec then completes on the pin
model in 39.1M cycles at the default quantum, and the delayed model's exec
dropped from 13.97M to 8.33M cycles at the same quantum.

The interval itself is the `timer_quantum_cycles` profile setting, default
2,000, bounded in `tools/configure.py` and again by `_Static_assert` in
[sw/kernel/include/timer.h](../sw/kernel/include/timer.h) — one define reaching
both the C handler and the assembly shim, which must agree because both arm the
same CLINT register. `configs/kernel-slow-memory.json` sets 64,000 for machines
like this one, and with it the same exec finishes in 9.86M cycles and the user
task retires 159 instructions per handler entry rather than one.
`check-sdboot` builds with that profile; `check-sdboot-exec` runs the same
workload at the default 2,000 to keep a failure case for renewed loss of user
progress, since with the quantum armed at entry again it does not finish at all.

Evidence: [sdram-exec-progress.json](../research/benchmarks/sdram-exec-progress.json)
for the diagnosis and [timer-quantum-fix.json](../research/benchmarks/timer-quantum-fix.json)
for the fix, both produced by `tools/sdram_progress_probe.py`.
