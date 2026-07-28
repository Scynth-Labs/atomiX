# Reproducible FPGA build.  The selected board component declares the flow
# (ECP5 open flow, or the Gowin open flow for Tang boards) through its manifest;
# the generated bitstream configures SRAM only.  `make flash` is deliberately a
# separate, explicit persistent operation.
YOSYS ?= yosys
NEXTPNR_ECP5 ?= nextpnr-ecp5
ECPPACK ?= ecppack
NEXTPNR_HIMBAECHEL ?= nextpnr-himbaechel
GOWIN_PACK ?= gowin_pack
OPENFPGALOADER ?= openFPGALoader
.DEFAULT_GOAL := all

BUILD := build
COMPONENT_CONFIG ?= ../../configs/ulx3s-85f.json
COMPONENT_CONFIG_KEY := $(subst /,_,$(basename $(abspath $(COMPONENT_CONFIG))))
COMPONENT_MK := $(BUILD)/component$(COMPONENT_CONFIG_KEY).mk
COMPONENT_MANIFESTS := $(wildcard ../../components/*/*/component.json)

$(COMPONENT_MK): $(COMPONENT_CONFIG) ../../tools/configure.py $(COMPONENT_MANIFESTS)
	mkdir -p $(@D)
	python3 ../../tools/configure.py resolve --config $(COMPONENT_CONFIG) --output $@

-include $(COMPONENT_MK)

$(COMPONENT_MK): $(COMPONENT_SELECTED_MANIFESTS)

# Flow is board-selected; default to the ECP5 flow for backward compatibility.
COMPONENT_FPGA_FLOW ?= ecp5
TOP ?= $(COMPONENT_FPGA_TOP)
PROGRAM ?= hello
RAM_BYTES ?= $(COMPONENT_RAM_BYTES)
PROGRAM_BUILD_DIR := build/ram$(RAM_BYTES)
PROGRAM_HEX := ../../sw/baremetal/$(PROGRAM_BUILD_DIR)/$(PROGRAM).hex
PROGRAM_KEY := $(subst /,_,$(basename $(abspath $(PROGRAM_HEX))))
# Gowin BRAM-only images bake software into the netlist, so the payload is part
# of the artifact identity. ECP5 boots from a separate ROM image and therefore
# remains keyed by configuration alone.
CONFIG_BUILD := $(BUILD)/$(COMPONENT_CONFIG_KEY)$(if $(filter gowin,$(COMPONENT_FPGA_FLOW)),/$(PROGRAM_KEY),)
BOOT_HEX := ../../sw/bootrom/build/bootrom.hex
JSON := $(CONFIG_BUILD)/$(TOP).json
SYNTH_SV := $(CONFIG_BUILD)/atomiX_synth.sv
# The simulation flow consumes Verilator's +define+NAME=VALUE spelling. Yosys
# needs the equivalent -DNAME=VALUE form; without this conversion profile
# parameters silently fall back to the RTL defaults during FPGA synthesis.
SYNTH_DEFINES := $(patsubst +define+%,-D%,$(COMPONENT_DEFINES)) \
  $(patsubst %,-D%,$(COMPONENT_FPGA_DEFINES))

SOURCES := $(COMPONENT_CORE_SOURCES) \
  $(COMPONENT_ALU_SOURCES) $(COMPONENT_MULDIV_SOURCES) \
  $(COMPONENT_REGFILE_SOURCES) $(COMPONENT_MMU_SOURCES) \
  $(COMPONENT_INTERCONNECT_SOURCES) $(COMPONENT_CACHE_SOURCES) \
  $(COMPONENT_MEMORY_SOURCES) \
  $(COMPONENT_ROM_SOURCES) $(COMPONENT_CLINT_SOURCES) \
  $(COMPONENT_UART_SOURCES) $(COMPONENT_SPI_SOURCES) \
  $(COMPONENT_ROLE_SOURCES) \
  $(COMPONENT_FINISHER_SOURCES) $(COMPONENT_SOC_SOURCES) \
  $(COMPONENT_BOARD_SOURCES)

# Synthesis only (yosys -> netlist).  Runs with just yosys installed, so it is
# the "does the design synthesise for this board" gate that needs no P&R tools.
synth: $(JSON)

