VERILATOR ?= verilator
# Shared component packages declare operation constants that some selected
# implementations do not consume. Verilator 5.051 reports those package-level
# constants as UNUSEDPARAM and makes warnings fatal under -Wall; they are
# intentional component API, not dead elaborated hardware. Older Verilator
# releases do not recognize the warning name, so apply it only on 5+.
VERILATOR_VERSION := $(word 2,$(shell $(VERILATOR) --version 2>/dev/null))
VERILATOR_MAJOR := $(firstword $(subst ., ,$(VERILATOR_VERSION)))
# Only the native console runner supplies runtime image arguments. Browser and
# unit harnesses retain their original initialization path and need no argv.
VFLAGS := -Wall --cc -O2 +define+AX_RUNTIME_RAM_IMAGE
ifneq ($(filter 5 6 7 8 9,$(VERILATOR_MAJOR)),)
VFLAGS += -Wno-UNUSEDPARAM
endif
# Keep lint in this Makefile rather than reconstructing the selected source
# list elsewhere.  SystemVerilog packages must precede users in older
# Verilator releases, and SOC_SVS is the ordering the simulator actually uses.
VLINTFLAGS := -Wall --lint-only +define+AX_RUNTIME_RAM_IMAGE
ifneq ($(filter 5 6 7 8 9,$(VERILATOR_MAJOR)),)
VLINTFLAGS += -Wno-UNUSEDPARAM
endif
.DEFAULT_GOAL := run

COMPONENT_CONFIG ?= ../../configs/sim-bram.json
# Keep one generated include per absolute profile path.  This avoids retaining
# a stale selection when two DIY worktrees both contain `sim-bram.json`.
COMPONENT_CONFIG_KEY := $(subst /,_,$(basename $(abspath $(COMPONENT_CONFIG))))
COMPONENT_MK := build/component$(COMPONENT_CONFIG_KEY).mk
COMPONENT_MANIFESTS := $(wildcard ../../components/*/*/component.json)

$(COMPONENT_MK): $(COMPONENT_CONFIG) ../../tools/configure.py $(COMPONENT_MANIFESTS)
	mkdir -p $(@D)
	python3 ../../tools/configure.py resolve --config $(COMPONENT_CONFIG) --output $@

-include $(COMPONENT_MK)

# The resolver learns external manifest paths from the configuration itself.
# Once included, make tracks them too, so editing an out-of-tree manifest
# regenerates the selected build without touching the profile JSON.
$(COMPONENT_MK): $(COMPONENT_SELECTED_MANIFESTS)

SOC_SVS  := $(COMPONENT_CORE_SOURCES) \
  $(COMPONENT_ALU_SOURCES) $(COMPONENT_MULDIV_SOURCES) \
  $(COMPONENT_REGFILE_SOURCES) $(COMPONENT_MMU_SOURCES) \
  $(COMPONENT_INTERCONNECT_SOURCES) $(COMPONENT_CACHE_SOURCES) \
  $(COMPONENT_MEMORY_SOURCES) \
  $(COMPONENT_ROM_SOURCES) $(COMPONENT_CLINT_SOURCES) $(COMPONENT_PLIC_SOURCES) \
  $(COMPONENT_UART_SOURCES) $(COMPONENT_SPI_SOURCES) \
  $(COMPONENT_ROLE_SOURCES) \
  $(COMPONENT_FINISHER_SOURCES) $(COMPONENT_SOC_SOURCES)
