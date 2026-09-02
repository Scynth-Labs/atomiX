# Live FPGA fitness records

Records in this directory preserve the exact two-snapshot input, oracle result,
derived deltas, rational metrics, and compact record passed to the selected
kernel evolution service. They are research evidence, not deployment approval.

Validate them with:

```bash
make fitness-check
```

`policy/l1-reviewed-gpu.json` is the L1 allow-list and example decision over
the two runtime programs used by `axhost --fast-switch`. Its result can only be
`hold`, `propose`, or `no-candidate`; actuation is explicitly unauthorized.

```bash
make policy-check
```

`registry/reviewed-gpu.json` assigns each reviewed program an immutable
`sha256:` identity over its artifact, source/profile hashes, tool versions, and
lineage. Its evidence and deployment references are separately content
addressed under `evidence/` and `deployments/`; adding a new observation does
not rename the constructed candidate. The physical evidence and deployment
records capture 30 exact-output passes on Tang Primer 25K, recovery coverage,
and the exact volatile runtime and kernel hashes without storing build output.

```bash
make registry-check
```

Run `make live-sim-check` for the complete hardware-free chain, ending in a
closed-loop virtual FPGA that drives the actual bounded C fitness/evolution
components through valid, incorrect, watchdog, canary-failure, and rollback
scenarios. The simulator cannot actuate real hardware; its manager boundary is
explicit and deterministic.

`l3/morph-search-space.json` bounds L3 adaptation to the two PE-descriptor
words of the 13-word `role.morph` genome.  The checked result compares a
complete lexicographic traversal, a fixed-seed full permutation, and greedy
coordinate descent on the exact scalar, SIMT, and systolic RTL-reference
workloads plus one deterministic canary each.  It is proposal evidence only:
the optimizer never receives actuation authority and the reviewed genomes
remain the rollback targets.  `l3/morph-rtl-trial.json` pins searched
non-reference scalar, SIMT, and systolic aliases into candidate-specific RTL
shadow runs over both deterministic cases for each mode.  It permits a
testbench-manager-only scalar volatile trial after those gates, injects a
deceptive genome that only the canary catches, and verifies rollback.  The
contract also mutation-tests authority, content identity, the mutable-word
boundary, oracle digests, descriptor binding, and workload completeness.  This
is simulation evidence, not physical-board or autonomous-deployment evidence.

```bash
make l3-check
```
