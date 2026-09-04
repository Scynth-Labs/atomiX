include ../../mk/toolchain.mk
PYTHON ?= python3
KERNEL_CONFIG ?= ../../configs/kernel-default.json
BUILD_DIR ?= build
# A profile may be outside this worktree; use its absolute location in the
# generated filename so equal basenames cannot collide.
KERNEL_CONFIG_KEY := $(subst /,_,$(basename $(abspath $(KERNEL_CONFIG))))
KERNEL_COMPONENT_MK := $(BUILD_DIR)/component$(KERNEL_CONFIG_KEY).mk
KERNEL_COMPONENT_MANIFESTS := $(wildcard ../../components/*/*/component.json)

$(KERNEL_COMPONENT_MK): $(KERNEL_CONFIG) ../../tools/configure.py $(KERNEL_COMPONENT_MANIFESTS)
	mkdir -p $(@D)
	$(PYTHON) ../../tools/configure.py resolve --config "$(KERNEL_CONFIG)" --output $@

-include $(KERNEL_COMPONENT_MK)
$(KERNEL_COMPONENT_MK): $(COMPONENT_SELECTED_MANIFESTS)

# Compact/evolving profiles carry their intended kernel personality with the
# profile. A command-line override still wins for controlled comparisons.
KERNEL_MODE ?= $(if $(COMPONENT_SETTING_KERNEL_MODE),$(COMPONENT_SETTING_KERNEL_MODE),full)

.DEFAULT_GOAL := all

CC      := $(RISCV_CC)
OBJCOPY := $(RISCV_OBJCOPY)
QEMU    ?= qemu-system-riscv32
export QEMU
RAM_BYTES ?= $(if $(COMPONENT_RAM_BYTES),$(COMPONENT_RAM_BYTES),131072)
ifeq ($(KERNEL_MODE),monitor)
RESIDENT_BUDGET ?= $(COMPONENT_EVOLUTION_RESIDENT_BUDGET)
else
RESIDENT_BUDGET ?= $(RAM_BYTES)
endif
EXTERNAL_MEMORY ?= 0
CACHES ?= 0
MAX_CYCLES ?= 100000
STORAGE ?= 0
SD_IMAGE ?=
FS_BLOCK ?= 0
BOOT_FS_BLOCK ?= 128

# The shell's interrupt numbering is declared in the SoC component that wires
# it; the header is derived from there rather than restated here. The kernel
# image is hardware-profile-independent, so it targets the reference shell's map
# by default -- point SOC_TOP at another shell to build against its.
SOC_TOP ?= ../../components/soc/reference/soc_top.sv
IRQ_MAP := $(BUILD_DIR)/ax_irq_map.h

$(IRQ_MAP): $(SOC_TOP) ../../tools/gen_irq_map.py | $(BUILD_DIR)
	$(PYTHON) ../../tools/gen_irq_map.py --soc $(SOC_TOP) --output $@

HOSTLINK ?= 0
CPPFLAGS := -Iinclude -I$(BUILD_DIR) -DAXOS_SD=$(STORAGE) -DAXFS_BLOCK=$(FS_BLOCK)
CPPFLAGS += -DAX_EVOLUTION_TIER=$(COMPONENT_EVOLUTION_TIER) \
            -DAX_EVOLUTION_CAPABILITIES=$(COMPONENT_EVOLUTION_CAPABILITIES) \
            -DAX_EVOLUTION_CAPACITY=$(COMPONENT_EVOLUTION_CAPACITY) \
            -DAX_EVOLUTION_STATE_BUDGET=$(COMPONENT_EVOLUTION_STATE_BUDGET)
CPPFLAGS += -DAX_FITNESS_ENABLED=$(COMPONENT_FITNESS_ENABLED) \
            -DAX_FITNESS_OBJECTIVE=$(COMPONENT_FITNESS_OBJECTIVE)

