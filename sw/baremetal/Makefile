include ../../mk/toolchain.mk
BUILD_DIR    ?= build
RAM_BYTES    ?= 131072
CC      := $(RISCV_PREFIX)gcc
OBJCOPY := $(RISCV_PREFIX)objcopy

CPPFLAGS := -Iinclude -I$(BUILD_DIR)

# The shell's interrupt numbering is declared in the SoC component that wires
# it; the header is derived from there rather than restated here. Point SOC_TOP
# at a different shell to build against its map.
SOC_TOP  ?= ../../components/soc/reference/soc_top.sv
IRQ_MAP  := $(BUILD_DIR)/ax_irq_map.h

$(IRQ_MAP): $(SOC_TOP) ../../tools/gen_irq_map.py | $(BUILD_DIR)
	python3 ../../tools/gen_irq_map.py --soc $(SOC_TOP) --output $@
# RISCV_ARCH is probed in mk/toolchain.mk, because the Zicsr spelling this
# needs depends on the compiler generation rather than on anything we choose.
CFLAGS   := -march=$(RISCV_ARCH) -mabi=ilp32 -mcmodel=medany -mno-relax \
            -msmall-data-limit=0 -ffreestanding -fno-builtin -fno-pic \
            -fno-stack-protector -Wall -Wextra -Werror -O2 -g
LDFLAGS  = -nostdlib -Wl,-T,link.ld -Wl,--defsym=__ram_bytes=$(RAM_BYTES) \
           -Wl,--gc-sections -Wl,-Map,$(@:.elf=.map)

PROGRAMS := hello timer preempt fencei spi sd role role_irq tpu gpu gpu_perf gpu1 cpu_perf render_perf game2048 snake
ELFS     := $(PROGRAMS:%=$(BUILD_DIR)/%.elf)
BINS     := $(ELFS:.elf=.bin)
HEXS     := $(ELFS:.elf=.hex)

all: $(ELFS)
images: $(BINS) $(HEXS)

