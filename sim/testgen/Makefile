COSIM := ../cosim
SIM   := $(abspath $(COSIM)/obj_dir/axcosim)

# Phase-1 exit campaign. Override these for a short local check, e.g.
# `make quick` or `make fuzz FUZZ_EVENTS=100000 FUZZ_INSNS=10000`.
FUZZ_EVENTS ?= 10000000
FUZZ_INSNS  ?= 1000000
FUZZ_SEED   ?= 1
include ../../mk/toolchain.mk
PAGING_SEEDS ?= 10
PAGING_INSNS ?= 10000
PAGING_WS    ?= 2
# The bare-metal link script takes its RAM region length from __ram_bytes;
# match the RAM the cosim harness models so the link-time overflow check
# reflects the memory the generated program will actually run in.
PAGING_RAM_BYTES ?= 33554432   # tb_cosim.cpp

quick: $(SIM)
	@mkdir -p build
	python3 gen.py --seed $(FUZZ_SEED) --count 10000 --out build/quick.bin
	$(SIM) --bin build/quick.bin --ws 2

fuzz: $(SIM)
	python3 run.py --sim $(SIM) --target-events $(FUZZ_EVENTS) \
	  --program-insns $(FUZZ_INSNS) --seed $(FUZZ_SEED)

# Phase-4 companion to the legacy M-mode generator.  Every seed executes a
# randomized user-mode RV32I stream through Sv32, then lock-step checks its
# return to M-mode.  The generated source is kept under build/ for replay.
paging: $(SIM)
	@mkdir -p build
	@total=0; \
	for seed in $$(seq 1 $(PAGING_SEEDS)); do \
	  src=build/sv32_$$seed.S; elf=build/sv32_$$seed.elf; bin=build/sv32_$$seed.bin; \
	  python3 sv32.py --seed $$seed --count $(PAGING_INSNS) --out $$src || exit 1; \
	  $(RISCV_CC) -march=$(RISCV_ARCH_I) -mabi=ilp32 -nostdlib -nostartfiles \
	    -Wl,--defsym=__ram_bytes=$(PAGING_RAM_BYTES) \
	    -Wl,-T,../../sw/baremetal/link.ld -o $$elf $$src || exit 1; \
	  $(RISCV_OBJCOPY) -O binary $$elf $$bin || exit 1; \
	  $(SIM) --bin $$bin --ws $(PAGING_WS) || exit 1; \
	  total=$$((total + $(PAGING_INSNS))); \
	  echo "[testgen] Sv32 seed=$$seed $(PAGING_INSNS) generated user instructions; total=$$total"; \
	done

$(SIM):
	$(MAKE) -C $(COSIM) axcosim

clean:
	rm -rf build

.PHONY: quick fuzz paging clean