# Component parameters reach the kernel the same way they reach the RTL.
# configure.py resolves a component's declared parameters -- with the profile's
# overrides applied and unknown names rejected -- into COMPONENT_DEFINES using
# Verilator's +define+ spelling; this is the compiler's spelling of the same
# thing.
#
# Without this line the mechanism was complete except for its last wire: the
# syscall component declared max_fds, path_max, write_max, io_chunk and
# role_max_payload with defaults and documentation, configure.py resolved them,
# and the kernel build dropped them on the floor. Setting one in a profile
# changed nothing and said nothing.
CPPFLAGS += $(patsubst +define+%,-D%,$(COMPONENT_DEFINES))

# TASK_SLOTS is not any one component's: the scheduler and the VM both only
# index what they are handed, so it belongs to the profile rather than to a
# manifest. Its #ifndef default lives in include/task.h, so a profile that does
# not name it builds exactly as before.
#
# The three role capacities are the same shape for the same reason. The role
# owns its memory size, but the kernel is built without a role -- KERNEL_CONFIG
# selects software components and the role comes from the hardware profile --
# so the kernel profile carries its assumptions about the role it will meet.
# ROLE_MAX_PAYLOAD and ROLE_STAGED_WORDS have #ifndef defaults in
# include/role.h, where task_slots' default also lives. ROLE_DATA_WORDS
# deliberately has none: a profile that does not declare the role's memory does
# not get the chunked transfer ops, because a capacity the build was never told
# is not one to guess.
AXOS_CAPACITIES := \
  $(if $(COMPONENT_SETTING_TASK_SLOTS),-DTASK_SLOTS=$(COMPONENT_SETTING_TASK_SLOTS)) \
  $(if $(COMPONENT_SETTING_ROLE_DATA_WORDS),-DROLE_DATA_WORDS=$(COMPONENT_SETTING_ROLE_DATA_WORDS)) \
  $(if $(COMPONENT_SETTING_ROLE_MAX_PAYLOAD),-DROLE_MAX_PAYLOAD=$(COMPONENT_SETTING_ROLE_MAX_PAYLOAD)) \
  $(if $(COMPONENT_SETTING_ROLE_STAGED_WORDS),-DROLE_STAGED_WORDS=$(COMPONENT_SETTING_ROLE_STAGED_WORDS))
CPPFLAGS += $(AXOS_CAPACITIES)
ifeq ($(KERNEL_MODE),monitor)
CPPFLAGS += -DAXOS_MONITOR=1 -DAXOS_EMBED_USER=0
else
ifeq ($(STORAGE),1)
CPPFLAGS += -DAXOS_EMBED_USER=0
KERNEL_USER_DEP = $(USER_ELF)
else
CPPFLAGS += -DAXOS_EMBED_USER=1
KERNEL_USER_DEP = $(USER_IMAGE)
endif
endif
# Host-managed personality: the console byte pipe carries the host-link
# protocol (docs/host-protocol.md) instead of the interactive shell.
ifeq ($(HOSTLINK),1)
CPPFLAGS += -DAXOS_HOSTLINK
endif
# GCC 10's ISA parser accepts rv32im (which includes CSR instructions) but
# rejects the newer explicit _zicsr spelling. Newer toolchains may override
# RISCV_ARCH without changing the source.
CFLAGS := -march=$(RISCV_ARCH) -mabi=ilp32 -mcmodel=medany -mno-relax \
          -msmall-data-limit=0 -ffreestanding -fno-builtin -fno-pic \
          -fno-stack-protector -Wall -Wextra -Werror -O2 -g
ifeq ($(KERNEL_MODE),monitor)
CFLAGS += -Os -ffunction-sections -fdata-sections
endif
LDFLAGS = -nostdlib -Wl,-T,link.ld -Wl,--defsym=AXOS_RAM_BYTES=$(RAM_BYTES) \
          -Wl,--defsym=AXOS_RESIDENT_BUDGET=$(RESIDENT_BUDGET) \
          -Wl,--gc-sections -Wl,-Map,$(@:.elf=.map)

