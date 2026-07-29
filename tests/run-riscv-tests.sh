#!/usr/bin/env bash
# Run the riscv-tests ISA suites (p environment) against a simulator.
# Usage: run-riscv-tests.sh [suite-glob ...]   (default: rv32ui rv32mi)
# SIM=<path> overrides the simulator (default: aXsim).
set -u
# Resolve SIM before changing directory: a relative path the caller gave is
# relative to where they ran this, not to tests/. Without this, running the
# documented `SIM=sim/cosim/obj_dir/axcosim tests/run-riscv-tests.sh` from the
# repository root reports every test as failed rather than as not-found.
sim=${SIM:-}
[[ -n $sim && $sim != /* ]] && sim="$PWD/$sim"
cd "$(dirname "$0")"
sim=${sim:-../sim/axsim/axsim}
globs=("${@:-rv32ui rv32mi}")
[[ $# -eq 0 ]] && globs=(rv32ui rv32mi)

# Policy exclusions, not failures:
#   rv32ui-p-ma_data expects HARDWARE misaligned data access support; atomiX
#   (like Spike without --misaligned) traps on misaligned accesses instead —
#   the complementary rv32mi-p-ma_addr test verifies those traps and must pass.
exclude=(rv32ui-p-ma_data rv32ui-v-ma_data)

pass=0 fail=0
failed=()
for suite in "${globs[@]}"; do
  # A suite named like "rv32ui-v" selects the virtual-memory environment
  # binaries; a plain suite name selects the physical "-p" ones.
  pat="$suite-p-*"
  [[ $suite == *-v ]] && pat="${suite%-v}-v-*"
  for t in riscv-tests/isa/$pat; do
    [[ $t == *.dump || ! -f $t ]] && continue
    [[ " ${exclude[*]} " == *" $(basename "$t") "* ]] && continue
    if "$sim" --bin "$t" --max 4000000 >/dev/null 2>&1; then
      pass=$((pass + 1))
    else
      fail=$((fail + 1))
      failed+=("$(basename "$t")")
    fi
  done
done

echo "riscv-tests: $pass passed, $fail failed"
((fail > 0)) && printf '  FAIL: %s\n' "${failed[@]}"
exit $((fail > 0))
