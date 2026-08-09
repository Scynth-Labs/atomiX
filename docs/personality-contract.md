# Reconfigurable compute-personality contract

This document defines the research contract between a workload, a compute
personality, and any fabric capable of hosting it.  The checked-in examples
live in [`research/personalities/`](../research/personalities/).

The contract is deliberately not a GPU ISA, TPU command format, FPGA vendor
bitstream, or fixed CGRA topology.  It standardises discovery and meaning while
leaving implementation replaceable.

## Design rules

1. **Meaning is separate from mechanism.** A workload defines exact inputs,
   arithmetic, outputs, and an oracle.  A personality describes requirements
   and may carry multiple implementation encodings.
2. **Logical shape is separate from physical width.** Thread count is not lane
   count; matrix dimensions are not MAC count.  A fabric may fold, pipeline, or
   widen an implementation without changing the result.
3. **Capabilities are namespaced strings.** No central company or atomiX enum
   gets to reserve the design space.  Examples use `org.atomix.*`; an external
   project can use a domain it controls.
4. **Encodings are replaceable.** `implementations[]` is a set of alternatives,
   not a preferred-vendor list.  A loader selects any encoding whose required
   capabilities it satisfies.
5. **Safety stays outside the personality.** Clock/reset, the management CPU,
   UART recovery, isolation, watchdog, and rollback remain shell-owned.
6. **Evidence is portable.** Correctness is assessed through the workload
   contract before cycles, area, frequency, or energy are compared.

## Files and versioning

There are two JSON document kinds:

- `org.atomix.personality`: requirements, tunable values, reconfiguration
  policy, workload bindings, and one or more implementation encodings;
- `org.atomix.workload`: semantic operation, parameters, typed buffers, oracle,
  exact test cases, and comparable metrics.

Each document carries:

```json
"schema": {"id": "org.atomix.personality", "major": 1, "minor": 0}
```

A major change may be incompatible.  Minor changes must remain additive and
must fit under `extensions`; a version-1 reader ignores extension entries it
does not use.  `revision` identifies a new immutable revision of one document
ID.  Changing an existing revision in an evidence record is forbidden; use a
new revision and content hash.

The JSON form is the build/research interchange format, not the runtime wire
format.  A future compact wire encoding may use TLVs or another container, but
it must preserve these semantics, length-prefix records, skip unknown optional
records, and reject unknown required capabilities.  Deferring those bytes
prevents the first prototype's memory geometry from becoming the platform ABI.

## Personality document

Required fields are:

| Field | Meaning |
|---|---|
| `id`, `revision`, `summary` | stable namespaced identity and immutable revision |
| `execution_model` | descriptive model such as `org.atomix.scalar`, not a dispatch enum |
| `requires` | capabilities every compatible fabric must advertise |
| `prefers` | optional capabilities used only for selection or optimisation |
| `parameters` | named values plus when each can change |
| `reconfiguration` | scope, quiesce, state, and rollback policy |
| `workload_bindings` | workload IDs and the dimensions used for comparison |
| `implementations` | replaceable configuration/IR encodings |
| `extensions` | namespaced optional data |

Parameter `mutability` is namespaced rather than an enum.  The initial values
are `org.atomix.per-job`, `org.atomix.load-time`, and
`org.atomix.build-time`.  A fabric may add a policy without changing schema.

An implementation entry contains a namespaced `format`, its own version,
format-specific requirements, and a `payload`.  The base validator treats the
payload as opaque.  Consequently, adding a dataflow graph, instruction stream,
e-graph, LUT truth tables, or an out-of-tree compiler format does not change the
envelope.  Format-specific validation belongs to that format's component.

Compatibility is set-based:

```text
personality.requires subset-of fabric.capabilities
implementation.requires subset-of fabric.capabilities
```

`prefers` ranks compatible choices but cannot make an otherwise compatible
fabric fail.  No implementation is selected merely because it appears first.

## Workload document

A workload fixes the semantics used to compare implementations:

- parameter values define logical problem dimensions;
- buffers define direction, element representation, logical shape, and layout;
- the oracle identifies exact arithmetic and supplies deterministic cases;
- metrics define what must be measured without prescribing how it is achieved.

The first contracts are:

| Workload | Purpose | Required result |
|---|---|---|
| `scalar-recurrence-i32` | dependent scalar ALU/control chain | 32-bit two's-complement wrap after every multiply/add step |
| `saxpy-i32` | independent logical threads for 4-lane SIMT experiments | `out[i] = a*x[i] + y[i]`, with 32-bit wrap and tail safety |
| `gemm-i8-i32` | systolic/dataflow matrix tile | signed int8 products accumulated into wrapping signed int32 outputs |

The comparison bindings use the existing atomiX evidence shapes where useful:
50 logical SAXPY threads and a `12x8 * 8x8` GEMM.  These are logical shapes.
They do not require 50 lanes, 64 MACs, a warp size, tensor-core tiles, or any
particular memory banking scheme.

## Reconfiguration transaction

A future loader follows one generic transaction for every encoding:

1. discover fabric capabilities and limits;
2. validate the descriptor and select a compatible encoding;
3. quiesce the role and assert shell-owned isolation;
4. snapshot or discard role state according to the descriptor;
5. load into an inactive/staging configuration and verify integrity;
6. activate it, run its canary workload, and expose the new generation;
7. roll back to the last-known-good generation on timeout or oracle failure.

The descriptor cannot request mutation of the shell or weaken these steps.
Native FPGA partial reconfiguration, overlay microcode loading, and ordinary
parameter writes are different implementations of the same transaction.

## Validation

The executable schema uses only Python's standard library:

```bash
make personality-check
python3 tools/personality_contract.py check research/personalities
```

It checks structure, namespaces, references, duplicate IDs, and the supplied
exact oracle cases.  Its self-test also proves that an unknown namespaced
capability, encoding, execution model, and extension are accepted while an
unnamespaced capability is rejected.  This is the regression against silently
turning the contract into a closed implementation list.