$(BUILD_DIR)/%.elf: crt0.S traps.S runtime.c examples/%.c link.ld include/platform.h include/bench_report.h include/csr.h include/clint.h include/role.h include/plic.h include/term.h $(IRQ_MAP) | $(BUILD_DIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ crt0.S traps.S runtime.c examples/$*.c

# The interactive-tier game at a compressed frame clock, for check-snake below.
# It is the same source and the same logic; only the deadline constant differs,
# because at the shipped 12 fps one frame is 2.08M cycles and a hundred-frame
# replay would be 200M simulated cycles to prove something that does not depend
# on the constant.  What the check gets from it is the pacing mechanism under
# test -- frames landing on their deadline, no frame overrunning its budget --
# at a rate a simulator can afford to run.
SNAKE_CHECK_HZ ?= 500
$(BUILD_DIR)/snake_fast.elf: crt0.S traps.S runtime.c examples/snake.c link.ld include/platform.h include/term.h $(IRQ_MAP) | $(BUILD_DIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) -DAX_SNAKE_FRAME_HZ=$(SNAKE_CHECK_HZ) $(LDFLAGS) \
	  -o $@ crt0.S traps.S runtime.c examples/snake.c

$(BUILD_DIR)/%.bin: $(BUILD_DIR)/%.elf
	$(OBJCOPY) -O binary $< $@

$(BUILD_DIR)/%.hex: $(BUILD_DIR)/%.bin ../../tests/directed/bin2hex.py
	python3 ../../tests/directed/bin2hex.py $< $@

$(BUILD_DIR):
	mkdir -p $@

run-iss: $(BUILD_DIR)/hello.elf ../../sim/axsim/axsim
	../../sim/axsim/axsim --bin $(BUILD_DIR)/hello.elf

run-qemu: $(BUILD_DIR)/hello.elf
	timeout --foreground 10s qemu-system-riscv32 -machine virt -bios none \
	  -nographic -kernel $<

run-rtl: $(BUILD_DIR)/hello.hex
	$(MAKE) -C ../../sim/soc run RAM_INIT_FILE=$(abspath $<) RESET_PC=0x80000000

check-hello: images
	$(MAKE) -C ../../sim/axsim axsim
	python3 check_hello.py

check-timer: images
	$(MAKE) -C ../../sim/axsim axsim
	python3 check_timer.py

check-preempt: images
	$(MAKE) -C ../../sim/axsim axsim
	python3 check_preempt.py

check-fencei: images
	$(MAKE) -C ../../sim/axsim axsim
	python3 check_fencei.py

check-spi: images
	python3 check_spi.py

check-sd: images
	python3 check_sd.py

# RTL-only: the ISS does not model the role window.  The profile selects
# role.loopback; the program exits through the finisher, so a wrong result
# fails this target.
check-role: $(BUILD_DIR)/role.hex
	$(MAKE) -C ../../sim/soc run \
	  COMPONENT_CONFIG=../../configs/sim-role-loopback.json \
	  RAM_INIT_FILE=$(abspath $(BUILD_DIR)/role.hex) RESET_PC=0x80000000

# The same completion, taken as an interrupt instead of polled: role line ->
# PLIC source 2 -> machine external interrupt.  The program parks in wfi with
# the job running, so it can only finish if the whole path works.
check-role-irq: $(BUILD_DIR)/role_irq.hex
	$(MAKE) -C ../../sim/soc run \
	  COMPONENT_CONFIG=../../configs/sim-role-loopback.json \
	  RAM_INIT_FILE=$(abspath $(BUILD_DIR)/role_irq.hex) RESET_PC=0x80000000

# RTL-only like check-role.  The raised cycle budget covers the on-core
# reference matmuls (three 12x8x8 GEMMs on the 32-cycle mul/div unit).
# Morph fabric: three personalities from one resident configuration, plus the
# descriptor-confinement cases.  RTL-only; the ISS does not model the window.
check-morph: $(BUILD_DIR)/morph.hex
	$(MAKE) -C ../../sim/soc run \
	  COMPONENT_CONFIG=../../configs/sim-morph.json \
	  RAM_INIT_FILE=$(abspath $(BUILD_DIR)/morph.hex) RESET_PC=0x80000000 \
	  MAX_CYCLES=1000000

# The R2 counter-experiment: the same SAXPY the morph fabric runs, on the
# existing programmable role at one lane, measured identically.
check-gpu-lane1: $(BUILD_DIR)/gpu_lane1.hex
	$(MAKE) -C ../../sim/soc run \
	  COMPONENT_CONFIG=../../configs/sim-gpu-lane1.json \
	  RAM_INIT_FILE=$(abspath $(BUILD_DIR)/gpu_lane1.hex) RESET_PC=0x80000000 \
	  MAX_CYCLES=2000000

# R1 on silicon: the role-window fence and Live FPGA telemetry, exercised
# through a real personality change.  RTL-only; the ISS models neither.
check-roleiso: $(BUILD_DIR)/roleiso.hex
	$(MAKE) -C ../../sim/soc run \
	  COMPONENT_CONFIG=../../configs/sim-morph.json \
	  RAM_INIT_FILE=$(abspath $(BUILD_DIR)/roleiso.hex) RESET_PC=0x80000000 \
	  MAX_CYCLES=2000000

# First shipped game, terminal tier.  Replays a fixed key sequence through the
# simulated UART and requires the exact final state: a game is only trustworthy
# as a platform demo if it is deterministic, so it is tested like any other
# profile rather than eyeballed.
check-game2048: $(BUILD_DIR)/game2048.hex
	$(MAKE) -C ../../sim/soc run \
	  COMPONENT_CONFIG=../../configs/sim-bram.json \
	  RAM_INIT_FILE=$(abspath $(BUILD_DIR)/game2048.hex) RESET_PC=0x80000000 \
	  UART_INPUT_FILE=$(abspath tests-game2048-keys.txt) MAX_CYCLES=4000000 \
	  | tee $(BUILD_DIR)/game2048.out
	@grep -q 'checksum=0x243eb403' $(BUILD_DIR)/game2048.out \
	  && echo "game2048: PASS (exact final state after a fixed key sequence)" \
	  || { echo "game2048: FAIL (final state changed)"; exit 1; }

# Second shipped game, interactive tier.  The key file is a frame-by-frame
# tape: the game takes at most one byte per frame, so byte N is what was
# pressed during frame N and '.' is a frame with nothing pressed.  It is
# generated by `python3 make_snake_tape.py`, which plays a host model of the
# same rules to find a tape that eats, levels up, pauses, dies, and restarts --
# a tape a human cannot write, because where the food lands is a function of
# the game's own RNG.  The pinned checksum below is the machine's, and the
# model independently predicts it.
#
# Two assertions, because a real-time game has two ways to fail that a
# turn-based one does not.  The checksum folds a per-frame trace, so it covers
# the whole session rather than its last picture; and drops=0 says no frame
# overran its budget.  Deliberately scoped: the simulated UART is a byte pipe
# with no baud rate, so this proves the *compute* keeps the deadline.  What a
# frame costs on the board is dominated by the serial link, which the game's
# own panel reports and only the board can measure.  The worst frame here uses
# about half its compressed budget, so the margin is real rather than nominal.
# The same game, delivered the way a board actually receives one: booted from
# the immutable ROM into blank RAM and uploaded as an AXK1 frame, at the
# Primer's 32 KiB.  This is the architectural gate, not a second game test —
# baking a payload into synthesis makes every program its own bitstream, its
# own placement, and its own timing claim, which is how a role once stopped
# fitting because of a software change.  Requiring the uploaded image to reach
# the *same checksum* as the baked one is what says a program is a payload
# rather than a hardware revision.
BOARD_RAM_BYTES ?= 32768
BOARD_BUILD     := build/ram$(BOARD_RAM_BYTES)
BOOTROM_HEX     := ../bootrom/build/uart-ram$(BOARD_RAM_BYTES)/bootrom.hex

$(BOOTROM_HEX):
	$(MAKE) -C ../bootrom images MODE=uart RAM_BYTES=$(BOARD_RAM_BYTES) \
	  BUILD_DIR=build/uart-ram$(BOARD_RAM_BYTES)

check-snake-loader: $(BOOTROM_HEX)
	$(MAKE) BUILD_DIR=$(BOARD_BUILD) RAM_BYTES=$(BOARD_RAM_BYTES) \
	  $(BOARD_BUILD)/snake_fast.bin
	python3 check_payload_boot.py --label snake-loader \
	  --payload $(BOARD_BUILD)/snake_fast.bin --keys tests-snake-keys.txt \
	  --ram-bytes $(BOARD_RAM_BYTES) \
	  --expect 'checksum=0xd824f761' --expect 'drops=0 '

check-snake: $(BUILD_DIR)/snake_fast.hex
	$(MAKE) -C ../../sim/soc run \
	  COMPONENT_CONFIG=../../configs/sim-bram.json \
	  RAM_INIT_FILE=$(abspath $(BUILD_DIR)/snake_fast.hex) RESET_PC=0x80000000 \
	  UART_INPUT_FILE=$(abspath tests-snake-keys.txt) MAX_CYCLES=12000000 \
	  | tee $(BUILD_DIR)/snake.out
	@grep -q 'snake: QUIT .* checksum=0xd824f761' $(BUILD_DIR)/snake.out \
	  && echo "snake: PASS (exact final state after a fixed key tape)" \
	  || { echo "snake: FAIL (final state changed)"; exit 1; }
	@grep -q 'snake: QUIT .* drops=0 ' $(BUILD_DIR)/snake.out \
	  && echo "snake: PASS (no frame missed its deadline)" \
	  || { echo "snake: FAIL (a frame overran its budget)"; exit 1; }

check-tpu: $(BUILD_DIR)/tpu.hex
	$(MAKE) -C ../../sim/soc run \
	  COMPONENT_CONFIG=../../configs/sim-tpu-lite.json \
	  RAM_INIT_FILE=$(abspath $(BUILD_DIR)/tpu.hex) RESET_PC=0x80000000 MAX_CYCLES=400000

# RTL-only like check-tpu.  The raised cycle budget covers the on-core saxpy
# reference and interpreter oracle over the SIMT kernels.
check-gpu: $(BUILD_DIR)/gpu.hex
	$(MAKE) -C ../../sim/soc run \
	  COMPONENT_CONFIG=../../configs/sim-gpu-compute.json \
	  RAM_INIT_FILE=$(abspath $(BUILD_DIR)/gpu.hex) RESET_PC=0x80000000 MAX_CYCLES=400000

# GPU performance regression: sweeps thread counts over two kernels, times the
# SIMT engine against on-core loops, and fails if the measured speedup drops
# below the bar.  The raised cycle budget covers the sweep plus the per-thread
# MMIO uploads.  To measure a different core/lane count, point COMPONENT_CONFIG
# at your own profile (the reduced-lane and minimal-host components ship in the
# catalog; compose a profile when you need it).
check-gpu-perf: $(BUILD_DIR)/gpu_perf.hex
	$(MAKE) -C ../../sim/soc run \
	  COMPONENT_CONFIG=../../configs/sim-gpu-compute.json \
	  RAM_INIT_FILE=$(abspath $(BUILD_DIR)/gpu_perf.hex) RESET_PC=0x80000000 MAX_CYCLES=4000000

# Component test SUITE, not a per-hardware check: one target exercises the whole
# minimal-host family — core.minimal driving the CPU (hello), the GPU role, and
# the TPU role — so a lean-component variant is covered without a separate check
# and profile per combination.  Add a suite (and its sim-<name>-* fixtures) when
# a family of components warrants coverage together.
check-suite-minimal: $(BUILD_DIR)/hello.hex $(BUILD_DIR)/gpu.hex $(BUILD_DIR)/tpu.hex
	@echo "== suite/minimal: core.minimal + CPU (hello) =="
	$(MAKE) -C ../../sim/soc run COMPONENT_CONFIG=../../configs/sim-minimal.json \
	  RAM_INIT_FILE=$(abspath $(BUILD_DIR)/hello.hex) RESET_PC=0x80000000 MAX_CYCLES=200000
	@echo "== suite/minimal: core.minimal + GPU (gpu-compute, 6 lanes) =="
	$(MAKE) -C ../../sim/soc run COMPONENT_CONFIG=../../configs/sim-minimal-gpu.json \
	  RAM_INIT_FILE=$(abspath $(BUILD_DIR)/gpu.hex) RESET_PC=0x80000000 MAX_CYCLES=600000
	@echo "== suite/minimal: core.minimal + TPU (tpu-lite) =="
	$(MAKE) -C ../../sim/soc run COMPONENT_CONFIG=../../configs/sim-minimal-tpu.json \
	  RAM_INIT_FILE=$(abspath $(BUILD_DIR)/tpu.hex) RESET_PC=0x80000000 MAX_CYCLES=400000
	@echo "suite/minimal: PASS"

# RTL-only, like check-gpu.  Drives the scalable gpu1 engine through the shell
# window and checks every kernel against an on-core interpreter of the gpu1 ISA.
# The program reads CAPS for the geometry, so this one image is the check for
# any parameterisation; the sweep itself lives in `make -C ../../sim/unit
# run-suite-gpu1`, which builds each point directly and needs no profile each.
check-gpu1: $(BUILD_DIR)/gpu1.hex
	$(MAKE) -C ../../sim/soc run \
	  COMPONENT_CONFIG=../../configs/sim-ax2-gpu1.json \
	  RAM_INIT_FILE=$(abspath $(BUILD_DIR)/gpu1.hex) RESET_PC=0x80000000 MAX_CYCLES=800000

# IPC regression.  Prints retired-instructions-per-cycle for five workloads on
# whichever core the profile selects.  `make -C ../../tools bench` sweeps this
# across cores and roles for the comparison table.
check-cpu-perf: $(BUILD_DIR)/cpu_perf.hex
	$(MAKE) -C ../../sim/soc run \
	  COMPONENT_CONFIG=../../configs/sim-ax2.json \
	  RAM_INIT_FILE=$(abspath $(BUILD_DIR)/cpu_perf.hex) RESET_PC=0x80000000 MAX_CYCLES=2000000

# Component test SUITE for the new core + new role: the SoC-integration leg.
# It runs the CPU program, both interrupt programs (timer and preempt need the
# CLINT, so they exist only at SoC level), the self-modifying-code program
# (fencei, which exercises the instruction-cache invalidate), the IPC
# benchmark, and the gpu1 role driven through the shell window.
#
# Parameter coverage is deliberately NOT here.  core.ax2 and role.gpu1 are each
# a single component whose sizes are build-time parameters, and
# `make -C ../../sim/unit run-suite-ax2` / `run-suite-gpu1` sweep that parameter
# space directly -- against the official ISA suite and against the ISA oracle
# respectively.  This target checks the one thing a parameter sweep cannot: that
# the component composes into a working SoC.
check-suite-ax2: $(BUILD_DIR)/hello.hex $(BUILD_DIR)/timer.hex $(BUILD_DIR)/preempt.hex \
                 $(BUILD_DIR)/fencei.hex $(BUILD_DIR)/cpu_perf.hex $(BUILD_DIR)/gpu1.hex
	@for p in hello timer preempt fencei cpu_perf; do \
	  echo "== suite/ax2: core.ax2 (defaults) + $$p =="; \
	  $(MAKE) -s -C ../../sim/soc run \
	    COMPONENT_CONFIG=../../configs/sim-ax2.json \
	    RAM_INIT_FILE=$(abspath $(BUILD_DIR))/$$p.hex RESET_PC=0x80000000 \
	    MAX_CYCLES=2000000 || exit 1; \
	done
	@echo "== suite/ax2: core.ax2 + gpu1 role =="
	@$(MAKE) -s -C ../../sim/soc run \
	  COMPONENT_CONFIG=../../configs/sim-ax2-gpu1.json \
	  RAM_INIT_FILE=$(abspath $(BUILD_DIR)/gpu1.hex) RESET_PC=0x80000000 \
	  MAX_CYCLES=1500000
	@echo "suite/ax2: PASS"

clean:
	rm -rf build

.PHONY: all images run-iss run-qemu run-rtl check-hello check-timer check-preempt check-fencei check-spi check-sd check-role check-role-irq check-roleiso check-morph check-gpu-lane1 check-game2048 check-snake check-snake-loader check-tpu check-gpu check-gpu-perf check-gpu1 check-cpu-perf check-suite-minimal check-suite-ax2 clean
