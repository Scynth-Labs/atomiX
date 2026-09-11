"""RISC-V software-model adapters for aXsim and QEMU.

Both targets run one unchanged bare-metal ELF, but they are not interchangeable.
aXsim is the project's instruction-set simulator and defines mcycle/minstret as
retired instructions. QEMU's virt machine is a system emulator whose guest
mcycle value follows its virtual-time implementation. The adapters therefore
publish separate target classes and measurement domains, even when their exact
UART output and checksum agree.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

from .contract import (
    Adapter, AdapterError, Blocked, CaseOutcome, Description, Execution,
    Prepared, ROOT, TERMINATION_COMPLETED, Unsupported, checked, run_bounded,
    sha256_file, sha256_json, tool_version,
)


RISCV_PREFIXES = (
    "riscv64-unknown-elf-", "riscv32-unknown-elf-", "riscv64-elf-",
    "riscv32-elf-", "riscv64-linux-gnu-",
)
COMMON_CAPABILITIES = frozenset({
    "org.atomix.capability.rv32im",
    "org.atomix.capability.zicsr",
    "org.atomix.capability.uart",
    "org.atomix.capability.test-finisher",
    "org.atomix.capability.riscv-baremetal-elf",
    "org.atomix.capability.m-mode-entry",
    "org.atomix.capability.process-timeout",
})


def riscv_prefix() -> str:
    for prefix in RISCV_PREFIXES:
        if shutil.which(f"{prefix}gcc"):
            return prefix
    raise Blocked(
        "no RISC-V toolchain found; tried "
        + ", ".join(f"{prefix}gcc" for prefix in RISCV_PREFIXES)
    )


class RiscvModelAdapter(Adapter):
    """Shared ELF build and exact cpu_perf UART parser."""

    profile_machine = ""
    profile_keys: frozenset[str] = frozenset()

    def profile(self, target: dict[str, Any]) -> dict[str, Any]:
        selector = target.get("profile")
        if not selector or selector.get("kind") != "org.atomix.riscv-software-machine":
            raise Unsupported(
                f"{self.adapter_id} needs an org.atomix.riscv-software-machine profile"
            )
        value = selector.get("value")
        if not isinstance(value, dict):
            raise Unsupported(f"{self.adapter_id} profile value must be an object")
        unknown = set(value) - self.profile_keys
        missing = self.profile_keys - set(value)
        if unknown or missing:
            raise Unsupported(
                f"{self.adapter_id} profile keys differ: missing={sorted(missing)}, "
                f"unknown={sorted(unknown)}"
            )
        if value["machine"] != self.profile_machine:
            raise Unsupported(
                f"{self.adapter_id} supports machine {self.profile_machine!r}, "
                f"not {value['machine']!r}"
            )
        if value["isa"] != "rv32im_zicsr" or value["abi"] != "ilp32":
            raise Unsupported(
                f"{self.adapter_id} requires isa=rv32im_zicsr and abi=ilp32"
            )
        return value

    def build_payload(self, implementation: dict[str, Any], workdir: Path,
                      limit_seconds: float,
                      cancel_after: float | None) -> tuple[Path, str, str]:
        build = implementation["build"]
        if build["kind"] != "org.atomix.riscv-baremetal-make":
            raise Unsupported(f"{self.adapter_id} cannot build {build['kind']}")
        value = build["value"]
        if value.get("toolchain") != "gcc":
            raise Unsupported(f"{self.adapter_id} supports toolchain=gcc for this fixture")
        artifact = ROOT / value["artifact"]
        if artifact.suffix != ".elf":
            raise Unsupported(f"{self.adapter_id} requires an ELF artifact")
        prefix = riscv_prefix()
        compiler = tool_version(f"{prefix}gcc")
        checked(
            run_bounded(
                ["make", "-s", "-C", str(ROOT / value["directory"]), value["target"],
                 "TOOLCHAIN=gcc", f"RISCV_PREFIX={prefix}"],
                limit_seconds, cwd=ROOT, cancel_after=cancel_after,
            ),
            f"baremetal build of {value['target']}",
        )
        if not artifact.is_file():
            raise Blocked(f"payload {value['artifact']} was not produced")
        build_sha = sha256_json({
            "make": [value["directory"], value["target"], "TOOLCHAIN=gcc",
                     f"RISCV_PREFIX={prefix}"],
            "compiler": compiler,
            "image_sha256": sha256_file(artifact),
        })
        return artifact, compiler, build_sha

    def prepared(self, implementation: dict[str, Any], artifact: Path,
                 compiler: str, build_sha: str, target_build: str,
                 profile: dict[str, Any]) -> Prepared:
        return Prepared(
            artifact=artifact,
            artifact_sha256=sha256_file(artifact),
            artifact_bytes=artifact.stat().st_size,
            build_sha256=build_sha,
            tools={"riscv-gcc": compiler},
            target_build_sha256=target_build,
            profile_sha256=sha256_json(profile),
            detail={"artifact_kind": "RISC-V bare-metal ELF"},
        )

    @staticmethod
    def cpu_perf(case: dict[str, Any], text: str) -> tuple[int, int]:
        measured = re.search(
            r"cpu_perf measured: cycles=(\d+) checksum=0x([0-9a-fA-F]+)", text
        )
        if not measured or "cpu_perf: PASS" not in text:
            raise AdapterError(
                f"case {case['name']}: cpu_perf did not print a passing result"
            )
        return int(measured.group(1)), int(measured.group(2), 16)


class AxsimAdapter(RiscvModelAdapter):
    adapter_id = "org.atomix.adapter.axsim"
    target_class = "org.atomix.instruction-set-simulator"
    profile_machine = "axsim"
    profile_keys = frozenset({"machine", "isa", "abi", "ram_bytes", "max_instructions"})

    def host_cxx(self) -> str:
        return os.environ.get("HOST_CXX", "g++")

    def describe(self, target: dict[str, Any]) -> Description:
        value = self.profile(target)
        ram_bytes = value["ram_bytes"]
        max_instructions = value["max_instructions"]
        if isinstance(ram_bytes, bool) or not isinstance(ram_bytes, int) or \
                not 4096 <= ram_bytes <= 0x80000000:
            raise Unsupported("aXsim ram_bytes must be in 4096..2147483648")
        if isinstance(max_instructions, bool) or \
                not isinstance(max_instructions, int) or \
                not 1 <= max_instructions <= 1_000_000_000:
            raise Unsupported("aXsim max_instructions must be in 1..1000000000")
        if shutil.which("make") is None:
            raise Blocked("make is not on PATH")
        if shutil.which(self.host_cxx()) is None:
            raise Blocked(f"aXsim host compiler {self.host_cxx()} is not on PATH")
        prefix = riscv_prefix()
        return Description(
            adapter=self.adapter_id,
            target_class=self.target_class,
            capabilities=COMMON_CAPABILITIES | {
                "org.atomix.capability.deterministic-retired-instructions"
            },
            measurement_domain="org.atomix.domain.iss-retired-instructions",
            tools={"make": tool_version("make"),
                   "host-c++": tool_version(self.host_cxx()),
                   "riscv-gcc": tool_version(f"{prefix}gcc")},
            limits={"ram_bytes": ram_bytes, "max_instructions": max_instructions},
        )

    def prepare(self, implementation: dict[str, Any], target: dict[str, Any],
                workdir: Path, cases: list[dict[str, Any]], *,
                limit_seconds: float, cancel_after: float | None = None) -> Prepared:
        del workdir, cases
        profile = self.profile(target)
        artifact, compiler, build_sha = self.build_payload(
            implementation, ROOT, limit_seconds, cancel_after
        )
        checked(
            run_bounded(["make", "-s", "-C", str(ROOT / "sim" / "axsim"), "axsim",
                         f"HOST_CXX={self.host_cxx()}"],
                        limit_seconds, cwd=ROOT, cancel_after=cancel_after),
            "aXsim build",
        )
        model = ROOT / "sim" / "axsim" / "axsim"
        if not model.is_file():
            raise Blocked("aXsim build produced no simulator")
        prepared = self.prepared(
            implementation, artifact, compiler, build_sha, sha256_file(model), profile
        )
        prepared.detail.update({"model": str(model)})
        return prepared

    def execute(self, prepared: Prepared, target: dict[str, Any],
                cases: list[dict[str, Any]], workdir: Path, *,
                limit_seconds: float, repetitions: int,
                cancel_after: float | None = None) -> Execution:
        del workdir, repetitions
        profile = self.profile(target)
        outcomes = []
        elapsed = 0.0
        for case in cases:
            result = run_bounded(
                [prepared.detail["model"], "--bin", str(prepared.artifact),
                 "--ram-bytes", str(profile["ram_bytes"]),
                 "--max", str(profile["max_instructions"])],
                limit_seconds, cwd=ROOT, cancel_after=cancel_after,
            )
            elapsed += result.elapsed_seconds
            checked(result, f"aXsim run of case {case['name']}")
            workload, checksum = self.cpu_perf(case, result.stdout)
            retired = re.search(r"retired=(\d+)", result.stderr)
            if not retired:
                raise AdapterError("aXsim printed no retired-instruction count")
            outcomes.append(CaseOutcome(
                name=case["name"], outputs={"checksum": [checksum]}, repetitions=1,
                measurements={
                    "org.atomix.metric.iss-workload-retired-instructions": (
                        workload,
                        "guest mcycle around the five kernels; aXsim defines mcycle "
                        "and minstret as one tick per retired instruction",
                    ),
                    "org.atomix.metric.iss-total-retired-instructions": (
                        int(retired.group(1)),
                        "aXsim retired-instruction count from reset through finisher exit",
                    ),
                },
                detail={"simulator_elapsed_ns": int(result.elapsed_seconds * 1e9)},
            ))
        return Execution(outcomes, TERMINATION_COMPLETED, elapsed)


class QemuRiscvAdapter(RiscvModelAdapter):
    adapter_id = "org.atomix.adapter.qemu-riscv32"
    target_class = "org.atomix.system-emulator"
    profile_machine = "virt"
    profile_keys = frozenset({"machine", "isa", "abi", "bios"})

    def executable(self) -> str:
        return os.environ.get("QEMU", "qemu-system-riscv32")

    def describe(self, target: dict[str, Any]) -> Description:
        value = self.profile(target)
        if value["bios"] != "none":
            raise Unsupported("the QEMU adapter supports only bios=none")
        qemu = self.executable()
        resolved = shutil.which(qemu)
        if resolved is None:
            raise Blocked(f"QEMU emulator {qemu} is not on PATH")
        version = tool_version(qemu)
        match = re.search(r"version\s+(\d+)(?:\.|\b)", version, re.IGNORECASE)
        if not match or int(match.group(1)) < 7:
            raise Blocked(
                f"QEMU >= 7 is required; could not accept version string {version!r}"
            )
        prefix = riscv_prefix()
        return Description(
            adapter=self.adapter_id,
            target_class=self.target_class,
            capabilities=COMMON_CAPABILITIES | {"org.atomix.capability.qemu-virt"},
            measurement_domain="org.atomix.domain.emulator-guest-counters",
            tools={"qemu-system-riscv32": version,
                   "riscv-gcc": tool_version(f"{prefix}gcc")},
            limits={"machine": "virt", "bios": "none"},
        )

    def prepare(self, implementation: dict[str, Any], target: dict[str, Any],
                workdir: Path, cases: list[dict[str, Any]], *,
                limit_seconds: float, cancel_after: float | None = None) -> Prepared:
        del workdir, cases
        profile = self.profile(target)
        artifact, compiler, build_sha = self.build_payload(
            implementation, ROOT, limit_seconds, cancel_after
        )
        resolved = shutil.which(self.executable())
        if resolved is None:
            raise Blocked(f"QEMU emulator {self.executable()} is not on PATH")
        prepared = self.prepared(
            implementation, artifact, compiler, build_sha, sha256_file(Path(resolved)), profile
        )
        prepared.detail.update({"qemu": resolved})
        return prepared

    def execute(self, prepared: Prepared, target: dict[str, Any],
                cases: list[dict[str, Any]], workdir: Path, *,
                limit_seconds: float, repetitions: int,
                cancel_after: float | None = None) -> Execution:
        del workdir, repetitions
        profile = self.profile(target)
        outcomes = []
        elapsed = 0.0
        for case in cases:
            result = run_bounded(
                [prepared.detail["qemu"], "-machine", profile["machine"],
                 "-bios", profile["bios"], "-nographic", "-kernel",
                 str(prepared.artifact)],
                limit_seconds, cwd=ROOT, cancel_after=cancel_after,
            )
            elapsed += result.elapsed_seconds
            checked(result, f"QEMU run of case {case['name']}")
            guest_mcycle, checksum = self.cpu_perf(case, result.stdout)
            outcomes.append(CaseOutcome(
                name=case["name"], outputs={"checksum": [checksum]}, repetitions=1,
                measurements={
                    "org.atomix.metric.emulator-guest-mcycle": (
                        guest_mcycle,
                        "mcycle read by the guest on QEMU virt; an emulator virtual-time "
                        "counter, not retired instructions, RTL cycles, or a measured clock",
                    ),
                },
                detail={"simulator_elapsed_ns": int(result.elapsed_seconds * 1e9)},
            ))
        return Execution(outcomes, TERMINATION_COMPLETED, elapsed)
