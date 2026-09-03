# sw/kernel/ — aXos

`aXos` is the small monolithic reference kernel. It runs unchanged on aXsim,
QEMU `virt`, and the RTL SoC.

## Deployment invariant

Every aXos configuration is emitted as a runtime `.bin` payload. FPGA kernel
profiles reset into the immutable `sw/bootrom` UART loader with blank RAM;
`axhost --upload-kernel` transfers the selected binary using the bounded,
CRC-checked `AXK1` envelope. Kernel source or policy changes therefore never
require synthesis, place-and-route, or a new bitstream.

`make -C sw/kernel check-uartboot` enforces the invariant against the full
kernel, including corrupt-CRC and oversized-image rejection.

It provides Sv32 paging, M/S/U trap transitions, a physical-page allocator,
CLINT-driven preemptive scheduling, and a minimal U-mode process model.
`clone` duplicates the Sv32 root, page table, user stack, trap context, and
descriptor state; `wait4` blocks, reports encoded exit status, and reaps the
child; `exit` releases every process page and resumes the resident S-mode
shell.

## Shell and filesystem

The resident shell runs in S-mode and uses the platform 16550 RX/TX console.
Its baseline commands are `help`, `clear`, `uname`, `uptime`, `free`, `ps`,
`pwd`, `ls`, `cat`, `stat`, `hexdump`, `touch`, `cp`, `mv`, `rm`, `write`,
`echo`, `fork`, `exec`, `run`, `role`, `shutdown`, and `exit`. The parser supports
single/double-quoted arguments and backslash quoting. `uptime`, `free`, and
`ps` use a read-only kernel observability interface rather than reaching into
allocator or scheduler state. AXFS is a flat root, so directory commands are
deliberately absent; runtime-created and copied files remain limited to one
512-byte sector.

## Interrupts

Two controllers reach the kernel, and they arrive by different routes because
the architecture allows only one of them to be delegated.

The CLINT owns the machine timer, and `mtime`/`mtimecmp` are M-mode state that
cannot be delegated to S-mode. A small M-mode shim in [trap.S](trap.S)
therefore re-arms the timer and raises delegated `SSIP`, which the kernel takes
as its scheduler tick — machine-owned hardware, S-mode policy.

Device interrupts need no such shim. The shell's PLIC has a supervisor context
(context 1, at the QEMU-virt addresses), so `mideleg` bit 9 delegates the
supervisor external interrupt and the kernel claims and completes directly from
S-mode. That is deliberate rather than incidental: QEMU's `virt` machine wires
context 1 the same way, so the identical driver runs on QEMU and on the RTL,
which is the three-platform rule applied to interrupts.

[plic.c](plic.c) is the driver. `plic_dispatch` claims, hands the source to its
device, and completes — in that order, because the sources are level-sensitive
and completing a device that is still asserting simply re-arms it. It does not
schedule: a device completion says nothing about which task should run next.

The kernel does not carry its own copy of the interrupt numbering. Which device
is which source, and which context maps to which privilege, is declared once in
the SoC component that does the wiring, and `tools/gen_irq_map.py` derives
`build/ax_irq_map.h` from it — so adding a device is an edit to the shell alone.
The generator also checks the ids cover their declared range exactly, which
turns "added a source and forgot to bump the count" from a silently unreachable
interrupt into a build failure that names the problem. `SOC_TOP` selects a
different shell's map; the default is the reference one, since the kernel image
is otherwise hardware-profile-independent.

The PLIC is probed once at boot through the same recoverable-load path the role
window uses, so its absence is an ordinary configuration rather than a failure.
The ISS models no PLIC at all; there, and in any profile without one, drivers
fall back to polling and behave identically at more cost.

The Primer monitor personality goes further and omits the driver at build time.
It has a 32 KiB image whose allocator pool begins on a 4 KiB boundary, so text
it never executes costs a whole page of allocatable memory; since it has no
interrupt policy of its own, `plic.c` is left out of its `SOURCES` and the
interrupt-driven wait is compiled out of [role.c](role.c). `make -C sw/kernel
check-primer` holds that budget — the monitor image is byte-for-byte the size
it was before this existed.

## Role control plane

