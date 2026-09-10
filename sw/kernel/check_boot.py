#!/usr/bin/env python3
"""Run aXos shell/fork sessions on all Phase 5 platforms or Phase 6 RTL."""
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SHELL_INPUT = ROOT / "sw/kernel/shell_input.txt"
FORK_INPUT = ROOT / "sw/kernel/fork_input.txt"
EXEC_INPUT = ROOT / "sw/kernel/exec_input.txt"
STORAGE_WRITE_INPUT = ROOT / "sw/kernel/storage_write_input.txt"
LOADER_WX_INPUT = ROOT / "sw/kernel/loader_wx_input.txt"
TORTURE_INPUT = ROOT / "sw/kernel/torture_input.txt"
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
# The adversarial ABI program prints exactly this and exits 0.  Any other exit
# code names the check that failed -- see userprog/torture.c, where the code
# ranges are documented.
TORTURE_OUTPUT = ("aXos: shell online\n"
                  "aXos> exec torture.elf\n"
                  "exec: torture: ok\n"
                  "aXos> exit\n")
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


SDRAM_CONFIG = ROOT / "configs/sim-sdram.json"
# What `--sd-boot` must resolve to before any of its output means what it says.
SDRAM_IDENTITY = {
    "COMPONENT_MEMORY_ID": "memory.sdram",
    "COMPONENT_HARNESS_ID": "harness.verilator-sdram",
    "COMPONENT_SIM_TOP": "soc_sdram_test_top",
    "COMPONENT_SIM_RUNNER": "run-sdram",
    "COMPONENT_USE_SDRAM": "1",
}
SDRAM_PINS = re.compile(
    r"\[soc\] sdram-pins: (?:(absent)|present activate=(\d+) read=(\d+) "
    r"write=(\d+) precharge=(\d+) refresh=(\d+))")


def resolve_profile(config: Path) -> dict[str, str]:
    """The component identities a profile actually selects.

    Reading them from the resolver rather than from the profile JSON is the
    point: a profile names components, and what the build compiles is what the
    resolver makes of those names plus every manifest it pulls in.
    """
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/configure.py"), "resolve",
         "--config", str(config)],
        cwd=ROOT, text=True, capture_output=True)
    if result.returncode:
        sys.stderr.write(result.stderr)
        raise SystemExit(f"[kernel] cannot resolve {config.name}")
    resolved = {}
    for line in result.stdout.splitlines():
        if ":=" in line:
            key, _, value = line.partition(":=")
            resolved[key.strip()] = value.strip()
    return resolved


def require_sdram_profile() -> dict[str, str]:
    """Refuse to run the SD boot check on anything but the pin-level machine.

    `check-sdboot` called `run-sdram` without selecting a profile, so it built
    the default on-chip-RAM machine and still printed a physical-SDRAM result.
    A name is not a selection, so this asserts the identities the run needs and
    prints them, and the run itself then has to show the pins moved.
    """
    resolved = resolve_profile(SDRAM_CONFIG)
    wrong = {k: (resolved.get(k, "<unset>"), v)
             for k, v in SDRAM_IDENTITY.items() if resolved.get(k) != v}
    if wrong:
        detail = "\n".join(f"  {k}: {got} (expected {want})"
                           for k, (got, want) in sorted(wrong.items()))
        raise SystemExit(
            f"[kernel] {SDRAM_CONFIG.name} no longer resolves to the "
            f"physical-SDRAM machine:\n{detail}")
    print(f"[kernel] SD boot machine: profile={resolved['COMPONENT_CONFIG_NAME']} "
          f"core={resolved['COMPONENT_CORE_ID']} "
          f"memory={resolved['COMPONENT_MEMORY_ID']} "
          f"cache={resolved['COMPONENT_CACHE_ID']} "
          f"harness={resolved['COMPONENT_HARNESS_ID']} "
          f"top={resolved['COMPONENT_SIM_TOP']}")
    return resolved


def require_sdram_pins(label: str, stderr: str) -> str:
    """A run only counts as physical-SDRAM evidence if the pins carried it.

    The controller cannot move a word without opening a row first, so a run
    that never issued an ACTIVATE never used these pins whatever the profile
    said -- and a build with no pin model at all reports itself absent.
    """
    match = SDRAM_PINS.search(stderr)
    if match is None:
        raise SystemExit(
            f"[kernel] {label}: the run reported no SDRAM pin activity line; "
            "the model predates the pin counters or is not the shared runner")
    if match.group(1) == "absent":
        raise SystemExit(
            f"[kernel] {label}: this build has no pin-level SDRAM model -- "
            "on-chip RAM was substituted for the physical path")
    activate = int(match.group(2))
    if activate == 0:
        raise SystemExit(
            f"[kernel] {label}: the SDRAM pins were never driven "
            "(activate=0); the run did not reach external memory")
    counts = (f"activate={activate} read={match.group(3)} "
              f"write={match.group(4)} precharge={match.group(5)} "
              f"refresh={match.group(6)}")
    print(f"[kernel] {label}: SDRAM pins exercised ({counts})")
    return counts


