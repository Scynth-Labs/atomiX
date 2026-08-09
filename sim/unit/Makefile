# Unit testbenches: one Verilator build+run per RTL module.
# `make` runs them all; `make run-<module>` runs one.
include ../../mk/toolchain.mk
VFLAGS    := -Wall --cc -O2
# Some unit tops elaborate only part of the shared operation package. Suppress
# Verilator 5's package-level UNUSEDPARAM diagnostic just as sim/soc does;
# older releases do not recognize the warning name.
ifneq ($(filter 5 6 7 8 9,$(VERILATOR_MAJOR)),)
VFLAGS += -Wno-UNUSEDPARAM
endif

CORE_COMPONENT := ../../components/core/pipeline5
COMPONENTS := ../../components
ALU_REFERENCE := $(COMPONENTS)/alu/single-cycle
MULDIV_REFERENCE := $(COMPONENTS)/muldiv/iterative32
MULDIV_FASTMUL := $(COMPONENTS)/muldiv/fast-mul
MULDIV_RADIX4 := $(COMPONENTS)/muldiv/radix4
REGFILE_REFERENCE := $(COMPONENTS)/regfile/flipflop
MMU_REFERENCE := $(COMPONENTS)/mmu/sv32
MEMORY_REFERENCE := $(COMPONENTS)/memory/reference
INTERCONNECT_REFERENCE := $(COMPONENTS)/interconnect/axbus-reference
CACHE_REFERENCE := $(COMPONENTS)/cache/direct-mapped
ROM_REFERENCE := $(COMPONENTS)/rom/axrom
CLINT_REFERENCE := $(COMPONENTS)/clint/qemu_virt
PLIC_REFERENCE := $(COMPONENTS)/plic/qemu_virt
UART_REFERENCE := $(COMPONENTS)/uart/mmio16550
SPI_REFERENCE := $(COMPONENTS)/spi/polling_mode0
FINISHER_REFERENCE := $(COMPONENTS)/finisher/sifive-test
ROLE_REFERENCE := $(COMPONENTS)/role/none
SOC_REFERENCE := $(COMPONENTS)/soc/reference
LIVE_MONITOR := $(SOC_REFERENCE)/axlivemon.sv
HARNESS_SDRAM := $(COMPONENTS)/harness/verilator-sdram
BOARD_ULX3S := $(COMPONENTS)/board/ulx3s_85f

MODULES := regfile alu immdec decoder branch_cmp muldiv plic

# Functional units selected out of the core live in their own components;
# each unit testbench builds the one source named here.
UNIT_SV_regfile    := $(REGFILE_REFERENCE)/regfile.sv
UNIT_SV_alu        := $(ALU_REFERENCE)/alu.sv
UNIT_SV_immdec     := $(CORE_COMPONENT)/immdec.sv
UNIT_SV_decoder    := $(CORE_COMPONENT)/decoder.sv
UNIT_SV_branch_cmp := $(CORE_COMPONENT)/branch_cmp.sv
UNIT_SV_muldiv     := $(MULDIV_REFERENCE)/muldiv.sv
UNIT_SV_plic       := $(PLIC_REFERENCE)/plic.sv

CORE_SOURCES := $(CORE_COMPONENT)/axcore_pkg.sv $(UNIT_SV_regfile) \
  $(UNIT_SV_alu) $(UNIT_SV_immdec) $(UNIT_SV_decoder) $(UNIT_SV_branch_cmp) \
  $(UNIT_SV_muldiv) $(CORE_COMPONENT)/csr_file.sv $(MMU_REFERENCE)/sv32_mmu.sv \
  $(CORE_COMPONENT)/axcore.sv
# core.minimal shares the reference core's decoder, immdec and branch comparator
# but has its own inline CSR block and single always_ff machine, so it needs its
# own ISA run -- passing rv32ui says nothing about its trap and CSR surface.
MINIMAL_SOURCES := $(CORE_COMPONENT)/axcore_pkg.sv $(UNIT_SV_regfile) \
  $(UNIT_SV_alu) $(UNIT_SV_immdec) $(UNIT_SV_decoder) $(UNIT_SV_branch_cmp) \
  $(UNIT_SV_muldiv) $(COMPONENTS)/core/minimal/axcore.sv
