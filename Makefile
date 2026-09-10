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
	@echo "  make experiment-check   # validate experiment plans and run records"
	@echo "  make experiment-run     # run an experiment plan through its adapters"
	@echo "  make adapter-check      # prove the execution adapters' refusals"
	@echo "  make live-check         # Live FPGA telemetry + shell-isolation RTL, unit and SoC"
	@echo "  make evolution-check    # bounded kernel-evolve tiers in Primer RAM"
	@echo "  make fitness-check      # deterministic Live FPGA fitness contract"
	@echo "  make registry-check     # content-addressed evolution candidates"
	@echo "  make policy-check       # L1 reviewed-program selection policy"
	@echo "  make live-sim-check     # closed-loop virtual FPGA fault scenarios"
	@echo "  make l3-check           # morph search + volatile RTL canary/rollback"
	@echo "  make ecp5-frame-check   # compressed/full/partial ECP5 frame decoder"
	@echo "  make pr-gate-check      # partial-bitstream load gate (R1 stage 3)"
	@echo "  make diagram-check      # every mermaid diagram is well formed"
	@echo "  make brand-check        # derived brand assets match the master lockup"
	@echo "  make static-analysis    # RTL lint, C path analysis, Python, shell"
	@echo "  make toolchain-llvm     # build and run the kernel with clang/lld"
	@echo "  make fuzz-loader        # binary-format parser regression with libFuzzer/sanitizers"
	@echo "  make fuzz-coverage      # what that corpus reaches inside the loader"
	@echo "  make verify-smoke       # fast integrated verification ladder"
	@echo "  make nightly-integrated # broad software/RTL suite with stage logs"
	@echo "  make component-test"
	@echo "  make web                 # boot the machine in a browser (needs emcc)"
	@echo "  make web-check           # headless WASM boot, timed against native"
	@echo "  make web-compare         # one binary on three cores, side by side"
	@echo "  make web-page-check      # both browser pages, in a headless browser"
	@echo "  make doctor              # what this host can build, and what it cannot"
	@echo "  make requirements-check  # host and docs against tools/requirements.json"

# Report the host's toolchain the way a build will actually see it: which
# programs were found, what the probes selected, and which dependency tier each
# missing tool would unlock.  Never fails -- a report that exits non-zero stops
# being readable at the first problem, which is the opposite of the point.
# Requirements are explicit and single-sourced: tools/requirements.json says
# what each tier needs, which versions have actually been run here and with what
# evidence, and which are known-bad and why.  `requirements` regenerates the
# tables in docs/dependencies.md from it; `requirements-check` fails a host that
# is outside the requirement, and fails the docs when they drift from the JSON.
# The docs half needs no toolchain and no network, which is what lets it run in
# CI on every change.
requirements:
	$(PYTHON) tools/requirements.py docs

requirements-check:
	$(PYTHON) tools/requirements.py docs-check
	$(PYTHON) tools/requirements.py check --tier core

doctor:
	@echo "atomiX toolchain report"
	@echo ""
	@echo "Requirements: tools/requirements.json (generated into docs/dependencies.md)"
	@$(PYTHON) tools/requirements.py report
	@echo ""
	@echo "RISC-V prefix probed: $(RISCV_PREFIX)"
	@if command -v $(RISCV_PREFIX)gcc >/dev/null 2>&1; then \
	  echo "ISA probed:           $(RISCV_ARCH), base $(RISCV_ARCH_I)"; \
	else \
	  echo "                      tried: $(RISCV_PREFIX_CANDIDATES)"; \
	  echo "                      set RISCV_PREFIX=<tuple>- if yours differs"; \
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

# An experiment plan is the input a user brings to the platform: one workload
# and oracle, the implementations that claim to satisfy it, the targets that
# can host them, and what each target class is able to measure. It depends on
# comparison-check because the R2 FPGA documents keep their own schema and
# validator -- this gate proves the split held rather than that one format
# quietly replaced the other.
experiment-check: comparison-check
	$(PYTHON) tools/experiment_contract.py check research/experiments
	$(PYTHON) tools/experiment_contract.py self-test

