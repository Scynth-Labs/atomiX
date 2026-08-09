# sim/ — simulation and verification infrastructure

The non-synthesizable half of the project: the golden model, the lock-step
cosimulation rig, and test generation. This is where "correct" gets defined
and enforced (DESIGN.md §6).

| Subdirectory | Role |
|---|---|
| [axsim/](axsim/) | `aXsim` — our instruction-set simulator, the golden model |
| [cosim/](cosim/) | Verilator harness comparing RTL against aXsim per retired instruction |
| [livefpga/](livefpga/) | Closed-loop virtual FPGA exercising the real fitness/evolution components and fault gates |
| [soc/](soc/) | Complete `soc_top` runner with `$readmemh`-initialized RAM |
| [testgen/](testgen/) | Random RISC-V instruction-stream generator for cosim fuzzing |
| [unit/](unit/) | Directed RTL block and SoC integration tests |

Three-platform rule: all software must run unchanged on aXsim, QEMU
(`-machine virt`), and the Verilated RTL — that's how "software bug" is
isolated from "hardware bug".

Use [docs/workflow.md](../docs/workflow.md) for the recommended test order and
[docs/design-checklist.md](../docs/design-checklist.md) for current evidence.