MEMORY_SOURCES := $(MEMORY_REFERENCE)/axram.sv $(MEMORY_REFERENCE)/axdram_model.sv \
  $(MEMORY_REFERENCE)/axsdram.sv $(MEMORY_REFERENCE)/axmem_reference.sv \
  $(MEMORY_REFERENCE)/axmem.sv
SOC_SVS := $(CORE_SOURCES) \
  $(INTERCONNECT_REFERENCE)/axbus_mux.sv $(CACHE_REFERENCE)/axcache.sv \
  $(MEMORY_SOURCES) $(ROM_REFERENCE)/axrom.sv \
  $(CLINT_REFERENCE)/clint.sv $(PLIC_REFERENCE)/plic.sv \
  $(UART_REFERENCE)/uart.sv \
  $(SPI_REFERENCE)/axspi.sv $(FINISHER_REFERENCE)/test_finisher.sv \
  $(ROLE_REFERENCE)/axrole.sv \
  $(LIVE_MONITOR) $(SOC_REFERENCE)/axroleiso.sv $(SOC_REFERENCE)/soc_top.sv

test: $(MODULES:%=run-%) run-muldiv-fastmul run-muldiv-radix4 run-axcore run-minimal-isa run-interrupt run-irq-collide run-privilege run-sv32 run-axdram-model run-axcache run-axsdram run-axuart-phy run-axspi run-axlivemon run-axroleiso run-soc run-soc-timer run-suite-gpu1 run-suite-ax2

# The alternate muldiv component passes the identical latency-agnostic
# testbench; a swapped unit earns the same evidence as the reference.
run-muldiv-fastmul: tb_muldiv.cpp
	$(VERILATOR) $(VFLAGS) --top-module muldiv --Mdir obj_dir_muldiv_fastmul \
	  --exe --build -o tb_muldiv $(MULDIV_FASTMUL)/muldiv.sv \
	  $(CURDIR)/tb_muldiv.cpp >/dev/null
	./obj_dir_muldiv_fastmul/tb_muldiv

# Radix-4 divide (16 cycles) + single-cycle multiply.  Same latency-agnostic
# testbench again: a unit that changes its timing but not its results earns the
# reference unit's evidence without a bespoke test.
run-muldiv-radix4: tb_muldiv.cpp
	$(VERILATOR) $(VFLAGS) --top-module muldiv --Mdir obj_dir_muldiv_radix4 \
	  --exe --build -o tb_muldiv $(MULDIV_RADIX4)/muldiv.sv \
	  $(CURDIR)/tb_muldiv.cpp >/dev/null
	./obj_dir_muldiv_radix4/tb_muldiv

run-%: tb_%.cpp
	$(VERILATOR) $(VFLAGS) --top-module $* --Mdir obj_dir_$* --exe --build \
	  -o tb_$* $(CORE_COMPONENT)/axcore_pkg.sv $(UNIT_SV_$*) $(CURDIR)/tb_$*.cpp >/dev/null
	./obj_dir_$*/tb_$*

# The ISA binaries are build products of the tests/riscv-tests submodule, so
# every target that runs a suite depends on this.  It is not optional
# convenience: the runners now fail on a suite that matched nothing, so without
# it a job with no binaries is a hard error rather than a vacuous pass.
isa-binaries:
	@../../tests/build-isa.sh

# core.minimal against the official ISA suites.  It had no simulation coverage
# at all before this -- only the formal flow -- so rv32mi trap/CSR bugs could
# sit in it indefinitely.  Wait states are swept for the same reason as ax2.
MINIMAL_WS := 0 1 3

run-minimal-isa: tb_ax2_isa.cpp $(MINIMAL_SOURCES) isa-binaries
	$(VERILATOR) $(VFLAGS) --top-module axcore --Mdir obj_dir_minimal_isa \
	  --exe --build -o tb_minimal_isa $(MINIMAL_SOURCES) \
	  $(CURDIR)/tb_ax2_isa.cpp >/dev/null
	@for ws in $(MINIMAL_WS); do \
	  WS=$$ws LABEL=minimal ./run_core_isa.sh \
	    $(CURDIR)/obj_dir_minimal_isa/tb_minimal_isa || exit 1; \
	done

