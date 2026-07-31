# sw/ — all software

Everything compiled with the **stock RISC-V GNU toolchain**
(`riscv64-unknown-elf-gcc`, multilib; `-march=rv32im -mabi=ilp32`) — no
custom compiler work, industry ELF format throughout. Newer toolchains may
use the explicit `rv32im_zicsr` spelling; see
[docs/dependencies.md](../docs/dependencies.md) for the GCC 10 compatibility
note.

| Subdirectory | Role | Runs on |
|---|---|---|
| [baremetal/](baremetal/) | crt0, linker scripts, bring-up programs | the target (no OS) |
| [kernel/](kernel/) | `aXos` — our monolithic kernel | the target |
| [user/](user/) | separately linked userland; the shipped programs still live beside the kernel in `kernel/userprog/` | the target, under aXos |
| [host/](host/) | `axhost` — host-link driver and kernel uploader | the host PC |

Three-platform rule (DESIGN.md §3.1): target software must run unchanged on
aXsim, QEMU `-machine virt`, and the RTL — never `#ifdef` per platform; if it
behaves differently somewhere, that's a bug in one of the platforms.

Build and verification commands are in [docs/workflow.md](../docs/workflow.md).