def reject_bram_substitution() -> None:
    """The negative control for the whole SD boot claim.

    Two independent things have to refuse an on-chip-RAM machine here, and
    both are exercised against a real BRAM profile rather than asserted: the
    `run-sdram` target must not build one, and a transcript produced by one
    must not be accepted as physical-SDRAM evidence.  Without this the check
    is back where it started -- passing, and describing a machine it never
    ran.
    """
    bram_config = ROOT / "configs/sim-bram.json"
    guard = subprocess.run(
        ["make", "-s", "--no-print-directory", "-C", str(ROOT / "sim/soc"),
         "run-sdram", f"COMPONENT_CONFIG={bram_config}",
         f"ROM_INIT_FILE={ROOT / 'sw/bootrom/build/bootrom.hex'}",
         "MAX_CYCLES=1000", "BUILD_ID=sdboot-substitution"],
        cwd=ROOT, text=True, capture_output=True, timeout=120)
    if guard.returncode == 0:
        raise SystemExit(
            "[kernel] run-sdram built a machine from "
            f"{bram_config.name} ({resolve_profile(bram_config)['COMPONENT_MEMORY_ID']}) "
            "instead of refusing it: the target's name would again be the only "
            "thing making this a physical-SDRAM run")
    if "no physical SDRAM pins" not in guard.stderr:
        sys.stderr.write(guard.stderr)
        raise SystemExit(
            "[kernel] run-sdram refused the BRAM profile for some other "
            "reason; the selection guard did not fire")
    print("[kernel] BRAM substitution: run-sdram refuses a profile with no "
          "SDRAM pins")

    # And the second half: what a BRAM machine actually prints must not read as
    # physical-SDRAM evidence.  This is a real run of that machine, not a
    # hand-written string, so it stays true if the reporting changes.
    substitute = subprocess.run(
        ["make", "-s", "--no-print-directory", "-C", str(ROOT / "sim/soc"),
         "run", f"COMPONENT_CONFIG={bram_config}",
         f"RAM_INIT_FILE={ROOT / 'sw/kernel/build/axos_boot.hex'}",
         "RESET_PC=0x80000000", "MAX_CYCLES=1000"],
        cwd=ROOT, text=True, capture_output=True, timeout=600)
    try:
        require_sdram_pins("BRAM substitution", substitute.stderr)
    except SystemExit as refusal:
        print(f"[kernel] BRAM substitution: rejected as evidence "
              f"({str(refusal).split(': ', 1)[-1]})")
        return
    raise SystemExit(
        "[kernel] a run on the on-chip-RAM machine was accepted as "
        "physical-SDRAM evidence; the pin check proves nothing")