# Core-level: run the directed test binaries on the RTL, with and without
# bus wait states (exercises the stall/hold paths).
run-axcore: $(CORE_SOURCES) tb_axcore.cpp
	$(VERILATOR) $(VFLAGS) --top-module axcore --Mdir obj_dir_axcore \
	  --exe --build -o tb_axcore \
	  $(CORE_SOURCES) $(CURDIR)/tb_axcore.cpp >/dev/null
	@mkdir -p build
	python3 ../../tests/directed/words2bin.py ../../tests/directed/smoke_ok.words build/smoke_ok.bin
	python3 ../../tests/directed/words2bin.py ../../tests/directed/trap_ecall.words build/trap_ecall.bin
	./obj_dir_axcore/tb_axcore build/smoke_ok.bin | grep -qx OK
	./obj_dir_axcore/tb_axcore build/smoke_ok.bin --ws 3 | grep -qx OK
	./obj_dir_axcore/tb_axcore build/trap_ecall.bin
	./obj_dir_axcore/tb_axcore build/trap_ecall.bin --ws 2
	@echo "tb_axcore: PASS"

# Timer line asserted before MTIE/MIE are enabled. The handler's finisher
# write proves precise delivery and the C++ test checks mcause/mepc/mip.
run-interrupt: $(CORE_SOURCES) tb_interrupt.cpp
	$(VERILATOR) $(VFLAGS) --top-module axcore --Mdir obj_dir_interrupt \
	  --exe --build -o tb_interrupt \
	  $(CORE_SOURCES) $(CURDIR)/tb_interrupt.cpp >/dev/null
	./obj_dir_interrupt/tb_interrupt

# Interrupt/commit collision regressions: a timer interrupt forced onto the
# exact commit cycle of a serialized CSR write and of an mret. The .S
# programs self-check mepc/mstatus/mscratch and exit via the finisher.
IRQ_TESTS := irq_csr irq_mret
IRQ_MARKER_irq_csr  := 34011073   # csrw mscratch, x2
IRQ_MARKER_irq_mret := 30200073   # mret

# The bare-metal link script takes its RAM region length from __ram_bytes, so
# each directed program is linked against the RAM its testbench actually
# models. Keeping the two in step is what makes the link-time overflow check
# mean something here.
IRQ_RAM_BYTES     := 16384     # tb_irq_collide.cpp
AXCORE_RAM_BYTES  := 4194304   # tb_axcore.cpp

run-irq-collide: $(CORE_SOURCES) tb_irq_collide.cpp
	$(VERILATOR) $(VFLAGS) --top-module axcore --Mdir obj_dir_irq_collide \
	  --exe --build -o tb_irq_collide \
	  $(CORE_SOURCES) $(CURDIR)/tb_irq_collide.cpp >/dev/null
	@mkdir -p build
	for t in $(IRQ_TESTS); do \
	  $(RISCV_PREFIX)gcc -march=$(RISCV_ARCH_I) -mabi=ilp32 -nostdlib -nostartfiles \
	    -Wl,--defsym=__ram_bytes=$(IRQ_RAM_BYTES) \
	    -Wl,-T,../../sw/baremetal/link.ld -o build/$$t.elf \
	    ../../tests/directed/$$t.S || exit 1; \
	  $(RISCV_PREFIX)objcopy -O binary build/$$t.elf build/$$t.bin || exit 1; \
	done
	./obj_dir_irq_collide/tb_irq_collide build/irq_csr.bin $(IRQ_MARKER_irq_csr)
	./obj_dir_irq_collide/tb_irq_collide build/irq_mret.bin $(IRQ_MARKER_irq_mret)

