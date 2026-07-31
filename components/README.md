# atomiX component catalog

This directory makes atomiX a DIY platform rather than a collection of
hard-wired implementation choices. Each `kind/implementation/component.json`
file describes a selectable implementation. Built-in RTL, harness, board, and
aXos-service assets live beside their owning component; an external manifest
may instead keep its sources in its own portable folder. Software components
remain project roots because their build contract includes startup code,
linker scripts, and produced images. The manifest, not a particular build
directory, is the integration boundary.

The built-in selections cover the verified five-stage CPU and two alternative
RISC-V cores (`core.minimal`, the compact multi-cycle accelerator host, and
`core.ax2`, the dual-issue superscalar family), the reference SoC shell, three
reference memory modes, three cache policies, standard peripherals including
the CLINT and PLIC, accelerator roles, simulation/ULX3S/Tang Nano/Tang Primer
boards and harnesses, SoC infrastructure, and aXos service policies.
`core.finisher-smoke` is a deliberately tiny,
non-RISC-V composition example: it proves an alternate CPU source can replace
the reference core, but is explicitly not a software-compatible CPU.

## Everyday use

```bash
make component-list
make component-show COMPONENT=memory.sdram
make config-check CONFIG=configs/sim-sdram.json
make sim CONFIG=configs/sim-bram.json \
  RAM_INIT_FILE="$PWD/sw/baremetal/build/hello.hex"
make fpga CONFIG=configs/ulx3s-85f.json
make -C sw/kernel kernel-config KERNEL_CONFIG=../../configs/kernel-cooperative.json
make component-show COMPONENT=cache.passthrough
```

`configs/` contains ready-to-run profiles. `sim-bram`, `sim-delayed`, and
`sim-sdram` select the same SoC with progressively more realistic backing
memory. `ulx3s-85f` selects the ECP5 board target.

## Lenient contracts

Manifests do **not** enumerate an ISA, pipeline structure, cache geometry,
memory policy, or verification status as a condition of selection. Those are
descriptive capabilities only. `tools/configure.py` validates component IDs and
source paths, but accepts partial or non-stock compositions too. It also
passes through custom component kinds (for example `gpio` or `debug_bridge`) as
`COMPONENT_<KIND>_*` Make variables. The stock simulation/FPGA Makefiles
naturally need the plug-ins they instantiate; non-stock designs may use their
own top/harness. Unknown configuration settings are preserved as
`COMPONENT_SETTING_*` Make variables instead of rejected.

The only contract imposed by the stock integration is at the point a chosen
module is plugged in:

| Selection | Stock integration module | Purpose |
|---|---|---|
| `core` | `axcore` | instruction/data aXbus masters, interrupt inputs (software, timer, machine-external, and supervisor-external — a machine-mode-only core accepts the last and ignores it), and the small commit signal set used for `fence.i` |
| `alu` | `alu` | combinational RV32I integer operations inside the reference core's EX stage |
| `muldiv` | `muldiv` | RV32M execution unit behind a latency-tolerant `start/busy/done` handshake |
| `regfile` | `regfile` | 2R1W register file, x0 hardwired, write-before-read bypass |
| `mmu` | `sv32_mmu` | per-port translation with the aXbus completion protocol plus a page-fault flavor |
| `memory` | `axmem` | independent instruction/data aXbus RAM ports plus optional SDRAM pins |
| `interconnect` | `axbus_mux` | one aXbus master decode and response selection |
| `cache` | `axcache` | optional core-port cache or transparent forwarding policy |
| `rom` | `axrom` | dual-port boot ROM |
| `finisher` | `test_finisher` | simulation termination endpoint |
| `uart` | `uart` | UART0 aXbus device and byte-level RX/TX sideband |
| `clint` | `clint` | CLINT aXbus device plus software/timer IRQs |
| `plic` | `plic` | external-interrupt controller: per-source priority/enable/threshold, claim-complete, a level-sensitive `sources` input driven by the shell's devices, and one `irq_external` delivery line per target context |
| `spi` | `axspi` | SPI0 aXbus device and four SPI pins |
| `role` | `axrole` | accelerator in the fixed 64 KiB window at `0x4000_0000`: `ROLE_ID`/`VERSION`/`DOORBELL`/`STATUS` header, then role-defined registers and windows, plus a level-sensitive `irq` line held while `STATUS.DONE` stands |
| `soc` | `soc_top` | complete-SoC simulation/board shell |
| `software` | its own Make target and produced image(s) | payload built independently, then supplied to the selected hardware profile |
| `harness` | selected simulation top + C++ testbench | simulation environment kept separate from board RTL |
| `scheduler` | `scheduler_select` | choose a runnable aXos task; context switching and syscalls stay in the kernel |
| `vm` | `vm_*` | bootstrap mappings and the user part of an aXos task address space |
| `allocator` | `page_*` | physical-page allocation policy |
| `shell` | `shell_run` | aXos console command loop |
| `filesystem` | `fs_*` | mounted filesystem policy: name lookup and byte-range reads, shared by the shell and the `read` syscall |
| `block` | `sd_*` | block-device transport |

