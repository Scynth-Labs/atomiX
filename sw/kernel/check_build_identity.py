#!/usr/bin/env python3
"""Does a reused build tree still hold the configuration it says it does?

The kernel's outputs live at one set of paths under `build/` whatever profile
and personality produced them, so switching configurations has to invalidate
them.  It did not: `make images KERNEL_CONFIG=A` followed by `=B` produced
byte-identical images, because the ELF's only configuration prerequisite was a
per-profile `.mk` that was already older than the ELF.  A profile switch was a
no-op that reported success, which is the same defect `check-sdboot` had at the
hardware end -- a selection that never reached the build.

So this is an A -> B -> A check.  For each configuration it builds twice: once
into a scratch tree that has never held anything else, and once into the shared
tree after that tree has held something different.  The two must agree.  Two
different configurations must not.  And the reused build has to boot as the
personality it claims, because an image that is merely *different* is not
necessarily the right one.

Identity is the loaded image, not the ELF: `axos_boot.elf` is not byte
reproducible across rebuilds of identical sources, while `axos_boot.bin` --
what the loader copies to RAM and the hart executes -- is.
"""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KERNEL = ROOT / "sw/kernel"
SHELL_INPUT = KERNEL / "shell_input.txt"
SCRATCH = "build/identity"

# The transcript a shell personality with the built-in root produces on the
# ISS.  Kept here rather than imported so a change to one check cannot quietly
# redefine the other's expectation.
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

# Every entry changes the emitted code without changing a source file, which is
# the whole class of switch that used to be lost.  `boot` is what the built
# kernel must print on the ISS with the same console script: ("exact", text)
# where the whole transcript is known, ("prefix", text) where the personality
# announces itself and then waits for something the ISS does not provide.
# Each one is a different answer, which is the point -- an image that is merely
# *different* from the last one is not necessarily the right one.
CONFIGURATIONS = [
    {"name": "default", "make": ["KERNEL_CONFIG=configs/kernel-default.json"],
     "why": "the baseline profile",
     "boot": ("exact", SHELL_OUTPUT, 0)},
    {"name": "slow-memory",
     "make": ["KERNEL_CONFIG=configs/kernel-slow-memory.json"],
     "why": "a setting that reaches both C and assembly (timer_quantum_cycles)",
     "boot": ("exact", SHELL_OUTPUT, 0)},
    {"name": "small-caps",
     "make": ["KERNEL_CONFIG=configs/kernel-small-caps.json"],
     "why": "component parameters: task slots, descriptors, path limit",
     "boot": ("exact", SHELL_OUTPUT, 0)},
    {"name": "storage",
     "make": ["KERNEL_CONFIG=configs/kernel-default.json", "STORAGE=1",
              "FS_BLOCK=128"],
     "why": "the storage personality: files come from the SD card, not the "
            "built-in root",
     # This personality's signature on a machine with no card: it prints
     # nothing and halts through the finisher with a failure, rather than
     # falling back to a built-in root. A build that printed the shell banner
     # here would be the non-storage kernel wearing the storage kernel's name.
     "boot": ("exact", "", 1)},
    {"name": "hostlink",
     "make": ["KERNEL_CONFIG=configs/kernel-default.json", "HOSTLINK=1"],
     "why": "the host-link personality: the link service owns the console",
     # Announces itself on the link and waits for a host that the ISS is not:
     # exit 3 is the instruction bound, which is this personality behaving
     # correctly rather than hanging.
     "boot": ("prefix", "AXRD", 3)},
    {"name": "monitor",
     "make": ["KERNEL_CONFIG=configs/kernel-primer-monitor.json"],
     "why": "a wholly different source list, console, and RAM envelope",
     "boot": ("prefix", "aXos: Primer monitor (32 KiB)\n"
                        "aXos: monitor shell online\n", 0)},
]

# A -> B -> A -> C -> A ...: every configuration is followed by a return to the
# baseline, so a stale tree has to survive a round trip to go unnoticed.
def visit_order() -> list[str]:
    order = ["default"]
    for entry in CONFIGURATIONS[1:]:
        order += [entry["name"], "default"]
    return order