# User programs are compiled with the profile's capacities, so their artifacts
# are keyed by the profile the way rtl/fpga keys its bitstreams. A single
# `userprog/` directory made switching profiles produce a stale binary carrying
# the previous profile's limits -- and because make compares against a
# *different* .mk file per profile, switching back did not rebuild it either.
USER_BUILD := $(BUILD_DIR)/userprog$(KERNEL_CONFIG_KEY)
USER_ELF := $(USER_BUILD)/hello.elf
USER_IMAGE := $(BUILD_DIR)/userprog_image.h

# This has to sit below USER_ELF, not up with the rest of CPPFLAGS: CPPFLAGS is
# simply-expanded, so `+=` expands the reference at the line it is written on.
# Set above, it compiled to -DAXOS_EMBED_USER_NAME='""' -- a valid build whose
# embedded program answered to no name, so `run hello.elf` returned ENOENT and
# only check-shell noticed. Empty is never right here, so say so at parse time
# rather than let the next reordering fail the same silent way.
ifeq ($(strip $(USER_ELF)),)
$(error USER_ELF must be defined before AXOS_EMBED_USER_NAME is built from it)
endif
CPPFLAGS += -DAXOS_EMBED_USER_NAME='"$(notdir $(USER_ELF))"'
# User programs are built against the selected libc component, and against the
# toolchain's own compiler runtime so 64-bit arithmetic resolves: RV32IM has no
# 64-bit divide, so `wide / 3ull` becomes a call to __udivdi3. RISCV_RTLIB is
# whichever runtime the selected toolchain owns -- libgcc under GCC,
# compiler-rt under LLVM -- and never the other one's, so a build reports the
# provenance it actually has. It comes after the objects because it only needs
# to satisfy references they leave behind.
USER_CFLAGS := -march=$(RISCV_ARCH) -mabi=ilp32 -mcmodel=medany -mno-relax \
               -msmall-data-limit=0 -ffreestanding -fno-builtin -fno-pic \
               -fno-stack-protector -Wall -Wextra -Werror -O2 -nostdlib \
               -I$(COMPONENT_LIBC_INCLUDE) $(AXOS_CAPACITIES) \
               $(patsubst +define+%,-D%,$(COMPONENT_DEFINES))

# The capacities reach these programs as -D, so the resolved profile is a
# real prerequisite: without it a stale binary keeps the previous
# profile's limits and disagrees with the kernel it is testing.
$(USER_ELF): userprog/hello.c userprog/user.ld $(COMPONENT_LIBC_SOURCES) \
             $(KERNEL_COMPONENT_MK)
	mkdir -p $(@D)
	$(CC) $(USER_CFLAGS) -Wl,-T,userprog/user.ld -o $@ \
	  userprog/hello.c $(COMPONENT_LIBC_SOURCES) $(RISCV_RTLIB)
	$(RISCV_STRIP) -s -R .comment $@

# The adversarial ABI program. It is never embedded in the kernel: it reaches
# the machine from the AXFS image, so testing every refusal path costs the
# shipped image nothing.
TORTURE_ELF := $(USER_BUILD)/torture.elf
$(TORTURE_ELF): userprog/torture.c userprog/user.ld $(COMPONENT_LIBC_SOURCES) \
                $(KERNEL_COMPONENT_MK)
	mkdir -p $(@D)
	$(CC) $(USER_CFLAGS) -Wl,-T,userprog/user.ld -o $@ \
	  userprog/torture.c $(COMPONENT_LIBC_SOURCES) $(RISCV_RTLIB)
	$(RISCV_STRIP) -s -R .comment $@

$(USER_IMAGE): $(USER_ELF) embed_image.py
	mkdir -p $(@D)
	$(PYTHON) embed_image.py $(USER_ELF) $@ axos_user_image

