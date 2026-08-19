# Top-level component-oriented entry points.  Existing per-directory Makefiles
# remain useful; these commands make an explicit configuration the normal way
# to compose a system.
include mk/toolchain.mk
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
	@echo "                           # ^ those bake a program in; bring-up only"
	@echo "  make fpga-loader-primer  # loader bitstream: blank RAM, no baked program"
	@echo "  make fpga-loader LOADER_CONFIG=configs/tangprimer25k-runtime-tpu.json"
	@echo "  make load PROGRAM=snake  # send a program to a board running the loader"
	@echo "  make kernel-primer       # exact 32 KiB ISS + RTL gate"
	@echo "  make runtime-primer      # two live GPU programs, no resynthesis"
	@echo "  make -C sw/kernel check-uartboot # upload full aXos into blank RAM"
	@echo "  make fpga-kernel-primer  # compatibility alias for loader-only image"
	@echo "  make fpga-runtime-primer # fast-switch gate, then build once"
	@echo "  make primer-runtime-preflight # simulation + bitstream evidence, no board"
	@echo "  python3 tools/bench.py cpu|gpu|tpu|tang"
	@echo "  make personality-check  # validate open compute-personality contracts"
	@echo "  make comparison-check   # validate research comparison/evidence contracts"
	@echo "  make live-check         # Live FPGA telemetry + shell-isolation RTL, unit and SoC"
	@echo "  make evolution-check    # bounded kernel-evolve tiers in Primer RAM"
	@echo "  make fitness-check      # deterministic Live FPGA fitness contract"
	@echo "  make registry-check     # content-addressed evolution candidates"
	@echo "  make policy-check       # L1 reviewed-program selection policy"
	@echo "  make live-sim-check     # closed-loop virtual FPGA fault scenarios"
	@echo "  make l3-check           # bounded morph-genome search + RTL reference"
	@echo "  make verify-smoke       # fast integrated verification ladder"
	@echo "  make nightly-integrated # broad software/RTL suite with stage logs"
	@echo "  make component-test"
	@echo "  make web                 # boot the machine in a browser (needs emcc)"
	@echo "  make web-check           # headless WASM boot, timed against native"
	@echo "  make doctor              # what this host can build, and what it cannot"

# Report the host's toolchain the way a build will actually see it: which
# programs were found, what the probes selected, and which dependency tier each
# missing tool would unlock.  Never fails -- a report that exits non-zero stops
# being readable at the first problem, which is the opposite of the point.
doctor:
	@echo "atomiX toolchain report"
	@echo ""
	@echo "Core tier (build + simulation + component tests)"
	@printf '  %-22s %s\n' "RISC-V prefix" "$(RISCV_PREFIX)"
	@if command -v $(RISCV_PREFIX)gcc >/dev/null 2>&1; then \
	  printf '  %-22s %s\n' "RISC-V GCC" \
	    "$$($(RISCV_PREFIX)gcc --version | awk 'NR==1')"; \
	  printf '  %-22s %s\n' "ISA (probed)" "$(RISCV_ARCH), base $(RISCV_ARCH_I)"; \
	else \
	  printf '  %-22s %s\n' "RISC-V GCC" "MISSING - target code cannot be built"; \
	  echo "                         tried: $(RISCV_PREFIX_CANDIDATES)"; \
	  echo "                         set RISCV_PREFIX=<tuple>- if yours differs"; \
	fi
	@if command -v $(VERILATOR) >/dev/null 2>&1; then \
	  printf '  %-22s %s\n' "Verilator" "$(VERILATOR_VERSION)"; \
	else \
	  printf '  %-22s %s\n' "Verilator" "MISSING - no RTL simulation"; \
	fi
	@printf '  %-22s %s\n' "Python" "$$($(PYTHON) --version 2>&1)"
	@echo ""
	@echo "Kernel tier (aXos S/U-mode boot checks; needs QEMU >= 7)"
	@if command -v qemu-system-riscv32 >/dev/null 2>&1; then \
	  printf '  %-22s %s\n' "QEMU" \
	    "$$(qemu-system-riscv32 --version | awk 'NR==1')"; \
	else \
	  printf '  %-22s %s\n' "QEMU" "not found - skip the three-platform checks"; \
	fi
	@echo ""
	@echo "Formal tier"
	@for tool in yosys sby; do \
	  if command -v $$tool >/dev/null 2>&1; then \
	    printf '  %-22s %s\n' "$$tool" "found"; \
	  else \
	    printf '  %-22s %s\n' "$$tool" "not found - skip make -C formal check"; \
	  fi; \
	done
	@if [ -d /opt/riscv-formal ]; then \
	  printf '  %-22s %s\n' "/opt/riscv-formal" "present"; \
	else \
	  printf '  %-22s %s\n' "/opt/riscv-formal" "absent - see docs/toolchain.md"; \
	fi
	@echo ""
	@echo "Browser tier (optional; WASM build of the Verilated model)"
	@if command -v emcc >/dev/null 2>&1; then \
	  printf '  %-22s %s\n' "Emscripten" \
	    "$$(emcc --version | awk 'NR==1{print $$(NF-1)}')"; \
	elif [ -f "$$HOME/emsdk/emsdk_env.sh" ]; then \
	  printf '  %-22s %s\n' "Emscripten" "installed, not sourced - source ~/emsdk/emsdk_env.sh"; \
	else \
	  printf '  %-22s %s\n' "Emscripten" "not found - skip the browser targets"; \
	fi
	@echo ""
	@echo "FPGA tier: make -C rtl/fpga check-tools CONFIG=<board profile>"
	@echo "Install guidance for every tier: docs/dependencies.md"

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

