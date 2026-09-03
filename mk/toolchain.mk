# Shared host-toolchain detection.
#
# The rule here is capability detection, not version detection: ask the
# installed tools what they accept rather than assume which distribution
# shipped them.  Every value below can still be overridden on the command line
# or in the environment, and an override always wins over a probe.
#
# Include from any Makefile that compiles target code:
#
#     include ../../mk/toolchain.mk
#
# Probed results are exported, so a recursive `make -C` inherits them instead
# of re-running the probes in every sub-make.

# ---------------------------------------------------------------- cross tools
# Distributions disagree on the tuple: Debian/Ubuntu ship
# `riscv64-unknown-elf-`, several others ship `riscv64-linux-gnu-` or
# `riscv64-elf-`.  Take the first one actually installed, and fall back to the
# documented name so a missing toolchain fails with a searchable command name
# rather than an empty string.
RISCV_PREFIX_CANDIDATES ?= riscv64-unknown-elf- riscv32-unknown-elf- \
                           riscv64-elf- riscv32-elf- riscv64-linux-gnu-
RISCV_PREFIX ?= $(or \
  $(firstword $(foreach candidate,$(RISCV_PREFIX_CANDIDATES), \
    $(if $(shell command -v $(candidate)gcc 2>/dev/null),$(candidate)))), \
  riscv64-unknown-elf-)
RISCV_PREFIX := $(RISCV_PREFIX)
export RISCV_PREFIX

# ----------------------------------------------------------------- toolchain
# Which toolchain builds target code.
#
# The rule is that **every component is compiled by the one active toolchain**.
# A build where the kernel came from clang and the directed regressions came
# from GCC is not a build of anything: its results belong to a configuration
# nobody selected, and a defect that only one front end emits would be
# attributed to the wrong one.  So RISCV_CC, RISCV_OBJCOPY and RISCV_STRIP are
# the only way target code is built anywhere in the tree -- sim/unit,
# sim/testgen and sim/livefpga included -- and HOST_CXX follows the same knob.
#
# The compiler runtime is the one exception, and deliberately so: libgcc.a and
# libclang_rt.builtins are prebuilt support archives for arithmetic the ISA
# lacks, not components of atomiX.  clang's own `--rtlib=libgcc` is the default
# on most Linux targets for exactly that reason.  See RISCV_RTLIB below.
#
# GCC is the default and stays it: it is what docs/dependencies.md records, what
# every recorded size and fmax number was measured with, and it produces a
# materially smaller image.  LLVM is here because a second front end sees
# defects the first does not -- it found dead code in sw/kernel/console.c that
# GCC 10 does not warn about -- and because it makes clang's own analyzer usable
# against the real target rather than a host approximation of it.
#
#   make -C sw/kernel check-shell TOOLCHAIN=llvm RAM_BYTES=262144
#
# The RAM_BYTES is not incidental.  clang 14 at -O2 emits roughly 45% more text
# for RV32IM than GCC 10 (68,791 vs 47,220 bytes for the default kernel), and
# the page pool is what is left of RAM after the image -- at the default 128 KiB
# the clang build boots and runs but leaves too few free pages for the ABI tests
# to allocate.  That is a size difference, not a miscompilation: the same kernel
# passes at 256 KiB.
TOOLCHAIN ?= gcc

ifeq ($(TOOLCHAIN),gcc)
RISCV_CC      ?= $(RISCV_PREFIX)gcc
RISCV_OBJCOPY ?= $(RISCV_PREFIX)objcopy
RISCV_STRIP   ?= $(RISCV_PREFIX)strip
# The compiler runtime a user program needs for the arithmetic the hardware does
# not do -- 64-bit divide, on RV32IM.
RISCV_RTLIB   ?= -lgcc
else ifeq ($(TOOLCHAIN),llvm)
# clang has to be told the target, and to use lld: with an `-elf` triple it
# otherwise looks for a cross `ld` that a distribution clang package does not
# ship.
CLANG ?= clang
RISCV_CC ?= $(CLANG) --target=riscv32-unknown-elf -fuse-ld=lld
# Ubuntu keeps these under the versioned LLVM directory rather than on PATH.
LLVM_BINDIR   ?= $(firstword $(wildcard /usr/lib/llvm-*/bin) /usr/bin)
RISCV_OBJCOPY ?= $(firstword $(wildcard $(LLVM_BINDIR)/llvm-objcopy) llvm-objcopy)
RISCV_STRIP   ?= $(firstword $(wildcard $(LLVM_BINDIR)/llvm-strip) llvm-strip)
# The compiler runtime -- the arithmetic the ISA does not have, so RV32IM's
# missing 64-bit divide becomes a call to __udivdi3.
#
# This is the one thing that is not a component, and the distinction matters
# because the rule everywhere else is that *every component is compiled by the
# one active toolchain*. A compiler runtime is a prebuilt support archive, not
# a part of atomiX: clang's own `--rtlib=libgcc` is the default on most Linux
# targets for exactly this reason.
#
# Prefer LLVM's when it exists. A distribution clang ships no compiler-rt
# builtins for bare riscv32, so fall back to GCC's libgcc.a for this exact
# -march/-mabi pair, asking GCC where it keeps it rather than hardcoding a path
# that is right on one distribution.
RISCV_COMPILER_RT := $(shell $(CLANG) --target=riscv32-unknown-elf \
                       --rtlib=compiler-rt -print-libgcc-file-name 2>/dev/null)
