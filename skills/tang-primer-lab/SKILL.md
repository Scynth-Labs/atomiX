---
name: tang-primer-lab
description: Connect, detect, program, test, recover, benchmark, or document the Sipeed Tang Primer 25K Dock for atomiX through Windows usbipd and WSL. Use for physical FPGA/JTAG/UART work, SRAM loading, Live FPGA kernel uploads, reset or power-cycle recovery, and hardware achievement or deployment evidence.
---

# Tang Primer lab

## Read before acting

Read `docs/tangprimer25k-bringup.md` completely for the procedure and
`docs/achievements/tangprimer25k.md` for current state. Treat it as the only
physically available FPGA unless the user says otherwise.

## Attach and detect

On Windows, identify VID:PID `0403:6010`, bind once if needed, then attach:

```powershell
usbipd list
usbipd bind --busid <BUSID>
usbipd attach --wsl --busid <BUSID>
```

Ignore unrelated WSL PATH-translation warnings if attachment succeeds. In WSL,
verify `lsusb`, `/dev/ttyUSB0`, `/dev/ttyUSB1`, and JTAG:

```bash
source "$HOME/opt/oss-cad-suite/environment"
openFPGALoader --detect
```

The verified Dock exposes channel A/JTAG as `/dev/ttyUSB0`, channel B/UART as
`/dev/ttyUSB1`, and GW5A-25 IDCODE `0x1281b`. Rediscover rather than assuming
device paths or BUSID.

## Program safely

- Program the **loader** bitstream, then upload programs over UART. Do not
  re-synthesize to change what the board runs: a baked payload makes every
  program its own bitstream, its own placement, and its own hash, which is how
  `role.tpu-lite` once stopped fitting after a software change. Reach for
  `make fpga PROGRAM=<name>` only for first bring-up of a profile that has no
  loader image. `make fpga-loader-primer` builds the plain one;
  `make fpga-loader LOADER_CONFIG=configs/tangprimer25k-runtime-{ax2,gpu4,tpu}.json`
  builds the role-carrying ones. Their rows are not locked yet — no P&R sweep
  has run since they were added — so measure before claiming a fit, especially
  for `runtime-tpu`, which synthesises about 7% larger than its baked form.
- Use `make -C rtl/fpga program ...` or `openFPGALoader -b tangprimer25k
  <image>` for reversible SRAM loading.
- Never use `make flash`, `openFPGALoader -f`, or another persistent write
  without explicit current-turn approval.
- Hash the exact image and kernel before testing. Do not commit either binary.

For the resident runtime, build/verify first, then follow the exact programming
command in the bring-up guide. Exercise the loader and live switching with:

```bash
python3 sw/host/axhost.py --test-loader-recovery \
  --upload-kernel sw/kernel/build/primer-runtime/axos_boot.bin \
  --fast-switch --repeat 10 --serial /dev/ttyUSB1 --baud 921600
```

Use `--interrupt-upload-at` only with S1 physically accessible. A power cycle
restores the prior flash image; reattach USB/IP, detect JTAG, reload SRAM, and
rerun the UART regression.

## Record physical evidence

- Capture board/debugger identity, tool versions, profile and SHA-256 values,
  P&R resources/timing, command, UART verdict, cycle counts, latency spread,
  recovery behavior, and whether flash was written.
- Update `docs/achievements/tangprimer25k.md`, capability/benchmark docs, and
  the Live FPGA registry when applicable. Keep physical records distinct from
  RTL evidence and validate them with `make registry-check`.
- Keep only the most mature release identity per profile. Never recreate or
  commit `artifacts/`, generated bitstreams, build directories, or transcripts.

