RISCV_PREFIX ?= riscv64-unknown-elf-
CC := $(RISCV_PREFIX)gcc
OBJCOPY := $(RISCV_PREFIX)objcopy
MODE ?= sd
RAM_BYTES ?= 131072
BUILD_DIR ?= build
CFLAGS := -march=rv32im -mabi=ilp32 -mcmodel=medany -mno-relax -ffreestanding \
  -fno-builtin -fno-pic -fno-stack-protector -Wall -Wextra -Werror -Os
CPPFLAGS := $(if $(filter uart,$(MODE)),-DAXBOOT_UART=1,-DAXBOOT_UART=0)
ELF := $(BUILD_DIR)/bootrom.elf

all: $(ELF)
images: $(ELF:.elf=.hex)
$(ELF): boot.S boot.c link.ld | $(BUILD_DIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) -nostdlib -Wl,-T,link.ld \
	  -Wl,--defsym=AXBOOT_RAM_BYTES=$(RAM_BYTES) -Wl,--gc-sections \
	  -o $@ boot.S boot.c
$(BUILD_DIR):
	mkdir -p $@
$(BUILD_DIR)/bootrom.bin: $(ELF)
	$(OBJCOPY) -O binary $< $@
$(BUILD_DIR)/bootrom.hex: $(BUILD_DIR)/bootrom.bin ../../tests/directed/bin2hex.py
	python3 ../../tests/directed/bin2hex.py $< $@
clean:
	rm -rf build
.PHONY: all images clean
