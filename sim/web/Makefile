# Browser-hosted simulation: the same Verilated SoC, compiled to WebAssembly.
#
# Nothing in the build, test, formal, or FPGA flow depends on this directory,
# and no evidence claim rests on it. It exists so the machine can be booted
# without a toolchain -- the cycle counts it reports come from the same RTL and
# the same runner code the native checks use.
VERILATOR ?= verilator
EMCC ?= em++
PYTHON ?= python3

# Verilator 5 reports component-API package constants as UNUSEDPARAM and makes
# warnings fatal under -Wall; mirror sim/soc's guard so both trees agree.
VERILATOR_VERSION := $(word 2,$(shell $(VERILATOR) --version 2>/dev/null))
VERILATOR_MAJOR := $(firstword $(subst ., ,$(VERILATOR_VERSION)))
VFLAGS := -Wall --cc -O2
ifneq ($(filter 5 6 7 8 9,$(VERILATOR_MAJOR)),)
VFLAGS += -Wno-UNUSEDPARAM
endif

.DEFAULT_GOAL := build

COMPONENT_CONFIG ?= ../../configs/sim-role-loopback.json
COMPONENT_CONFIG_KEY := $(subst /,_,$(basename $(abspath $(COMPONENT_CONFIG))))
COMPONENT_MK := build/component$(COMPONENT_CONFIG_KEY).mk
COMPONENT_MANIFESTS := $(wildcard ../../components/*/*/component.json)

$(COMPONENT_MK): $(COMPONENT_CONFIG) ../../tools/configure.py $(COMPONENT_MANIFESTS)
	mkdir -p $(@D)
	$(PYTHON) ../../tools/configure.py resolve --config $(COMPONENT_CONFIG) --output $@