personality-check:
	$(PYTHON) tools/personality_contract.py check research/personalities
	$(PYTHON) tools/personality_contract.py self-test

comparison-check: personality-check
	$(PYTHON) tools/comparison_contract.py check research/comparisons
	$(PYTHON) tools/comparison_contract.py self-test

# The unit benches prove the monitor and the fence; check-livecount proves the
# wiring between them in an assembled SoC.  That last one is not optional
# padding: DESCRIPTOR_REJECTIONS read zero for every possible input until
# 2026-08-13 because soc_top tied its producer off, and a gate that only ran
# unit benches is what let that survive.
live-check:
	$(MAKE) -C sim/unit run-axlivemon run-axroleiso run-axroleiso-no-role-events
	$(MAKE) -C sw/baremetal check-livecount

evolution-check:
	$(MAKE) -C sw/kernel evolution-check

fitness-check: evolution-check
	$(PYTHON) tools/live_fitness.py check research/live-fpga/fitness-example.json
	$(PYTHON) tools/live_fitness.py self-test

registry-check:
	$(PYTHON) tools/candidate_registry.py check
	$(PYTHON) tools/candidate_registry.py self-test

policy-check: fitness-check registry-check
	$(PYTHON) tools/live_policy.py check
	$(PYTHON) tools/live_policy.py self-test

shadow-check: policy-check
	$(PYTHON) tools/live_shadow.py check
	$(PYTHON) tools/live_shadow.py self-test

# Regenerates the shadow record from real RTL runs; not part of the fast gates
# because it re-simulates every candidate.
shadow-rebuild:
	$(PYTHON) tools/live_shadow_build.py

live-sim-check: shadow-check
	$(MAKE) -C sim/livefpga check

# The contract half recomputes every search result and is intentionally free of
# an FPGA toolchain.  The complete gate also runs the reviewed genomes on the
# RTL, keeping model evidence distinct from a candidate's future shadow pass.
l3-contract-check:
	$(PYTHON) tools/morph_search.py check
	$(PYTHON) tools/morph_search.py self-test

l3-check: l3-contract-check
	$(MAKE) -C sim/unit run-morph-fabric

# Hold the Primer synthesis results to their locked baseline. Give it a sweep
# report from tools/tangprimer_synth_benchmark.py; --partial checks only the
# profiles that report contains.
synth-baseline:
	$(PYTHON) tools/synth_baseline.py show
	@test -n "$(REPORT)" || { echo "usage: make synth-baseline REPORT=<sweep.json>"; exit 2; }
	$(PYTHON) tools/synth_baseline.py check $(REPORT)