# M/S/U transition and trap-routing regression.  The directed program is
# self-checking and uses the same behavioral aXbus testbench as run-axcore.
run-privilege: $(CORE_SOURCES) tb_axcore.cpp ../../tests/directed/privilege.S
	$(VERILATOR) $(VFLAGS) --top-module axcore --Mdir obj_dir_privilege \
	  --exe --build -o tb_axcore \
	  $(CORE_SOURCES) $(CURDIR)/tb_axcore.cpp >/dev/null
	@mkdir -p build
	$(RISCV_PREFIX)gcc -march=$(RISCV_ARCH_I) -mabi=ilp32 -nostdlib -nostartfiles \
	  -Wl,--defsym=__ram_bytes=$(AXCORE_RAM_BYTES) \
	  -Wl,-T,../../sw/baremetal/link.ld -o build/privilege.elf \
	  ../../tests/directed/privilege.S
	$(RISCV_PREFIX)objcopy -O binary build/privilege.elf build/privilege.bin
	./obj_dir_privilege/tb_axcore build/privilege.bin
	./obj_dir_privilege/tb_axcore build/privilege.bin --ws 2
	@echo "tb_privilege: PASS"

# End-to-end Sv32 execution: M-mode page-table setup, U-mode fetch/data
# walks, hardware A/D updates, and a precise store-page-fault trap.
run-sv32: $(CORE_SOURCES) tb_axcore.cpp ../../tests/directed/sv32.S
	$(VERILATOR) $(VFLAGS) --top-module axcore --Mdir obj_dir_sv32 \
	  --exe --build -o tb_axcore \
	  $(CORE_SOURCES) $(CURDIR)/tb_axcore.cpp >/dev/null
	@mkdir -p build
	$(RISCV_PREFIX)gcc -march=$(RISCV_ARCH_I) -mabi=ilp32 -nostdlib -nostartfiles \
	  -Wl,--defsym=__ram_bytes=$(AXCORE_RAM_BYTES) \
	  -Wl,-T,../../sw/baremetal/link.ld -o build/sv32.elf \
	  ../../tests/directed/sv32.S
	$(RISCV_PREFIX)objcopy -O binary build/sv32.elf build/sv32.bin
	./obj_dir_sv32/tb_axcore build/sv32.bin
	./obj_dir_sv32/tb_axcore build/sv32.bin --ws 2
	@echo "tb_sv32: PASS"

# Phase 6 memory contract: both ports incur a fixed delayed response, honor
# byte lanes, and report bad requests only at completion.
run-axdram-model: $(MEMORY_REFERENCE)/axdram_model.sv tb_axdram_model.cpp
	$(VERILATOR) $(VFLAGS) --top-module axdram_model --Mdir obj_dir_axdram_model \
	  -GBYTES=64 -GLATENCY=3 --exe --build -o tb_axdram_model \
	  $< $(CURDIR)/tb_axdram_model.cpp >/dev/null
	./obj_dir_axdram_model/tb_axdram_model

# Physical-SDRAM controller contract against a CAS-2 x16 SDRAM behavioural
# model, including init, bank/row/column sequencing, byte masks, and faults.
run-axsdram: $(MEMORY_REFERENCE)/axsdram.sv $(HARNESS_SDRAM)/ax_sdram_sim.sv axsdram_test_top.sv tb_axsdram.cpp
	$(VERILATOR) $(VFLAGS) --top-module axsdram_test_top --Mdir obj_dir_axsdram \
	  --exe --build -o tb_axsdram $(MEMORY_REFERENCE)/axsdram.sv $(HARNESS_SDRAM)/ax_sdram_sim.sv $(CURDIR)/axsdram_test_top.sv \
	  $(CURDIR)/tb_axsdram.cpp >/dev/null
	./obj_dir_axsdram/tb_axsdram

run-axuart-phy: $(BOARD_ULX3S)/axuart_phy.sv axuart_phy_loopback_top.sv tb_axuart_phy.cpp
	$(VERILATOR) $(VFLAGS) --top-module axuart_phy_loopback_top --Mdir obj_dir_axuart_phy \
	  --exe --build -o tb_axuart_phy $^ >/dev/null
	./obj_dir_axuart_phy/tb_axuart_phy

run-axcache: $(CACHE_REFERENCE)/axcache.sv $(MEMORY_REFERENCE)/axdram_model.sv axcache_test_top.sv tb_axcache.cpp
	$(VERILATOR) $(VFLAGS) --top-module axcache_test_top --Mdir obj_dir_axcache \
	  --exe --build -o tb_axcache $^ >/dev/null
	./obj_dir_axcache/tb_axcache

