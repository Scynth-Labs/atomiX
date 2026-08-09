# Cross-implementation comparison contract

This contract defines how atomiX compares a fixed CPU, hard accelerator role,
morph fabric, full FPGA image, or partial image against the same versioned
workload.  The machine-readable R2 plan and evidence template live in
[`research/comparisons/`](../research/comparisons/).

It answers one narrow question: what did this implementation cost and deliver
for this exact workload and evidence level?  It does not assign a universal
score to an architecture.

## Comparison rules

1. **Correctness is a hard gate.** Failed or unexecuted oracle cases cannot be
   ranked by performance.
2. **Missing is not zero.** An unavailable measurement is JSON `null` with
   status `org.atomix.unavailable`.  Zero remains a valid measured value, such
   as a design using no DSP blocks.
3. **Execution and end-to-end cost stay separate.** Doorbell-to-done cycles do
   not include upload, checked readback, or configuration transfer.  Total
   cycles do.
4. **Switch latency is end-to-end.** It starts when quiescing is requested and
   ends only after activation and the canary oracle pass.  UART wire time or
   configuration transfer time alone is not reported as switch latency.
5. **Logical work is architecture-neutral.** Each candidate declares a logical
   work unit and count.  Physical lanes, issue width, PE count, folding, and
   pipeline depth are implementation details.
6. **Evidence levels never collapse.** Simulation, synthesis, place-and-route,
   and physical observations are separate namespaced levels.
7. **Resource counts need context.** LUT, FF, block-RAM, and DSP used/available
   pairs are recorded.  Raw LUTs are compared directly only on the same FPGA
   family; fractions help capacity planning but do not make different primitive
   architectures equivalent.
8. **No weighted composite score.** The result is a Pareto table.  A project
   decision may prioritise latency, area, throughput, energy, or recoverability,
   but it must state that policy separately.

## R2 candidates

The initial plan contains matched pairs:

| Personality | Existing baseline | Research candidate | Logical work |
|---|---|---|---|
| scalar recurrence | `core.pipeline5` | morph scalar mode | 64 input items |
| SIMT SAXPY | `role.gpu-compute`, 4 lanes | morph SIMT mode | 50 output elements |
| systolic GEMM | `role.tpu-lite` | morph dataflow mode | 96 output elements from `12x8 * 8x8` |

The workload ID, workload revision, parameters, personality ID, and personality
revision are pinned per candidate.  A later CPU, GPU, TPU, CGRA, LUT fabric, or
out-of-tree component adds another candidate; it does not modify an existing
one.

## Required matrix

Each evidence record has every metric below.  A metric may be unavailable at a
given evidence level, but it may not be silently omitted.

| Metric | Unit | Meaning |
|---|---|---|
| `switch-latency` | ns | quiesce request through activated canary pass |
| `configuration-transfer-latency` | ns | payload transport only; never substituted for switch latency |
| `execute-cycles` | cycles | workload execution boundary only |
| `total-cycles` | cycles | upload/staging, execute, readback, and verification |
| `work-items` | logical items | candidate's declared architecture-neutral work count |
| `clock-frequency` | Hz | clock used for the observation |
| `configuration-bytes` | bytes | transferred personality/image payload |
| `lut-used`, `lut-available` | count | LUT capacity context |
| `flip-flop-used`, `flip-flop-available` | count | register capacity context |
| `block-ram-bits-used`, `block-ram-bits-available` | bits | memory capacity independent of vendor block naming |
| `dsp-used`, `dsp-available` | count | hard arithmetic capacity context |
| `maximum-frequency` | Hz | post-route Fmax, not requested clock |
| `total-energy` | joules | complete workload boundary; unavailable until measured |

Cycles per work item, time, throughput, utilisation fractions, and energy per
item are derived from these primitives.  They are not independently entered,
which prevents internally inconsistent evidence.

## Evidence identity

An observation pins:

- comparison plan and candidate ID;
- source commit, dirty state, and optional diff hash;
- evidence level, timestamp, board/device identity, and tool versions;
- transition source, mechanism, and whether the management shell was retained;
- workload/personality identities inherited from the plan;
- correctness status, oracle case count, and output SHA-256;
- every required metric with value, unit, status, and method.

An evidence file with `claim: org.atomix.template` is explicitly not a result.
An `org.atomix.observation` must identify its source and say `pass` or `fail`.
Only a passing observation can enter the Pareto comparison.

Metric keys, units, candidate classes, evidence levels, and selectors are
namespaced.  The base validator therefore does not contain a CPU/GPU/TPU vendor
allow-list.  Format-specific tooling may add metrics, but a plan must declare
them before an observation can use them.

## Validation

```bash
make comparison-check
python3 tools/comparison_contract.py check research/comparisons
```

The self-test verifies that an external candidate class and selector are
accepted, unavailable is distinct from measured zero, incorrect units are
rejected, and a non-passing observation cannot be ranked.