def build(entry: dict, build_dir: str) -> Path:
    command = ["make", "-s", "--no-print-directory", "-C", str(KERNEL),
               "images", f"BUILD_DIR={build_dir}"]
    for setting in entry["make"]:
        key, _, value = setting.partition("=")
        # Profile paths are given relative to the repository root above and
        # made absolute here: the kernel Makefile resolves them from its own
        # directory, and a scratch tree must not change what they mean.
        if key == "KERNEL_CONFIG":
            value = str(ROOT / value)
        command.append(f"{key}={value}")
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True,
                            timeout=900)
    if result.returncode:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(f"[identity] {entry['name']}: build failed")
    image = KERNEL / build_dir / "axos_boot.bin"
    if not image.exists():
        raise SystemExit(f"[identity] {entry['name']}: {image} was not built")
    return image


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def boot(entry: dict, build_dir: str) -> tuple[int, str]:
    """Run the built kernel on the ISS and return what it printed.

    Bounded by instruction count, not wall clock: a personality that waits for
    a host or a card never reaches the finisher, and waiting for it to is
    indistinguishable from a hang.
    """
    elf = KERNEL / build_dir / "axos_boot.elf"
    result = subprocess.run(
        [str(ROOT / "sim/axsim/axsim"), "--bin", str(elf),
         "--uart-input-file", str(SHELL_INPUT), "--max", "5000000"],
        cwd=ROOT, text=True, capture_output=True, timeout=600)
    return result.returncode, result.stdout


# The same question one layer down, and the case the kernel side cannot reach:
# a profile *edited in place*.  Switching profile files gives the simulator a
# different build tag and therefore a different object directory, so the hard
# case is one profile whose content changed under the same path and name --
# exactly what happens while a parameter is being tuned.  The build tag hashes
# the resolved defines for this reason; without that, the model would be reused
# with the previous parameter compiled into it.
SIM_PROBE = "sim/soc/build/identity-probe.json"
SIM_PROGRESS = "[soc] progress:"
# Printed by every build of the shared runner, so its absence means no run.
SIM_RAN = "[soc] sdram-pins:"


def sim_probe_config(monitor: bool) -> str:
    base = json.loads((ROOT / "configs/sim-bram.json").read_text())
    base["name"] = "identity-probe"
    if monitor:
        base["parameters"] = {"soc": {"progress_monitor": 1}}
    return json.dumps(base, indent=2) + "\n"


def sim_run(label: str) -> str:
    """Build and run the probe profile, and return the runner's own report."""
    result = subprocess.run(
        ["make", "-s", "--no-print-directory", "-C", str(ROOT / "sim/soc"),
         "run", f"COMPONENT_CONFIG={ROOT / SIM_PROBE}",
         f"RAM_INIT_FILE={KERNEL / 'build/axos_boot.hex'}",
         "RESET_PC=0x80000000", "MAX_CYCLES=2000",
         "BUILD_ID=identity-probe"],
        cwd=ROOT, text=True, capture_output=True, timeout=1800)
    # The run is deliberately cut short by its cycle bound, so make exits
    # non-zero every time. What separates that from a build that never
    # produced a model is whether the runner reported at all.
    if SIM_RAN not in result.stderr:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(f"[identity] simulator {label}: the model did not run")
    return result.stderr


def check_simulator() -> None:
    probe = ROOT / SIM_PROBE
    probe.parent.mkdir(parents=True, exist_ok=True)
    try:
        for label, monitor in (("without the monitor", False),
                               ("with the monitor", True),
                               ("without the monitor again", False)):
            probe.write_text(sim_probe_config(monitor))
            report = sim_run(label)
            present = SIM_PROGRESS in report
            if present != monitor:
                raise SystemExit(
                    f"[identity] the simulator built {label} reported "
                    f"progress={present}: editing a profile in place did not "
                    "reach the model, so the previous parameter is still "
                    "compiled in")
            print(f"[identity] simulator {label:26} -> "
                  f"progress counters {'present' if present else 'absent'}")
    finally:
        probe.unlink(missing_ok=True)


# Tool selection is in the identity too, and is the one input this check cannot
# exercise by building: the LLVM toolchain may not be installed.  It does not
# need to be.  The stamp rule runs the identity writer and nothing else, so
# asking make for the stamp under two toolchains says whether the choice is
# part of the build's identity -- which is the whole question.  If it were not,
# switching toolchains would leave the other one's objects in place.
TOOLCHAIN_CASES = [
    ("gcc (the default)", []),
    ("a different C compiler", ["RISCV_CC=clang --target=riscv32-unknown-elf"]),
    ("a different architecture", ["RISCV_ARCH=rv32i"]),
    ("a different linked RAM envelope", ["RAM_BYTES=65536"]),
]


