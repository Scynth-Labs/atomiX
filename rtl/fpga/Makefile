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
.DELETE_ON_ERROR:

BUILD := build
COMPONENT_CONFIG ?= ../../configs/ulx3s-85f.json
COMPONENT_CONFIG_KEY := $(subst /,_,$(basename $(abspath $(COMPONENT_CONFIG))))
COMPONENT_MK := $(BUILD)/component$(COMPONENT_CONFIG_KEY).mk
COMPONENT_MANIFESTS := $(wildcard ../../components/*/*/component.json)

$(COMPONENT_MK): $(COMPONENT_CONFIG) ../../tools/configure.py $(COMPONENT_MANIFESTS)
	mkdir -p $(@D)
	python3 ../../tools/configure.py resolve --config $(COMPONENT_CONFIG) --output $@

-include $(COMPONENT_MK)

# Placement is profile-selectable but can still be overridden explicitly.
# Seed-specific P&R outputs below prevent make from reusing a route produced
# with a different seed while leaving the synthesized netlist reusable.
PNR_SEED ?= $(or $(COMPONENT_SETTING_PNR_SEED),1)

$(COMPONENT_MK): $(COMPONENT_SELECTED_MANIFESTS)

# Flow is board-selected; default to the ECP5 flow for backward compatibility.
COMPONENT_FPGA_FLOW ?= ecp5
TOP ?= $(COMPONENT_FPGA_TOP)
PROGRAM ?= hello
RAM_BYTES ?= $(COMPONENT_RAM_BYTES)
RAM_INIT_FILE ?=
ROM_INIT_FILE ?=
RESET_PC_VALUE := $(if $(COMPONENT_RESET_PC),$(shell python3 -c 'print(int("$(COMPONENT_RESET_PC)", 0))'))
RUNTIME_KERNEL_PROFILE := $(if $(and $(filter gowin,$(COMPONENT_FPGA_FLOW)),$(filter 4096,$(RESET_PC_VALUE))),1,)
# A Gowin profile that resets into ROM is a runtime-software profile by
# construction. Unless explicitly overridden (for example by an SD loader),
# synthesize the immutable UART loader and blank kernel RAM.
ifeq ($(RUNTIME_KERNEL_PROFILE),1)
ifeq ($(strip $(RAM_INIT_FILE)),)
RAM_INIT_FILE := ../../sw/bootrom/blank.hex
endif
ifeq ($(strip $(ROM_INIT_FILE)),)
ROM_INIT_FILE := ../../sw/bootrom/build/uart-ram$(RAM_BYTES)/bootrom.hex
AUTO_UART_ROM := 1
endif
endif
PROGRAM_BUILD_DIR := build/ram$(RAM_BYTES)
PROGRAM_HEX := $(if $(RAM_INIT_FILE),$(abspath $(RAM_INIT_FILE)),../../sw/baremetal/$(PROGRAM_BUILD_DIR)/$(PROGRAM).hex)
PROGRAM_KEY := $(subst /,_,$(basename $(abspath $(PROGRAM_HEX))))
ROM_INIT_DEP := $(if $(ROM_INIT_FILE),$(abspath $(ROM_INIT_FILE)),)
ROM_KEY := $(if $(ROM_INIT_FILE),/rom$(subst /,_,$(basename $(abspath $(ROM_INIT_FILE)))),)
# Gowin BRAM-only images bake software into the netlist, so the payload is part
# of the artifact identity. ECP5 boots from a separate ROM image and therefore
# remains keyed by configuration alone.
CONFIG_BUILD := $(BUILD)/$(COMPONENT_CONFIG_KEY)$(if $(filter gowin,$(COMPONENT_FPGA_FLOW)),/$(PROGRAM_KEY)$(ROM_KEY),)
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
  $(COMPONENT_ROM_SOURCES) $(COMPONENT_CLINT_SOURCES) $(COMPONENT_PLIC_SOURCES) \
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
	@echo "  P&R seed: $(PNR_SEED)"
	@if [ "$(COMPONENT_FPGA_FLOW)" = "gowin" ]; then echo "  payload: $(PROGRAM_HEX)"; fi

$(BOOT_HEX):
	$(MAKE) -C ../../sw/bootrom images

ifeq ($(AUTO_UART_ROM),1)
$(ROM_INIT_DEP):
	$(MAKE) -C ../../sw/bootrom images MODE=uart RAM_BYTES=$(RAM_BYTES) \
	  BUILD_DIR=build/uart-ram$(RAM_BYTES)
