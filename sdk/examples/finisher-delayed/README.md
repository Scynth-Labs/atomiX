# Delayed finisher external-component example

This package is the smallest complete atomiX component onboarding example. It
implements the stock `test_finisher` SystemVerilog boundary, but deliberately
waits before acknowledging an aXbus access. Its manifest, RTL, profiles,
license, compatibility claim, and conformance runner stay together, so the
directory can be copied outside an atomiX checkout unchanged.

Run it from an atomiX checkout with Python, Make, and a supported Verilator:

```bash
python3 sdk/examples/finisher-delayed/check.py --atomix-root "$PWD"
```

The runner copies this directory to a temporary location outside the source
tree before resolving or compiling it. It then checks the manifest's default
`ack_delay_cycles=1`, the non-default value `4`, and the exact successful
completion latency of each. It also requires a precise rejection for the
out-of-range value `17` and refuses the supplied Tang Primer 25K profile: this
component is a simulation endpoint, and no simulation result is physical-board
evidence.

## Boundary and compatibility

The implementation supplies the `test_finisher` module instantiated by
`soc.reference`. The port list is the contract: independent instruction and
data aXbus request/response channels plus `finished` and `exit_code`. A master
must retain its request fields until `ready`, as required by aXbus. Writes of
`0x5555` or `0x7777` pass; `0x3333` uses the upper halfword as a failure code.

The runnable evidence covers only this combination:

- `soc.reference`
- `board.sim`
- `harness.verilator-soc`
- `core.finisher-smoke`

Other cores may use the same port boundary, but have not earned this package's
conformance claim. FPGA boards are intentionally unsupported: the endpoint is
only observed by a simulation harness. The generic resolver remains lenient;
the component-owned conformance runner interprets the manifest's compatibility
scope and rejects combinations outside it.

## Parameters and migration

`ack_delay_cycles` accepts integers from 0 through 16. Zero gives a
combinational acknowledgment; positive values hold `ready` low for that many
whole cycles. The manifest default is part of this package's behavior and its
range is enforced by the resolver and again in RTL for direct compilation.

Consumers migrate by preserving the module name and complete port behavior.
Adding an optional implementation knob is backward compatible when its default
retains prior behavior. Renaming/removing a port, changing the request-hold
rule, or changing pass/fail encoding is a contract break and requires a new
component ID plus new compatibility evidence. Adding support for another SoC,
harness, core, or board requires widening `compatibility.requires` and adding
that combination to `check.py`; editing the generic SoC to conceal an
incompatible component is not migration.

This package is MIT licensed; see [LICENSE](LICENSE). It is an SDK example, not
a portability or verification claim for unrelated external components.
