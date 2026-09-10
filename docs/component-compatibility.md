# Component compatibility and evidence inventory

Selection and conformance are separate. The resolver checks that a profile is
well formed and makes the selected sources reach a build; it does not grant an
alternative the reference implementation's ISA, timing, synthesis, or physical
status. Each row below states only the modes actually exercised in this
repository. “Missing” is a tracked evidence gap, not an implied failure.

The reference selections are `core.pipeline5`, `muldiv.iterative32`,
`cache.direct-mapped`, `memory.bram`, `scheduler.round-robin`, `shell.axsh`, and
an empty `role.none`. Other single-choice kinds are omitted because no supplied
replacement exists at that boundary yet. Boards are targets rather than
drop-in implementations; they are included because substituting one changes
the evidence domain.

| Supplied alternative | Supported modes and required companions | Exercised parameters and runnable evidence | Missing or explicitly unverified |
|---|---|---|---|
| `example.finisher-delayed` (external SDK package) | `soc.reference`, `board.sim`, `harness.verilator-soc`, and `core.finisher-smoke` only | `ack_delay_cycles=1,4`; `make external-component-check` also rejects `17` and a Tang Primer profile | Other cores/harnesses; synthesis and every physical board |
| `core.finisher-smoke` | Stock aXbus shell and simulation finisher; intentionally non-RISC-V | `configs/sim-finisher.json`; `make component-test` | Every ISA, privilege, formal, software, synthesis, and physical claim |
| `core.minimal` | RV32IM/Zicsr M-mode accelerator host; no MMU or S/U mode | Wait-state ISA runs plus GPU/TPU compositions: `make -C sim/unit run-minimal-isa`; `make -C sw/baremetal check-suite-minimal` | Formal, privilege/Sv32, OS boot, synthesis/P&R, physical board evidence |
| `core.ax2` | RV32IM/Zicsr M-mode stock SoC; physical addressing | Points `(issue_width,icache_kb,btb_entries)={1/1/16,2/2/32,2/8/128,2/1/0,1/8/0}` in `make -C sim/unit run-suite-ax2`; SoC integration in `make -C sw/baremetal check-suite-ax2`; bounded formal in `make -C formal check-ax2` | S/U/Sv32, full reference-core formal depth, and physical-board evidence for every tier |
| `muldiv.fast-mul`, `muldiv.radix4` | `core.pipeline5` start/busy/done boundary | Both use `make -C sim/unit run-muldiv-fastmul run-muldiv-radix4`; fast-mul additionally runs directed cosim, rv32um, and fuzz through documented overrides | Radix-4 does not yet have the full fast-mul cosim/ISA/fuzz matrix; no per-unit physical claim |
| `cache.passthrough`, `cache.writeback` | Stock aXbus cache boundary; write-back requires no instruction-port writes | Passthrough composition: `make component-test`; reference cache unit behavior: `make -C sim/unit run-axcache` | No dedicated write-back runnable check is linked; unsupported companions are not rejected |
| `memory.delayed`, `memory.sdram` and `harness.verilator-sdram` | Stock `axmem`; SDRAM requires the pin-level harness and ROM boot | `make component-test`; `make -C sim/unit run-axdram-model run-axsdram`; `make -C sw/kernel check-sdboot check-sdboot-exec` | SDRAM evidence is behavioral-model/simulation only; it is not physical SDRAM proof on any board |
| `scheduler.cooperative` | aXos kernel scheduler boundary with the reference VM/services | Default and cooperative boot semantics: `make -C sw/kernel kernel-component-test` | Other service combinations and starvation/performance characterization |
| `shell.monitor` | 32-KiB management profiles with the reference kernel services | Built in all kernel profiles and exercised by `make evolution-check` and Primer runtime simulation checks | Full `axsh` command compatibility is intentionally absent; combinations outside monitor profiles are not rejected |
| `role.loopback` | Stock role window, isolation fence, PLIC, and role-aware payload | `make -C sw/baremetal check-role check-role-irq`; kernel driver/IRQ checks | Compute semantics by design; no standalone physical claim |
| `role.tpu-lite`, `role.gpu-compute` | Stock role ABI and bare-metal/aXos drivers | `make -C sw/baremetal check-tpu check-gpu check-gpu-perf`; GPU lanes `1,2,4,8` in `make experiment-sweep-check` | Formal and exhaustive parameter coverage; physical results apply only to identities explicitly recorded in the Primer achievement log |
| `role.gpu1` | Stock role ABI; banked global memory | `(lanes,banks)={4/4,8/8,16/16,32/32,16/4,6/8}`, plus division/shuffle off at `8/8`, in `make -C sim/unit run-suite-gpu1`; SoC oracle in `make -C sw/baremetal check-gpu1` | Formal, all cross-products beyond the finite suite, and physical evidence |
| `role.gpu-tpu` | Resident GPU plus TPU under one guarded role window | Default compact GPU geometry: `make -C sim/unit run-gpu-tpu`; `make -C sw/baremetal check-gpu-tpu` | Parameter sweep, formal proof, and a physical runtime-switch claim |
| `role.morph` | R2 research fabric behind the stock role/isolation boundary | `pes={1,2,4}`, documented data sizes and modes through `make l3-check` and `make -C sw/baremetal check-morph` | Experimental only; not a generally supported accelerator or persistent partial-reconfiguration claim |
| `evolution.{none,small,mid,large}`, `fitness.cycles-per-work` | aXos Live FPGA controller profiles; small/mid/large retain independent 32-KiB fit gates | `make evolution-check`; `make fitness-check`; `make live-check` | No autonomous promotion, flash persistence, or claim beyond the recorded bounded policies |
| `board.tangprimer25k`, `board.tangnano20k`, `board.ulx3s-{45f,85f}` | Their named FPGA flows and constraints only | Synthesis/P&R commands and records are board-specific; Tang Primer physical results are separately recorded in `docs/achievements/tangprimer25k.md` | Tang Nano and ULX3S have no physical claim here; simulation or P&R never fills that gap |

This inventory does not yet close the Component discipline gate: several rows
still lack component-owned negative compatibility checks or a full linked
evidence matrix. New evidence should narrow a named gap in this table; a result
from a different component or evidence domain must not be copied across.
