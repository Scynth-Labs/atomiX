# rtl/soc/ — SoC architecture entry point

The selectable SoC implementation files live in their owning component
directories. This directory keeps the architecture overview and the generic
integration vocabulary; `components/soc/reference/` owns the reference shell,
and the fabric, memory, peripheral, and finisher folders own their respective
implementations.

Everything around the core that forms the stock shell + role platform
(DESIGN.md §3.3):

- `axbus_*` — the interconnect: minimal synchronous valid/ready bus, address
  decode, arbiter slot for future DMA/debug masters. Deliberately a
  near-subset of Wishbone classic so a bridge to third-party cores is thin.
- Boot ROM + RAM (BRAM by default, dual-port to serve ibus and dbus), plus a
  delayed external-memory model, optional split I/D caches, and
  `axsdram.sv`, the ULX3S x16 SDR SDRAM controller.
- The role slot that [`rtl/roles/`](../roles/) designs plug into, and the
  host-link endpoint above it — the link is framed over the existing UART
  rather than a separate controller, so no new shell device was needed
  ([docs/host-protocol.md](../../docs/host-protocol.md)).
- `axbus_mux.sv` — one fixed-map aXbus decode fabric per aXcore master;
  unmapped accesses complete with an error rather than hanging.
- `axrom.sv`, `axram.sv` — dual-port BRAM-shaped memory blocks. ROM is
  `$readmemh` initialized through `soc_top`'s `ROM_INIT_FILE` parameter.
- `axdram_model.sv` — fixed-latency, 32 MiB-capable simulation backing store.
- `axsdram.sv` — dual-aXbus to x16 SDRAM controller: init, refresh, CAS-2
  reads, byte-masked writes, and explicit DQ I/O direction for a board top.
- `axcache.sv` — the reference optional cache: direct-mapped, write-through.
  It caches only the RAM range; all MMIO bypasses it. A committed `fence.i`
  flushes the I$. Geometry is a profile setting (`cache_lines`,
  `cache_words_per_line`), and `cache.writeback` and `cache.passthrough` are
  selectable alternatives to this policy.
- `axspi.sv` — polling mode-0 SPI controller at `0x1001_0000`, with explicit
  SCLK/MOSI/CS_N/MISO pins. It is the SD-card transport; the SD protocol and
  filesystem stay in software.
- `clint.sv` — hart 0 `msip`, `mtimecmp`, and `mtime`, using the QEMU-virt
  offsets and raising core software/timer interrupt lines.
- `uart.sv` — 16550-style THR/RBR plus LSR subset (matches QEMU-`virt`, so
  software runs unchanged on ISS/QEMU/RTL). A one-byte RX holding register
  reports LSR.DR and is driven by the simulation console sideband; byte
  registers are packed correctly into aXbus's word-aligned read lanes. It also
  drives a level-sensitive receive interrupt line.
- `plic.sv` — the external-interrupt controller at `0x0c00_0000`, a QEMU-virt
  compatible subset: per-source priority, enable, threshold, and the
  claim/complete handshake. Two targets ("contexts") at the QEMU-virt strides —
  context 0 is hart 0's machine context and context 1 its supervisor context,
  with independent enable words and thresholds — so a bare-metal program owns
  the first and aXos, which runs in S-mode, owns the second. Sources
  are **level-sensitive**, which is what the shell's devices actually provide:
  source 1 is UART receive, source 2 is role completion held while the role's
  `STATUS.DONE` stands. That numbering is declared once, as the `PLIC_SRC_*` /
  `PLIC_CTX_*` localparams in `soc_top.sv`, and `tools/gen_irq_map.py` derives
  the C header every software tree includes — so a new device is added here and
  nowhere else, and the generator fails the build if the ids and counts
  disagree. The `sources` vector is indexed by id rather than concatenated, so
  bit order cannot encode the numbering a second time. The gateway masks a
  source between claim and complete
  rather than latching an edge, so a device still asserted when its handler
  completes becomes pending again. Context 0 drives the core's machine-external
  interrupt — as the union of this controller's output and `soc_top`'s own
  `irq_external` input, so a board or testbench can still raise the line
  directly — and context 1 drives its `irq_s_external` input, which the
  reference core routes to `mip.SEIP`.
- `test_finisher.sv` — synthesizable simulation endpoint for QEMU's
  `sifive_test` pass/fail convention.
- `axmem.sv` — the selected memory-component boundary. The default wrapper
  delegates to `axmem_reference.sv`, retaining BRAM, delayed memory, and SDRAM
  behavior; a DIY memory component may provide another `axmem` implementation.
- `soc_top.sv` — ties the shell together; reset defaults to boot ROM
  (`0x0000_1000`). It accepts selected `axcore`, `axmem`, UART, CLINT, PLIC,
  SPI, and `axrole` implementations, and converges their interrupt lines into
  the PLIC; the actual named-port instantiations are the compact stock
  integration contracts.

The memory map is QEMU-`virt`-aligned — see DESIGN.md §3.1/§3.2. The currently
implemented shell is covered by:

```bash
make -C sim/unit run-soc        # ROM, RAM, UART, and finisher
make -C sim/unit run-soc-timer  # CLINT -> precise timer interrupt -> handler
make -C sim/unit run-plic       # PLIC register contract and level-sensitive gateway
make -C sim/unit run-axdram-model
make -C sim/unit run-axcache
make -C sim/unit run-axsdram
make -C sim/unit run-axspi
```

The device-to-core interrupt path is covered end to end from software:

```bash
make -C sw/baremetal check-timer     # CLINT timer interrupt
make -C sw/baremetal check-role-irq  # role -> PLIC context 0 -> M-mode program
make -C sw/kernel check-role-irq     # role -> PLIC context 1 -> S-mode aXos
```

See [components/README.md](../../components/README.md) to select or supply a
component without editing this reference shell.