# The adapters' refusals, which a passing experiment never exercises: the
# native leg building with every RISC-V and FPGA tool shadowed, a missing
# prerequisite reported as blocked rather than substituted, semantics refused
# before execution, and a limit that actually reaches the process group.
adapter-check:
	$(PYTHON) tools/adapter_conformance.py

# Run one plan through its adapters. Records land outside tracked source
# unless RECORDS points into the evidence tree, because a scratch run is not
# evidence. ONLY selects candidates; LIMIT_SECONDS overrides the plan budget.
EXPERIMENT_PLAN ?= research/experiments/saxpy-native-vs-rtl.json
EXPERIMENT_RECORDS ?= build/experiments/records
experiment-run:
	$(PYTHON) tools/experiment_run.py $(EXPERIMENT_PLAN) \
	  --records $(EXPERIMENT_RECORDS) \
	  $(foreach candidate,$(ONLY),--only $(candidate)) \
	  $(if $(LIMIT_SECONDS),--limit-seconds $(LIMIT_SECONDS)) \
	  $(if $(REPETITIONS),--repetitions $(REPETITIONS))

# Re-run a recorded candidate and compare identities, oracle outputs, and the
# cycle counts that are supposed to be deterministic.
experiment-replay:
	@test -n "$(RECORD)" || { echo "RECORD is required"; exit 2; }
	$(PYTHON) tools/experiment_run.py $(EXPERIMENT_PLAN) --replay $(RECORD)

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

# The contract half recomputes every search result and the candidate-specific
# trial record without an FPGA toolchain.  The complete gate separately runs
# both the reviewed reference genomes and the bounded volatile L3 loop in RTL.
l3-contract-check:
	$(PYTHON) tools/morph_search.py check
	$(PYTHON) tools/morph_search.py self-test
	$(PYTHON) tools/morph_l3_trial.py check
	$(PYTHON) tools/morph_l3_trial.py self-test

l3-check: l3-contract-check
	$(MAKE) -C sim/unit run-morph-fabric
	$(MAKE) -C sim/unit run-morph-l3

# Diagrams are documentation that breaks silently: a bad mermaid block renders
# as an error box and no ordinary build looks at it.
diagram-check:
	$(PYTHON) tools/diagram_check.py

# Every cloud asset is cut from docs/assets/atomix-logo-cloud.svg, so only one
# file in the family carries sample data and none of them can drift apart.
brand:
	$(PYTHON) tools/brand_cloud.py

brand-check:
	$(PYTHON) tools/brand_cloud.py --check

# Locally this tolerates a missing cppcheck or shellcheck and says which ran;
# CI installs all of them and drops --allow-skips, so an analyzer that fails to
# install there is a failure rather than a silent narrowing of coverage.
# Empty this in CI: there every analyzer is installed, so one that fails to
# install must fail the run rather than quietly narrow what was checked.
ANALYSIS_FLAGS ?= --allow-skips
ANALYSIS_JSON ?= build/static-analysis/report.json
ANALYSIS_SARIF ?= build/static-analysis/report.sarif
static-analysis:
	$(PYTHON) tools/static_analysis.py $(ANALYSIS_FLAGS) \
	  --json $(ANALYSIS_JSON) --sarif $(ANALYSIS_SARIF)

# Build the kernel with clang/lld and run it, so a clang-only diagnostic is
# caught rather than discovered by whoever next tries TOOLCHAIN=llvm. Not a
# replacement for the GCC build and not a gate on it: GCC stays the toolchain
# every recorded number was measured with.
#
# RAM_BYTES is 256 KiB because clang 14 emits ~45% more text than GCC 10 for
# this target, and the page pool is what is left of RAM after the image. At the
# default 128 KiB the clang kernel boots and runs but leaves too few free pages
# for the ABI tests to allocate. See mk/toolchain.mk.
LLVM_RAM_BYTES ?= 262144
CLANG ?= clang
toolchain-llvm:
	$(MAKE) -C sim/axsim clean
	$(MAKE) -C sim/axsim test TOOLCHAIN=llvm
	$(MAKE) -C sw/kernel check-shell TOOLCHAIN=llvm RAM_BYTES=$(LLVM_RAM_BYTES)