config: $(COMPONENT_MK)
	@echo "configuration: $(COMPONENT_CONFIG_NAME)"
	@echo "  flow:   $(COMPONENT_FPGA_FLOW)"
	@echo "  top:    $(TOP)"
	@echo "  core:   $(COMPONENT_CORE_ID)"
	@echo "  soc:    $(COMPONENT_SOC_ID)"
	@echo "  memory: $(COMPONENT_MEMORY_ID)"
	@echo "  role:   $(COMPONENT_ROLE_ID)"
	@echo "  board:  $(COMPONENT_BOARD_ID)"
	@if [ "$(COMPONENT_FPGA_FLOW)" = "gowin" ]; then echo "  program: $(PROGRAM) ($(PROGRAM_HEX))"; fi

$(BOOT_HEX):
	$(MAKE) -C ../../sw/bootrom images

$(SYNTH_SV): prepare_synth.py $(COMPONENT_MK) $(SOURCES) | $(CONFIG_BUILD)
	python3 prepare_synth.py $@ $(SOURCES)

$(BUILD):
	mkdir -p $@

$(CONFIG_BUILD):
	mkdir -p $@

ifeq ($(COMPONENT_FPGA_FLOW),gowin)
# ---- Gowin open flow (Tang boards): synth_gowin -> nextpnr-himbaechel -> gowin_pack.
DEVICE := $(COMPONENT_FPGA_DEVICE)
FAMILY := $(COMPONENT_FPGA_FAMILY)
SYNTH_FAMILY := $(or $(COMPONENT_FPGA_SYNTH_FAMILY),gw1n)
CST ?= $(COMPONENT_FPGA_CST)
PROG_BOARD := $(COMPONENT_FPGA_PROG_BOARD)
PNR_JSON := $(CONFIG_BUILD)/$(TOP)_pnr.json
BIT := $(CONFIG_BUILD)/$(TOP).fs

# BRAM-only Tang bring-up bakes the selected bare-metal program into on-chip
# block RAM at synthesis time. Build that exact payload first and specialize
# the board top's string parameter so $readmemh cannot fall back to hello.hex.
$(PROGRAM_HEX):
	$(MAKE) -C ../../sw/baremetal BUILD_DIR=$(PROGRAM_BUILD_DIR) \
	  RAM_BYTES=$(RAM_BYTES) $(PROGRAM_BUILD_DIR)/$(PROGRAM).hex

# The board top's RAM_BYTES follows the selected profile so a role-heavy
# profile can trade main memory for the block RAM its accelerator needs.
RAM_BYTES_CHPARAM := $(if $(RAM_BYTES),chparam -set RAM_BYTES $(RAM_BYTES) $(TOP);,)
PROGRAM_CHPARAM := chparam -set RAM_INIT_FILE "$(abspath $(PROGRAM_HEX))" $(TOP);

# Yosys 0.67's final Gowin `autoname` pass scales pathologically on the larger
# CPU/GPU/TPU netlists. Cell names are not part of nextpnr's input contract, so
# run the documented flow through map_cells, reproduce its remaining commands,
# and omit only autoname. This yields the same checked JSON netlist in seconds
# instead of hanging for minutes.
GOWIN_SETUNDEF_CMD := $(if $(filter -setundef,$(COMPONENT_GOWIN_SYNTH_ARGS)),setundef -undriven -params -zero;,)
$(JSON): $(SYNTH_SV) $(PROGRAM_HEX) Makefile | $(CONFIG_BUILD)
	$(YOSYS) -q -l $(CONFIG_BUILD)/synth.log -p 'read_verilog -sv $(SYNTH_DEFINES) $(SYNTH_SV); $(RAM_BYTES_CHPARAM) $(PROGRAM_CHPARAM) synth_gowin -noabc9 -family $(SYNTH_FAMILY) $(COMPONENT_GOWIN_SYNTH_ARGS) -top $(TOP) -run begin:map_cells; techmap -map +/gowin/cells_map.v; opt_lut_ins -tech gowin; $(GOWIN_SETUNDEF_CMD) hilomap -singleton -hicell VCC V -locell GND G; clean; hierarchy -check; stat; check -noinit; blackbox =A:whitebox; write_json $@'

