# sw/bootrom/ — immutable kernel loaders

This is a small M-mode loader linked at `0x0000_1000`. It uses the polling
SPI controller to initialize an SDHC card, reads an `AXBT` header and raw
kernel sectors, copies them to `0x8000_0000`, then jumps to the normal aXos
entry point. Scratch BSS and the boot stack use the final 1 KiB of the RAM size
selected at link time.

The reproducible end-to-end check is run from the kernel directory:

```bash
make -C sw/kernel check-sdboot
```

It builds this ROM, a storage-enabled kernel, and a combined SD image before
starting the RTL SoC at the ROM reset address.

UART mode is the kernel-development and FPGA-runtime default. It scans for an
`AXK1` frame, validates the payload length and CRC-32, writes the kernel binary
to `0x8000_0000`, executes `fence.i`, and jumps there:

```text
host -> ROM  "AXK1" | length(u32 LE) | crc32(u32 LE) | payload
ROM  -> host "AXOK" | length(u32 LE)
             "AXER" | error(u32 LE)
```

Build a loader sized for the target RAM:

```bash
make -C sw/bootrom images MODE=uart RAM_BYTES=32768 \
  BUILD_DIR=build/uart-ram32768
```