ELF := $(BUILD_DIR)/axos_boot.elf
BIN := $(ELF:.elf=.bin)
HEX := $(ELF:.elf=.hex)
ifeq ($(KERNEL_MODE),monitor)
# The monitor personality has a 32 KiB image budget and no interrupt policy of
# its own: it never routes a source, so the PLIC and console drivers are left
# out entirely rather than linked and garbage-collected.  Role waits and
# console reads there stay polled, exactly as they do on any platform without a
# controller -- which is also why this personality selects shell.monitor rather
# than shell.axsh, the shell that reads through the console driver.
SOURCES := boot.S trap.S monitor.c role.c \
           $(COMPONENT_ALLOCATOR_SOURCES) $(COMPONENT_SHELL_SOURCES) \
           $(COMPONENT_EVOLUTION_SOURCES) $(COMPONENT_FITNESS_SOURCES)
else
SOURCES := boot.S trap.S user.S kernel.c plic.c console.c role.c $(COMPONENT_SYSCALL_SOURCES) \
           $(COMPONENT_LOADER_SOURCES) \
           $(COMPONENT_ALLOCATOR_SOURCES) $(COMPONENT_SHELL_SOURCES) \
           $(COMPONENT_FILESYSTEM_SOURCES) $(COMPONENT_BLOCK_SOURCES) \
           $(COMPONENT_SCHEDULER_SOURCES) $(COMPONENT_VM_SOURCES) \
           $(COMPONENT_EVOLUTION_SOURCES) $(COMPONENT_FITNESS_SOURCES)
endif
ifeq ($(HOSTLINK),1)
SOURCES += hostlink.c
endif

all: $(ELF)
images: $(BIN) $(HEX)

