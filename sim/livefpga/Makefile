include ../../mk/toolchain.mk

CC ?= cc
CFLAGS ?= -O2
CFLAGS += -std=c11 -Wall -Wextra -Werror

BUILD_DIR := build
TARGET := $(BUILD_DIR)/live-evolution-sim
RV32_TARGET := $(BUILD_DIR)/live-evolution-rv32.elf
RV32_CC := $(RISCV_PREFIX)gcc
AXSIM := ../axsim/axsim
KERNEL_INCLUDE := ../../sw/kernel/include
EVOLUTION := ../../components/evolution/core/evolution.c
FITNESS := ../../components/fitness/core/fitness.c
DEFINES := \
	-DAX_EVOLUTION_TIER=1 \
	-DAX_EVOLUTION_CAPABILITIES=3 \
	-DAX_EVOLUTION_CAPACITY=4 \
	-DAX_EVOLUTION_STATE_BUDGET=96 \
	-DAX_FITNESS_ENABLED=1 \
	-DAX_FITNESS_OBJECTIVE=1

$(TARGET): tb_live_evolution.c $(EVOLUTION) $(FITNESS) \
	$(KERNEL_INCLUDE)/evolution.h $(KERNEL_INCLUDE)/fitness.h
	mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) $(DEFINES) -I$(KERNEL_INCLUDE) \
		tb_live_evolution.c $(EVOLUTION) $(FITNESS) -o $@

$(RV32_TARGET): tb_live_evolution_rv32.c $(EVOLUTION) $(FITNESS) \
	$(KERNEL_INCLUDE)/evolution.h $(KERNEL_INCLUDE)/fitness.h \
	../../sw/baremetal/crt0.S ../../sw/baremetal/link.ld
	mkdir -p $(BUILD_DIR)
	$(RV32_CC) -std=c11 -Wall -Wextra -Werror -O2 \
		-march=$(RISCV_ARCH) -mabi=ilp32 -mcmodel=medany -mno-relax \
		-msmall-data-limit=0 -ffreestanding -fno-builtin -fno-pic \
		-fno-stack-protector $(DEFINES) -I$(KERNEL_INCLUDE) -nostdlib \
		-Wl,-T,../../sw/baremetal/link.ld -Wl,--defsym=__ram_bytes=32768 \
		-Wl,--gc-sections -o $@ ../../sw/baremetal/crt0.S \
		tb_live_evolution_rv32.c $(EVOLUTION) $(FITNESS)

$(AXSIM):
	$(MAKE) -C ../axsim axsim

check: $(TARGET) $(RV32_TARGET) $(AXSIM)
	$(TARGET)
	$(AXSIM) --bin $(RV32_TARGET) --ram-bytes 32768 --max 1000000
	@echo "live evolution RV32/aXsim: PASS"

clean:
	rm -rf $(BUILD_DIR)

.PHONY: check clean
