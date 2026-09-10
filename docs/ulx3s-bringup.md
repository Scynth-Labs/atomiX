# ULX3S-85F bring-up checklist

This is the final hardware gate. It applies to an ULX3S v2/v3 with an
LFE5U-85F-6BG381C, the on-board 32 MiB SDR SDRAM, and a microSD card used as
the atomiX boot disk. Do not treat passing simulation as proof of board
electrical timing. Review [dependencies.md](dependencies.md) and the physical
section of [design-checklist.md](design-checklist.md) before starting.

## 1. Regressions before a bitstream

From a clean source checkout, run the three relevant simulation gates:

```bash
make -C sim/unit run-axsdram run-axuart-phy
make -C sw/kernel check-storage-write
make -C sw/kernel check-sdboot
```

The last command also creates `sw/kernel/build/axos_boot.img`, the exact raw
SD image used below. It boots that image through the SDRAM pin model as far as
the shell, fork/wait and ELF exec. The image is built with
`configs/kernel-slow-memory.json`, whose scheduling quantum is sized for a
machine backed by SDRAM rather than on-chip RAM — see
[memory.md](memory.md#the-scheduling-quantum-on-slow-memory).

## 2. FPGA tool check and build

Install the documented ECP5 tools, load their environment in the current
shell, and record the versions with the build result:

```bash
command -v yosys nextpnr-ecp5 ecppack openFPGALoader
yosys -V
nextpnr-ecp5 --version
ecppack --version
openFPGALoader --version
make -C rtl/fpga
```

`nextpnr-ecp5` reports utilisation and timing at the end of place-and-route.
Save that terminal output with the commit or issue that records bring-up; the
25 MHz `clk_25mhz` constraint must pass. Do not program a bitstream from a
failed or unconstrained P&R run.

## 3. Prepare the SD card

First use `lsblk` to identify the whole removable device. **Replace
`/dev/sdX` only after checking its size and model.** The following overwrites
every partition on that selected device, so it is intentionally not a Make
target:

```bash
lsblk -o NAME,SIZE,MODEL,TRAN,RM
sudo dd if=sw/kernel/build/axos_boot.img of=/dev/sdX bs=4M conv=fsync status=progress
sync
```

Eject it cleanly, insert it into the ULX3S microSD slot, and attach the board's
USB cable.

## 4. Reversible board proof

Program FPGA SRAM first; a power cycle restores the prior flash image:

```bash
make -C rtl/fpga program
picocom -b 115200 /dev/ttyUSB0
```

The first successful boot has the `aXboot` banner followed by `aXos: shell
online`. In the serial terminal run:

```text
ls
cat motd
write note board-proof
cat note
fork
```

Power-cycle and run `cat note` again to demonstrate that the CMD24 update
persists. Record the board revision, SD-card model, tool versions, bitstream
hash, P&R timing summary, and serial transcript.

Only after this evidence passes may a maintainer choose the persistent command
below. It writes configuration flash; `program` is the normal development
path.

```bash
make -C rtl/fpga flash
```