$(ELF): $(SOURCES) $(KERNEL_COMPONENT_MK) $(KERNEL_USER_DEP) $(IRQ_MAP) link.ld include/platform.h include/page.h include/task.h include/scheduler.h include/vm.h include/syscall.h include/loader.h include/fs.h include/sd.h include/role.h include/plic.h include/hostlink.h include/kernel_info.h include/process.h include/evolution.h include/fitness.h | $(BUILD_DIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ $(SOURCES)

$(BIN): $(ELF)
	$(OBJCOPY) -O binary $< $@

$(HEX): $(BIN) ../../tests/directed/bin2hex.py
	python3 ../../tests/directed/bin2hex.py $< $@

$(BUILD_DIR):
	mkdir -p $@

run-iss: $(ELF) ../../sim/axsim/axsim
	../../sim/axsim/axsim --bin $<

run-qemu: $(ELF)
	timeout --foreground 10s $(QEMU) -machine virt -bios none \
	  -cpu rv32,pmp=false -nographic -kernel $<

run-rtl: $(HEX)
	$(MAKE) -C ../../sim/soc run RAM_INIT_FILE=$(abspath $<) RESET_PC=0x80000000 \
	  UART_INPUT_FILE=$(UART_INPUT_FILE) RAM_BYTES=$(RAM_BYTES) \
	  EXTERNAL_MEMORY=$(EXTERNAL_MEMORY) CACHES=$(CACHES) MAX_CYCLES=$(MAX_CYCLES) SD_IMAGE=$(SD_IMAGE)

check-boot: images
	$(MAKE) -B images STORAGE=0
	$(MAKE) -C ../../sim/axsim axsim
	python3 check_boot.py

check-shell:
	$(MAKE) -B $(ELF) STORAGE=0 HOSTLINK=0
	$(MAKE) -C ../../sim/axsim axsim
	$(PYTHON) check_shell.py

primer-images:
	$(MAKE) images KERNEL_MODE=monitor \
	  KERNEL_CONFIG=$(abspath ../../configs/kernel-primer-monitor.json) \
	  BUILD_DIR=build/primer RAM_BYTES=32768 STORAGE=0 HOSTLINK=0

check-primer: primer-images
	$(MAKE) -C ../../sim/axsim axsim
	$(PYTHON) check_primer.py

primer-runtime-images:
	$(MAKE) images KERNEL_MODE=monitor HOSTLINK=1 \
	  KERNEL_CONFIG=$(abspath ../../configs/kernel-primer-monitor.json) \
	  BUILD_DIR=build/primer-runtime RAM_BYTES=32768 RESIDENT_BUDGET=16384 \
	  STORAGE=0

primer-uartboot:
	$(MAKE) -C ../bootrom images MODE=uart RAM_BYTES=32768 \
	  BUILD_DIR=build/uart-ram32768

uartboot-images:
	$(MAKE) -B images HOSTLINK=0 STORAGE=0
	$(MAKE) -C ../bootrom images MODE=uart RAM_BYTES=131072 \
	  BUILD_DIR=build/uart-ram131072

check-uartboot: uartboot-images
	$(PYTHON) check_uartboot.py

check-primer-runtime: primer-runtime-images primer-uartboot
	python3 ../host/axhost.py --fast-switch \
	  --boot-rom $(abspath ../bootrom/build/uart-ram32768/bootrom.hex) \
	  --upload-kernel $(abspath build/primer-runtime/axos_boot.bin) \
	  --config $(abspath ../../configs/sim-primer-runtime-gpu.json) \
	  --baud 921600 --max-cycles 2000000

# RTL-only: aXos's in-kernel role driver discovers and drives the accelerator
# through the shell control plane.  Selects role.loopback so the shell's `role`
# command exercises the full descriptor/doorbell/completion cycle.
check-role-driver: images
	$(MAKE) -B images STORAGE=0
	python3 check_role_driver.py

# The same driver, but the claim underneath it: completion arrives as a
# supervisor external interrupt through the PLIC's S-mode context, and two
# consecutive jobs prove the level-sensitive source is completed and re-armed.
check-role-irq: images
	$(MAKE) -B images STORAGE=0
	python3 check_role_irq.py

# RTL-only: build the host-managed personality and drive it with axhost over
# the simulated console byte pipe (the virtual-pipe transport), against
# role.loopback.  Proves the whole host -> shell -> role control plane.
check-hostlink:
	$(MAKE) -B images HOSTLINK=1
	python3 ../host/axhost.py --demo --role loopback \
	  --image $(abspath build/axos_boot.hex) \
	  --config $(abspath ../../configs/sim-role-loopback.json)
	python3 ../host/axhost.py --demo --role tpu \
	  --image $(abspath build/axos_boot.hex) \
	  --config $(abspath ../../configs/sim-tpu-lite.json)
	python3 ../host/axhost.py --demo --role gpu \
	  --image $(abspath build/axos_boot.hex) \
	  --config $(abspath ../../configs/sim-gpu-compute.json)

# The same control plane, but a job whose data no longer fits the frame that
# asks for it.
#
# Three legs, all on sim-gpu-compute, so the hardware is constant at 4096 role
# words and what varies is only what the kernel profile declares. That is the
# point: pairing each declared capacity with matching hardware would leave both
# bounds agreeing and the run unable to say which one it measured.
#
#   1. 4096 declared -- reaches all of it, rejects word 4096.
#   2. 1024 declared, with the payload and staged caps also moved off their
#      defaults -- bounds at exactly 1024 on 4096-word hardware, and the
#      79-word chunk cap that a 320-byte frame implies is enforced too.
#   3. nothing declared -- the ops are not compiled and answer BAD_OP, because
#      a capacity the build was never told is not one to guess.
#
# The host derives every limit from the profile and from role.h's #ifndef
# defaults, and probes the device for its own edge, so no number is written
# down in the test. Re-hardcoding any of the three fails at least one leg.
check-hostlink-stream:
	$(MAKE) -B images HOSTLINK=1 BUILD_DIR=build/stream \
	  KERNEL_CONFIG=$(abspath ../../configs/kernel-hostlink-stream.json)
	$(PYTHON) ../host/axstream.py \
	  --image $(abspath build/stream/axos_boot.hex) \
	  --kernel-config $(abspath ../../configs/kernel-hostlink-stream.json) \
	  --config $(abspath ../../configs/sim-gpu-compute.json) \
	  --max-cycles 4000000
	$(MAKE) -B images HOSTLINK=1 BUILD_DIR=build/stream-caps \
	  KERNEL_CONFIG=$(abspath ../../configs/kernel-stream-caps.json)
	$(PYTHON) ../host/axstream.py \
	  --image $(abspath build/stream-caps/axos_boot.hex) \
	  --kernel-config $(abspath ../../configs/kernel-stream-caps.json) \
	  --config $(abspath ../../configs/sim-gpu-compute.json) \
	  --max-cycles 4000000
	$(MAKE) -B images HOSTLINK=1 BUILD_DIR=build/stream-none
	$(PYTHON) ../host/axstream.py \
	  --image $(abspath build/stream-none/axos_boot.hex) \
	  --kernel-config $(abspath ../../configs/kernel-default.json) \
	  --config $(abspath ../../configs/sim-gpu-compute.json) \
	  --max-cycles 4000000

check-memory: images
	$(MAKE) -B images STORAGE=0
	python3 check_boot.py --external-memory

check-storage: images $(USER_ELF)
	$(MAKE) -B images STORAGE=1
	python3 make_fs_image.py $(USER_ELF) build/axfs.img
	SD_IMAGE=$(abspath build/axfs.img) python3 check_boot.py --external-memory

check-storage-write: images $(USER_ELF)
	$(MAKE) -B images STORAGE=1
	python3 make_fs_image.py $(USER_ELF) build/axfs.img
	SD_IMAGE=$(abspath build/axfs.img) python3 check_boot.py --storage-write

# The ABI under attack rather than under demonstration: null, kernel-space and
# straddling pointers, a read into a read-only page, an unterminated path, a
# full descriptor table, and seek arithmetic that overflows -- at whatever
# capacities the profile declares.
#
# One recipe, invoked through a sub-make so that KERNEL_CONFIG is a command-line
# variable and the := paths below it (which are keyed by the profile) resolve
# to that profile's artifacts. Setting it as a target-specific variable does not
# work here: those apply to the recipe, but the paths were already expanded at
# parse time against the default profile.
TORTURE_IMG ?= build/axfs-torture.img
torture-run: $(TORTURE_ELF) $(USER_ELF)
	$(MAKE) -B images STORAGE=1 RAM_BYTES=$(TORTURE_RAM_BYTES)
	python3 make_fs_image.py --with-torture $(TORTURE_ELF) $(USER_ELF) \
	  $(TORTURE_IMG)
	SD_IMAGE=$(abspath $(TORTURE_IMG)) python3 check_boot.py --torture

# Linked for 1 MiB rather than the default 128 KiB. The RTL already provides
# 32 MiB; the kernel's own AXOS_RAM_BYTES is what bounds its page pool, and at
# 128 KiB there are about fifteen free pages -- not enough to clone an address
# space, so the fork checks could not run at all.
TORTURE_RAM_BYTES ?= 1048576

check-abi-torture:
	$(MAKE) torture-run

# The same program against non-default capacities: 2 task slots, 3 descriptors,
# a 12-byte path limit, 3 argv entries. It derives every limit from the same -D
# the kernel was built with, so passing here *and* at the defaults is the
# evidence that the knobs are real -- a hardcoded capacity makes exactly one of
# the two runs fail, and each of the counts it checks is exact.
check-abi-torture-small:
	$(MAKE) torture-run \
	  KERNEL_CONFIG=$(abspath ../../configs/kernel-small-caps.json) \
	  TORTURE_IMG=build/axfs-torture-small.img

# The loader's W^X rejection, end to end. The fixture lives on the AXFS image
# rather than in the built-in root, so testing a rejection costs the shipped
# kernel image nothing.
check-loader-wx: images $(USER_ELF)
	$(MAKE) -B images STORAGE=1
	python3 make_fs_image.py --with-wx $(USER_ELF) build/axfs-wx.img
	SD_IMAGE=$(abspath build/axfs-wx.img) python3 check_boot.py --loader-wx

boot-disk: images
	$(MAKE) -B images STORAGE=1 FS_BLOCK=$(BOOT_FS_BLOCK)
	$(MAKE) -C ../bootrom images
	python3 make_boot_disk.py build/axos_boot.bin $(USER_ELF) \
	  build/axos_boot.img $(BOOT_FS_BLOCK)

check-sdboot: boot-disk
	SD_IMAGE=$(abspath build/axos_boot.img) BOOT_ROM=$(abspath ../bootrom/build/bootrom.hex) python3 check_boot.py --sd-boot

kernel-config: $(KERNEL_COMPONENT_MK)
	@test -n "$(COMPONENT_SCHEDULER_ID)" || { echo "$(KERNEL_CONFIG): select a scheduler component"; exit 2; }
	@test -n "$(COMPONENT_VM_ID)" || { echo "$(KERNEL_CONFIG): select a vm component"; exit 2; }
	@test -n "$(COMPONENT_ALLOCATOR_ID)" || { echo "$(KERNEL_CONFIG): select an allocator component"; exit 2; }
	@test -n "$(COMPONENT_SHELL_ID)" || { echo "$(KERNEL_CONFIG): select a shell component"; exit 2; }
	@test -n "$(COMPONENT_FILESYSTEM_ID)" || { echo "$(KERNEL_CONFIG): select a filesystem component"; exit 2; }
	@test -n "$(COMPONENT_BLOCK_ID)" || { echo "$(KERNEL_CONFIG): select a block component"; exit 2; }
	@test -n "$(COMPONENT_EVOLUTION_ID)" || { echo "$(KERNEL_CONFIG): select an evolution component"; exit 2; }
	@test -n "$(COMPONENT_FITNESS_ID)" || { echo "$(KERNEL_CONFIG): select a fitness component"; exit 2; }
	@echo "[kernel] scheduler: $(COMPONENT_SCHEDULER_ID)"
	@echo "[kernel] vm:        $(COMPONENT_VM_ID)"
	@echo "[kernel] allocator: $(COMPONENT_ALLOCATOR_ID)"
	@echo "[kernel] shell:     $(COMPONENT_SHELL_ID)"
	@echo "[kernel] filesystem: $(COMPONENT_FILESYSTEM_ID)"
	@echo "[kernel] block:     $(COMPONENT_BLOCK_ID)"
	@echo "[kernel] evolution: $(COMPONENT_EVOLUTION_ID)"
	@echo "[kernel] fitness:   $(COMPONENT_FITNESS_ID)"

# The policy unit test checks exact capacity, correctness gating, deterministic
# tie-breaking, and overflow behavior for every implementation. The profile
# test then links every public kernel-evolve profile inside the Primer's exact
# 32 KiB memory envelope, confirms its linked state allocation, and boots it on
# the ISS.
HOST_CC ?= cc
EVOLUTION_CORE := ../../components/evolution/core/evolution.c
EVOLUTION_TEST := check_evolution.c
FITNESS_CORE := ../../components/fitness/core/fitness.c
FITNESS_TEST := check_fitness.c

evolution-policy-test: | $(BUILD_DIR)
	$(HOST_CC) -std=c11 -Wall -Wextra -Werror -Iinclude \
	  -DAX_EVOLUTION_TIER=0 -DAX_EVOLUTION_CAPABILITIES=0 \
	  -DAX_EVOLUTION_CAPACITY=0 -DAX_EVOLUTION_STATE_BUDGET=0 \
	  $(EVOLUTION_CORE) $(EVOLUTION_TEST) -o $(BUILD_DIR)/evolution-none-test
	$(BUILD_DIR)/evolution-none-test
	$(HOST_CC) -std=c11 -Wall -Wextra -Werror -Iinclude \
	  -DAX_EVOLUTION_TIER=1 -DAX_EVOLUTION_CAPABILITIES=3 \
	  -DAX_EVOLUTION_CAPACITY=4 -DAX_EVOLUTION_STATE_BUDGET=96 \
	  $(EVOLUTION_CORE) $(EVOLUTION_TEST) -o $(BUILD_DIR)/evolution-small-test
	$(BUILD_DIR)/evolution-small-test
	$(HOST_CC) -std=c11 -Wall -Wextra -Werror -Iinclude \
	  -DAX_EVOLUTION_TIER=2 -DAX_EVOLUTION_CAPABILITIES=3 \
	  -DAX_EVOLUTION_CAPACITY=16 -DAX_EVOLUTION_STATE_BUDGET=336 \
	  $(EVOLUTION_CORE) $(EVOLUTION_TEST) -o $(BUILD_DIR)/evolution-mid-test
	$(BUILD_DIR)/evolution-mid-test
	$(HOST_CC) -std=c11 -Wall -Wextra -Werror -Iinclude \
	  -DAX_EVOLUTION_TIER=3 -DAX_EVOLUTION_CAPABILITIES=3 \
	  -DAX_EVOLUTION_CAPACITY=64 -DAX_EVOLUTION_STATE_BUDGET=1296 \
	  $(EVOLUTION_CORE) $(EVOLUTION_TEST) -o $(BUILD_DIR)/evolution-large-test
	$(BUILD_DIR)/evolution-large-test

fitness-policy-test: | $(BUILD_DIR)
	$(HOST_CC) -std=c11 -Wall -Wextra -Werror -Iinclude \
	  -DAX_FITNESS_ENABLED=0 -DAX_FITNESS_OBJECTIVE=0 \
	  $(FITNESS_CORE) $(FITNESS_TEST) -o $(BUILD_DIR)/fitness-none-test
	$(BUILD_DIR)/fitness-none-test
	$(HOST_CC) -std=c11 -Wall -Wextra -Werror -Iinclude \
	  -DAX_FITNESS_ENABLED=1 -DAX_FITNESS_OBJECTIVE=1 \
	  $(FITNESS_CORE) $(FITNESS_TEST) -o $(BUILD_DIR)/fitness-cycles-test
	$(BUILD_DIR)/fitness-cycles-test

evolution-profile-images:
	$(MAKE) images KERNEL_CONFIG=$(abspath ../../configs/kernel-primer-monitor.json) BUILD_DIR=build/evolution-none
	$(MAKE) images KERNEL_CONFIG=$(abspath ../../configs/kernel-evolve-small.json) BUILD_DIR=build/evolution-small
	$(MAKE) images KERNEL_CONFIG=$(abspath ../../configs/kernel-evolve-mid.json) BUILD_DIR=build/evolution-mid
	$(MAKE) images KERNEL_CONFIG=$(abspath ../../configs/kernel-evolve-large.json) BUILD_DIR=build/evolution-large

evolution-check: kernel-config-check-all evolution-policy-test fitness-policy-test evolution-profile-images
	$(MAKE) -C ../../sim/axsim axsim
	$(PYTHON) check_evolution_profiles.py

kernel-config-check:
	$(PYTHON) ../../tools/configure.py resolve --config "$(KERNEL_CONFIG)"

kernel-config-check-all:
	@for kernel_config in ../../configs/kernel-*.json; do \
	  $(PYTHON) ../../tools/configure.py resolve --config "$$kernel_config" >/dev/null || exit $$?; \
	  echo "kernel configuration: $$kernel_config: PASS"; \
	done

# Both policies must preserve the same shell/fork semantics. The cooperative
# profile is an actual alternate scheduler, not merely a manifest parse test.
kernel-component-test: kernel-config-check-all
	$(MAKE) -B check-boot KERNEL_CONFIG=$(abspath ../../configs/kernel-default.json)
	$(MAKE) -B check-boot KERNEL_CONFIG=$(abspath ../../configs/kernel-cooperative.json)

clean:
	rm -rf build

.PHONY: all images primer-images primer-runtime-images primer-uartboot uartboot-images run-iss run-qemu run-rtl check-boot check-shell check-primer check-primer-runtime check-uartboot check-role-driver check-role-irq check-hostlink check-hostlink-stream check-memory check-storage check-storage-write check-loader-wx check-abi-torture check-abi-torture-small torture-run boot-disk check-sdboot kernel-config kernel-config-check kernel-config-check-all kernel-component-test evolution-policy-test fitness-policy-test evolution-profile-images evolution-check clean
