# sw/baremetal/ — bare-metal runtime and bring-up programs

The no-OS layer used before (and alongside) the kernel. It uses no libc or
firmware: the image enters at RAM `0x8000_0000`, and talks directly to the
QEMU-virt-aligned UART, CLINT, and test-finisher addresses.

- `crt0.S` — reset entry: set `sp`, zero `.bss`, call `main`
- `link.ld` — linker script for the DESIGN.md §3.2 map (RAM at `0x8000_0000`)
- `include/platform.h` — volatile MMIO helpers, a polling 16550 TX console,
  and the `sifive_test` exit protocol
- `examples/hello.c` — first platform customer; prints then passes
- `traps.S`, `include/csr.h`, `include/clint.h`, and `examples/timer.c` — a
  full-register M-mode trap entry plus a three-tick machine-timer demo
- `examples/preempt.c` — two task contexts on distinct stacks, switched by
  timer interrupts. Its expected UART transcript is `preempt: ABABAB`.
- `include/spi.h`, `examples/spi.c`, and `examples/sd.c` — the polling SPI
  register interface, an idle-MISO smoke image, and SPI-mode SDHC
  initialization plus a 512-byte CMD17 sector read.
- `include/role.h` and `examples/role.c` — the accelerator-window driver:
  discovery through `ROLE_ID`, descriptor setup, doorbell, and polled `STATUS`.
  `examples/tpu.c`, `gpu.c`, and `gpu1.c` drive the real accelerator roles
  through the same header.
- `include/plic.h` and `examples/role_irq.c` — the same completion taken as an
  interrupt instead of polled: the role's level-sensitive line reaches the core
  through PLIC source 2 as a machine external interrupt. The program starts the
  job with the source masked (proving nothing bypasses the PLIC), then routes
  it and parks in `wfi`, so it can only finish if the whole path works.
- `examples/cpu_perf.c`, `gpu_perf.c`, and `render_perf.c` — the measured
  workload payloads described under *Performance payloads* below.

Build and run the current bring-up program:

```bash
make -C sw/baremetal images      # ELF, flat binary, and RTL RAM .hex image
make -C sw/baremetal run-iss
make -C sw/baremetal run-qemu
make -C sw/baremetal run-rtl
make -C sw/baremetal check-hello # asserts identical UART output on all three
make -C sw/baremetal check-timer # CLINT timer interrupts on all three
make -C sw/baremetal check-preempt # timer-preempted task switching on all three
make -C sw/baremetal check-spi   # polling SPI controller on RTL
make -C sw/baremetal check-sd    # virtual SDHC init + block read on RTL
make -C sw/baremetal check-role  # role window, polled completion, on RTL
make -C sw/baremetal check-role-irq # the same completion through the PLIC
make -C sw/baremetal check-tpu   # TPU-lite GEMM vs an on-core reference
make -C sw/baremetal check-gpu   # SIMT engine vs an on-core interpreter
make -C sw/baremetal check-gpu1  # the banked SIMT engine, same battery
```

The role checks need a profile with an accelerator in the window; the targets
above select `configs/sim-role-loopback.json` and its per-role siblings
themselves.

`RISCV_PREFIX` defaults to `riscv64-unknown-elf-`. GCC 10 accepts
`RISCV_ARCH=rv32im` (its Zicsr support is included in that spelling); newer
toolchains may be invoked with `RISCV_ARCH=rv32im_zicsr`.

`RAM_BYTES` defaults to 128 KiB and controls both the linker overflow check and
`__stack_top`. FPGA builds set it to the selected Tang profile's actual 16 or
32 KiB and keep those images under `build/ram<RAM_BYTES>/`; this prevents a
128 KiB-stack image from being baked into a smaller BRAM. `BUILD_DIR` may also
be set directly when producing a memory-size-specific image.

The timer-preemption demo is covered by `check-preempt`. It saves
all integer registers into the interrupted task's frame, selects another
frame/`mepc`, restores it, and executes `mret`; this is intentionally a small
and inspectable scheduler substrate rather than a kernel API.

## Performance payloads

The CPU, GPU, and TPU performance images are correctness tests as well as
benchmarks:

```bash
make -C sw/baremetal check-cpu-perf
make -C sw/baremetal check-gpu-perf
make -C sw/baremetal check-tpu
```

`cpu_perf` reports the sum of the measured workload windows, excluding setup
and UART output. `gpu_perf` and `tpu` retain their doorbell-to-done compute
number and also report `upload`, `compute`, `readback+verify`, and full `total`
cycles. Readback includes MMIO reads, comparison, and checksum generation, so
it deliberately measures the complete correctness-checked offload boundary.
The phase counters and `total` each include their own counter/control overhead,
so the phase sum need not equal `total` exactly.

Every summary ends with a checksum and projected time at the two Tang target
clocks: `us@27MHz` for Tang Nano 20K and `us@25MHz` for Tang Primer 25K.
These are pre-place-and-route projections from RTL cycles. The achieved clock
and UART transcript on physical hardware are authoritative; matching checksums
make simulation-versus-board comparison unambiguous.

For the concise comparison of the exact independently maximized profiles:

```bash
python3 tools/bench.py tang
```
