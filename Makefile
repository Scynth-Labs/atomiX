# Top-level component-oriented entry points.  Existing per-directory Makefiles
# remain useful; these commands make an explicit configuration the normal way
# to compose a system.
PYTHON ?= python3
CONFIG ?= configs/sim-bram.json
# The absolute path makes profiles from separate DIY worktrees independent even
# when they happen to share a filename such as `sim-bram.json`.
COMPONENT_CONFIG_KEY := $(subst /,_,$(basename $(abspath $(CONFIG))))
COMPONENT_MK := build/component$(COMPONENT_CONFIG_KEY).mk
COMPONENT_MANIFESTS := $(wildcard components/*/*/component.json)

$(COMPONENT_MK): $(CONFIG) tools/configure.py $(COMPONENT_MANIFESTS)
	mkdir -p $(@D)
	$(PYTHON) tools/configure.py resolve --config "$(CONFIG)" --output $@

-include $(COMPONENT_MK)
$(COMPONENT_MK): $(COMPONENT_SELECTED_MANIFESTS)

.DEFAULT_GOAL := help

help:
	@echo "atomiX component build"
	@echo "  make component-list"
	@echo "  make component-show COMPONENT=memory.sdram"
	@echo "  make config-check CONFIG=configs/sim-sdram.json"
	@echo "  make sim CONFIG=configs/sim-bram.json RAM_INIT_FILE=/path/program.hex"
	@echo "  make software CONFIG=configs/sim-hello.json"
	@echo "  make fpga CONFIG=configs/ulx3s-85f.json"
	@echo "  make fpga CONFIG=configs/tangprimer25k.json"
	@echo "  make fpga CONFIG=configs/tangprimer25k-ax2.json PROGRAM=cpu_perf"
	@echo "  make fpga CONFIG=configs/tangprimer25k-gpu.json PROGRAM=gpu_perf"
	@echo "  python3 tools/bench.py cpu|gpu|tpu|tang"
	@echo "  make component-test"

component-list:
	$(PYTHON) tools/configure.py list

component-show:
	@test -n "$(COMPONENT)" || { echo "COMPONENT is required"; exit 2; }
	$(PYTHON) tools/configure.py describe "$(COMPONENT)"

config-check:
	$(PYTHON) tools/configure.py resolve --config "$(CONFIG)"

config-check-all:
	@for component_config in configs/*.json; do \
	  $(PYTHON) tools/configure.py resolve --config "$$component_config" >/dev/null || exit $$?; \
	  echo "configuration: $$component_config: PASS"; \
	done

sim:
	$(MAKE) -C sim/soc run-config COMPONENT_CONFIG="$(abspath $(CONFIG))"

fpga:
	$(MAKE) -C rtl/fpga all COMPONENT_CONFIG="$(abspath $(CONFIG))"

# Build and run the software component selected by a profile.  The component
# owns its own Makefile and image format; this target merely passes the result
# to the selected hardware profile.  That keeps a replacement kernel or
# bare-metal project independent from aXos's source tree.
software: $(COMPONENT_MK)
	@test -n "$(COMPONENT_SOFTWARE_ID)" || { echo "$(CONFIG): no software component selected"; exit 2; }
	@case "$(COMPONENT_SOFTWARE_RUNNER)" in ram|sdboot) ;; *) \
	  echo "unsupported software runner: $(COMPONENT_SOFTWARE_RUNNER)"; exit 2;; esac
	$(MAKE) -C "$(COMPONENT_SOFTWARE_MAKE_DIR)" "$(COMPONENT_SOFTWARE_MAKE_TARGET)" $(if $(COMPONENT_KERNEL_CONFIG),KERNEL_CONFIG="$(COMPONENT_KERNEL_CONFIG)")
	@if [ "$(COMPONENT_SOFTWARE_RUNNER)" = "ram" ]; then \
	  $(MAKE) sim CONFIG="$(COMPONENT_CONFIG_PATH)" \
	    RAM_INIT_FILE="$(COMPONENT_SOFTWARE_RAM_HEX)" \
	    MAX_CYCLES="$(COMPONENT_SOFTWARE_MAX_CYCLES)" BUILD_ID=software-$(COMPONENT_CONFIG_NAME); \
	else \
	  $(MAKE) sim CONFIG="$(COMPONENT_CONFIG_PATH)" \
	    ROM_INIT_FILE="$(COMPONENT_SOFTWARE_ROM_HEX)" \
	    SD_IMAGE="$(COMPONENT_SOFTWARE_SD_IMAGE)" \
	    UART_INPUT_FILE="$(COMPONENT_SOFTWARE_UART_INPUT)" \
	    MAX_CYCLES="$(COMPONENT_SOFTWARE_MAX_CYCLES)" BUILD_ID=software-$(COMPONENT_CONFIG_NAME); \
	fi

# Covers all supplied simulation profiles, including the deliberately minimal
# alternate CPU. FPGA P&R and physical-board validation remain separate gates.
component-test: config-check-all
	$(MAKE) software CONFIG=configs/sim-hello.json
	$(MAKE) sim CONFIG=configs/sim-delayed.json RAM_INIT_FILE="$(abspath sw/baremetal/build/hello.hex)" MAX_CYCLES=10000 BUILD_ID=component-delayed
	$(MAKE) sim CONFIG=configs/sim-delayed-passthrough-cache.json RAM_INIT_FILE="$(abspath sw/baremetal/build/hello.hex)" MAX_CYCLES=10000 BUILD_ID=component-passthrough-cache
	$(MAKE) sim CONFIG=configs/sim-finisher.json RAM_INIT_FILE="$(abspath sw/baremetal/build/hello.hex)" MAX_CYCLES=100 BUILD_ID=component-finisher
	$(MAKE) software CONFIG=configs/sim-axos.json

.PHONY: help component-list component-show config-check config-check-all sim software fpga component-test
