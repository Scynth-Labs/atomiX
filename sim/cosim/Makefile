# Lock-step ISS/RTL cosimulation.  The harness links aXsim directly into the
# Verilator executable, so it compares each architectural event without any
# file-based trace parsing.
include ../../mk/toolchain.mk
VERILATOR ?= verilator
VFLAGS    := -Wall --cc -O2
# Shared component packages intentionally expose operations that a selected
# implementation may not consume. Verilator 5 reports those package constants
# as UNUSEDPARAM and makes them fatal under -Wall; match the SoC runner's
# version-gated policy without weakening any functional warning.
ifneq ($(filter 5 6 7 8 9,$(VERILATOR_MAJOR)),)
VFLAGS += -Wno-UNUSEDPARAM
endif

CORE_COMPONENT := ../../components/core/pipeline5
COMPONENTS := ../../components
AXSIM     := ../axsim/src
# Functional-unit selections.  Override any of these on the command line to
# run the full lock-step evidence against a swapped unit, for example:
#   make test rv32um MULDIV_SV=../../components/muldiv/fast-mul/muldiv.sv
ALU_SV     ?= $(COMPONENTS)/alu/single-cycle/alu.sv
MULDIV_SV  ?= $(COMPONENTS)/muldiv/iterative32/muldiv.sv
REGFILE_SV ?= $(COMPONENTS)/regfile/flipflop/regfile.sv
MMU_SV     ?= $(COMPONENTS)/mmu/sv32/sv32_mmu.sv
CORE_SOURCES := $(CORE_COMPONENT)/axcore_pkg.sv \
  $(REGFILE_SV) $(ALU_SV) \
  $(CORE_COMPONENT)/immdec.sv $(CORE_COMPONENT)/decoder.sv \
  $(CORE_COMPONENT)/branch_cmp.sv \
  $(MULDIV_SV) \
  $(CORE_COMPONENT)/csr_file.sv \
  $(MMU_SV) \
  $(CORE_COMPONENT)/axcore.sv

axcosim: $(CORE_SOURCES) tb_cosim.cpp $(AXSIM)/bus.h \
         $(AXSIM)/cpu.h $(AXSIM)/cpu.cpp $(AXSIM)/elf.h $(AXSIM)/elf.cpp
	$(VERILATOR) $(VFLAGS) --top-module axcore --Mdir obj_dir --exe --build \
	  -o axcosim $(CORE_SOURCES) tb_cosim.cpp \
	  $(AXSIM)/cpu.cpp $(AXSIM)/elf.cpp -CFLAGS '-I$(abspath $(AXSIM))'

test: axcosim
	@mkdir -p build
	python3 ../../tests/directed/words2bin.py ../../tests/directed/smoke_ok.words build/smoke_ok.bin
	python3 ../../tests/directed/words2bin.py ../../tests/directed/trap_ecall.words build/trap_ecall.bin
	./obj_dir/axcosim --bin build/smoke_ok.bin
	./obj_dir/axcosim --bin build/smoke_ok.bin --ws 3
	./obj_dir/axcosim --bin build/trap_ecall.bin
	./obj_dir/axcosim --bin build/trap_ecall.bin --ws 2
	@echo "cosim directed tests: PASS"

# The ISA binaries must be built first as documented in tests/README.md.
riscv-tests: axcosim
	SIM=$(CURDIR)/obj_dir/axcosim ../../tests/run-riscv-tests.sh

M_TESTS := rv32um-p-div rv32um-p-divu rv32um-p-mul rv32um-p-mulh \
           rv32um-p-mulhsu rv32um-p-mulhu rv32um-p-rem rv32um-p-remu

rv32um: axcosim
	$(MAKE) -C ../../tests/riscv-tests/isa XLEN=32 RISCV_PREFIX=riscv64-unknown-elf- $(M_TESTS)
	SIM=$(CURDIR)/obj_dir/axcosim ../../tests/run-riscv-tests.sh rv32um

clean:
	rm -rf obj_dir build

.PHONY: test riscv-tests rv32um clean
