# Dependencies and compatibility

atomiX separates its dependency tiers so a simulator-only user does not need a
formal toolchain or FPGA tools.  The commands below are safe to review and run
yourself; this repository never installs system packages automatically.

## Start here: `make doctor`

```bash
make doctor
```

It reports what this host has, what the build will actually use, and which
tier each missing tool would unlock.  It never fails, so the whole report is
readable even on a machine with nothing installed yet.

The build adapts to the toolchain it finds rather than requiring a particular
one ([`mk/toolchain.mk`](../mk/toolchain.mk)):

- **Cross-compiler tuple.** Distributions disagree — `riscv64-unknown-elf-`,
  `riscv64-linux-gnu-` and `riscv64-elf-` are all in use.  The first installed
  candidate wins; override with `RISCV_PREFIX=<tuple>-` if yours is elsewhere.
- **ISA string.** Zicsr is a separate extension in the ratified ISA: GCC before
  12 folds it into the base ISA and *rejects* the explicit `_zicsr` spelling,
  while GCC 12 and later require it for any CSR instruction.  No single string
  works on both, so the build compiles a CSR instruction and keeps the spelling
  that survives.  Override with `RISCV_ARCH=` / `RISCV_ARCH_I=` to pin it.

Because these are probed rather than assumed, a newer or differently-named
toolchain generally needs no configuration at all.  `make doctor` prints what
was selected, so a surprising result is visible rather than silent.

For exact installation procedures, version-specific workarounds, and commands
for a local QEMU or upstream formal stack, use [toolchain.md](toolchain.md).

## Core: simulation and target software

Required for the ISS, Verilator simulation, and bare-metal images on
Ubuntu/Debian:

```bash
sudo apt update
sudo apt install build-essential gcc-riscv64-unknown-elf \
  picolibc-riscv64-unknown-elf verilator qemu-system-misc git make python3
```

`gcc-riscv64-unknown-elf` includes RV32 multilib support despite its package
name.  The project defaults to `-march=rv32im -mabi=ilp32`, which works with
Ubuntu 22.04's GCC 10.2 as well as newer toolchains.

## Kernel checks: a current QEMU

The packaged Ubuntu 22.04 QEMU 6.2 is adequate for basic experiments but is
not adequate for the PMP-less S/U-mode aXos checks.  Use QEMU 7 or newer and
pass it explicitly when needed:

```bash
make -C sw/kernel check-boot QEMU=/path/to/qemu-system-riscv32
```

