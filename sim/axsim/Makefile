include ../../mk/toolchain.mk
# HOST_CXX honours TOOLCHAIN (llvm picks clang++) and falls back to whichever
# of the two is installed. An explicit CXX= on the command line still wins.
CXX      := $(HOST_CXX)
CXXFLAGS ?= -std=c++17 -O2 -g -Wall -Wextra

SRC := src/main.cpp src/cpu.cpp src/elf.cpp
HDR := src/bus.h src/cpu.h src/elf.h

axsim: $(SRC) $(HDR)
	$(CXX) $(CXXFLAGS) -o $@ $(SRC)

# Hand-assembled directed tests (stopgap until the RISC-V GCC toolchain is
# installed; then directed tests become real .S sources). Tests exit through
# the sifive_test finisher, so the exit code is the verdict.
test: smoke trap

smoke: axsim
	@mkdir -p build
	python3 ../../tests/directed/words2bin.py ../../tests/directed/smoke_ok.words build/smoke_ok.bin
	./axsim --bin build/smoke_ok.bin | tee build/smoke.out
	@grep -qx "OK" build/smoke.out && echo "SMOKE PASS" || (echo "SMOKE FAIL"; exit 1)

trap: axsim
	@mkdir -p build
	python3 ../../tests/directed/words2bin.py ../../tests/directed/trap_ecall.words build/trap_ecall.bin
	./axsim --bin build/trap_ecall.bin && echo "TRAP TEST PASS"

clean:
	rm -rf axsim build

.PHONY: test smoke trap clean