# Coverage-guided regression testing of kernel binary-format parsers.
# FUZZ_TIMEOUT bounds the CI run; `make -C sim/fuzz explore` is the unbounded
# one to use when the parser itself has changed.
FUZZ_TIMEOUT ?= 120
ANALYSIS_FUZZ_JSON ?= build/static-analysis/fuzz.json
fuzz-loader:
	$(PYTHON) tools/fuzz_report.py --timeout $(FUZZ_TIMEOUT) \
	  --json $(ANALYSIS_FUZZ_JSON)

fuzz-coverage:
	$(MAKE) -C sim/fuzz coverage

# check-record cross-checks the recorded decode against prjtrellis's device
# database, which ships with the FPGA toolchain and not with this repository.
# The evidence record pins a digest of tools/ecp5_frames.py, so the tool cannot
# absorb the absence without invalidating the record it verifies -- a runner
# with no toolchain gets the deterministic decoder tests and a stated skip.
TRELLIS_DB ?= $(HOME)/opt/oss-cad-suite/share/trellis/database
ecp5-frame-check:
	$(PYTHON) tools/test_ecp5_bitstream.py
	@if [ -f "$(TRELLIS_DB)/devices.json" ]; then \
	  TRELLIS_DB="$(TRELLIS_DB)" $(PYTHON) tools/ecp5_frames.py check-record; \
	else \
	  echo "ECP5 frame evidence: SKIPPED (no Trellis device database at"; \
	  echo "  $(TRELLIS_DB) -- set TRELLIS_DB to cross-check the geometry)"; \
	fi

# R1 stage-3 load gate. The self-test needs no build output, so it runs on a
# machine with no FPGA toolchain; point DELTA/REFERENCE at a `pr-delta` build
# to gate a real candidate as well.
PR_REGION ?= research/partial-reconfig/ulx3s-45f-role-window.json
pr-gate-check:
	$(PYTHON) tools/pr_verify_delta.py self-test
	@if [ -n "$(DELTA)" ]; then \
	  $(PYTHON) tools/pr_verify_delta.py verify "$(DELTA)" \
	    --region $(PR_REGION) \
	    $(if $(REFERENCE),--reference "$(REFERENCE)") \
	    $(if $(FULL_IMAGE),--full-image "$(FULL_IMAGE)"); \
	fi

# Hold the Primer synthesis results to their locked baseline. Give it a sweep
# report from tools/tangprimer_synth_benchmark.py; --partial checks only the
# profiles that report contains.
synth-baseline:
	$(PYTHON) tools/synth_baseline.py show
	@test -n "$(REPORT)" || { echo "usage: make synth-baseline REPORT=<sweep.json>"; exit 2; }
	$(PYTHON) tools/synth_baseline.py check $(REPORT)

# `validate` checks the manifest; `self-test` checks the runner, by running a
# suite built to go wrong: a stage whose tool is missing, one naming a
# configuration that does not resolve, and one the suite asks for but never
# reaches. None of the three may read as a pass, and the two that never ran
# must not silently vanish from the summary.
# Depends on `coverage-map` rather than repeating its command: one spelling,
# and the inventory's own accounting can then see that this rule runs it.
verification-check: coverage-map formal-coverage
	$(PYTHON) tools/verify.py validate
	$(PYTHON) tools/verify.py self-test

# The inventory on its own, with REPORT=1 to print it rather than only check it.
coverage-map:
	$(PYTHON) tools/coverage_map.py $(if $(REPORT),--report)

# Boot every documentation example and require it to print exactly what the
# document says it prints -- and to still be the command that document shows.
example-replay: bug-report-check
	$(PYTHON) tools/example_replay.py $(if $(EXAMPLE),--only $(EXAMPLE))

# Render the verification evidence that is otherwise a second of terminal
# output: what the bounded proofs cover and any counterexample, the first
# ISS/RTL divergence, and an injected fault with the rollback that followed it.
# FORMAL_LOG and COSIM_LOG point at real run output; without them the views
# render what the committed records hold.
evidence-views:
	$(PYTHON) tools/evidence_views.py self-test
	$(PYTHON) tools/evidence_views.py render \
	  $(if $(FORMAL_LOG),--formal-log $(FORMAL_LOG)) \
	  $(if $(COSIM_LOG),--cosim-log $(COSIM_LOG))