def run(label: str, command: list[str], input_file: Path, expected: str | None,
        timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        # The command may need to build a fresh Verilated model when a selected
        # component lives in a new directory.  This is host compilation time,
        # not simulated execution time; the runner still enforces its own
        # MAX_CYCLES limit. Leave enough room for a cold build on a developer
        # workstation before classifying a platform as hung.
        result = subprocess.run(command, cwd=ROOT, text=True,
                                input=input_file.read_text(), capture_output=True,
                                timeout=timeout)
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
    return result


def check_abi_contract() -> None:
    """The kernel, axlibc and abi.md must agree before anything is run."""
    script = ROOT / "sw/kernel/check_abi_contract.py"
    if script.exists():
        subprocess.run([sys.executable, str(script)], check=True)


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
    check_abi_contract()
    sd_image = os.environ.get("SD_IMAGE", "")
    # Every mode but SD boot runs all three stages on a machine with no SDRAM
    # pins to report, at the default subprocess timeout.
    stages = ("shell", "fork", "exec")
    sdram_pins = False
    rtl_timeout = 60
    sd_exec_cycles = None
    if sys.argv[1:] == ["--torture"]:
        if not sd_image:
            raise SystemExit("--torture requires SD_IMAGE")
        command = [
            "make", "-s", "--no-print-directory", "-C", str(ROOT / "sim/soc"),
            "run", f"RAM_INIT_FILE={image}", "RESET_PC=0x80000000",
            "RAM_BYTES=33554432", "EXTERNAL_MEMORY=1", "CACHES=1",
            "MAX_CYCLES=60000000", f"SD_IMAGE={sd_image}",
            "BUILD_ID=torture",
        ]
        run("RTL ABI torture", command + [f"UART_INPUT_FILE={TORTURE_INPUT}"],
            TORTURE_INPUT, TORTURE_OUTPUT)
        print("[kernel] adversarial ABI conformance: PASS on cached external-memory RTL")
        return
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
    elif sys.argv[1:] in (["--sd-boot"], ["--sd-boot-exec"]):
        boot_rom = os.environ.get("BOOT_ROM", "")
        if not sd_image or not boot_rom:
            raise SystemExit(f"{sys.argv[1]} requires SD_IMAGE and BOOT_ROM")
        require_sdram_profile()
        reject_bram_substitution()
        # Select the profile rather than the runner: `run-config` dispatches on
        # the memory component's own declared runner, so the machine under test
        # is the one the profile resolves to and cannot silently become another.
        platforms = [("RTL SD boot", [
            "make", "-s", "--no-print-directory", "-C", str(ROOT / "sim/soc"),
            "run-config", f"COMPONENT_CONFIG={SDRAM_CONFIG}",
            f"ROM_INIT_FILE={boot_rom}",
            # Every budget here is a hang bound derived from a measurement on
            # this machine, not a BRAM figure scaled by guesswork. Shell
            # completes at 7.54M cycles and fork at 8.47M, so 12M leaves room
            # for cache behaviour to change without hiding a hang.
            "MAX_CYCLES=12000000", "BUILD_ID=sdboot-physical",
            f"SD_IMAGE={sd_image}",
        ])]
        # Wall-clock, not simulated time: a cold Verilator build of the
        # pin-level model plus a 7.5M-cycle boot measured ~21 s here, and the
        # long-quantum exec run ~45 s. The BRAM default of 60 s, which this
        # path outgrew, is what let a hang look like a slow workstation.
        rtl_timeout = 1800
        sdram_pins = True
        if sys.argv[1] == "--sd-boot-exec":
            stages = ("exec",)
            # The default kernel profile's 2,000-cycle quantum is far shorter
            # than this machine's cost to service a tick, which is the exact
            # condition under which user progress used to be lost entirely.
            # Measured 39,112,456 cycles; 60M is the hang bound. If the
            # quantum is ever armed on the way into the handler again, this
            # does not finish -- that is the point of keeping it.
            sd_exec_cycles = "60000000"
        else:
            # Measured 9,864,981 cycles with the slow-memory kernel profile
            # this target builds.
            sd_exec_cycles = "15000000"
    elif sys.argv[1:]:
        raise SystemExit(
            "usage: check_boot.py "
            "[--external-memory|--storage-write|--sd-boot|--sd-boot-exec]")
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
        timeout = rtl_timeout if label.startswith("RTL") else 60
        if "shell" in stages:
            shell_command = command + ([f"UART_INPUT_FILE={SHELL_INPUT}"] if label.startswith("RTL")
                                       else ["--uart-input-file", str(SHELL_INPUT)] if label == "ISS"
                                       else [])
            expected_shell = SHELL_OUTPUT_STORAGE if sd_image else SHELL_OUTPUT
            if label == "RTL SD boot":
                expected_shell = BOOT_PREFIX + SHELL_OUTPUT_SDBOOT
            result = run(f"{label} shell", shell_command, SHELL_INPUT,
                         expected_shell, timeout)
            if sdram_pins:
                require_sdram_pins(f"{label} shell", result.stderr)
        if "fork" in stages:
            fork_command = command + ([f"UART_INPUT_FILE={FORK_INPUT}"] if label.startswith("RTL")
                                      else ["--uart-input-file", str(FORK_INPUT)] if label == "ISS"
                                      else [])
            result = run(f"{label} fork", fork_command, FORK_INPUT, None, timeout)
            if sdram_pins:
                require_sdram_pins(f"{label} fork", result.stderr)
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
        if "exec" not in stages:
            continue
        slow_exec = "external memory" in label or label == "RTL SD boot"
        exec_cycles = ("15000000" if slow_exec else "1500000")
        if label == "RTL SD boot" and sd_exec_cycles:
            exec_cycles = sd_exec_cycles
        exec_command = command + (
            [f"UART_INPUT_FILE={EXEC_INPUT}", f"MAX_CYCLES={exec_cycles}"]
            if label.startswith("RTL")
            else ["--uart-input-file", str(EXEC_INPUT)] if label == "ISS"
            else [])
        expected_exec = EXEC_OUTPUT
        if label == "RTL SD boot": expected_exec = BOOT_PREFIX + EXEC_OUTPUT
        result = run(f"{label} exec", exec_command, EXEC_INPUT, expected_exec,
                     timeout)
        if sdram_pins:
            require_sdram_pins(f"{label} exec", result.stderr)
    if sys.argv[1:] == ["--external-memory"]:
        print("[kernel] shell + fork/wait: PASS on 32 MiB cached external-memory RTL")
    elif sys.argv[1:] == ["--sd-boot"]:
        print("[kernel] SD boot + AXFS shell + fork/wait + ELF exec: PASS on "
              "the physical-SDRAM pin model")
    elif sys.argv[1:] == ["--sd-boot-exec"]:
        print("[kernel] SD boot + ELF exec at the default 2,000-cycle quantum: "
              "PASS on the physical-SDRAM pin model")
    else:
        print("[kernel] shell + fork/wait + ELF exec: PASS on ISS, QEMU, and RTL")


if __name__ == "__main__":
    main()
