# Live FPGA: adaptive reconfiguration research

“Live FPGA” is the atomiX name for a closed-loop system that observes its own
execution, evaluates bounded alternatives, activates a verified candidate, and
rolls back when correctness or safety fails.  The initial implementation is
L0 observation.  It does not yet claim autonomous improvement.

## Immutable boundary

Telemetry, role isolation, reset, UART recovery, the watchdog, and the
configuration-generation counter belong to the management shell.  They must
not be synthesized into the role that will be replaced.  Otherwise a candidate
could erase its history, hide a failure, or damage the recovery mechanism.

`axlivemon` is therefore a shell component with generic event inputs.  It has
no knowledge of Gowin, ECP5, AMD DFX, an accelerator ISA, or the eventual morph
fabric.  Hard roles, overlay programs, and native partial loaders can feed the
same events.

## L0 telemetry schema, version 1.0

The reference shell maps the monitor into the existing shell-control page at
`0x1002_0100`.  It remains reachable while the role window at `0x4000_0000` is
isolated.

| Offset | Name | Access | Meaning |
|---:|---|---|---|
| `0x00` | `LIVE_ID` | RO | `0x61584c56` (`aXLV`) |
| `0x04` | `LIVE_VERSION` | RO | `0x00010000` (major 1, minor 0) |
| `0x08` | `LIVE_COMMAND` | WO | `1` snapshot, `2` record verified activation |
| `0x0c` | `LIVE_SEQUENCE` | RO | snapshot sequence, modulo 2^32 |
| `0x10` | `CYCLES` | RO, 64-bit | shell clocks since reset |
| `0x18` | `WORK_COMPLETED` | RO, 64-bit | rising completion events |
| `0x20` | `MEMORY_STALLS` | RO, 64-bit | cycles with a waiting role transaction |
| `0x28` | `DESCRIPTOR_REJECTIONS` | RO, 64-bit | explicit rejected-candidate/job events |
| `0x30` | `WATCHDOG_EVENTS` | RO, 64-bit | watchdog firings |
| `0x38` | `CONFIGURATION_GENERATION` | RO, 64-bit | verified activation commands |

Each 64-bit value occupies low then high 32-bit words.  Reads return the last
snapshot, not live counters, so a 32-bit CPU cannot observe a torn value.  A
snapshot includes its clock edge and every event asserted on that edge.  Live
counters continue running afterward.  Counters wrap modulo 2^64; consumers use
modular deltas between snapshots.

Version 1 is a minimum observation set, not a closed event registry.  A future
sensor block publishes its own namespaced schema/version at another aligned
offset; it does not repurpose these counters or require every role to adopt one
vendor's telemetry model.

`ACTIVATE` is intentionally explicit.  Isolation changes, reset release, UART
bytes, or a bitstream transfer do not imply a successful new generation.  The
management path records activation only after integrity checks, role discovery,
and the canary workload have passed.  The current project has no automatic
writer yet, so a generation increment is not evidence of partial
reconfiguration by itself.

## Current event wiring

- `CYCLES` is counted unconditionally outside reset.
- `WORK_COMPLETED` is derived from a rising role completion interrupt while
  the role is not isolated.
- `MEMORY_STALLS` counts any clock with a valid role-window transaction waiting
  for `ready`; simultaneous instruction/data stalls count as one stalled cycle.
- descriptor rejection and watchdog are explicit shell inputs.  The reference
  SoC ties them low until those producers land; the unit tests drive both.
- configuration generation comes only from `LIVE_CMD_ACTIVATE`.

This distinction prevents a generic bus error from being mislabeled as a
rejected adaptive candidate.

## What this enables next

The next Live FPGA item is a deterministic fitness record.  It will take two
snapshots around a versioned workload, require its correctness oracle to pass,
then derive cycles/work item, stall rate, rejection count, watchdog count, and
generation identity.  Performance can rank correct candidates; it can never
turn an incorrect candidate into an improvement.

## Evidence

```bash
make live-check
```

The first test proves exact event and snapshot semantics, including simultaneous
events, snapshot stability, and reset priority.  The second proves the monitor
is reachable through the immutable shell page alongside a role that can be
isolated.
