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

Run `make live-sim-check` for the complete hardware-free chain, ending in a
closed-loop virtual FPGA that drives the actual bounded C fitness/evolution
components through valid, incorrect, watchdog, canary-failure, and rollback
scenarios. The simulator cannot actuate real hardware; its manager boundary is
explicit and deterministic.
