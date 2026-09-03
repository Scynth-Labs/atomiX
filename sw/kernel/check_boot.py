#!/usr/bin/env python3
"""Run aXos shell/fork sessions on all Phase 5 platforms or Phase 6 RTL."""
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SHELL_INPUT = ROOT / "sw/kernel/shell_input.txt"
FORK_INPUT = ROOT / "sw/kernel/fork_input.txt"
EXEC_INPUT = ROOT / "sw/kernel/exec_input.txt"
STORAGE_WRITE_INPUT = ROOT / "sw/kernel/storage_write_input.txt"
LOADER_WX_INPUT = ROOT / "sw/kernel/loader_wx_input.txt"
SHELL_OUTPUT = (
    "aXos: shell online\n"
    "aXos> help\n"
    "commands: help clear uname uptime console free ps pwd ls cat stat hexdump "
    "touch cp mv rm write echo fork exec run role shutdown exit\n"
    "aXos> ls\n"
    "motd\n"
    "readme\n"
    "aXos> cat motd\n"
    "Welcome to aXos.\n"
    "aXos> echo atomiX\n"
    "atomiX\n"
    "aXos> exit\n"
)
SHELL_OUTPUT_STORAGE = SHELL_OUTPUT.replace(
    "motd\nreadme\n", "motd\nreadme\nhello.elf\n")
SHELL_OUTPUT_SDBOOT = SHELL_OUTPUT_STORAGE
# A W+X ELF must be refused by the loader, not mapped.  The fixture exits 0 if
# it ever runs (see make_fs_image.py), so "load failed" is the only output that
# distinguishes an enforced W^X from an unenforced one.
LOADER_WX_OUTPUT = ("aXos: shell online\n"
                    "aXos> exec wx.elf\n"
                    "exec: load failed\n"
                    "aXos> exit\n")
BOOT_PREFIX = "aXboot\n"
FORK_PREFIX = "aXos: shell online\naXos> fork\nfork demo: "
# The loaded program prints exactly this and then exits 0.  Anything else -- a
# different string, a non-zero exit -- means the loader mapped something wrong,
# and the program's exit code says which check failed (see userprog/hello.c).
EXEC_OUTPUT = ("aXos: shell online\naXos> exec hello.elf one two\n"
               "exec: axlibc: pid=1 n=42 hex=beef str=reused motd=17\n"
               "aXos> exit\n")
STORAGE_WRITE_OUTPUT = (
    "aXos: shell online\n"
    "aXos> write note phase6-persistent\n"
    "aXos> cat note\n"
    "phase6-persistentaXos> cp note note-copy\n"
    "aXos> cat note-copy\n"
    "phase6-persistentaXos> mv note-copy moved\n"
    "aXos> stat moved\n"
    "moved: 17 bytes, read-write\n"
    "aXos> touch empty\n"
    "aXos> stat empty\n"
    "empty: 0 bytes, read-write\n"
    "aXos> rm moved\n"
    "aXos> rm empty\n"
    "aXos> ls\n"
    "motd\n"
    "readme\n"
    "hello.elf\n"
    "note\n"
    "aXos> exit\n"
)


def run(label: str, command: list[str], input_file: Path, expected: str | None) -> None:
    try:
        # The command may need to build a fresh Verilated model when a selected
        # component lives in a new directory.  This is host compilation time,
        # not simulated execution time; the runner still enforces its own
        # MAX_CYCLES limit. Leave enough room for a cold build on a developer
        # workstation before classifying a platform as hung.
        result = subprocess.run(command, cwd=ROOT, text=True,
                                input=input_file.read_text(), capture_output=True,
                                timeout=60)
    except subprocess.TimeoutExpired:
        if label == "QEMU":
            raise SystemExit(
                "[kernel] QEMU: TIMEOUT (QEMU 6.2 has an upstream RISC-V "
                "PMP bug on mret to S/U; install QEMU >= 7 and use "
                "-cpu rv32,pmp=false; see docs/toolchain.md)")
        raise SystemExit(f"[kernel] {label}: TIMEOUT")
    if result.returncode:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(f"[kernel] {label}: exit {result.returncode}")
    if expected is not None and result.stdout != expected:
        sys.stderr.write(result.stderr)
        raise SystemExit(
            f"[kernel] {label}: UART mismatch\n"
            f"  expected: {expected!r}\n"
            f"  got:      {result.stdout!r}")
    fork_prefix = BOOT_PREFIX + FORK_PREFIX if label.startswith("RTL SD boot") else FORK_PREFIX
    if expected is None and (
            not result.stdout.startswith(fork_prefix) or
            result.stdout[len(fork_prefix):] not in {
                "PCWaXos> exit\n", "CPWaXos> exit\n"}):
        sys.stderr.write(result.stderr)
        raise SystemExit(
            f"[kernel] {label}: fork UART mismatch\n"
            f"  expected: {fork_prefix!r} followed by PCW or CPW, then a "
            "returned shell prompt\n"
            f"  got:      {result.stdout!r}")
    print(f"[kernel] {label}: PASS")


