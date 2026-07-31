# sim/soc/ — complete-SoC simulations

This runner instantiates the selected `soc_top` component, initializes its RAM from a
word-per-line `$readmemh` image, and captures UART output until the standard
`sifive_test` finisher exits. The UART includes a one-byte RX holding register:
pass a byte script with `UART_INPUT_FILE` to drive a software console.

It is driven by the bare-metal build:

```bash
make -C sw/baremetal run-rtl
```

## Batch and interactive sessions

By default a run is **batch**: it consumes a fixed byte script, runs to the
finisher or a cycle bound, and prints the whole transcript at the end. That is
what a self-checking test wants, and every `check-*` target uses it.

`--uart-interactive` instead keeps the console byte pipe open in both
directions for the life of the process — stdin becomes UART receive, and UART
transmit is streamed to stdout as it is produced rather than buffered to the
end. This is what makes a *session* possible: in batch mode every exchange is a
separate process, so the machine reboots between commands and nothing carries
over. Interactive runs end when the console closes or the finisher fires, so
they are not bounded by `MAX_CYCLES`.

Because a session must outlive one command, the build and the launch are
separate targets. `model-path` builds the model and prints only its path, for a
caller that wants to spawn it once and keep it open:

```bash
MODEL=$(make -s -C sim/soc model-path \
  RAM_INIT_FILE="$PWD/sw/kernel/build/axos_boot.hex" RESET_PC=0x80000000 \
  COMPONENT_CONFIG=../../configs/sim-role-loopback.json)
"$MODEL" --uart-interactive     # type at the aXos prompt; Ctrl-D ends it
```

State genuinely persists across commands — running the shell's `role` twice
reports `irq=1` then `irq=2`, because it is one machine rather than two boots.

For a direct invocation, provide a RAM image and an entry point:

```bash
make -C sim/soc run RAM_INIT_FILE=/absolute/path/program.hex RESET_PC=0x80000000
```

The component-aware entry point selects the SoC shell, CPU, fabric, cache,
memory, ROM, peripherals, finisher, and appropriate simulation harness. The
supplied profiles make BRAM, delayed memory, and the physical SDRAM behavioral
path reproducible:

```bash
make sim CONFIG=configs/sim-bram.json \
  RAM_INIT_FILE="$PWD/sw/baremetal/build/hello.hex"
make sim CONFIG=configs/sim-delayed.json \
  RAM_INIT_FILE="$PWD/sw/baremetal/build/hello.hex"
make sim CONFIG=configs/sim-sdram.json \
  ROM_INIT_FILE="$PWD/sw/bootrom/build/bootrom.hex" \
  SD_IMAGE="$PWD/sw/kernel/build/axos_boot.img"
```

Run `make -C sim/soc config COMPONENT_CONFIG=/absolute/path/profile.json` to
show the resolved component IDs. Details of custom or external components are
in [components/README.md](../../components/README.md).

For a scripted aXos shell session:

```bash
make -C sw/kernel run-rtl UART_INPUT_FILE="$PWD/sw/kernel/shell_input.txt"
```

`run` normally uses BRAM or the delayed-memory model selected by its
parameters. `run-sdram` instead instantiates the physical x16 SDRAM pins of
`axsdram` against the CAS-2 behavioral SDRAM device:

```bash
make -C sw/kernel check-sdboot
```

That complete ROM → SD → SDRAM boot regression is the simulation gate for the
ULX3S target; it is not a substitute for board-level timing and electrical
validation.