ifneq ($(wildcard $(RISCV_COMPILER_RT)),)
RISCV_RTLIB   ?= --rtlib=compiler-rt
else
RISCV_LIBGCC_DIR ?= $(dir $(shell $(RISCV_PREFIX)gcc -march=$(RISCV_BASE) \
                      -mabi=ilp32 -print-libgcc-file-name 2>/dev/null))
RISCV_RTLIB   ?= $(if $(RISCV_LIBGCC_DIR),-L$(RISCV_LIBGCC_DIR)) -lgcc
endif
else
$(error TOOLCHAIN must be gcc or llvm, not '$(TOOLCHAIN)')
endif
RISCV_CC      := $(RISCV_CC)
RISCV_OBJCOPY := $(RISCV_OBJCOPY)
RISCV_STRIP   := $(RISCV_STRIP)
RISCV_RTLIB   := $(RISCV_RTLIB)

# Host C++ -- the ISS and the Verilator harness -- follows the same selection,
# so one knob means one toolchain everywhere. An explicit CXX still wins.
ifeq ($(TOOLCHAIN),llvm)
HOST_CXX ?= clang++
else
HOST_CXX ?= g++
endif
HOST_CXX := $(HOST_CXX)
export TOOLCHAIN RISCV_CC RISCV_OBJCOPY RISCV_STRIP RISCV_RTLIB HOST_CXX

# ------------------------------------------------------------------ ISA string
# Zicsr became a separate extension in the ratified ISA: GCC before 12 folds it
# into the base ISA and *rejects* the explicit `_zicsr` spelling, while GCC 12
# and later require it for any CSR instruction.  No single string satisfies
# both, so compile a CSR instruction and keep the spelling that survives.
#
# Probed with the *selected* compiler, not with GCC: clang accepts the `_zicsr`
# spelling that GCC 10 rejects, so the answer is a property of the toolchain in
# use and asking the wrong one produces a string the build cannot compile.
#
# `-x assembler-with-cpp` matters: plain `-x assembler` hands the file straight
# to the assembler without running cc1, and -march validation lives in cc1 --
# so that form reports broken ISA strings as valid.
riscv_probe_arch = $(or $(shell \
  for arch in $(1)_zicsr $(1); do \
    printf '.text\ncsrr t0, mstatus\n' | \
      $(RISCV_CC) -march=$$arch -mabi=ilp32 \
        -x assembler-with-cpp -c - -o /dev/null >/dev/null 2>&1 \
      && { echo $$arch; break; }; \
  done),$(1))

# RISCV_ARCH is the product ISA (RV32IM).  RISCV_ARCH_I is the base-only
# spelling the directed assembly regressions are built with.  Each is probed at
# most once per `make` tree: `?=` skips the probe when a value already arrived
# from the environment or the command line, and `:=` collapses the result so it
# is not re-evaluated on every use.
RISCV_BASE   ?= rv32im
RISCV_ARCH   ?= $(call riscv_probe_arch,$(RISCV_BASE))
RISCV_ARCH   := $(RISCV_ARCH)
RISCV_ARCH_I ?= $(call riscv_probe_arch,rv32i)
RISCV_ARCH_I := $(RISCV_ARCH_I)
export RISCV_ARCH RISCV_ARCH_I

# ------------------------------------------------------------------ Verilator
# Exposed so a Makefile can branch on the 4/5 boundary where their flags or
# warnings differ, and so `make doctor` can report what a build will use.
VERILATOR ?= verilator
VERILATOR_VERSION ?= $(shell $(VERILATOR) --version 2>/dev/null)
VERILATOR_VERSION := $(VERILATOR_VERSION)
VERILATOR_MAJOR := $(shell printf '%s' '$(VERILATOR_VERSION)' | \
                     sed -n 's/^Verilator \([0-9][0-9]*\).*/\1/p')
export VERILATOR_VERSION
