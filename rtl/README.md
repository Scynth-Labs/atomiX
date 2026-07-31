# rtl/ — synthesizable hardware

Synthesizable hardware lives in component folders, with this directory holding
the generic FPGA flow and the architecture signposts for the core, SoC, and
role areas. All authored
SystemVerilog stays within the Yosys-synthesizable subset: synchronous
single-clock design, BRAM-shaped memories, no latches. Everything must pass
both Verilator (simulation) and Yosys (synthesis) — that dual gate is what
keeps us FPGA-portable.

| Subdirectory | Role |
|---|---|
| [../components/core/](../components/core/) | Replaceable `aXcore` implementations |
| [../components/](../components/README.md) | SoC shell, fabric, memory, peripherals, and board implementations |
| [roles/](roles/) | Swappable accelerator roles (loopback, TPU-lite, GPU-compute, gpu1) |
| [soc/](soc/) | SoC architecture notes and integration entry point |
| [fpga/](fpga/) | Generic ECP5 synthesis, P&R, and programming flow |

The reference design is also available through the component catalog in
[`components/`](../components/README.md). A custom source can replace a CPU,
memory backend, stock peripheral, interconnect, cache policy, boot ROM,
simulation finisher, SoC shell, or board target at the build boundary without
requiring a rewrite of unrelated RTL. The intentional private boundaries are
listed in [docs/component-map.md](../docs/component-map.md).

Conventions:

- One module per file, filename = module name.
- No vendor primitives outside a `board` component; inference (BRAM, DSP) over instantiation.
- Every module gets at least a smoke testbench under `sim/` before integration.