def check_user_elf_segments() -> None:
    """The loader's protection is only as good as the segments it is handed.

    Page-aligning sections in the linker script does not separate them: ld
    assigns sections to segments by flag compatibility, so .rodata (A) was
    silently sharing .text's R+E segment and being mapped executable.  Nothing
    caught it, because every behavioural test still passed -- an executable
    .rodata reads exactly like a read-only one.  This is the check that fails
    instead, and it is structural because the defect is structural.
    """
    path = ROOT / "sw/kernel/build/userprog/hello.elf"
    if not path.exists():
        return
    data = path.read_bytes()
    phoff = int.from_bytes(data[28:32], "little")
    phentsize = int.from_bytes(data[42:44], "little")
    phnum = int.from_bytes(data[44:46], "little")
    loads = []
    for i in range(phnum):
        ph = data[phoff + i * phentsize:phoff + (i + 1) * phentsize]
        if int.from_bytes(ph[0:4], "little") != 1:  # PT_LOAD
            continue
        loads.append((int.from_bytes(ph[8:12], "little"),
                      int.from_bytes(ph[24:28], "little")))
    loads.sort()
    flags = [f for _, f in loads]
    R, W, X = 4, 2, 1
    expected = [R | X, R, R | W]
    if flags != expected:
        names = {R | X: "R+X", R: "R", R | W: "R+W", R | W | X: "R+W+X"}
        got = " ".join(names.get(f, str(f)) for f in flags)
        want = " ".join(names[f] for f in expected)
        raise SystemExit(
            f"[kernel] user ELF segments are {got}, expected {want}: "
            f"sections are sharing a segment and therefore a permission set")
    for vaddr, f in loads:
        if (f & W) and (f & X):
            raise SystemExit(
                f"[kernel] user ELF segment at 0x{vaddr:08x} is W+X")
    print("[kernel] user ELF segments: PASS (R+X, R, R+W; no W^X violation)")


