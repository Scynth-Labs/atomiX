# libFuzzer harnesses for the components that parse untrusted input.
#
# clang rather than the RISC-V toolchain, and clang regardless of TOOLCHAIN.
# That is not a break in the one-toolchain rule: this builds a *host* test
# harness, not a component of the machine, and libFuzzer's coverage
# instrumentation exists only in clang -- GCC has no equivalent to select. The
# code under test is the same source the kernel links, with no copy and no
# reimplementation, so a finding here is a finding in the shipped loader.
CLANG ?= clang
# Ubuntu ships these under the versioned directory rather than on PATH.
LLVM_COV ?= $(shell command -v llvm-cov 2>/dev/null || echo /usr/lib/llvm-14/bin/llvm-cov)
LLVM_PROFDATA ?= $(shell command -v llvm-profdata 2>/dev/null || echo /usr/lib/llvm-14/bin/llvm-profdata)
ROOT := ../..
RUNS ?= 200000
TIMEOUT ?= 120

# Three detectors, and they do not overlap:
#
#   ASan   heap corruption, use-after-free, and a stack trace for the SIGSEGV
#          the guard page raises. Its report says *where*, which a bare fault
#          does not.
#   LSan   comes with ASan and is left on. The harness allocates pages from a
#          fixed pool, so a leak here means the loader lost a mapping -- on the
#          real machine that is a page the address space never gets back.
#   UBSan  inline checks: misaligned loads and signed overflow in a parser
#          reading attacker-controlled offsets.
#
# The guard page in fuzz_loader.c stays alongside ASan rather than instead of
# it. ASan does not poison an mmap'd region, so the guard is what makes an
# overread deterministic; ASan is what turns the resulting fault into a report
# with a stack trace. tools/fuzz_report.py parses all three into the same
# findings schema the static analysis uses, so they reach the same issue.
#
# -std=c11 stays, so the component under test is compiled the way the kernel
# compiles it. _DEFAULT_SOURCE is for the harness only: strict C11 hides
# MAP_ANONYMOUS, and the guard page needs it. The loader itself includes
# nothing that the macro changes.
CFLAGS := -g -O1 -std=c11 -D_DEFAULT_SOURCE -Wall -Wextra \
          -fsanitize=fuzzer,address,undefined \
          -fno-sanitize-recover=undefined -fno-omit-frame-pointer \
          -I$(ROOT)/sw/kernel/include -I$(ROOT)/sw/kernel/build

LOADER_SRC := $(ROOT)/components/loader/elf32/loader.c

.PHONY: all run explore corpus coverage clean
all: build/fuzz_loader

build/fuzz_loader: fuzz_loader.c $(LOADER_SRC)
	@mkdir -p $(@D)
	$(CLANG) $(CFLAGS) -o $@ $^

# Seed the corpus with images the toolchain actually produced. Starting from
# valid ELF is what makes the run productive: mutating a real header reaches
# the segment-walking code in seconds, where random bytes spend the whole
# budget being rejected by the magic check.
corpus:
	@mkdir -p build/corpus
	@cp corpus/*.elf build/corpus/ 2>/dev/null || true
	@found=0; \
	for elf in $$(find $(ROOT)/sw -name '*.elf' 2>/dev/null | head -20); do \
	  cp "$$elf" build/corpus/$$(basename $$elf); found=1; \
	done; \
	if [ $$found -eq 0 ]; then \
	  printf '\177ELF\001\001\001' > build/corpus/minimal; \
	  echo "no built ELF found; seeded with a bare header only"; \
	else \
	  echo "corpus: $$(ls build/corpus | wc -l) seed(s)"; \
	fi

# A bounded run, for CI: enough to catch a regression in a parser that was
# already fuzzed, not enough to call the space explored.
run: build/fuzz_loader corpus
	ASAN_OPTIONS=detect_leaks=1:symbolize=1 \
	./build/fuzz_loader build/corpus -runs=$(RUNS) -max_total_time=$(TIMEOUT) \
	  -max_len=65536 -print_final_stats=1 -detect_leaks=1

# Unbounded, for when the parser has actually changed.
explore: build/fuzz_loader corpus
	./build/fuzz_loader build/corpus -max_len=65536

# White box, on the same component the fuzzing drives: replay the corpus with
# source-based instrumentation and report which of the loader's own lines and
# branches it reached. "We fuzzed the parser" is a claim about effort; a branch
# coverage number is a claim about reach, and only the second one says whether
# the corpus is exercising the error paths or bouncing off the magic check.
build/cov_loader: fuzz_loader.c $(LOADER_SRC)
	@mkdir -p $(@D)
	$(CLANG) -g -O0 -std=c11 -D_DEFAULT_SOURCE -Wall -Wextra -fsanitize=fuzzer \
	  -fprofile-instr-generate -fcoverage-mapping \
	  -I$(ROOT)/sw/kernel/include -I$(ROOT)/sw/kernel/build -o $@ $^

coverage: build/cov_loader corpus
	@rm -f build/loader.profraw build/loader.profdata
	LLVM_PROFILE_FILE=build/loader.profraw ./build/cov_loader build/corpus -runs=0
	$(LLVM_PROFDATA) merge -sparse build/loader.profraw -o build/loader.profdata
	@echo
	$(LLVM_COV) report ./build/cov_loader \
	  -instr-profile=build/loader.profdata $(LOADER_SRC)
	@echo "Uncovered regions, if any:"
	@$(LLVM_COV) show ./build/cov_loader -instr-profile=build/loader.profdata \
	  $(LOADER_SRC) --region-coverage-lt=1 --show-line-counts 2>/dev/null \
	  | grep -E "^\s+[0-9]+\|\s+0\|" | head -20 || true

clean:
	rm -rf build