verification-check:
	$(PYTHON) tools/verify.py validate

verify-smoke: verification-check
	$(PYTHON) tools/verify.py run smoke

nightly-integrated: verification-check
	$(PYTHON) tools/verify.py run nightly-integrated --keep-going

sim:
	$(MAKE) -C sim/soc run-config COMPONENT_CONFIG="$(abspath $(CONFIG))"

fpga:
	$(MAKE) -C rtl/fpga all COMPONENT_CONFIG="$(abspath $(CONFIG))"

# The loader bitstream: blank RAM, immutable UART ROM, no baked program.  This
# is what a board should be running, and it is built once per profile rather
# than once per program.
#
# `make fpga ... PROGRAM=<name>` bakes the payload into synthesis instead,
# because block RAM contents are set when the device is configured.  That path
# exists for first bring-up of a board with no loader image; it is not how
# software ships, because it makes every program its own bitstream, its own
# placement, and its own timing claim.
fpga-loader-primer:
	$(MAKE) -C sw/bootrom images MODE=uart RAM_BYTES=32768 \
	  BUILD_DIR=build/uart-ram32768
	$(MAKE) -C rtl/fpga all \
	  COMPONENT_CONFIG="$(abspath configs/tangprimer25k-runtime.json)" \
	  RAM_INIT_FILE="$(abspath sw/bootrom/blank.hex)" \
	  ROM_INIT_FILE="$(abspath sw/bootrom/build/uart-ram32768/bootrom.hex)"

# The same thing for any profile that resets into the ROM.  `reset_pc` is what
# declares a loader profile, and rtl/fpga derives blank RAM and a correctly
# sized UART ROM from it, so there is nothing else to pass -- there is no
# payload to name.  The check is the point of having a target at all: aimed at
# a baked profile this would quietly produce an image carrying one program,
# which is the coupling the loader exists to remove.
LOADER_CONFIG ?= configs/tangprimer25k-runtime.json
fpga-loader:
	@$(PYTHON) -c 'import json,sys; s=json.load(open("$(LOADER_CONFIG)")).get("settings",{}); pc=int(str(s.get("reset_pc","0")),0); sys.exit(0 if pc==0x1000 else "$(LOADER_CONFIG): reset_pc is %#x, not 0x1000, so this profile boots a baked payload rather than the loader. Use a runtime profile, or `make fpga CONFIG=... PROGRAM=<name>` if a baked image is really what you want." % pc)'
	$(MAKE) -C rtl/fpga all COMPONENT_CONFIG="$(abspath $(LOADER_CONFIG))"

# Send a program to a board already running a loader bitstream.  No synthesis,
# no reconfiguration: the hardware is not a function of the program, and this
# does not change the bitstream the board is running.
PROGRAM ?= snake
SERIAL ?= /dev/ttyUSB1
BAUD ?= 921600
BOARD_RAM_BYTES ?= 32768
load:
	@test -e "$(SERIAL)" || { echo "no board at $(SERIAL): attach the Dock (docs/tangprimer25k-bringup.md), or pass SERIAL=<tty>"; exit 2; }
	$(MAKE) -C sw/baremetal BUILD_DIR=build/ram$(BOARD_RAM_BYTES) \
	  RAM_BYTES=$(BOARD_RAM_BYTES) build/ram$(BOARD_RAM_BYTES)/$(PROGRAM).bin
	$(PYTHON) sw/host/axhost.py --serial $(SERIAL) --baud $(BAUD) \
	  --upload-kernel sw/baremetal/build/ram$(BOARD_RAM_BYTES)/$(PROGRAM).bin

# Kernel binaries are simulation artifacts and runtime payloads.  They are
# deliberately never passed to the FPGA flow as RAM initialisation.
kernel-primer:
	$(MAKE) -C sw/kernel check-primer

