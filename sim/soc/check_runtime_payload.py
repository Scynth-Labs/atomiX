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
        "schema": "org.atomix.runtime-payload-check.v1", "evidence_kind": "simulation",
        "command": "make -C sim/soc check-runtime-payload",
        "verilator": run([os.environ.get("VERILATOR", "verilator"), "--version"]).stdout.strip(),
        "payload_sha256": {"hello": digest(hello), "timer": digest(timer),
                           "kernel": digest(kernel)},
        "cases": records, "interactive_uart": session.stdout,
        "result": "pass",
    }
    output = SOC / "build/runtime-payload-evidence.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"[runtime-payload] PASS: three memory configurations, payload reuse, "
          f"baked parity, invalid paths, and interactive continuity; {output}")


if __name__ == "__main__":
    main()
