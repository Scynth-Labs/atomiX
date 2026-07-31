# sw/user/ — userland

Programs running in U-mode under aXos, against its syscall ABI (static ELF,
no dynamic linking).  The contract they compile against is
[docs/abi.md](../../docs/abi.md): the RISC-V Linux asm-generic syscall numbers,
the System V ELF entry and initial stack, and the private `0x1000+` range for
calls with no Linux equivalent (the accelerator role driver being the first).

Everything that contract needs now exists — `syscall.linux-compat` dispatches
`ecall`, `loader.elf32` maps a static ET_EXEC image and enters at `e_entry`,
and `libc.axlibc` supplies `crt0`, syscall wrappers with errno, string/memory
primitives, a first-fit `malloc` over a real `brk`, and a console `printf`
subset.  libgcc is linked, so 64-bit arithmetic resolves.

The shipped user program still lives beside the kernel as
`sw/kernel/userprog/hello.c`, built as its own freestanding ELF and reaching
the kernel only as a byte array or as a file in AXFS.  It is an ordinary C
`main()` — malloc/free/calloc/realloc, strings, 64-bit division, `printf`, and
open/read/lseek/fstat on a file — and it runs on the ISS, QEMU, and the RTL,
against both the built-in read-only root and an SD card.  Evidence:
`make -C sw/kernel check-boot` and `check-storage`.

The resident aXos shell and the U-mode fork/wait fixture stay in `sw/kernel/`;
the shell supplies `ls`, `cat`, and `echo`, plus the `exec`/`run` entry points
that build an `argc`/`argv` frame for a U-mode program.

This directory becomes the home for separately linked `init`, `sh`, and
coreutils once there is more than one such program to build — the loader,
libc, and filesystem prerequisites are no longer what is missing.  Filesystem
access through the ABI is read-only (`-EROFS`), so a writable userland is the
next real prerequisite.

Later: role demo clients (e.g. matmul driving the TPU role from *inside* the
box through `role_info`/`role_submit`/`role_wait`, complementing host-driven
offload).