endif

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
PNR_JSON := $(CONFIG_BUILD)/$(TOP)_seed$(PNR_SEED)_pnr.json
PNR_REPORT := $(CONFIG_BUILD)/$(TOP)_seed$(PNR_SEED)_timing.json
BIT := $(CONFIG_BUILD)/$(TOP)_seed$(PNR_SEED).fs
EVIDENCE ?= $(CONFIG_BUILD)/evidence_seed$(PNR_SEED).json
EVIDENCE_KERNEL ?=
EVIDENCE_RUNTIME_GATE ?=

# BRAM-only Tang bring-up bakes the selected bare-metal program into on-chip
# block RAM at synthesis time. Build that exact payload first and specialize
# the board top's string parameter so $readmemh cannot fall back to hello.hex.
ifeq ($(RAM_INIT_FILE),)
$(PROGRAM_HEX):
	$(MAKE) -C ../../sw/baremetal BUILD_DIR=$(PROGRAM_BUILD_DIR) \
	  RAM_BYTES=$(RAM_BYTES) $(PROGRAM_BUILD_DIR)/$(PROGRAM).hex
endif

# The board top's RAM_BYTES follows the selected profile so a role-heavy
# profile can trade main memory for the block RAM its accelerator needs.
RAM_BYTES_CHPARAM := $(if $(RAM_BYTES),chparam -set RAM_BYTES $(RAM_BYTES) $(TOP);,)
PROGRAM_CHPARAM := chparam -set RAM_INIT_FILE "$(abspath $(PROGRAM_HEX))" $(TOP);
ROM_CHPARAM := $(if $(ROM_INIT_FILE),chparam -set ROM_INIT_FILE "$(abspath $(ROM_INIT_FILE))" $(TOP);,)
RESET_PC_CHPARAM := $(if $(RESET_PC_VALUE),chparam -set RESET_PC $(RESET_PC_VALUE) $(TOP);,)

# Yosys 0.67's final Gowin `autoname` pass scales pathologically on the larger
# CPU/GPU/TPU netlists. Cell names are not part of nextpnr's input contract, so
# run the documented flow through map_cells, reproduce its remaining commands,
# and omit only autoname. This yields the same checked JSON netlist in seconds
# instead of hanging for minutes.
GOWIN_SETUNDEF_CMD := $(if $(filter -setundef,$(COMPONENT_GOWIN_SYNTH_ARGS)),setundef -undriven -params -zero;,)
$(JSON): $(SYNTH_SV) $(PROGRAM_HEX) $(ROM_INIT_DEP) Makefile | $(CONFIG_BUILD)
	$(YOSYS) -q -l $(CONFIG_BUILD)/synth.log -p 'read_verilog -sv $(SYNTH_DEFINES) $(SYNTH_SV); $(RAM_BYTES_CHPARAM) $(PROGRAM_CHPARAM) $(ROM_CHPARAM) $(RESET_PC_CHPARAM) synth_gowin -noabc9 -family $(SYNTH_FAMILY) $(COMPONENT_GOWIN_SYNTH_ARGS) -top $(TOP) -run begin:map_cells; techmap -map +/gowin/cells_map.v; opt_lut_ins -tech gowin; $(GOWIN_SETUNDEF_CMD) hilomap -singleton -hicell VCC V -locell GND G; clean; hierarchy -check; stat; check -noinit; blackbox =A:whitebox; write_json $@'

$(PNR_JSON): $(JSON) $(CST) | check-tools
	$(NEXTPNR_HIMBAECHEL) $(COMPONENT_NEXTPNR_ARGS) --seed $(PNR_SEED) --device $(DEVICE) --vopt family=$(FAMILY) --vopt cst=$(CST) --json $(JSON) --write $@ --report $(PNR_REPORT) --detailed-timing-report

$(BIT): $(PNR_JSON)
	$(GOWIN_PACK) $(COMPONENT_GOWIN_PACK_ARGS) -d $(FAMILY) -o $@ $(PNR_JSON)

# A build artifact is not board evidence by itself, but this manifest pins the
# exact source state, payloads, tool versions, timing result, and bitstream hash
# that a later physical transcript must refer to.
gowin-evidence: all
	python3 ../../tools/fpga_evidence.py \
	  --config "$(COMPONENT_CONFIG)" --bitstream "$(BIT)" \
	  --pnr-seed "$(PNR_SEED)" \
	  --timing-report "$(PNR_REPORT)" --synth-log "$(CONFIG_BUILD)/synth.log" \
	  --input "ram=$(PROGRAM_HEX)" \
	  $(if $(ROM_INIT_DEP),--input "rom=$(ROM_INIT_DEP)") \
	  $(if $(EVIDENCE_KERNEL),--input "kernel=$(EVIDENCE_KERNEL)") \
	  $(if $(EVIDENCE_RUNTIME_GATE),--gate "runtime-fast-switch=$(EVIDENCE_RUNTIME_GATE)") \
	  --output "$(EVIDENCE)"

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
TEXTCFG := $(CONFIG_BUILD)/$(TOP)_seed$(PNR_SEED).config
BIT := $(CONFIG_BUILD)/$(TOP)_seed$(PNR_SEED).bit

