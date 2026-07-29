#!/usr/bin/env bash
# Run the official riscv-tests ISA suites against an ax2 tier on the RTL.
#
# usage: run_ax2_isa.sh SIMULATOR [suite ...]      (default suites: rv32ui rv32um)
# WS=N inserts N bus wait states on every access, which exercises the cache
# refill and execute-stage stall paths that a zero-latency memory never reaches.
#
# Only the "-p" (physical, machine-mode) environments are run: ax2 implements
# machine mode with physical addressing, so the "-v" virtual-memory binaries are
# out of scope by design, not by omission.
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
suites=("${@:-rv32ui rv32um}")
[[ $# -eq 0 ]] && suites=(rv32ui rv32um)

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Policy exclusion, matching tests/run-riscv-tests.sh: ma_data expects hardware
# misaligned-access support, which atomiX traps on instead.  The complementary
# rv32mi-p-ma_addr test verifies those traps and is expected to pass.
exclude=(rv32ui-p-ma_data)

pass=0 fail=0
failed=()
for suite in "${suites[@]}"; do
  for t in ../../tests/riscv-tests/isa/$suite-p-*; do
    [[ $t == *.dump || ! -f $t ]] && continue
    name=$(basename "$t")
    [[ " ${exclude[*]} " == *" $name "* ]] && continue
    # Each binary reports through its own `tohost` symbol; find it rather than
    # assuming a fixed address.
    th=$("${RISCV_PREFIX}nm" "$t" 2>/dev/null | awk '$3=="tohost"{print "0x"$1}')
    [[ -z $th ]] && continue
    "${RISCV_PREFIX}objcopy" -O binary "$t" "$work/$name.bin"
    if "$sim" "$work/$name.bin" --tohost "$th" --ws "${WS:-0}" \
         --max 8000000 >/dev/null 2>&1; then
      pass=$((pass + 1))
    else
      fail=$((fail + 1))
      failed+=("$name")
    fi
  done
done

echo "ax2 riscv-tests (ws=${WS:-0}): $pass passed, $fail failed"
((fail > 0)) && printf '  FAIL: %s\n' "${failed[@]}"
exit $((fail > 0))