`role` is the first piece of the shell + role control plane (DESIGN.md §3.3):
aXos itself — not a bare-metal test program — administers the accelerator.
`vm_bootstrap_map` maps the physical 64 KiB role window through a kernel-only
`0x5000_0000` virtual alias, and the in-kernel driver in [role.c](role.c)
discovers the role (`ROLE_ID`/`VERSION`) and drives the generic
doorbell/status/descriptor cycle. The alias remains present under process page
tables while user ELF text keeps its existing `0x4000_0000` virtual address.

The shell `role` command drives a loopback self-test. U-mode programs use
`role_info`, `role_submit`, and `role_wait`; the kernel validates and copies
the same loopback, TPU GEMM, and GPU kernel encodings used by the host link, so
neither userspace nor the host daemon receives raw MMIO access. Completion is
tokenized and retry-safe.

Waiting for a job is interrupt-driven where it can be. When both a role and a
PLIC are present, `role_init` routes the role's completion line to the
supervisor context and `role_wait_done` sleeps in `wfi` until the handler
reports the job, instead of reading `STATUS` in a loop. The test-and-sleep race
is closed by dropping `sstatus.SIE` around the check, since `wfi` still wakes on
a pending enabled interrupt. Two cases deliberately keep polling: a syscall runs
with interrupts masked, so the `role_submit` path would never observe the
handler, and re-enabling interrupts underneath a half-finished syscall is not
worth the re-entrancy; and a platform without a PLIC has nothing to wait on.
Both paths stay bounded so a wedged device cannot hang the kernel.

The shell reports which one happened — `irq=N polled=M` after a loopback copy —
so "waited on the interrupt" is checkable rather than assumed. Evidence:
`make -C sw/kernel check-role-driver` runs both the resident-shell and U-mode
paths against RTL `role.loopback`; `check-role-irq` runs two consecutive jobs
and requires `irq=2 polled=0`, which fails if the source is not completed and
re-armed between them; `check-hostlink` covers all three role job formats.

The initial immutable RAM disk is a named-file table. An optional AXFS v1 SD
image path runs on cached external-memory RTL: `check-storage` mounts `motd`,
`readme`, and `hello.elf` through the kernel SPI block driver. Packaged files
may occupy contiguous sector extents, while `write`, `touch`, and `cp` create
one-sector files and `mv`/`rm` update the flat directory through SD CMD24; it is
deliberately not a crash-safe general filesystem. Storage builds load
`hello.elf` from AXFS for `exec`. The boot image places AXFS at sector 128,
leaving a 64 KiB kernel envelope without changing the ROM's length-prefixed
loader contract. Diskless ISS/QEMU/RTL builds retain the built-in root and
embedded user program.

`exec [FILE [ARG...]]` and `run FILE [ARG...]` construct a System V
`argc`/`argv` stack, run the root process synchronously, and restore the saved
supervisor shell context when it exits. Repeated runs do not reboot the
machine. `fork` launches the U-mode parent/child conformance fixture: the child
exits with status 7, the parent verifies `wait4` reported `7 << 8`, and the
fixture returns to the prompt. The shell test accepts either valid first
scheduling order, `PCW` or `CPW`.

## Program images and W^X

The loader maps each `PT_LOAD` with its own `p_flags`, and refuses one that is
both writable and executable. That refusal is not decoration: a hand-built ELF
whose only defect is `R+W+X` — correct magic, `ET_EXEC`, `EM_RISCV`, in-range
vaddr, and three real instructions calling `exit(0)` — **loads and runs to a
clean exit** with the check removed. `make -C sw/kernel check-loader-wx` runs it
off the AXFS image and requires `exec: load failed`; keeping the fixture on the
disk rather than in the built-in root means testing the rejection costs the
shipped kernel image nothing.

The matching linker rule is worth knowing because it fails quietly. Aligning
sections to a page does not separate their permissions — `ld` groups sections
into segments by flag compatibility, so a page-aligned `.rodata` still shares
`.text`'s `R+E` segment and gets mapped executable. [userprog/user.ld](userprog/user.ld)
declares three segments explicitly, and `check_boot.py` asserts the built image
is `R+X`, `R`, `R+W`. That check is structural on purpose: no behavioural test
can see the difference, because an executable `.rodata` reads exactly like a
read-only one.

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
96. It then proves the ROM loader, the real `axsdram` pin-level controller
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