$(JSON): $(SYNTH_SV) $(BOOT_HEX) Makefile | $(CONFIG_BUILD)
	$(YOSYS) -q -l $(CONFIG_BUILD)/synth.log -p 'read_verilog -lib +/ecp5/cells_sim.v; read_verilog -lib +/ecp5/cells_bb.v; read_verilog -sv $(SYNTH_DEFINES) $(SYNTH_SV); hierarchy -check -top $(TOP); synth_ecp5 -noabc9 -top $(TOP) -json $@'

$(TEXTCFG): $(JSON) $(LPF) | check-tools
	$(NEXTPNR_ECP5) $(COMPONENT_NEXTPNR_ARGS) --seed $(PNR_SEED) --json $(JSON) --lpf $(LPF) --textcfg $@

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

gowin-evidence:
	@echo "gowin-evidence requires a Gowin board profile"; exit 2
endif

# Stage 2 of docs/partial-reconfig.md.  Build two machines whose only selected
# component difference is the role, with an explicit shared placement seed,
# then measure both tile spread and exact CRAM-frame churn.  Upstream Trellis
# currently refuses to encode partial frame addresses for the 85F, so the
# final ecppack invocation is an intentional capability probe: the known 45F-
# only diagnostic is recorded without discarding the completed measurement.
PR_REFERENCE_CONFIG ?= ../../configs/ulx3s-85f.json
PR_CANDIDATE_CONFIG ?= ../../configs/ulx3s-85f-loopback.json
PR_BUILD := $(abspath $(BUILD)/pr-delta-seed$(PNR_SEED))
PR_REFERENCE_TEXTCFG := $(PR_BUILD)/reference.config
PR_CANDIDATE_TEXTCFG := $(PR_BUILD)/candidate.config
PR_REFERENCE_BIT := $(PR_BUILD)/reference.bit
PR_CANDIDATE_BIT := $(PR_BUILD)/candidate.bit
PR_DELTA_BIT := $(PR_BUILD)/candidate.delta.bit
PR_REPORT := $(PR_BUILD)/report.json
PR_ECPPACK_LOG := $(PR_BUILD)/ecppack-delta.log

pr-delta: check-tools
	mkdir -p $(PR_BUILD)
	$(MAKE) --no-print-directory COMPONENT_CONFIG=$(PR_REFERENCE_CONFIG) \
	  PNR_SEED=$(PNR_SEED) TEXTCFG=$(PR_REFERENCE_TEXTCFG) \
	  BIT=$(PR_REFERENCE_BIT) all
	$(MAKE) --no-print-directory COMPONENT_CONFIG=$(PR_CANDIDATE_CONFIG) \
	  PNR_SEED=$(PNR_SEED) TEXTCFG=$(PR_CANDIDATE_TEXTCFG) \
	  BIT=$(PR_CANDIDATE_BIT) all
	python3 ../../tools/pr_delta.py \
	  $(PR_REFERENCE_TEXTCFG) $(PR_CANDIDATE_TEXTCFG) \
	  --reference-bit $(PR_REFERENCE_BIT) \
	  --candidate-bit $(PR_CANDIDATE_BIT) --json $(PR_REPORT)
	@if $(ECPPACK) $(COMPONENT_ECPPACK_ARGS) $(PR_CANDIDATE_TEXTCFG) \
	    $(PR_DELTA_BIT) --delta $(PR_REFERENCE_TEXTCFG) \
	    >$(PR_ECPPACK_LOG) 2>&1; then \
	  echo "delta bitstream: $(PR_DELTA_BIT)"; \
	elif grep -q "partial bitstreams only supported for ECP5-45k" \
	    $(PR_ECPPACK_LOG); then \
	  echo "delta bitstream: blocked by Trellis's 45F-only address encoder"; \
	  echo "packer log: $(PR_ECPPACK_LOG)"; \
	else \
	  cat $(PR_ECPPACK_LOG); \
	  exit 1; \
	fi

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

.PHONY: all synth config check-tools toolchain-report gowin-evidence pr-delta program flash clean
