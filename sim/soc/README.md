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
  RESET_PC=0x80000000 \
  COMPONENT_CONFIG=../../configs/sim-role-loopback.json)
"$MODEL" --ram-image "$PWD/sw/kernel/build/axos_boot.hex" --uart-interactive
                              # type at the aXos prompt; Ctrl-D ends it
```

State genuinely persists across commands — running the shell's `role` twice
reports `irq=1` then `irq=2`, because it is one machine rather than two boots.

## Runtime payloads

Build the payloads separately, then launch the same executable with another
`--ram-image` to select a program without recompiling the machine:

```bash
make -C sw/baremetal build/hello.hex build/timer.hex
"$MODEL" --ram-image "$PWD/sw/baremetal/build/hello.hex"
"$MODEL" --ram-image "$PWD/sw/baremetal/build/timer.hex"
make -C sim/soc check-runtime-payload
```

The argument accepts a word-per-line `$readmemh` image, with paths resolved from
the launch directory. It applies before the first reset evaluation, through a
simulation-only plusarg consumed by the selected BRAM or delayed-memory model.
Both asynchronous and registered BRAM reads use it. Capacity and reset PC stay
properties of the compiled machine, so payloads must fit and be linked for it.
The physical SDRAM pin model uses the ROM loader and rejects `--ram-image`.
There is no change to FPGA memory initialization or UART upload protocols.

`RAM_INIT_FILE` remains an optional baked default for existing callers. A
runtime image overrides that default for one launch; without either, RAM starts
with the simulator's default contents. Omitting `RAM_INIT_FILE` from
`model-path` keeps payload filenames out of the model's build identity.

The regression boots hello → timer → hello on one executable, compares output
and cycles against baked initialization, replaces bytes at the same path, checks
invalid arguments, and runs two role commands in one interactive aXos session.
It covers both BRAM timings and cached delayed memory. Compact simulation
evidence, including executable and payload hashes, is written to
`sim/soc/build/runtime-payload-evidence.json`.

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
parameters. For the physical x16 SDRAM pin model, select
`COMPONENT_CONFIG=../../configs/sim-sdram.json` with `run-sdram`; the profile
chooses `axsdram` and the CAS-2 behavioral SDRAM device.

The legacy boot regression remains available:

```bash
make -C sw/kernel check-sdboot
```

It currently omits that profile selection, so its printed physical-SDRAM label
describes a BRAM run. Explicit pin-model testing boots the shell but does not
complete exec even at 120M cycles, with PC samples pointing to timer-service
starvation. The [follow-up evidence](../../research/benchmarks/sdram-exec-followup.json)
records this open simulation issue; no board result is implied.
