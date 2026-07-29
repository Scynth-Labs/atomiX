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

# ------------------------------------------------------------------ ISA string
# Zicsr became a separate extension in the ratified ISA: GCC before 12 folds it
# into the base ISA and *rejects* the explicit `_zicsr` spelling, while GCC 12
# and later require it for any CSR instruction.  No single string satisfies
# both, so compile a CSR instruction and keep the spelling that survives.
#
# `-x assembler-with-cpp` matters: plain `-x assembler` hands the file straight
# to the assembler without running cc1, and -march validation lives in cc1 --
# so that form reports broken ISA strings as valid.
riscv_probe_arch = $(or $(shell \
  for arch in $(1)_zicsr $(1); do \
    printf '.text\ncsrr t0, mstatus\n' | \
      $(RISCV_PREFIX)gcc -march=$$arch -mabi=ilp32 \
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
