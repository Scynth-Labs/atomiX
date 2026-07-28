# sw/kernel/ — aXos

`aXos` is the small monolithic reference kernel. It runs unchanged on aXsim,
QEMU `virt`, and the RTL SoC.

It provides Sv32 paging, M/S/U trap transitions, a physical-page allocator,
CLINT-driven preemptive scheduling, and a minimal U-mode process model.
`SYS_FORK` clones the Sv32 root, page table, user stack, and trap
context; `SYS_WAIT` blocks and later reaps the child; `SYS_EXIT` releases every
process page; `SYS_CONSOLE_PUTC` is the first user-visible write syscall.

## Shell and filesystem

The resident shell runs in S-mode and uses the platform 16550 RX/TX console.
Its baseline commands are `help`, `clear`, `uname`, `uptime`, `free`, `ps`,
`pwd`, `ls`, `cat`, `stat`, `hexdump`, `touch`, `cp`, `mv`, `rm`, `write`,
`echo`, `fork`, `exec`, `role`, `shutdown`, and `exit`. The parser supports
single/double-quoted arguments and backslash quoting. `uptime`, `free`, and
`ps` use a read-only kernel observability interface rather than reaching into
allocator or scheduler state. AXFS is a flat root, so directory commands are
deliberately absent; runtime-created and copied files remain limited to one
512-byte sector.

## Role control plane

`role` is the first piece of the shell + role control plane (DESIGN.md §3.3):
aXos itself — not a bare-metal test program — administers the accelerator.
`vm_bootstrap_map` device-maps the fixed 64 KiB role window into the kernel's
S-mode address space, and the in-kernel driver in [role.c](role.c) discovers
the role (`ROLE_ID`/`VERSION`) and drives the generic
doorbell/status/descriptor cycle.  The shell `role` command prints the
discovered role and, for `role.loopback`, drives one copy job end-to-end as a
self-test.  Per-role job marshaling (GEMM for TPU-lite, SIMT kernels for
GPU-compute) and the host-link service that will call this driver on behalf of
remote requests layer on top of this same header driver. Evidence:
`make -C sw/kernel check-role-driver`.

The initial immutable RAM disk is a named-file table. An optional AXFS v1 SD
image path runs on cached external-memory RTL: `check-storage` mounts `motd`,
`readme`, and `hello.elf` through the kernel SPI block driver. Packaged files
may occupy contiguous sector extents, while `write`, `touch`, and `cp` create
one-sector files and `mv`/`rm` update the flat directory through SD CMD24; it is
deliberately not a crash-safe general filesystem. Storage builds load
`hello.elf` from AXFS for `exec`, which keeps the boot kernel below the
sector-64 filesystem boundary. Diskless ISS/QEMU/RTL builds retain the built-in
root and embedded user program.

`fork` launches the U-mode parent/child demonstration. The child gets return
value zero; the parent receives a child PID, blocks in `wait`, wakes when the
child exits, and reaps it. The shell test accepts either valid first scheduling
order, `PCW` or `CPW`.

## Replaceable kernel policies

The trap/syscall kernel is stable, but scheduler, virtual-memory, allocator,
shell, filesystem, and block-driver implementations are selected at build
time. The default profile is
`../../configs/kernel-default.json`, which selects `scheduler.round-robin` and
`vm.sv32` plus the reference allocator, shell, filesystem, and SD block
driver. A working alternative retains the current task across timer ticks until
it blocks or exits:

```bash
make -C sw/kernel kernel-config KERNEL_CONFIG=../../configs/kernel-cooperative.json
make -C sw/kernel check-boot \
  KERNEL_CONFIG=../../configs/kernel-cooperative.json \
  QEMU=/path/to/qemu-system-riscv32
```

`include/scheduler.h` defines the narrow task-selection contract and
`include/vm.h` defines bootstrap and user-address-space lifecycle. The
reference service implementations live in their owning `components/` folders;
an external manifest can also supply the `page_*`, `shell_run`, `fs_*`, or
`sd_*` source implementation without copying it into aXos. These
interfaces do not constrain a custom kernel: it can instead supply a separate
software component and its own build rules.

## Run and verify

Run the complete shell and fork/wait regression on the ISS, QEMU, and RTL:

```bash
make -C sw/kernel check-shell
make -C sw/kernel check-boot QEMU="$HOME/.local/bin/qemu-system-riscv32"
```

The board-independent memory regression retains the same kernel image but runs
it on 32 MiB of delayed RAM through optional I/D caches:

```bash
make -C sw/kernel check-memory
make -C sw/kernel check-storage
make -C sw/kernel check-storage-write
make -C sw/kernel check-sdboot
make -C sw/kernel kernel-component-test QEMU=/path/to/qemu-system-riscv32
```

`check-sdboot` builds `build/axos_boot.img`, a bootable SD-card image with the
kernel at its ROM-loader location and AXFS (including `hello.elf`) at sector
64. It then proves the ROM loader, the real `axsdram` pin-level controller
model, the mounted shell, and filesystem-backed ELF execution in one RTL run.
See [docs/ulx3s-bringup.md](../../docs/ulx3s-bringup.md) to use the same image
on an ULX3S.

To run the RTL console with a reproducible command script:

```bash
make -C sw/kernel run-rtl \
  UART_INPUT_FILE="$PWD/sw/kernel/shell_input.txt"
```

For the delayed external-memory model and caches, add
`RAM_BYTES=33554432 EXTERNAL_MEMORY=1 CACHES=1`. The fork/wait script needs a
larger runner budget: add `MAX_CYCLES=500000`.

The file supplies newline-terminated bytes to the synthesizable UART RX
holding register. Replace it with any command script; `exit` sends the normal
test-finisher success value. QEMU 7 or newer is required with
`-cpu rv32,pmp=false` because aXcore does not implement PMP; setup is in
[docs/dependencies.md](../../docs/dependencies.md).
Memory-model and cache design details are in [docs/memory.md](../../docs/memory.md).
