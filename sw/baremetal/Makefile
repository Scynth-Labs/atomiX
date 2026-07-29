include ../../mk/toolchain.mk
BUILD_DIR    ?= build
RAM_BYTES    ?= 131072
CC      := $(RISCV_PREFIX)gcc
OBJCOPY := $(RISCV_PREFIX)objcopy

CPPFLAGS := -Iinclude
# RISCV_ARCH is probed in mk/toolchain.mk, because the Zicsr spelling this
# needs depends on the compiler generation rather than on anything we choose.
CFLAGS   := -march=$(RISCV_ARCH) -mabi=ilp32 -mcmodel=medany -mno-relax \
            -msmall-data-limit=0 -ffreestanding -fno-builtin -fno-pic \
            -fno-stack-protector -Wall -Wextra -Werror -O2 -g
LDFLAGS  = -nostdlib -Wl,-T,link.ld -Wl,--defsym=__ram_bytes=$(RAM_BYTES) \
           -Wl,--gc-sections -Wl,-Map,$(@:.elf=.map)

PROGRAMS := hello timer preempt fencei spi sd role tpu gpu gpu_perf gpu1 cpu_perf render_perf
ELFS     := $(PROGRAMS:%=$(BUILD_DIR)/%.elf)
BINS     := $(ELFS:.elf=.bin)
HEXS     := $(ELFS:.elf=.hex)

all: $(ELFS)
images: $(BINS) $(HEXS)

$(BUILD_DIR)/%.elf: crt0.S traps.S runtime.c examples/%.c link.ld include/platform.h include/bench_report.h include/csr.h include/clint.h include/role.h | $(BUILD_DIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ crt0.S traps.S runtime.c examples/$*.c

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

# RTL-only like check-role.  The raised cycle budget covers the on-core
# reference matmuls (three 12x8x8 GEMMs on the 32-cycle mul/div unit).
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

.PHONY: all images run-iss run-qemu run-rtl check-hello check-timer check-preempt check-fencei check-spi check-sd check-role check-tpu check-gpu check-gpu-perf check-gpu1 check-cpu-perf check-suite-minimal check-suite-ax2 clean