run-axspi: $(SPI_REFERENCE)/axspi.sv tb_axspi.cpp
	$(VERILATOR) $(VFLAGS) --top-module axspi --Mdir obj_dir_axspi \
	  --exe --build -o tb_axspi $^ >/dev/null
	./obj_dir_axspi/tb_axspi

run-axlivemon: $(LIVE_MONITOR) tb_axlivemon.cpp
	$(VERILATOR) $(VFLAGS) --top-module axlivemon --Mdir obj_dir_axlivemon \
	  --exe --build -o tb_axlivemon $^ >/dev/null
	./obj_dir_axlivemon/tb_axlivemon

run-axroleiso: $(LIVE_MONITOR) $(SOC_REFERENCE)/axroleiso.sv tb_axroleiso.cpp
	$(VERILATOR) $(VFLAGS) --top-module axroleiso --Mdir obj_dir_axroleiso \
	  --exe --build -o tb_axroleiso $^ >/dev/null
	./obj_dir_axroleiso/tb_axroleiso

run-soc: $(SOC_SVS) tb_soc.cpp data/soc_smoke.hex
	$(VERILATOR) $(VFLAGS) --top-module soc_top --Mdir obj_dir_soc \
	  -GROM_INIT_FILE=\"$(CURDIR)/data/soc_smoke.hex\" --exe --build -o tb_soc \
	  $(SOC_SVS) $(CURDIR)/tb_soc.cpp >/dev/null
	./obj_dir_soc/tb_soc

run-soc-timer: $(SOC_SVS) tb_soc_timer.cpp data/soc_timer.hex
	$(VERILATOR) $(VFLAGS) --top-module soc_top --Mdir obj_dir_soc_timer \
	  -GROM_INIT_FILE=\"$(CURDIR)/data/soc_timer.hex\" --exe --build -o tb_soc_timer \
	  $(SOC_SVS) $(CURDIR)/tb_soc_timer.cpp >/dev/null
	./obj_dir_soc_timer/tb_soc_timer

clean:
	rm -rf obj_dir_* build

.PHONY: test clean run-muldiv-fastmul run-axcore run-interrupt run-irq-collide run-privilege run-sv32 run-axdram-model run-axcache run-axsdram run-axuart-phy run-axspi run-axlivemon run-axroleiso run-soc run-soc-timer

# ---------------------------------------------------------------------------
# gpu1 and ax2 suites.  Both are SUITES rather than per-component checks: one
# target covers a whole tier family, because the tiers differ only in
# parameters and the point of the suite is that the parameter does not change
# the architectural result.
# ---------------------------------------------------------------------------

GPU1_ENGINE  := $(COMPONENTS)/role/gpu1/gpu1_engine.sv
GPU1_ROLE    := $(COMPONENTS)/role/gpu1/axrole.sv
ROLE_MUL     := $(COMPONENTS)/role/common/ax_gw5a_mul.sv

# gpu1 is one component with build-time parameters, so the suite sweeps the
# PARAMETER SPACE rather than a list of component variants.  These points are
# chosen to cover what can actually break: the smallest configuration, the
# baseline, banks fewer than lanes (so a coalesced access takes several rounds),
# a non-power-of-two lane count, the optional ISA groups switched off, and the
# largest configuration.  The testbench reads the geometry from CAPS, so one
# source checks all of them.
GPU1_POINTS := \
  4,4,1,1 \
  8,8,1,1 \
  16,16,1,1 \
  32,32,1,1 \
  16,4,1,1 \
  6,8,1,1 \
  8,8,0,0

