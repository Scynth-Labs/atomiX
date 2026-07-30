CHECKS := insn_add_ch0 insn_beq_ch0 insn_lw_ch0 insn_sw_ch0
# ax2 retires a bundle of two, so the same instruction is proved on both
# channels: channel 1 is where dual issue can go wrong.
AX2_CHECKS := insn_add_ch0 insn_add_ch1 insn_beq_ch0 insn_lw_ch0 \
              insn_lw_ch1 insn_sw_ch0 insn_sw_ch1

.PHONY: check check-ax2 check-minimal check-all generate generate-ax2 \
        generate-minimal list list-ax2 list-minimal full full-ax2 \
        full-minimal clean

check: generate

	python3 run_checks.py $(CHECKS)

check-ax2: generate-ax2

	python3 run_checks.py --core ax2 $(AX2_CHECKS)

check-minimal: generate-minimal

	python3 run_checks.py --core minimal $(CHECKS)

check-all: check check-minimal check-ax2

generate:

	python3 run.py

generate-ax2:

	python3 run.py --core ax2

generate-minimal:

	python3 run.py --core minimal

list: generate

	find build/riscv-formal/cores/axcore/checks -maxdepth 1 -name '*.sby' -printf '%f\n' | sort

list-ax2: generate-ax2

	find build/riscv-formal/cores/ax2/checks -maxdepth 1 -name '*.sby' -printf '%f\n' | sort

list-minimal: generate-minimal

	find build/riscv-formal/cores/minimal/checks -maxdepth 1 -name '*.sby' -printf '%f\n' | sort

full: generate

	python3 run_checks.py --all

full-ax2: generate-ax2

	python3 run_checks.py --core ax2 --all

full-minimal: generate-minimal

	python3 run_checks.py --core minimal --all

clean:

	rm -rf build
