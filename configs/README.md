# atomiX configuration profiles

Each JSON file selects a set of components for one reproducible build. The
resolver accepts built-in component IDs or an external manifest path:

```json
{
  "schema": 1,
  "name": "my-machine",
  "components": {
    "core": {"manifest": "../my-core/component.json"},
    "soc": "soc.reference",
    "memory": "memory.delayed",
    "uart": "uart.mmio16550",
    "clint": "clint.qemu-virt",
    "spi": "spi.polling-mode0",
    "board": "board.sim",
    "interconnect": "interconnect.axbus-reference",
    "cache": "cache.direct-mapped",
    "rom": "rom.axrom",
    "finisher": "finisher.sifive-test",
    "harness": "harness.verilator-soc",
    "software": "software.baremetal-hello"
  },
  "settings": {
    "ram_bytes": 33554432,
    "caches": true,
    "reset_pc": "0x80000000"
  }
}
```

The stock simulation/FPGA Makefiles need the components they instantiate; the
resolver itself also accepts partial and non-stock compositions for a custom
top or harness. The stock fabric, peripheral, and simulation-harness
selections are currently needed by `soc.reference`. Unknown `settings` remain
available as
`COMPONENT_SETTING_*` Make variables. They are deliberately not rejected, so a
custom component may define its own knobs without changing the common resolver.

| Profile | Purpose |
|---|---|
| `sim-bram.json` | reference CPU and SoC with 128 KiB BRAM |
| `sim-fastmul.json` | the BRAM machine with one line changed: `muldiv.fast-mul` replaces the core's default mul/div unit |
| `sim-minimal.json` | the compact multi-cycle `core.minimal` in place of the reference CPU |
| `sim-ax2.json` | the dual-issue `core.ax2` in place of the reference CPU |
| `sim-role-loopback.json` | the BRAM machine with `role.loopback` in the accelerator window instead of the default `role.none` |
| `sim-tpu-lite.json` | reference CPU plus the folded int8 GEMM role (`check-tpu`) |
| `sim-gpu-compute.json` | reference CPU plus the SIMT vector role (`check-gpu`) |
| `sim-ax2-gpu1.json` | dual-issue CPU plus the banked SIMT role (`check-gpu1`) |
| `sim-minimal-gpu.json` | minimal host plus the SIMT role — the Tang GPU pairing in simulation |
| `sim-minimal-tpu.json` | minimal host plus the TPU role — the Tang TPU pairing in simulation |
| `sim-primer-runtime-gpu.json` | the Primer runtime platform in simulation: ROM reset, blank RAM, 1-lane programmable GPU |
| `sim-delayed.json` | 32 MiB delayed backing store plus I/D caches |
| `sim-delayed-passthrough-cache.json` | delayed memory with the transparent cache implementation |
| `sim-sdram.json` | x16 SDRAM controller against the behavioral SDRAM model |
| `sim-finisher.json` | alternate minimal CPU composition smoke test; not RISC-V |
| `sim-hello.json` | reference BRAM machine plus selectable bare-metal payload |
| `sim-axos.json` | reference SDRAM machine plus selectable aXos SD-boot payload |
| `ulx3s-85f.json` | ULX3S/ECP5 board implementation and constraints |
| `ulx3s-85f-gpu.json` | ULX3S with a minimal host and the SIMT GPU role |
| `ulx3s-85f-tpu.json` | ULX3S with the reference CPU and the TPU-lite role |
| `tangnano20k.json` | Tang Nano 20K/GW2A BRAM-only board target |
| `tangnano20k-gpu.json` | Nano max GPU: minimal host plus 6-lane SIMT engine |
| `tangnano20k-tpu.json` | Nano max TPU: folded 24-MAC int8 GEMM engine |
| `tangprimer25k.json` | Tang Primer 25K Dock/GW5A BRAM-only board target |
| `tangprimer25k-ax2.json` | Primer max CPU: dual-issue AX2, 2 KiB I-cache, 64-entry BTB |
| `tangprimer25k-gpu.json` | Primer verified GPU: minimal host plus 4-lane SIMT engine using 12 DSPs |
| `tangprimer25k-runtime-gpu.json` | Primer kernel platform: ROM reset, blank RAM, runtime-uploaded aXos, and programmable 1-lane GPU |
| `tangprimer25k-tpu.json` | Primer max TPU: folded 24-MAC int8 GEMM engine |
| `kernel-default.json` | aXos round-robin scheduling with the reference Sv32 VM |
| `kernel-cooperative.json` | aXos cooperative-until-blocked scheduling with the reference Sv32 VM |
| `kernel-primer-monitor.json` | aXos sized for the Primer's 32 KiB RAM, with the compact `shell.monitor` console |

Validate before building:

```bash
make config-check CONFIG=configs/sim-sdram.json
make config-check-all
make software CONFIG=configs/sim-hello.json
make software CONFIG=configs/sim-axos.json
make -C sw/kernel kernel-config KERNEL_CONFIG=../../configs/kernel-cooperative.json
```

For Gowin profiles, `reset_pc: "0x00001000"` declares runtime software:
the FPGA flow automatically selects blank RAM plus the immutable UART loader
sized to `ram_bytes`. Kernel binaries are runtime payloads, never synthesis
inputs.