run-suite-gpu1: tb_gpu1.cpp $(ROLE_MUL) $(GPU1_ENGINE) $(GPU1_ROLE)
	@for pt in $(GPU1_POINTS); do \
	  l=$${pt%%,*}; r=$${pt#*,}; b=$${r%%,*}; r=$${r#*,}; \
	  d=$${r%%,*}; sh=$${r#*,}; \
	  echo "== suite/gpu1: lanes=$$l banks=$$b div=$$d shfl=$$sh =="; \
	  $(VERILATOR) $(VFLAGS) --top-module axrole --Mdir obj_dir_gpu1_$${l}_$${b}_$${d}_$${sh} \
	    +define+GPU1_LANES=$$l +define+GPU1_BANKS=$$b \
	    +define+GPU1_ENABLE_DIV=$$d +define+GPU1_ENABLE_SHFL=$$sh \
	    --exe --build -o tb_gpu1 $(ROLE_MUL) $(GPU1_ENGINE) $(GPU1_ROLE) \
	    $(CURDIR)/tb_gpu1.cpp >/dev/null || exit 1; \
	  ./obj_dir_gpu1_$${l}_$${b}_$${d}_$${sh}/tb_gpu1 || exit 1; \
	done
	@echo "suite/gpu1: PASS"

AX2_DIR := $(COMPONENTS)/core/ax2
# ax2 shares the reference core's decoder, immdec and branch comparator, so a
# decode-rule change is caught by both cores' suites at once.
AX2_SOURCES  = $(CORE_COMPONENT)/axcore_pkg.sv $(AX2_DIR)/ax2_pkg.sv \
  $(CORE_COMPONENT)/immdec.sv $(CORE_COMPONENT)/decoder.sv \
  $(CORE_COMPONENT)/branch_cmp.sv $(ALU_REFERENCE)/alu.sv \
  $(MULDIV_REFERENCE)/muldiv.sv $(AX2_DIR)/ax2_regfile.sv \
  $(AX2_DIR)/ax2_icache.sv $(AX2_DIR)/ax2_core.sv $(AX2_DIR)/axcore.sv

# Same idea for the core: sweep the parameter space, not a variant list.  The
# points cover single versus dual issue, the smallest and largest caches, and
# prediction switched off entirely -- the extremes are where a parameterised
# design breaks (a zero-entry BTB is a zero-width index if you are careless).
AX2_POINTS := \
  1,1,16 \
  2,2,32 \
  2,8,128 \
  2,1,0 \
  1,8,0
AX2_WS := 0 1 3

run-suite-ax2: tb_ax2_isa.cpp tb_axcore.cpp $(AX2_SOURCES) isa-binaries
	@mkdir -p build
	@python3 ../../tests/directed/words2bin.py ../../tests/directed/smoke_ok.words build/smoke_ok.bin
	@python3 ../../tests/directed/words2bin.py ../../tests/directed/trap_ecall.words build/trap_ecall.bin
	@for pt in $(AX2_POINTS); do \
	  w=$${pt%%,*}; r=$${pt#*,}; k=$${r%%,*}; b=$${r#*,}; \
	  tag=w$${w}k$${k}b$${b}; \
	  echo "== suite/ax2: issue_width=$$w icache_kb=$$k btb_entries=$$b =="; \
	  defs="+define+AX2_ISSUE_WIDTH=$$w +define+AX2_ICACHE_KB=$$k +define+AX2_BTB_ENTRIES=$$b"; \
	  $(VERILATOR) $(VFLAGS) --top-module axcore --Mdir obj_dir_ax2_$$tag \
	    $$defs --exe --build -o tb_axcore $(AX2_SOURCES) \
	    $(CURDIR)/tb_axcore.cpp >/dev/null || exit 1; \
	  ./obj_dir_ax2_$$tag/tb_axcore build/smoke_ok.bin | grep -qx OK || exit 1; \
	  ./obj_dir_ax2_$$tag/tb_axcore build/smoke_ok.bin --ws 3 | grep -qx OK || exit 1; \
	  ./obj_dir_ax2_$$tag/tb_axcore build/trap_ecall.bin || exit 1; \
	  $(VERILATOR) $(VFLAGS) --top-module axcore --Mdir obj_dir_ax2isa_$$tag \
	    $$defs --exe --build -o tb_ax2_isa $(AX2_SOURCES) \
	    $(CURDIR)/tb_ax2_isa.cpp >/dev/null || exit 1; \
	  for ws in $(AX2_WS); do \
	    WS=$$ws LABEL=ax2 ./run_core_isa.sh \
	      $(CURDIR)/obj_dir_ax2isa_$$tag/tb_ax2_isa || exit 1; \
	  done; \
	done
	@echo "suite/ax2: PASS"

.PHONY: run-suite-gpu1 run-suite-ax2 run-minimal-isa isa-binaries