HARNESS_SVS := $(filter %.sv,$(COMPONENT_HARNESS_SOURCES))
HARNESS_CPP := $(filter %.cpp,$(COMPONENT_HARNESS_SOURCES))
# A harness source is a thin front end over shared runner code; the device
# models it pulls in are headers, which the manifest does not list because they
# are not compiled on their own.  Depend on them here so editing one rebuilds.
HARNESS_HDRS := $(wildcard ../../components/harness/common/*.h)
SIM_TOP ?= $(COMPONENT_SIM_TOP)

# Optional baked default. Omit it to build once, then launch the model with
# --ram-image <word-per-line $readmemh image> for each payload.
RAM_INIT_FILE ?=
ROM_INIT_FILE ?=
RESET_PC ?= $(COMPONENT_RESET_PC)
RAM_BYTES ?= $(COMPONENT_RAM_BYTES)
EXTERNAL_MEMORY ?= $(COMPONENT_USE_DRAM_MODEL)
CACHES ?= $(COMPONENT_CACHES)
# Registered-read (block-RAM) main memory.  Off by default so the async
# single-cycle model stays the reference; FPGA BRAM bring-up sets it to 1.
SYNC_READ ?= 0
DEFINES ?= $(COMPONENT_DEFINES)
# The progress monitor is a Verilog define, but the runner has to know whether
# the model has those ports before it can read them.  Deriving the C++ macro
# from the same resolved profile keeps one source of truth: a profile that
# declines the monitor builds a runner that does not look for it.  Deferred
# (`=`, not `:=`) because DEFINES may still be overridden on the command line.
PROGRESS_CFLAGS = $(if $(findstring AX_PROGRESS_MONITOR,$(DEFINES)),-CFLAGS -DAX_PROGRESS_MONITOR,)
# The runner validates a runtime image against the RAM the model elaborated, so
# it has to be told how large that is by the same variable the RTL gets.
# RAM_BYTES is already part of the build tag, so a capacity change cannot reuse
# a runner compiled for the old one.
RAM_BYTES_CFLAGS = -CFLAGS -DAX_RAM_BYTES=$(RAM_BYTES)

# Elaboration parameters belong to the selected harness top, not to this
# Makefile.  The pin-level SDRAM top fixes its own RAM size, cache enable and
# reset PC -- the board it models has one of each -- and declares none of the
# parameters the BRAM/delayed top does, so passing them is a Verilator error.
# That is why `make lint` on a pin-level profile failed rather than linted, and
# why the static-analysis sweep counted it as linted while Verilator had
# actually refused to elaborate: the error it prints carries no file:line, so
# the finding parser saw nothing.  One list, used by lint and by every build.
ifeq ($(COMPONENT_USE_SDRAM),1)
ELAB_PARAMS = -GROM_INIT_FILE=\"$(ROM_INIT_FILE)\"
else
ELAB_PARAMS = -GRESET_PC=$(RESET_PC) -GRAM_BYTES=$(RAM_BYTES) \
  -GUSE_DRAM_MODEL=$(EXTERNAL_MEMORY) -GUSE_CACHES=$(CACHES) \
  -GSYNC_READ=$(SYNC_READ) -GCACHE_LINES=$(CACHE_LINES) \
  -GCACHE_WORDS_PER_LINE=$(CACHE_WORDS_PER_LINE) \
  -GRAM_INIT_FILE=\"$(RAM_INIT_FILE)\" -GROM_INIT_FILE=\"$(ROM_INIT_FILE)\"
endif
CACHE_LINES ?= $(if $(COMPONENT_SETTING_CACHE_LINES),$(COMPONENT_SETTING_CACHE_LINES),16)
CACHE_WORDS_PER_LINE ?= $(if $(COMPONENT_SETTING_CACHE_WORDS_PER_LINE),$(COMPONENT_SETTING_CACHE_WORDS_PER_LINE),4)
MAX_CYCLES ?= 100000
SD_IMAGE ?=
BUILD_ID ?= default
UART_INPUT ?=
UART_INPUT_FILE ?=

# The RAM image, elaborated memory/cache parameters, and selected source paths
# are compiled into the Verilator model. Hash the resolved source list as well
# as the runtime settings: a DIY component can move to another directory (or
# an external manifest) without reusing a generated Makefile that names the
# old C++ harness path.
SOURCE_LAYOUT_TAG := $(shell printf '%s' '$(SOC_SVS) $(HARNESS_SVS) $(HARNESS_CPP)' | cksum | awk '{print $$1}')
VERILATOR_LAYOUT_TAG := $(shell printf '%s' '$(VERILATOR_VERSION) $(VFLAGS)' | cksum | awk '{print $$1}')
# `$(DEFINES)` is already hashed below; the C++ macro derived from it rides
# along so a monitor build and a plain one never share an object directory.
BUILD_TAG := $(COMPONENT_CONFIG_NAME)-$(notdir $(RAM_INIT_FILE))-$(notdir $(ROM_INIT_FILE))-ram$(RAM_BYTES)-dram$(EXTERNAL_MEMORY)-cache$(CACHES)x$(CACHE_LINES)x$(CACHE_WORDS_PER_LINE)-sync$(SYNC_READ)-src$(SOURCE_LAYOUT_TAG)-vl$(VERILATOR_LAYOUT_TAG)-def$(shell echo '$(DEFINES)$(PROGRESS_CFLAGS)' | cksum | cut -d' ' -f1)-$(BUILD_ID)
MDIR := obj_dir_$(BUILD_TAG)

config: $(COMPONENT_MK)
	@echo "configuration: $(COMPONENT_CONFIG_NAME)"
	@echo "  core:   $(COMPONENT_CORE_ID)"
	@echo "  soc:    $(COMPONENT_SOC_ID)"
	@echo "  memory: $(COMPONENT_MEMORY_ID)"
	@echo "  interconnect: $(COMPONENT_INTERCONNECT_ID)"
	@echo "  cache:  $(COMPONENT_CACHE_ID)"
	@echo "  rom:    $(COMPONENT_ROM_ID)"
	@echo "  harness: $(COMPONENT_HARNESS_ID)"
	@echo "  board:  $(COMPONENT_BOARD_ID)"

# Run the harness selected by the memory component.  `run` and `run-sdram`
# remain available for direct, explicit experiments and backwards compatibility.
run-config: $(COMPONENT_MK)
	@case "$(COMPONENT_SIM_RUNNER)" in run|run-sdram) ;; *) \
	  echo "unsupported simulation runner: $(COMPONENT_SIM_RUNNER)"; exit 2;; esac
	$(MAKE) $(COMPONENT_SIM_RUNNER)

build-model: $(SOC_SVS) $(HARNESS_SVS) $(HARNESS_CPP) $(HARNESS_HDRS)
	@test -n "$(SIM_TOP)" || (echo "simulation profile must select a harness"; exit 2)
	$(VERILATOR) $(VFLAGS) --top-module $(SIM_TOP) --Mdir $(MDIR) --exe --build \
	  $(ELAB_PARAMS) $(DEFINES) $(PROGRESS_CFLAGS) $(RAM_BYTES_CFLAGS) \
	  -o axsoc $(SOC_SVS) $(HARNESS_SVS) $(HARNESS_CPP) >/dev/null

# Profile-wide static analysis calls this target.  It deliberately mirrors the
# selected RTL, top, defines, and elaboration parameters above, but stops
# before C++ generation or requiring a RAM image.
lint: $(SOC_SVS) $(HARNESS_SVS) $(COMPONENT_MK)
	@test -n "$(SIM_TOP)" || (echo "simulation profile must select a harness"; exit 2)
	$(VERILATOR) $(VLINTFLAGS) --top-module $(SIM_TOP) \
	  $(ELAB_PARAMS) $(DEFINES) $(SOC_SVS) $(HARNESS_SVS)

run: build-model
	./$(MDIR)/axsoc --max-cycles $(MAX_CYCLES) --uart-input '$(UART_INPUT)' $(if $(UART_INPUT_FILE),--uart-input-file "$(UART_INPUT_FILE)",) $(if $(SD_IMAGE),--sd-image "$(SD_IMAGE)",)

# Build the model and print nothing but its path.  An interactive session
# spawns the binary once and keeps it open for the life of the session, which
# `run` cannot do: it is one process per invocation, so every command would
# reboot the machine.  Keeping the build and the launch separate is what makes
# that possible without duplicating the Verilator command line.
model-path: build-model
	@echo $(abspath $(MDIR)/axsoc)

check-runtime-payload:
	VERILATOR="$(VERILATOR)" python3 check_runtime_payload.py

# Full Phase-6 controller integration: the SoC uses axsdram rather than the
# delayed array model, and a CAS-2 x16 SDRAM model sits at the physical pins.
#
# The selection guard is not defensive tidiness.  This target is reachable with
# any profile, and the default profile is on-chip RAM, so `make run-sdram` used
# to build a BRAM machine and run it under an SDRAM name -- which is exactly
# what `check-sdboot` did, and what its physical-SDRAM claim rested on.  Naming
# the memory and the harness the profile actually resolved to, on stderr where
# it cannot disturb a transcript comparison, makes the substitution visible in
# every log rather than only to whoever reads the profile.
run-sdram: $(SOC_SVS) $(HARNESS_SVS) $(HARNESS_CPP) $(HARNESS_HDRS)
	@test -n "$(ROM_INIT_FILE)" || (echo "ROM_INIT_FILE is required"; exit 2)
	@test -n "$(SIM_TOP)" || (echo "simulation profile must select a harness"; exit 2)
	@test "$(COMPONENT_USE_SDRAM)" = "1" || { \
	  echo "run-sdram: profile '$(COMPONENT_CONFIG_NAME)' selects $(COMPONENT_MEMORY_ID)," >&2; \
	  echo "  which has no physical SDRAM pins. Select a memory component whose runner is" >&2; \
	  echo "  run-sdram, e.g. COMPONENT_CONFIG=../../configs/sim-sdram.json." >&2; \
	  exit 2; }
	@echo "[soc] profile: $(COMPONENT_CONFIG_NAME) memory=$(COMPONENT_MEMORY_ID) harness=$(COMPONENT_HARNESS_ID) top=$(SIM_TOP)" >&2
	$(VERILATOR) $(VFLAGS) --top-module $(SIM_TOP) --Mdir $(MDIR)-sdram --exe --build \
	  $(ELAB_PARAMS) $(DEFINES) $(PROGRESS_CFLAGS) -o axsoc \
	  $(SOC_SVS) $(HARNESS_SVS) $(HARNESS_CPP) >/dev/null
	./$(MDIR)-sdram/axsoc --max-cycles $(MAX_CYCLES) --uart-input '$(UART_INPUT)' $(if $(UART_INPUT_FILE),--uart-input-file "$(UART_INPUT_FILE)",) $(if $(SD_IMAGE),--sd-image "$(SD_IMAGE)",)

clean:
	rm -rf obj_dir build

.PHONY: config run run-sdram run-config build-model lint model-path check-runtime-payload clean