$(PNR_JSON): $(JSON) $(CST) | check-tools
	$(NEXTPNR_HIMBAECHEL) $(COMPONENT_NEXTPNR_ARGS) --device $(DEVICE) --vopt family=$(FAMILY) --vopt cst=$(CST) --json $(JSON) --write $@

$(BIT): $(PNR_JSON)
	$(GOWIN_PACK) $(COMPONENT_GOWIN_PACK_ARGS) -d $(FAMILY) -o $@ $(PNR_JSON)

check-tools:
	@command -v $(YOSYS) >/dev/null || { echo "missing: $(YOSYS)"; exit 2; }
	@command -v $(NEXTPNR_HIMBAECHEL) >/dev/null || { echo "missing: $(NEXTPNR_HIMBAECHEL) (Gowin nextpnr; from apicula/OSS CAD Suite)"; exit 2; }
	@command -v $(GOWIN_PACK) >/dev/null || { echo "missing: $(GOWIN_PACK) (apicula)"; exit 2; }

toolchain-report: check-tools
	@$(YOSYS) -V
	@$(NEXTPNR_HIMBAECHEL) --version 2>&1 | head -1
	@if command -v $(OPENFPGALOADER) >/dev/null; then $(OPENFPGALOADER) --version; else echo "openFPGALoader: not found (programming only)"; fi

else
# ---- ECP5 open flow (ULX3S): synth_ecp5 -> nextpnr-ecp5 -> ecppack.
LPF ?= $(COMPONENT_FPGA_LPF)
PROG_BOARD := ulx3s
TEXTCFG := $(CONFIG_BUILD)/$(TOP).config
BIT := $(CONFIG_BUILD)/$(TOP).bit

$(JSON): $(SYNTH_SV) $(BOOT_HEX) Makefile | $(CONFIG_BUILD)
	$(YOSYS) -q -l $(CONFIG_BUILD)/synth.log -p 'read_verilog -lib +/ecp5/cells_sim.v; read_verilog -lib +/ecp5/cells_bb.v; read_verilog -sv $(SYNTH_DEFINES) $(SYNTH_SV); hierarchy -check -top $(TOP); synth_ecp5 -noabc9 -top $(TOP) -json $@'

$(TEXTCFG): $(JSON) $(LPF) | check-tools
	$(NEXTPNR_ECP5) $(COMPONENT_NEXTPNR_ARGS) --json $(JSON) --lpf $(LPF) --textcfg $@

$(BIT): $(TEXTCFG)
	$(ECPPACK) $(COMPONENT_ECPPACK_ARGS) $(TEXTCFG) $@

check-tools:
	@command -v $(YOSYS) >/dev/null || { echo "missing: $(YOSYS)"; exit 2; }
	@command -v $(NEXTPNR_ECP5) >/dev/null || { echo "missing: $(NEXTPNR_ECP5)"; exit 2; }
	@command -v $(ECPPACK) >/dev/null || { echo "missing: $(ECPPACK)"; exit 2; }

toolchain-report: check-tools
	@$(YOSYS) -V
	@$(NEXTPNR_ECP5) --version
	@$(ECPPACK) --version
	@if command -v $(OPENFPGALOADER) >/dev/null; then $(OPENFPGALOADER) --version; else echo "openFPGALoader: not found (programming only)"; fi
endif

# BIT is selected inside the board-flow branch above, so define the aggregate
# target only after that branch has been parsed.
all: check-tools $(BIT)

# Reversible SRAM configuration. It is the only programming command needed for
# bring-up; power-cycling restores the board's flash-loaded image.
program: $(BIT)
	@command -v $(OPENFPGALOADER) >/dev/null || { echo "missing: $(OPENFPGALOADER)"; exit 2; }
	$(OPENFPGALOADER) -b $(PROG_BOARD) $(BIT)

# Persistent configuration flash write. Run only after `program` and the
# console regression have passed on the physical board.
flash: $(BIT)
	@command -v $(OPENFPGALOADER) >/dev/null || { echo "missing: $(OPENFPGALOADER)"; exit 2; }
	$(OPENFPGALOADER) -b $(PROG_BOARD) -f $(BIT)

clean:
	rm -rf $(BUILD)

.PHONY: all synth config check-tools toolchain-report program flash clean