def main() -> None:
    elf = ROOT / "sw/kernel/build/axos_boot.elf"
    image = ROOT / "sw/kernel/build/axos_boot.hex"
    qemu = os.environ.get("QEMU", "qemu-system-riscv32")
    check_user_elf_segments()
    sd_image = os.environ.get("SD_IMAGE", "")
    if sys.argv[1:] == ["--loader-wx"]:
        if not sd_image:
            raise SystemExit("--loader-wx requires SD_IMAGE")
        command = [
            "make", "-s", "--no-print-directory", "-C", str(ROOT / "sim/soc"),
            "run", f"RAM_INIT_FILE={image}", "RESET_PC=0x80000000",
            "RAM_BYTES=33554432", "EXTERNAL_MEMORY=1", "CACHES=1",
            "MAX_CYCLES=15000000", f"SD_IMAGE={sd_image}",
            "BUILD_ID=loader-wx",
        ]
        run("RTL W^X rejection", command + [f"UART_INPUT_FILE={LOADER_WX_INPUT}"],
            LOADER_WX_INPUT, LOADER_WX_OUTPUT)
        print("[kernel] loader W^X rejection: PASS on cached external-memory RTL")
        return
    if sys.argv[1:] == ["--storage-write"]:
        if not sd_image:
            raise SystemExit("--storage-write requires SD_IMAGE")
        command = [
            "make", "-s", "--no-print-directory", "-C", str(ROOT / "sim/soc"),
            "run", f"RAM_INIT_FILE={image}", "RESET_PC=0x80000000",
            "RAM_BYTES=33554432", "EXTERNAL_MEMORY=1", "CACHES=1",
            "MAX_CYCLES=800000", f"SD_IMAGE={sd_image}",
            "BUILD_ID=storage-write",
        ]
        run("RTL AXFS write/readback", command + [f"UART_INPUT_FILE={STORAGE_WRITE_INPUT}"],
            STORAGE_WRITE_INPUT, STORAGE_WRITE_OUTPUT)
        print("[kernel] AXFS write/readback: PASS on cached external-memory RTL")
        return
    if sys.argv[1:] == ["--external-memory"]:
        platforms = [
            ("RTL external memory + caches", [
                "make", "-s", "--no-print-directory", "-C", str(ROOT / "sim/soc"),
                "run", f"RAM_INIT_FILE={image}", "RESET_PC=0x80000000",
                "RAM_BYTES=33554432", "EXTERNAL_MEMORY=1", "CACHES=1",
                # A hang bound, not a performance budget.  Raised from 500,000
                # when WFI stopped retiring as a nop: a hart that parks until
                # an interrupt resumes on the next scheduler tick rather than
                # spinning straight through an idle loop, which costs real
                # cycles in a batch run precisely because there is no idle time
                # to reclaim.  The fork demo measured 492,933 cycles before
                # that change and 504,702 after -- the old bound had 1.4%
                # margin, which was too little to distinguish "slower" from
                # "hung".
                "MAX_CYCLES=700000",
            ]),
        ]
        if sd_image:
            platforms[0][1].append(f"SD_IMAGE={sd_image}")
    elif sys.argv[1:] == ["--sd-boot"]:
        boot_rom = os.environ.get("BOOT_ROM", "")
        if not sd_image or not boot_rom:
            raise SystemExit("--sd-boot requires SD_IMAGE and BOOT_ROM")
        platforms = [("RTL SD boot", [
            "make", "-s", "--no-print-directory", "-C", str(ROOT / "sim/soc"),
            "run-sdram", f"ROM_INIT_FILE={boot_rom}",
            "MAX_CYCLES=3000000", "BUILD_ID=sdboot-physical", f"SD_IMAGE={sd_image}",
        ])]
    elif sys.argv[1:]:
        raise SystemExit("usage: check_boot.py [--external-memory|--storage-write|--sd-boot]")
    else:
        platforms = [
        ("ISS", [str(ROOT / "sim/axsim/axsim"), "--bin", str(elf)]),
        ("QEMU", [qemu, "-machine", "virt", "-bios", "none",
                  "-cpu", "rv32,pmp=false", "-nographic", "-kernel", str(elf)]),
        ("RTL", ["make", "-s", "--no-print-directory", "-C",
                 str(ROOT / "sim/soc"), "run", f"RAM_INIT_FILE={image}",
                 "RESET_PC=0x80000000", "MAX_CYCLES=200000"]),
        ]
    for label, command in platforms:
        shell_command = command + ([f"UART_INPUT_FILE={SHELL_INPUT}"] if label.startswith("RTL")
                                   else ["--uart-input-file", str(SHELL_INPUT)] if label == "ISS"
                                   else [])
        expected_shell = SHELL_OUTPUT_STORAGE if sd_image else SHELL_OUTPUT
        if label == "RTL SD boot":
            expected_shell = BOOT_PREFIX + SHELL_OUTPUT_SDBOOT
        run(f"{label} shell", shell_command, SHELL_INPUT, expected_shell)
        fork_command = command + ([f"UART_INPUT_FILE={FORK_INPUT}"] if label.startswith("RTL")
                                  else ["--uart-input-file", str(FORK_INPUT)] if label == "ISS"
                                  else [])
        run(f"{label} fork", fork_command, FORK_INPUT, None)
        # Loading an ELF is real work -- parsing headers, copying segments into
        # a fresh address space, then running a program that allocates -- so the
        # exec run needs a budget matched to it rather than the shell's.
        #
        # External memory needs an order of magnitude more than on-chip RAM:
        # ~10M cycles for ~110k instructions, because the default cache is 16
        # lines of 4 words (256 bytes) and thrashes on a working set this size.
        # That is a real property of the default profile, not slack in the test
        # -- see docs/hardware-capabilities.md, where the same cache costs the
        # render workload a 2.9x slowdown.  Sizing the cache is a profile
        # decision; this budget only has to not hide it.
        # SD boot also has to fetch the multi-sector user ELF over the
        # bit-serial SPI model before the loader can inspect it.
        slow_exec = "external memory" in label or label == "RTL SD boot"
        exec_cycles = "15000000" if slow_exec else "1500000"
        exec_command = command + (
            [f"UART_INPUT_FILE={EXEC_INPUT}", f"MAX_CYCLES={exec_cycles}"]
            if label.startswith("RTL")
            else ["--uart-input-file", str(EXEC_INPUT)] if label == "ISS"
            else [])
        expected_exec = EXEC_OUTPUT
        if label == "RTL SD boot": expected_exec = BOOT_PREFIX + EXEC_OUTPUT
        run(f"{label} exec", exec_command, EXEC_INPUT, expected_exec)
    if sys.argv[1:] == ["--external-memory"]:
        print("[kernel] shell + fork/wait: PASS on 32 MiB cached external-memory RTL")
    elif sys.argv[1:] == ["--sd-boot"]:
        print("[kernel] SD boot + AXFS shell: PASS through the physical-SDRAM RTL path")
    else:
        print("[kernel] shell + fork/wait + ELF exec: PASS on ISS, QEMU, and RTL")


if __name__ == "__main__":
    main()
