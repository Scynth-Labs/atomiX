#!/usr/bin/env bash
# Run the official riscv-tests ISA suites against an M-mode core on the RTL.
# Any core whose top module presents the axcore ibus/dbus interface works;
# core.ax2 and core.minimal both use this.
#
# usage: run_core_isa.sh SIMULATOR [suite ...]  (default: rv32ui rv32mi rv32um)
#
# rv32mi is not optional decoration: it is the suite that exercises the trap
# and CSR surface (WARL fields, misaligned fetch, counters), which is where a
# core that passes every rv32ui test can still be wrong.
# WS=N inserts N bus wait states on every access, which exercises the cache
# refill and execute-stage stall paths that a zero-latency memory never reaches.
# LABEL=name prefixes the result line, so a sweep says which core it ran.
#
# Only the "-p" (physical, machine-mode) environments are run: these cores
# implement machine mode with physical addressing, so the "-v" virtual-memory
# binaries are out of scope by design, not by omission.
set -u
cd "$(dirname "$0")"

# The cross-toolchain tuple differs by distribution, so honour RISCV_PREFIX the
# way the Makefiles do (mk/toolchain.mk exports it) and otherwise find the
# first installed candidate rather than assuming the Debian/Ubuntu name.
if [[ -z ${RISCV_PREFIX:-} ]]; then
  for candidate in riscv64-unknown-elf- riscv32-unknown-elf- riscv64-elf- \
                   riscv32-elf- riscv64-linux-gnu-; do
    if command -v "${candidate}nm" >/dev/null 2>&1; then
      RISCV_PREFIX=$candidate
      break
    fi
  done
  RISCV_PREFIX=${RISCV_PREFIX:-riscv64-unknown-elf-}
fi
sim=$1; shift
suites=("${@:-rv32ui rv32mi rv32um}")
[[ $# -eq 0 ]] && suites=(rv32ui rv32mi rv32um)

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Policy exclusions live in one file shared with tests/run-riscv-tests.sh, so
# this runner and the ISS/cosim one cannot disagree about what is deliberately
# not implemented. Each entry is justified there.
mapfile -t exclude < <(sed 's/#.*//' ../../tests/isa-exclusions.txt |
                       tr -d ' \t' | grep .)

pass=0 fail=0
failed=()
for suite in "${suites[@]}"; do
  before=$((pass + fail))
  for t in ../../tests/riscv-tests/isa/$suite-p-*; do
    [[ $t == *.dump || ! -f $t ]] && continue
    name=$(basename "$t")
    [[ " ${exclude[*]} " == *" $name "* ]] && continue
    # Each binary reports through its own `tohost` symbol; find it rather than
    # assuming a fixed address. A binary without one cannot report a result, so
    # it counts as a failure rather than being skipped into silence.
    th=$("${RISCV_PREFIX}nm" "$t" 2>/dev/null | awk '$3=="tohost"{print "0x"$1}')
    if [[ -z $th ]]; then
      fail=$((fail + 1)); failed+=("$name (no tohost symbol)"); continue
    fi
    "${RISCV_PREFIX}objcopy" -O binary "$t" "$work/$name.bin"
    if "$sim" "$work/$name.bin" --tohost "$th" --ws "${WS:-0}" \
         --max 8000000 >/dev/null 2>&1; then
      pass=$((pass + 1))
    else
      fail=$((fail + 1))
      failed+=("$name")
    fi
  done
  # An unbuilt or renamed suite must not look like a clean run; see the same
  # guard in tests/run-riscv-tests.sh.
  if (( pass + fail == before )); then
    echo "${LABEL:-core} riscv-tests: suite '$suite' matched no binaries" >&2
    echo "  build them with: make -C tests/riscv-tests/isa XLEN=32 ..." >&2
    exit 1
  fi
done

echo "${LABEL:-core} riscv-tests (ws=${WS:-0}): $pass passed, $fail failed"
((fail > 0)) && printf '  FAIL: %s\n' "${failed[@]}"
exit $((fail > 0))