runtime-primer:
	$(MAKE) -C sw/kernel check-primer-runtime

fpga-runtime-primer: runtime-primer
	$(MAKE) -C rtl/fpga all \
	  COMPONENT_CONFIG="$(abspath configs/tangprimer25k-runtime-gpu.json)" \
	  RAM_INIT_FILE="$(abspath sw/bootrom/blank.hex)" \
	  ROM_INIT_FILE="$(abspath sw/bootrom/build/uart-ram32768/bootrom.hex)"

# Finish every gate that does not need the Dock.  The generated evidence JSON
# identifies the exact volatile image to program when the hardware is attached.
primer-runtime-preflight: runtime-primer
	$(MAKE) -C rtl/fpga gowin-evidence \
	  COMPONENT_CONFIG="$(abspath configs/tangprimer25k-runtime-gpu.json)" \
	  RAM_INIT_FILE="$(abspath sw/bootrom/blank.hex)" \
	  ROM_INIT_FILE="$(abspath sw/bootrom/build/uart-ram32768/bootrom.hex)" \
	  EVIDENCE_KERNEL="$(abspath sw/kernel/build/primer-runtime/axos_boot.bin)" \
	  EVIDENCE_RUNTIME_GATE=PASS

# Kept for scripts that used the old name.  This now produces the same
# loader-only image; no aXos kernel is synthesized into FPGA memory.
fpga-kernel-primer: fpga-runtime-primer

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

# Browser tier.  Optional and load-bearing for nothing: it compiles the same
# Verilated model to WebAssembly so the machine can be booted without a
# toolchain.  WEB_CONFIG selects the profile the page boots and WEB_PAYLOAD the
# program it runs; both default to the aXos shell with the loopback role, which
# is the selection that has something to demonstrate.
WEB_CONFIG ?= configs/sim-role-loopback.json
WEB_PAYLOAD ?= sw/kernel/build/axos_boot.hex

# One script rather than a second implementation: it sources the SDK, picks a
# Verilator the suite is green on, builds a missing payload, verifies headlessly,
# finds a free port, and opens the page.  The per-directory targets underneath
# it stay available for anyone who wants the steps separately.
web:
	./tools/web.sh --config "$(WEB_CONFIG)" --payload "$(WEB_PAYLOAD)"

web-check:
	./tools/web.sh --config "$(WEB_CONFIG)" --payload "$(WEB_PAYLOAD)" --check-only

web-bench:
	$(MAKE) -C sim/web bench COMPONENT_CONFIG="$(abspath $(WEB_CONFIG))" PAYLOAD="$(abspath $(WEB_PAYLOAD))"

# Covers all supplied simulation profiles, including the deliberately minimal
# alternate CPU. FPGA P&R and physical-board validation remain separate gates.
component-test: config-check-all personality-check comparison-check
	$(MAKE) software CONFIG=configs/sim-hello.json
	$(MAKE) sim CONFIG=configs/sim-delayed.json RAM_INIT_FILE="$(abspath sw/baremetal/build/hello.hex)" MAX_CYCLES=10000 BUILD_ID=component-delayed
	$(MAKE) sim CONFIG=configs/sim-delayed-passthrough-cache.json RAM_INIT_FILE="$(abspath sw/baremetal/build/hello.hex)" MAX_CYCLES=10000 BUILD_ID=component-passthrough-cache
	$(MAKE) sim CONFIG=configs/sim-finisher.json RAM_INIT_FILE="$(abspath sw/baremetal/build/hello.hex)" MAX_CYCLES=100 BUILD_ID=component-finisher
	$(MAKE) software CONFIG=configs/sim-axos.json

.PHONY: help load fpga-loader fpga-loader-primer doctor component-list component-show config-check config-check-all personality-check comparison-check live-check evolution-check fitness-check registry-check policy-check live-sim-check l3-contract-check l3-check verification-check verify-smoke nightly-integrated sim software fpga kernel-primer runtime-primer fpga-kernel-primer fpga-runtime-primer primer-runtime-preflight component-test web web-check web-bench
