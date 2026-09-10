#!/usr/bin/env python3
"""Prove payload reuse, baked-image parity, and interactive state on native RTL."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SOC = ROOT / "sim/soc"


def run(command, *, cwd=ROOT, input=None, success=True):
    result = subprocess.run([str(arg) for arg in command], cwd=cwd, input=input,
                            text=True, capture_output=True, timeout=300)
    if success and result.returncode:
        raise RuntimeError(f"{command}\n{result.stdout}{result.stderr}")
    return result


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def model(config, sync, image=""):
    result = run(["make", "-s", "--no-print-directory", "-C", SOC,
                  "model-path", f"COMPONENT_CONFIG={ROOT / 'configs' / config}",
                  f"SYNC_READ={sync}", f"RAM_INIT_FILE={image}",
                  "ROM_INIT_FILE=", "BUILD_ID=runtime-payload-check"])
    path = Path(result.stdout.strip().splitlines()[-1])
    if not path.is_file():
        raise RuntimeError(f"model-path did not return a model: {result.stdout}")
    return path


def verilator_major():
    """The installed Verilator's major version, or 0 if it cannot be read.

    Options come and go between generations, and this check runs on both: CI
    is pinned to 4.038 per docs/dependencies.md while development hosts are on
    5.x. Guarding by version is what the Makefiles already do for -Wno-
    UNUSEDPARAM; the same rule applies here.
    """
    result = run([os.environ.get("VERILATOR", "verilator"), "--version"],
                 success=False)
    text = (result.stdout or result.stderr).split()
    if len(text) < 2:
        return 0
    digits = text[1].split(".")[0]
    return int(digits) if digits.isdigit() else 0


def preprocessed(source, *, define):
    """The memory source as a compiler would see it, with and without the guard.

    Verilator's -E is the same preprocessor the build uses, so this is the text
    that actually reaches elaboration rather than an approximation of it.

    --no-std suppresses Verilator 5's built-in std package. Verilator 4 has no
    such package and rejects the option outright, which failed this check on CI
    while passing on every 5.x development host.
    """
    command = [os.environ.get("VERILATOR", "verilator"), "-E"]
    if verilator_major() >= 5:
        command.append("--no-std")
    if define:
        command.append("+define+AX_RUNTIME_RAM_IMAGE")
    command.append(str(source))
    return run(command).stdout


# Where the RAM starts on the aXbus map. One place, so the entry check below
# and the memory components cannot disagree about it silently.
RAM_BASE = 0x80000000


def ram_geometry(config):
    """The profile's RAM capacity in words, its base, and its reset PC.

    Read from the resolver rather than assumed: the capacity the runner refuses
    an image against is the one the model elaborated, and both come from the
    profile's own settings.
    """
    resolved = run(["python3", ROOT / "tools/configure.py", "resolve",
                    "--config", ROOT / "configs" / config]).stdout
    values = {}
    for key in ("COMPONENT_RAM_BYTES", "COMPONENT_RESET_PC"):
        match = re.search(rf"^{key} := (\S+)$", resolved, re.M)
        if not match:
            raise RuntimeError(f"{config} does not declare {key}")
        values[key] = int(match[1], 0)
    return (values["COMPONENT_RAM_BYTES"] // 4, RAM_BASE,
            values["COMPONENT_RESET_PC"])


def boot(binary, image=None, *, cwd=ROOT):
    args = [binary, "--max-cycles", "2000000"]
    if image is not None:
        args += ["--ram-image", image]
    result = run(args, cwd=cwd)
    match = re.search(r"\[soc\] exit 0 \(cycles=(\d+)\)", result.stderr)
    if not match:
        raise RuntimeError(f"missing finisher evidence: {result.stderr}")
    return {"uart": result.stdout, "cycles": int(match[1])}


def main():
    run(["make", "-s", "-C", ROOT / "sw/baremetal",
         "build/hello.hex", "build/timer.hex"])
    hello = ROOT / "sw/baremetal/build/hello.hex"
    timer = ROOT / "sw/baremetal/build/timer.hex"
    records = []
    rejections = []
    interactive_model = None
    for config, sync in (("sim-role-loopback.json", 0),
                         ("sim-role-loopback.json", 1),
                         ("sim-delayed.json", 0)):
        print(f"[runtime-payload] checking {config} SYNC_READ={sync}", flush=True)
        binary = model(config, sync)
        identity = (digest(binary), binary.stat().st_mtime_ns)
        first = boot(binary, hello)
        second = boot(binary, timer)
        assert first["uart"] == "hello from atomiX\n", first
        assert second["uart"] == "timer demo: TTT\n", second
        assert boot(binary, hello) == first, "payload state leaked across launches"

        # Same basename, spaces, relative paths, and replacing bytes at the
        # same path must all select the actual file, without make in between.
        with tempfile.TemporaryDirectory(prefix="atomix payload ") as directory:
            staging = Path(directory)
            image = staging / "program image.hex"
            image.write_bytes(hello.read_bytes())
            assert boot(binary, image.name, cwd=staging) == first
            image.write_bytes(timer.read_bytes())
            assert boot(binary, image.name, cwd=staging) == second
            empty = staging / "empty.hex"
            empty.touch()
            for args in (["--ram-image"], ["--ram-image", staging / "missing"],
                         ["--ram-image", empty],
                         ["--ram-image", hello, "--ram-image", timer]):
                rejected = run([binary, *args], success=False)
                assert rejected.returncode == 2, rejected
                assert "[soc]" in rejected.stderr, rejected

            # An image the loader would mis-load rather than fail on.
            # $readmemh stops at the first non-hex character, moves the words
            # after an `@` record somewhere else in the array, and drops words
            # past the end of it -- each of which boots something that is not
            # the program, and reports a cycle count for whatever that was.
            # Every one must be refused by name, and, because a refused launch
            # must leave nothing behind, the next valid launch must still be
            # bit-for-bit the run it was before.
            # The entry half of the same question.  A runtime image is loaded
            # at word zero of the RAM array, so it only lands under the reset
            # PC while that PC *is* the RAM base -- true of every profile that
            # accepts the argument, and checked rather than assumed, because a
            # profile that booted elsewhere would run whatever happened to be
            # at its entry rather than the image it was handed. The one profile
            # whose reset PC is the ROM refuses the argument outright, which is
            # checked below.
            capacity_words, ram_base, reset_pc = ram_geometry(config)
            assert reset_pc == ram_base, (
                f"{config} resets at {reset_pc:#010x} but its RAM starts at "
                f"{ram_base:#010x}: a runtime image would not be at the entry")
            malformed = {
                "not a hex word": "00020117\nzzzz\n",
                "wider than 32 bits": "00020117\n123456789\n",
                "an address record": "@00000100\n00020117\n",
                "a block comment": "/* payload */\n00020117\n",
                "more words than RAM": "00000013\n" * (capacity_words + 1),
            }
            for description, text in malformed.items():
                broken = staging / "broken.hex"
                broken.write_text(text)
                rejected = run([binary, "--ram-image", broken], success=False)
                assert rejected.returncode == 2, (description, rejected)
                assert "rejected RAM image" in rejected.stderr, \
                    (description, rejected.stderr)
                assert boot(binary, hello) == first, \
                    f"a launch after {description} was rejected did not match"
                rejections.append({"case": description,
                                   "stderr": rejected.stderr.strip()
                                             .replace(str(staging), "{staging}")})
        assert (digest(binary), binary.stat().st_mtime_ns) == identity, "model changed"

        # Legacy callers still boot the compiled default; runtime selection
        # must override it and produce exactly the unbaked model's cycles.
        baked = model(config, sync, timer)
        assert boot(baked) == second, "baked/runtime timer divergence"
        assert boot(baked, hello) == first, "runtime image did not override default"
        records.append({"profile": config, "sync_read": sync,
                        "model_sha256": identity[0], "hello": first, "timer": second})
        if config == "sim-role-loopback.json" and sync == 0:
            interactive_model = binary

    # The boundary the other initialization paths sit behind.  Unit benches,
    # the browser bundle, and every synthesis flow build these same memory
    # sources *without* AX_RUNTIME_RAM_IMAGE, and what that has to mean is that
    # the runtime branch is not in their text at all -- not merely unreachable.
    # Checked by preprocessing the sources both ways rather than by reading
    # the guard, because a guard is easy to write and easy to widen.
    boundary = []
    for source in ("components/memory/reference/axram.sv",
                   "components/memory/reference/axdram_model.sv"):
        without = preprocessed(ROOT / source, define=False)
        with_define = preprocessed(ROOT / source, define=True)
        assert "$value$plusargs" not in without, (
            f"{source} reaches the runtime-image branch without "
            "AX_RUNTIME_RAM_IMAGE: a synthesis or unit build would carry it")
        assert "$value$plusargs" in with_define, (
            f"{source} no longer has a runtime-image branch to guard")
        assert "$readmemh" in without, (
            f"{source} lost its original initialization path")
        boundary.append({"source": source,
                         "sha256_without_define": hashlib.sha256(
                             without.encode()).hexdigest()})

    # The initialization paths this argument does *not* serve, checked rather
    # than assumed.  The pin-level SDRAM machine has no writable array to
    # preload -- it boots through the ROM loader off the card -- so it must say
    # so and stop, not accept the argument and boot the previous contents of a
    # model that never read it.
    unsupported = []
    sdram = run(["make", "-s", "--no-print-directory", "-C", SOC, "build-model",
                 f"COMPONENT_CONFIG={ROOT / 'configs/sim-sdram.json'}",
                 f"ROM_INIT_FILE={ROOT / 'sw/bootrom/build/bootrom.hex'}",
                 "BUILD_ID=runtime-payload-sdram"], success=False)
    if sdram.returncode == 0:
        sdram_model = Path(run(
            ["make", "-s", "--no-print-directory", "-C", SOC, "model-path",
             f"COMPONENT_CONFIG={ROOT / 'configs/sim-sdram.json'}",
             f"ROM_INIT_FILE={ROOT / 'sw/bootrom/build/bootrom.hex'}",
             "BUILD_ID=runtime-payload-sdram"]).stdout.strip().splitlines()[-1])
        refused = run([sdram_model, "--ram-image", hello], success=False)
        assert refused.returncode == 2, refused
        assert "--ram-image is unavailable for pin-level SDRAM" in refused.stderr, \
            refused.stderr
        unsupported.append({"profile": "sim-sdram.json",
                            "reason": refused.stderr.strip()})
    else:
        # Recorded as not checked rather than quietly omitted: this is the one
        # case whose model this check does not otherwise need, so a build
        # failure here must not read as a pass.
        unsupported.append({"profile": "sim-sdram.json",
                            "reason": "skipped: the pin-level model did not build"})
        print("[runtime-payload] SKIPPED the pin-level SDRAM refusal: "
              "its model did not build", flush=True)

    # Dedicated build keeps another test's host-link/storage personality out
    # of this session, without replacing the developer's current kernel image.
    run(["make", "-s", "-C", ROOT / "sw/kernel", "images",
         "BUILD_DIR=build/runtime-payload-check", "HOSTLINK=0", "STORAGE=0",
         f"KERNEL_CONFIG={ROOT / 'configs/kernel-default.json'}"])
    kernel = ROOT / "sw/kernel/build/runtime-payload-check/axos_boot.hex"
    session = run([interactive_model, "--ram-image", kernel, "--uart-interactive"],
                  input="role\nrole\nexit\n")
    for marker in ("aXos: shell online", "role: copy ok irq=1 polled=0",
                   "role: copy ok irq=2 polled=0", "aXos> exit"):
        assert session.stdout.count(marker) == 1, session.stdout
    assert boot(interactive_model, hello)["uart"] == "hello from atomiX\n"
    evidence = {
        "schema": "org.atomix.runtime-payload-check.v2", "evidence_kind": "simulation",
        "command": "make -C sim/soc check-runtime-payload",
        "verilator": run([os.environ.get("VERILATOR", "verilator"), "--version"]).stdout.strip(),
        "payload_sha256": {"hello": digest(hello), "timer": digest(timer),
                           "kernel": digest(kernel)},
        "cases": records, "interactive_uart": session.stdout,
        "rejected_images": rejections,
        "unsupported_initialization": unsupported,
        "initialization_boundary": boundary,
        "result": "pass",
    }
    output = SOC / "build/runtime-payload-evidence.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"[runtime-payload] PASS: three memory configurations, payload reuse, "
          f"baked parity, {len(rejections)} malformed-image refusals with a "
          f"valid launch after each, the pin-level machine's refusal of the "
          f"argument, invalid paths, and interactive continuity; {output}")


if __name__ == "__main__":
    main()
