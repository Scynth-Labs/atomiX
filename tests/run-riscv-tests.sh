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
cd "$(dirname "$0")" || exit
sim=${sim:-../sim/axsim/axsim}
globs=("${@:-rv32ui rv32mi}")
[[ $# -eq 0 ]] && globs=(rv32ui rv32mi)

# Policy exclusions live in one file shared with sim/unit/run_ax2_isa.sh, so
# the ISS/cosim runs and the RTL core suites cannot disagree about what is
# deliberately not implemented. Each entry is justified there.
mapfile -t exclude < <(sed 's/#.*//' isa-exclusions.txt | tr -d ' \t' | grep .)

pass=0 fail=0
failed=()
for suite in "${globs[@]}"; do
  # A suite named like "rv32ui-v" selects the virtual-memory environment
  # binaries; a plain suite name selects the physical "-p" ones.
  pat="$suite-p-*"
  [[ $suite == *-v ]] && pat="${suite%-v}-v-*"
  before=$((pass + fail))
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
  # An unbuilt or renamed suite must not look like a clean run. Without this a
  # glob that matches nothing reports "0 passed, 0 failed" and exits 0, so the
  # whole job goes green having verified nothing.
  if (( pass + fail == before )); then
    echo "riscv-tests: suite '$suite' matched no binaries under riscv-tests/isa/" >&2
    echo "  build them first (see the isa-suite job in .github/workflows/nightly.yml)" >&2
    exit 1
  fi
done

echo "riscv-tests: $pass passed, $fail failed"
((fail > 0)) && printf '  FAIL: %s\n' "${failed[@]}"
exit $((fail > 0))