-include $(COMPONENT_MK)
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
SIM_TOP ?= $(COMPONENT_SIM_TOP)
HARNESS_DIR := ../../components/harness/common
HARNESS_HDRS := $(wildcard $(HARNESS_DIR)/*.h)
DRIVER := tb_soc_wasm.cpp

RESET_PC ?= $(COMPONENT_RESET_PC)
RAM_BYTES ?= $(COMPONENT_RAM_BYTES)
EXTERNAL_MEMORY ?= $(COMPONENT_USE_DRAM_MODEL)
CACHES ?= $(COMPONENT_CACHES)
SYNC_READ ?= 0
DEFINES ?= $(COMPONENT_DEFINES)
CACHE_LINES ?= $(if $(COMPONENT_SETTING_CACHE_LINES),$(COMPONENT_SETTING_CACHE_LINES),16)
CACHE_WORDS_PER_LINE ?= $(if $(COMPONENT_SETTING_CACHE_WORDS_PER_LINE),$(COMPONENT_SETTING_CACHE_WORDS_PER_LINE),4)

# The payload the page boots. It is staged beside the module and fetched at run
# time rather than embedded, so one compiled machine can boot more than one
# program: the model's `$readmemh` reads this path out of the in-memory
# filesystem when ax_boot() constructs it, and the caller writes it first.
# The path is deliberately virtual -- baking a developer's absolute home
# directory into a published page would be both useless and a small leak.
PAYLOAD ?= $(abspath ../../sw/kernel/build/axos_boot.hex)
RAM_INIT_PATH := /payload.hex

OUT_DIR := public
MODULE := $(OUT_DIR)/axsoc.js
MDIR := obj_dir_$(COMPONENT_CONFIG_NAME)

# The bundle lives at one fixed path because the page loads it by name, but the
# machine inside it is profile-specific -- so file timestamps alone cannot say
# whether it is current. Switching profiles leaves a *newer* bundle built from
# the *other* selection, and make would call that up to date and serve the
# wrong machine while the page confidently labels it the right one. Key the
# staged files on the selection instead: the stamp is only rewritten when the
# selection changes, so an unchanged one rebuilds nothing.
BUNDLE_TAG := $(COMPONENT_CONFIG_NAME)|$(PAYLOAD)|$(RAM_INIT_PATH)
STAMP := $(OUT_DIR)/.bundle-tag

$(STAMP): FORCE
	@mkdir -p $(OUT_DIR)
	@[ "$$(cat $@ 2>/dev/null)" = '$(BUNDLE_TAG)' ] || printf '%s' '$(BUNDLE_TAG)' > $@
FORCE:

EMFLAGS := -O3 --no-entry \
  -sMODULARIZE=1 -sEXPORT_NAME=createAxSoc \
  -sENVIRONMENT=web,worker,node \
  -sALLOW_MEMORY_GROWTH=1 -sINITIAL_MEMORY=67108864 \
  -sEXPORTED_RUNTIME_METHODS=['FS','ccall','cwrap','UTF8ToString','HEAPU8'] \
  -sEXPORTED_FUNCTIONS=['_ax_boot','_ax_shutdown','_ax_send','_ax_run','_ax_run_to_output','_ax_cpu_idle','_ax_recv','_ax_cycles','_ax_finished','_ax_exit_code','_ax_ram_init_path','_ax_profile','_malloc','_free']

config: $(COMPONENT_MK)
	@echo "configuration: $(COMPONENT_CONFIG_NAME)"
	@echo "  core:   $(COMPONENT_CORE_ID)"
	@echo "  soc:    $(COMPONENT_SOC_ID)"
	@echo "  memory: $(COMPONENT_MEMORY_ID)"
	@echo "  role:   $(COMPONENT_ROLE_ID)"
	@echo "  payload: $(PAYLOAD)"

# Verilate to C++ only. The generated model is then compiled by Emscripten
# rather than by the host compiler, which is the whole trick: no RTL, no
# harness code, and no build parameter differs from the native model.
$(MDIR)/V$(SIM_TOP).mk: $(SOC_SVS) $(COMPONENT_MK)
	@test -n "$(SIM_TOP)" || { echo "profile must select a harness"; exit 2; }
	$(VERILATOR) $(VFLAGS) --top-module $(SIM_TOP) --Mdir $(MDIR) \
	  -GRESET_PC=$(RESET_PC) -GRAM_BYTES=$(RAM_BYTES) \
	  -GUSE_DRAM_MODEL=$(EXTERNAL_MEMORY) -GUSE_CACHES=$(CACHES) \
	  -GSYNC_READ=$(SYNC_READ) \
	  -GCACHE_LINES=$(CACHE_LINES) -GCACHE_WORDS_PER_LINE=$(CACHE_WORDS_PER_LINE) \
	  $(DEFINES) \
	  -GRAM_INIT_FILE=\"$(RAM_INIT_PATH)\" -GROM_INIT_FILE=\"\" \
	  $(SOC_SVS)

VERILATOR_ROOT_DIR := $(shell $(VERILATOR) --getenv VERILATOR_ROOT)
MODEL_SRCS = $(wildcard $(MDIR)/*.cpp) $(VERILATOR_ROOT_DIR)/include/verilated.cpp

$(MODULE): $(MDIR)/V$(SIM_TOP).mk $(DRIVER) $(HARNESS_HDRS) $(STAMP)
	@command -v $(EMCC) >/dev/null 2>&1 || { \
	  echo "$(EMCC) not found - source ~/emsdk/emsdk_env.sh (docs/toolchain.md)"; exit 2; }
	mkdir -p $(OUT_DIR)
	$(EMCC) $(EMFLAGS) \
	  -DAX_RAM_INIT_PATH='"$(RAM_INIT_PATH)"' \
	  -DAX_PROFILE_NAME='"$(COMPONENT_CONFIG_NAME)"' \
	  -I$(MDIR) -I$(HARNESS_DIR) -I$(VERILATOR_ROOT_DIR)/include \
	  -I$(VERILATOR_ROOT_DIR)/include/vltstd \
	  $(DRIVER) $(MODEL_SRCS) -o $@

# The payload is a build product of the software component, not of this one;
# it is staged here so the page can fetch it and load it into the machine.
$(OUT_DIR)/payload.hex: $(PAYLOAD) $(STAMP)
	@test -f "$(PAYLOAD)" || { \
	  echo "payload not found: $(PAYLOAD) (build it, e.g. make -C sw/kernel images)"; exit 2; }
	mkdir -p $(OUT_DIR)
	cp $(PAYLOAD) $@

build: $(MODULE) $(OUT_DIR)/payload.hex
	@echo "browser bundle: $(abspath $(OUT_DIR))"

# Headless boot under Node: the WASM counterpart of a batch `make sim` run, and
# the measurement that says whether a boot in a tab is imperceptible.
check: build
	@command -v $(NODE) >/dev/null 2>&1 || { \
	  echo "$(NODE) not found - emsdk ships one (docs/toolchain.md)"; exit 2; }; \
	  $(NODE) boot.mjs

# The same measurement, against the native model built from the same profile on
# the same host. Comparing against the baseline recorded in the checklist would
# conflate "WebAssembly is slower" with "this machine is slower", and those two
# have opposite consequences for whether the browser console is worth having.
BENCH_CYCLES ?= 2000000
bench: build
	@model=$$($(MAKE) -s --no-print-directory -C ../soc model-path \
	    VERILATOR="$(VERILATOR)" RAM_INIT_FILE="$(PAYLOAD)" RESET_PC=$(RESET_PC) \
	    COMPONENT_CONFIG="$(abspath $(COMPONENT_CONFIG))" | tail -n 1); \
	  test -x "$$model" || { echo "native model not built: '$$model'"; exit 2; }; \
	  start=$$(date +%s%N); \
	  "$$model" --max-cycles $(BENCH_CYCLES) --uart-input '' >/dev/null 2>&1; \
	  elapsed=$$(( ($$(date +%s%N) - start) / 1000000 )); \
	  test "$$elapsed" -gt 0 || { echo "native run did not execute"; exit 2; }; \
	  rate=$$(( $(BENCH_CYCLES) * 1000 / elapsed )); \
	  echo "[native] $(BENCH_CYCLES) cycles in $$elapsed ms ($$rate cycles/s)"; \
	  $(NODE) boot.mjs --bench --native-rate=$$rate

NODE ?= node

# Any static file server works; the module is fetched, so file:// will not do.
# tools/web.sh announces the URL itself, with the port it just found free, so
# it sets ANNOUNCE=0 rather than having the reader told twice.
serve: build
	@test "$(ANNOUNCE)" = "0" || echo "http://localhost:$(PORT)/"
	$(PYTHON) -m http.server $(PORT) --directory $(OUT_DIR)
PORT ?= 8000
ANNOUNCE ?= 1

clean:
	rm -rf obj_dir_* build $(OUT_DIR)/axsoc.js $(OUT_DIR)/axsoc.wasm \
	  $(OUT_DIR)/payload.hex $(STAMP)

.PHONY: config build check bench serve clean FORCE
