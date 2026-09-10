#!/usr/bin/env python3
"""Run the delayed-finisher package's portable conformance checks."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


EXIT_RE = re.compile(r"\[soc\] exit 0 \(cycles=(\d+)\)")


class CheckError(Exception):
    pass


def run(command: list[str], cwd: Path, expect_success: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    if (result.returncode == 0) != expect_success:
        transcript = (result.stdout + result.stderr).strip()
        expected = "succeed" if expect_success else "fail"
        raise CheckError(
            f"command should {expected} (exit {result.returncode}): "
            f"{' '.join(command)}\n{transcript}")
    return result


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise CheckError(f"{path}: expected a JSON object")
    return value


def selected_id(selection: Any, package: Path) -> str:
    if isinstance(selection, str):
        return selection
    if isinstance(selection, dict) and set(selection) == {"manifest"}:
        manifest = Path(selection["manifest"])
        if not manifest.is_absolute():
            manifest = package / "profiles" / manifest
        return str(load_json(manifest.resolve()).get("id", ""))
    return ""


def check_compatibility(profile: Path, package: Path) -> None:
    manifest = load_json(package / "component.json")
    config = load_json(profile)
    components = config.get("components", {})
    requires = manifest.get("compatibility", {}).get("requires", {})
    for kind, allowed in requires.items():
        actual = selected_id(components.get(kind), package)
        if actual not in allowed:
            raise CheckError(
                f"unsupported combination: {kind}={actual or '<missing>'}; "
                f"{manifest['id']} conformance covers {', '.join(allowed)}")


def resolve(root: Path, profile: Path, expect_success: bool = True) -> subprocess.CompletedProcess[str]:
    return run(
        [sys.executable, str(root / "tools" / "configure.py"), "resolve",
         "--config", str(profile)],
        root,
        expect_success=expect_success,
    )


def simulate(root: Path, package: Path, profile_name: str, delay: int) -> int:
    profile = package / "profiles" / profile_name
    check_compatibility(profile, package)
    resolved = resolve(root, profile)
    source_line = f"COMPONENT_FINISHER_SOURCES := {(package / 'test_finisher.sv').resolve()}"
    if source_line not in resolved.stdout:
        raise CheckError("resolver did not select the package-local finisher source")
    define = f"+define+AX_SDK_FINISHER_ACK_DELAY={delay}"
    if define not in resolved.stdout:
        raise CheckError(f"resolver did not wire {define}")

    result = run(
        ["make", "--no-print-directory", "-s", "-C", "sim/soc", "run-config",
         f"COMPONENT_CONFIG={profile}", "MAX_CYCLES=64",
         f"BUILD_ID=sdk-finisher-delay{delay}"],
        root,
    )
    transcript = result.stdout + result.stderr
    match = EXIT_RE.search(transcript)
    if not match:
        raise CheckError(f"missing successful finisher transcript:\n{transcript.strip()}")
    cycles = int(match.group(1))
    expected_cycles = delay + 1
    if cycles != expected_cycles:
        raise CheckError(
            f"delay {delay} completed in {cycles} cycles, expected {expected_cycles}")
    print(f"PASS {profile_name}: delay={delay}, exit=0, cycles={cycles}")
    return cycles


def suite(root: Path, source_package: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="atomix-external-component-") as temporary:
        package = Path(temporary) / "finisher-delayed"
        shutil.copytree(source_package, package)
        if package.resolve().is_relative_to(root.resolve()):
            raise CheckError("conformance package was not copied outside the source tree")

        default_cycles = simulate(root, package, "sim-default.json", 1)
        nondefault_cycles = simulate(root, package, "sim-delay4.json", 4)
        if nondefault_cycles - default_cycles != 3:
            raise CheckError("non-default parameter did not change observable latency")

        unsupported = package / "profiles" / "tangprimer25k-unsupported.json"
        resolve(root, unsupported)
        try:
            check_compatibility(unsupported, package)
        except CheckError as exc:
            if not str(exc).startswith("unsupported combination: board=board.tangprimer25k"):
                raise
            print(f"PASS unsupported profile refused: {exc}")
        else:
            raise CheckError("unsupported physical-board profile was accepted")

        invalid = load_json(package / "profiles" / "sim-delay4.json")
        invalid["name"] = "sdk-finisher-out-of-range"
        invalid["parameters"]["finisher"]["ack_delay_cycles"] = 17
        invalid_path = package / "profiles" / "sim-out-of-range.json"
        invalid_path.write_text(json.dumps(invalid, indent=2) + "\n")
        rejected = resolve(root, invalid_path, expect_success=False)
        if "above the maximum 16" not in rejected.stderr:
            raise CheckError(f"out-of-range rejection was not specific:\n{rejected.stderr.strip()}")
        print("PASS out-of-range parameter refused: ack_delay_cycles=17")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atomix-root", required=True, type=Path)
    args = parser.parse_args()
    root = args.atomix_root.resolve()
    package = Path(__file__).resolve().parent
    try:
        suite(root, package)
    except (CheckError, OSError, json.JSONDecodeError) as exc:
        print(f"external-component-check: FAIL: {exc}", file=sys.stderr)
        return 1
    print("external-component-check: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
