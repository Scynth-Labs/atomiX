#!/usr/bin/env bash
# Build the riscv-tests ISA binaries atomiX runs against.
#
# usage: build-isa.sh [suite ...]        (default: rv32ui rv32mi rv32um)
#
# The binaries are build products of the tests/riscv-tests submodule, not
# checked-in files, so every job that runs an ISA suite has to build them
# first.  This lives in one script because both .github/workflows/nightly.yml
# and sim/unit/Makefile need it, and a second copy of the target-name logic
# would be one more thing to drift.
#
# The suite's own default target also builds the -v (Sv32 demand-paging) and
# rv32uc variants, which need libc headers the Ubuntu cross package hides
# inside picolibc, so the -p targets are named explicitly -- see tests/README.md.
set -eu
cd "$(dirname "$0")"

if [[ ! -f riscv-tests/isa/Makefile ]]; then
  echo "build-isa: tests/riscv-tests is empty -- the submodule is not checked out" >&2
  echo "  git submodule update --init --recursive tests/riscv-tests" >&2
  exit 1
fi

# Same toolchain probe as sim/unit/run_core_isa.sh: the tuple differs by
# distribution, so honour RISCV_PREFIX and otherwise take the first installed
# candidate rather than assuming the Debian/Ubuntu name.
if [[ -z ${RISCV_PREFIX:-} ]]; then
  for candidate in riscv64-unknown-elf- riscv32-unknown-elf- riscv64-elf- \
                   riscv32-elf- riscv64-linux-gnu-; do
    if command -v "${candidate}gcc" >/dev/null 2>&1; then
      RISCV_PREFIX=$candidate
      break
    fi
  done
  RISCV_PREFIX=${RISCV_PREFIX:-riscv64-unknown-elf-}
fi

suites=("${@:-rv32ui rv32mi rv32um}")
[[ $# -eq 0 ]] && suites=(rv32ui rv32mi rv32um)

targets=()
for suite in "${suites[@]}"; do
  for src in riscv-tests/isa/"$suite"/*.S; do
    [[ -f $src ]] || continue
    targets+=("$suite-p-$(basename "$src" .S)")
  done
done

if [[ ${#targets[@]} -eq 0 ]]; then
  echo "build-isa: no sources found for: ${suites[*]}" >&2
  exit 1
fi

echo "[isa] building ${#targets[@]} test binaries (${suites[*]})"
make -C riscv-tests/isa XLEN=32 RISCV_PREFIX="$RISCV_PREFIX" "${targets[@]}"