# The report format's own failure modes: a finished session and a failed one
# must both reproduce from their records, and a payload that is stale or
# missing must be refused rather than silently substituted.
bug-report-check:
	$(PYTHON) tools/bug_report.py self-test

# Export one session as a record anyone can replay, and replay one. A report
# names its payload's hash, so a program whose bytes have moved on is refused
# rather than quietly substituted for the one the report is about.
bug-report:
	@test -n "$(EXAMPLE)$(RECORD)" || { echo "usage: make bug-report EXAMPLE=<name> | RECORD=<path>"; exit 2; }
	$(PYTHON) tools/bug_report.py $(if $(RECORD),replay $(RECORD),export $(EXAMPLE) $(if $(OUTPUT),--output $(OUTPUT)))

# What the bounded proofs prove, derived from the check lists, the .cfg files,
# and each RVFI wrapper, and compared against the committed record. WRITE=1
# rewrites the record, which a proof getting wider or narrower requires --
# deliberately, because that is a change of claim.
formal-coverage:
	$(PYTHON) tools/formal_coverage.py $(if $(REPORT),--report) $(if $(WRITE),--write)

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

# The same binary on several component selections at once, each with its own
# cycle count.  WEB_MACHINES selects them; they deliberately differ in one
# component, so the spread between them is attributable rather than merely
# observed.  web-compare-check is the headless half and is the evidence; web-
# compare also serves the page.
WEB_MACHINES ?= sim-minimal sim-bram sim-ax2

web-compare:
	./tools/web.sh --compare --machines "$(WEB_MACHINES)"

web-compare-check:
	./tools/web.sh --compare --machines "$(WEB_MACHINES)" --check-only

# The pages themselves, driven in a headless browser.  `web-check` and
# `web-compare-check` drive the machines through the same C API the pages use,
# which is where the evidence is; this covers the page around them -- module
# loading, asset paths, the scheduling loop, and whether any number reaches the
# screen.  Skips rather than fails when no browser is installed; AX_BROWSER
# picks one.  It never terminates a browser process it did not start.
web-page-check:
	./tools/web.sh --page-check --machines "$(WEB_MACHINES)"

# Covers all supplied simulation profiles, including the deliberately minimal
# alternate CPU. FPGA P&R and physical-board validation remain separate gates.
component-test: config-check-all personality-check comparison-check experiment-check adapter-check
	$(MAKE) software CONFIG=configs/sim-hello.json
	$(MAKE) sim CONFIG=configs/sim-delayed.json RAM_INIT_FILE="$(abspath sw/baremetal/build/hello.hex)" MAX_CYCLES=10000 BUILD_ID=component-delayed
	$(MAKE) sim CONFIG=configs/sim-delayed-passthrough-cache.json RAM_INIT_FILE="$(abspath sw/baremetal/build/hello.hex)" MAX_CYCLES=10000 BUILD_ID=component-passthrough-cache
	$(MAKE) sim CONFIG=configs/sim-finisher.json RAM_INIT_FILE="$(abspath sw/baremetal/build/hello.hex)" MAX_CYCLES=100 BUILD_ID=component-finisher
	$(MAKE) software CONFIG=configs/sim-axos.json

.PHONY: help load fpga-loader fpga-loader-primer doctor requirements requirements-check component-list component-show config-check config-check-all personality-check comparison-check experiment-check adapter-check experiment-run experiment-replay live-check evolution-check fitness-check registry-check policy-check live-sim-check l3-contract-check l3-check ecp5-frame-check pr-gate-check diagram-check brand brand-check static-analysis toolchain-llvm fuzz-loader fuzz-coverage verification-check coverage-map formal-coverage example-replay bug-report bug-report-check evidence-views verify-smoke nightly-integrated sim software fpga kernel-primer runtime-primer fpga-kernel-primer fpga-runtime-primer primer-runtime-preflight component-test web web-check web-bench web-compare web-compare-check web-page-check
