# ULX3S-85F achievements

## Hardware status

- **Available:** no; no ULX3S-85F is currently owned or physically tested.
- **FPGA target:** Lattice ECP5 LFE5U-85F-6BG381C.
- **Strongest evidence:** simulation, synthesis, and tool-generated bitstream.
  Nothing on this page is physical-board evidence.

The safe future procedure is in [ulx3s-bringup.md](../ulx3s-bringup.md).

## Successfully completed without hardware

- The ULX3S board component, pin constraints, 25 MHz clock, UART, microSD, and
  external SDRAM integration exist.
- CPU, 16-lane GPU, and folded TPU profiles pass their board-independent RTL
  suites and synthesize within the 85F resource budget.
- The baseline open ECP5 flow has generated a routed `.bit` image.
- The partial-reconfiguration research flow can build reference and candidate
  images and measure their placement/frame delta. The available packer still
  blocks final 85F partial-bitstream encoding.

## Pending

- [ ] Acquire or borrow an ULX3S-85F before claiming any BOARD result.
- [ ] Verify the exact board revision, USB serial device, SRAM programming, and
  25 MHz timing on silicon.
- [ ] Boot aXos from microSD and capture the UART transcript.
- [ ] Validate external SDRAM and persistent SD writes across a power cycle.
- [ ] Run CPU, GPU, and TPU profiles on the board and preserve their exact
  working images and hashes.
- [ ] Exercise a real partial role load only after the toolchain can encode a
  correct 85F partial bitstream; verify isolation and rollback on hardware.
- [ ] Keep persistent FPGA flash untouched until the complete SRAM regression
  passes repeatedly.