[toolchain.md](toolchain.md#qemu-for-axos) gives a small RISC-V-only local
build procedure that coexists with the distro package and needs no system-wide
installation.

## Formal verification

The formal flow needs a current upstream Yosys, SymbiYosys (`sby`), and a
separate riscv-formal checkout.  Boolector and Z3 are useful solver choices:

```bash
sudo apt install boolector z3
```

Ubuntu 22.04's Yosys 0.9 is too old for the SystemVerilog packages used by
this project.  Follow the guarded upstream installation in
[toolchain.md](toolchain.md#formal-verification) before running:

```bash
make -C formal check
```

## FPGA synthesis, place-and-route, and programming

A board component selects one of two open flows through its manifest, and both
ship in the prebuilt [OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build/releases).
Use it rather than compiling the FPGA stack locally: it keeps the tools matched
and avoids a long one-off build.

| Board | Flow | Tools |
|---|---|---|
| ULX3S-85F (Lattice ECP5) | `ecp5` | `yosys` (`synth_ecp5`), `nextpnr-ecp5`, `ecppack` |
| Tang Nano 20K (Gowin GW2A-18C) | `gowin` | `yosys` (`synth_gowin`), `nextpnr-himbaechel` (apicula), `gowin_pack` (apicula) |
| Tang Primer 25K Dock (Gowin GW5A-25A) | `gowin` | current `yosys` with GW5A mapping, `nextpnr-himbaechel` (apicula), `gowin_pack` (apicula) |

`make -C rtl/fpga check-tools` verifies exactly the tools the selected flow
needs, and `make -C rtl/fpga synth` is a yosys-only "does it synthesise for this
board" gate that needs no place-and-route tools installed.

`openFPGALoader` and `picocom` are only needed for physical-board work.  The
setup, tool verification, and safe SRAM-versus-flash distinction are in
[toolchain.md](toolchain.md#ecp5-fpga-tools) and
[tangprimer25k-bringup.md](tangprimer25k-bringup.md) and
[ulx3s-bringup.md](ulx3s-bringup.md).

## Browser-hosted simulation (optional)

Only needed to build the Verilated model as WebAssembly, so a reader can boot
the machine in a tab with no toolchain at all.  Nothing in the normal build,
test, or FPGA flow depends on it, and no evidence claim rests on it.

Emscripten is installed through its own SDK rather than from apt: the packaged
version lags, and `emsdk` needs no root and coexists with everything else.

```bash
git clone https://github.com/emscripten-core/emsdk.git ~/emsdk
cd ~/emsdk && ./emsdk install latest && ./emsdk activate latest
```

Each shell that uses it sources the environment first; this deliberately does
not touch `.bashrc`, so an unsourced shell keeps the ordinary native toolchain:

```bash
source ~/emsdk/emsdk_env.sh
emcc --version
```

`emsdk` brings its own Node, which is what runs a headless WASM boot for
timing.  A separately installed Node also works.  Budget about 1.5 GB.

## LLVM: a second toolchain and the analyzers (optional)

Only needed for `TOOLCHAIN=llvm`, `make toolchain-llvm`, `make static-analysis`
and `make fuzz-loader`.  Nothing in the normal build, test, or FPGA flow depends
on it, and **no recorded size, timing, or fmax number was measured with it** --
GCC remains the toolchain every claim in this repository rests on.

It is here because a second front end sees defects the first does not.  On its
first build clang reported an unused `static inline` in `sw/kernel/console.c`
that GCC 10 does not warn about, and it makes `clang --analyze` usable against
the real `riscv32` target instead of a host approximation of it.

```bash
sudo apt-get install -y clang lld llvm cppcheck shellcheck
python3 -m pip install ruff
```

**One toolchain compiles every component.**  `TOOLCHAIN=gcc` builds all target
code with GCC and all host C++ with `g++`; `TOOLCHAIN=llvm` builds all of it
with clang, `lld` and `clang++`.  That covers `sim/unit`, `sim/testgen` and
`sim/livefpga` as well as the kernel and the bare-metal images -- those three
used to hardcode GCC, so an LLVM build previously produced a kernel from clang
and directed regressions from GCC.  A result from a build like that belongs to
a configuration nobody selected, and a defect only one front end emits gets
attributed to the wrong one.

The compiler runtime is the deliberate exception: `libgcc.a` and
`libclang_rt.builtins` are prebuilt support archives for arithmetic the ISA
lacks -- RV32IM has no 64-bit divide, so `wide / 3ull` becomes a call to
`__udivdi3` -- not components of atomiX.  clang's own `--rtlib=libgcc` is the
default on most Linux targets for the same reason.  `mk/toolchain.mk` prefers
LLVM's `compiler-rt` when it is present and falls back to GCC's `libgcc.a` for
the exact `-march`/`-mabi` pair when it is not, which on Ubuntu 22.04 it is not:
the clang package ships no `compiler-rt` builtins for bare `riscv32`.

`clang` compiles target code with `--target=riscv32-unknown-elf`; `lld` links it
(a distribution clang ships no cross `ld`, so `-fuse-ld=lld` is not optional);
`llvm` provides `llvm-objcopy` and `llvm-strip`.

One number to know before using it: clang 14 emits roughly 45% more text for
RV32IM than GCC 10 (68,791 bytes against 47,220 for the default kernel).  The
page pool is whatever RAM is left after the image, so a clang-built kernel needs
more than the default 128 KiB to leave enough free pages for the ABI tests --
`make toolchain-llvm` uses 256 KiB.  At 128 KiB it boots and runs correctly and
simply cannot allocate; that is a size difference, not a miscompilation.

### Sanitizers, and where their reports go

`make fuzz-loader` builds the binary-format parser regression harnesses with
AddressSanitizer, LeakSanitizer and UndefinedBehaviorSanitizer.  They answer
different questions: ASan finds heap corruption and gives a stack trace for a
fault, LSan finds a page the loader mapped and lost, UBSan finds a misaligned
load or a signed overflow while processing externally supplied offsets.

A sanitizer report is the most actionable thing this repository produces and the
easiest to lose, so it does not stay in the log.  `tools/fuzz_report.py` parses
all three -- plus libFuzzer's own verdicts -- into the same findings schema the
static analysis uses, and the nightly workflow hands both reports to
`tools/analysis_issue.py`, which puts them in one issue with the file, the line,
the allocating or faulting function, and the command that reproduces them.

The guard page in `sim/fuzz/fuzz_loader.c` sits alongside ASan rather than
instead of it: ASan does not poison an `mmap`'d region, so the guard is what
makes a read past the image deterministic, and ASan is what turns the resulting
fault into a report that names a line.

## Recorded working baseline

The following is a compatibility record from the verified Ubuntu 22.04.5 WSL2
host on 2026-07-18, not a set of strict pins — the build probes for capability
rather than matching these versions.  `make doctor` prints the same record for
your own host, which is the useful thing to quote in a bug report:

| Tool | Recorded version | Use |
|---|---:|---|
| RISC-V GCC | 10.2.0 | RV32 bare-metal and kernel images |
| Verilator | 4.038 | RTL simulation |
| QEMU | 8.2.10 (local) | Three-platform and aXos checks |
| Yosys | 0.67+ (upstream) | Formal flow |
| Python | 3.10.12 | Test generation and runners |
| GNU Make | 4.3 | Build orchestration |
| Emscripten | 6.0.5 | Browser-hosted simulation (optional) |
| Node | 24.18.0 host / 22.16.0 via emsdk | Headless WASM boot and timing (optional) |

Newer compatible releases are welcome.  Record the version and the evidence
you ran when changing a toolchain assumption.