The real SystemVerilog instantiation is the authoritative port list; no second
hand-maintained IDL can drift from it. A user who wants a different boundary
can select a custom `soc` component, use a custom test harness, or build their
own board top. The component system deliberately does not fence them in.

A manifest may declare `defaults`, naming the implementation it expects for
kinds a profile leaves unselected. `core.pipeline5` defaults its four
functional units (`alu.single-cycle`, `muldiv.iterative32`,
`regfile.flipflop`, `mmu.sv32`), so existing profiles select a complete CPU
with one line while a one-line addition swaps a single unit —
`configs/sim-fastmul.json` does exactly that, selecting the
single-cycle-multiply `muldiv.fast-mul` unit. `soc.reference` likewise
defaults `role` to `role.none` (the role window reads `ROLE_ID` = 0) and
`plic` to `plic.qemu-virt`, so a profile inherits a working interrupt
controller without naming one; `configs/sim-role-loopback.json` swaps in
`role.loopback`, the accelerator contract proof driven by
`make -C sw/baremetal check-role` and `check-role-irq`. An explicit
selection always wins over a default, and if two selected components default
the same kind differently the resolver requires the profile to choose.
A swapped functional unit re-runs through the same lock-step cosimulation,
ISA suite, and riscv-formal gates as the reference core — stronger evidence
than any coarser replacement gets — but the swap must still earn that
evidence itself; selection alone claims nothing.

Software has no source-level ABI requirement. A manifest names its own Make
directory/target and whether it produces a RAM hex image or an SD-boot payload.
`make software CONFIG=configs/sim-hello.json` runs the supplied bare-metal
image; `make software CONFIG=configs/sim-axos.json` builds aXos and boots it
through the selected SDRAM/SD profile. An external kernel may use the same
small manifest fields while keeping all of its sources and build rules outside
atomiX.

For the supplied aXos kernel, `KERNEL_CONFIG` selects the scheduler, VM,
allocator, shell, filesystem, and block components independently of the system
profile. The default is
`configs/kernel-default.json`; `configs/kernel-cooperative.json` is a working
alternative policy that retains a task until it blocks or exits. An external
component may include `sw/kernel/include/scheduler.h` or `vm.h`, build beside
its manifest, and be selected through the same `{ "manifest": ... }` form.
These headers intentionally expose no trap or syscall internals.

The repository-wide selection policy and its intentional private boundaries
are in [docs/component-map.md](../docs/component-map.md).
The shortest profile-to-verification path is in
[docs/workflow.md](../docs/workflow.md).

## External component example

An implementation may live anywhere. Create a manifest next to its sources:

```json
{
  "schema": 1,
  "id": "mycore.singlecycle",
  "kind": "core",
  "title": "My single-cycle experiment",
  "sources": ["mycore.sv"],
  "stock_soc_module": "axcore",
  "capabilities": ["rv32i", "single-cycle"]
}
```

Then select it without copying anything into this repository:

```json
"core": {"manifest": "../my-components/mycore/component.json"}
```

inside a configuration's `components` object. For an external manifest,
`sources` are relative to that manifest's folder. Built-in manifests use paths
relative to the repository root.

Run `python3 tools/configure.py resolve --config path/to/config.json` first;
the compiler and the selected test suite are the next compatibility gates.
Claiming conformance to the reference CPU's ISA/formal/cosim status remains a
separate, evidence-based decision by the component author.