def check_identity_covers_tools() -> None:
    stamps = {}
    for label, overrides in TOOLCHAIN_CASES:
        build_dir = f"{SCRATCH}/toolsel"
        stamp = KERNEL / build_dir / "kernel-identity.stamp"
        stamp.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["make", "-s", "--no-print-directory", "-C", str(KERNEL),
             str(Path(build_dir) / "kernel-identity.stamp"),
             f"BUILD_DIR={build_dir}"] + overrides,
            cwd=ROOT, text=True, capture_output=True, timeout=300)
        if result.returncode:
            sys.stderr.write(result.stdout + result.stderr)
            raise SystemExit(f"[identity] stamp for {label} failed")
        stamps[label] = stamp.read_text()
    baseline = stamps["gcc (the default)"]
    for label, text in stamps.items():
        if label == "gcc (the default)":
            continue
        if text == baseline:
            raise SystemExit(
                f"[identity] {label} produces the same build identity as the "
                "default, so switching to it would reuse the other one's "
                "objects")
        changed = sorted(set(text.splitlines()) - set(baseline.splitlines()))
        print(f"[identity] {label:32} changes the build identity "
              f"({', '.join(changed[:2]) or 'no new token, but the set differs'})")


def main() -> None:
    by_name = {entry["name"]: entry for entry in CONFIGURATIONS}

    # Every configuration built into a tree that has never held another one.
    # This is the reference: without it, two reused builds that are stale in
    # the same way would agree with each other and prove nothing.
    clean: dict[str, str] = {}
    for entry in CONFIGURATIONS:
        scratch = KERNEL / SCRATCH / entry["name"]
        if scratch.exists():
            shutil.rmtree(scratch)
        clean[entry["name"]] = digest(
            build(entry, f"{SCRATCH}/{entry['name']}"))
        print(f"[identity] clean {entry['name']:12} "
              f"{clean[entry['name']][:16]}  ({entry['why']})")

    # A configuration that produces the same image as another is not a
    # configuration, and would make every comparison below vacuous.
    seen: dict[str, str] = {}
    for name, value in clean.items():
        if value in seen:
            raise SystemExit(
                f"[identity] {name} and {seen[value]} build the same image; "
                "one of them changes nothing the compiler emits, so this "
                "check cannot tell a stale tree from a correct one")
        seen[value] = name
    print(f"[identity] {len(clean)} configurations, {len(seen)} distinct images")

    # The shared tree, switched back and forth.
    for name in visit_order():
        entry = by_name[name]
        got = digest(build(entry, "build"))
        if got != clean[name]:
            raise SystemExit(
                f"[identity] reusing build/ for {name} produced "
                f"{got[:16]}, but a clean build of the same configuration "
                f"produces {clean[name][:16]}: the switch did not reach the "
                "build and the previous configuration's kernel is still there")
        print(f"[identity] reused build/ as {name:12} {got[:16]} matches clean")

    # Different is not the same as correct: prove the personality boots as
    # itself.  Six configurations, six distinct answers.
    for entry in CONFIGURATIONS:
        mode, want, want_exit = entry["boot"]
        code, output = boot(entry, f"{SCRATCH}/{entry['name']}")
        matched = output == want if mode == "exact" else output.startswith(want)
        if not matched or code != want_exit:
            raise SystemExit(
                f"[identity] {entry['name']}: exit {code} printing\n"
                f"  {output[:400]!r}\nexpected exit {want_exit} with {mode}\n"
                f"  {want!r}")
        shown = ("printed nothing and halted" if want == ""
                 else repr(want.splitlines()[0][:52]))
        print(f"[identity] {entry['name']:12} boot: PASS on the ISS -- "
              f"exit {code}, {shown}")

    check_identity_covers_tools()
    check_simulator()
    print("[identity] PASS: every configuration reaches the build, a reused "
          "tree matches a clean one, and each personality boots as itself")


if __name__ == "__main__":
    main()
