"""Native host CPU adapter: an ordinary process, and nothing else.

This is the leg that proves the platform is not an FPGA project wearing a
product's clothes.  It builds a host executable with the host's own compiler
and runs it.  There is no SoC, no board, no loader, no role window, and no
RISC-V toolchain anywhere in its path -- if any of those were required here,
the portability claim would be circular.  A Verilated binary is a native
program too, and is deliberately not this: it simulates the design instead of
implementing the workload.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .contract import (
    Adapter, AdapterError, Blocked, CaseOutcome, Description, Execution,
    Prepared, ROOT, TERMINATION_COMPLETED, Unsupported, case_file_text,
    checked, run_bounded, sha256_file, sha256_json, tool_version,
)


class NativeCpuAdapter(Adapter):
    adapter_id = "org.atomix.adapter.native-cpu"
    target_class = "org.atomix.native-host"

    # What a hosted C11 implementation genuinely gives this workload. These are
    # properties of the toolchain and the C standard, not of any plan: exact
    # 32-bit integer arithmetic is available when the source writes the wrap
    # explicitly, which is why the implementation does its arithmetic unsigned.
    CAPABILITIES = frozenset({
        "org.atomix.capability.exact-int32-wrap",
        "org.atomix.capability.host-c11-toolchain",
        "org.atomix.capability.monotonic-clock",
        "org.atomix.capability.process-timeout",
    })

    # The executable refuses larger inputs itself; the limit is repeated here so
    # a plan is rejected during discovery rather than by a run that had to be
    # started first.
    MAX_ITEMS = 1048576

    def compiler(self) -> str:
        return os.environ.get("CC", "cc")

    def build_configuration(self, implementation: dict[str, Any]) -> tuple[str, str, list[str]]:
        """Resolve either the original host-cc form or an explicit AX-11 choice."""
        value = implementation["build"]["value"]
        configuration = value.get("compiler_configuration")
        if configuration is None:
            return self.compiler(), value["standard"], list(value["flags"])
        if set(configuration) != {"executable", "standard", "flags"} or not isinstance(
                configuration["flags"], list):
            raise Unsupported(
                "compiler_configuration needs executable, standard, and flags"
            )
        return (
            str(configuration["executable"]), str(configuration["standard"]),
            [str(flag) for flag in configuration["flags"]],
        )

    def check_factors(self, implementation: dict[str, Any]) -> None:
        value = implementation["build"]["value"]
        supported = {
            "algorithm": "org.atomix.algorithm.saxpy-scalar-loop",
            "layout": "org.atomix.layout.separate-contiguous-arrays",
            "runtime_policy": "org.atomix.runtime.in-process-repetitions",
        }
        for name, expected in supported.items():
            selected = value.get(name, expected)
            if selected != expected:
                raise Unsupported(f"native SAXPY does not implement {name}={selected!r}")

    def describe(self, target: dict[str, Any]) -> Description:
        compiler = self.compiler()
        if shutil.which(compiler) is None:
            raise Blocked(
                f"no host C compiler: {compiler} is not on PATH (set CC to choose one)"
            )
        return Description(
            adapter=self.adapter_id,
            target_class=self.target_class,
            capabilities=self.CAPABILITIES,
            measurement_domain="org.atomix.domain.host-elapsed",
            tools={"cc": tool_version(compiler)},
            limits={"max_items": self.MAX_ITEMS},
        )

    def fingerprint(self, implementation: dict[str, Any], target: dict[str, Any],
                    cases: list[dict[str, Any]]) -> dict[str, Any] | None:
        build = implementation["build"]
        source = ROOT / build["value"]["source"]
        if build["kind"] != "org.atomix.host-cc" or not source.is_file():
            return None
        # Explicit AX-11 configurations also retain the runtime libraries
        # selected for the resulting executable. Those are only knowable after
        # linking, so build first and use the complete reuse key rather than a
        # cheaper fingerprint that would omit execution identity.
        if "compiler_configuration" in build["value"]:
            return None
        self.check_factors(implementation)
        compiler, standard, flags = self.build_configuration(implementation)
        return {
            "source": build["value"]["source"],
            "source_sha256": sha256_file(source),
            "standard": standard,
            "flags": flags,
            "compiler": tool_version(compiler),
            "algorithm": build["value"].get("algorithm"),
            "layout": build["value"].get("layout"),
            "runtime_policy": build["value"].get("runtime_policy"),
        }

    def prepare(self, implementation: dict[str, Any], target: dict[str, Any],
                workdir: Path, cases: list[dict[str, Any]], *,
                limit_seconds: float, cancel_after: float | None = None) -> Prepared:
        build = implementation["build"]
        if build["kind"] != "org.atomix.host-cc":
            raise Unsupported(f"{self.adapter_id} cannot build {build['kind']}")
        value = build["value"]
        source = ROOT / value["source"]
        if not source.is_file():
            raise Blocked(f"implementation source {value['source']} does not exist")
        for case in cases:
            items = int(case["parameters"]["items"])
            if items > self.MAX_ITEMS:
                raise Unsupported(
                    f"case {case['name']} has {items} items, above this target's "
                    f"limit of {self.MAX_ITEMS}"
                )

        self.check_factors(implementation)
        compiler, standard, flags = self.build_configuration(implementation)
        version = tool_version(compiler)
        artifact = workdir / "saxpy_i32"
        command = [
            compiler, f"-std={standard}", *flags,
            "-o", str(artifact), str(source),
        ]
        checked(
            run_bounded(command, limit_seconds, cwd=ROOT, cancel_after=cancel_after),
            f"{compiler} build of {value['source']}",
        )
        compiler_path = Path(shutil.which(compiler) or compiler).resolve()
        libraries = self.runtime_libraries(artifact)
        runtime_tools = {
            f"runtime:{Path(library['path']).name}": library["sha256"]
            for library in libraries
        }
        return Prepared(
            artifact=artifact,
            artifact_sha256=sha256_file(artifact),
            artifact_bytes=artifact.stat().st_size,
            # The build identity is what a replay must reproduce: this source,
            # these flags, this compiler. The output path is deliberately not
            # part of it, because a different scratch directory is not a
            # different build.
            build_sha256=sha256_json({
                "command": [compiler, f"-std={standard}", *flags,
                            value["source"]],
                "source_sha256": sha256_file(source),
                "compiler": version,
                "compiler_sha256": sha256_file(compiler_path),
            }),
            tools={compiler_path.name: version, **runtime_tools},
            detail={
                "source": value["source"], "artifact_kind": "host executable",
                "compiler_executable": str(compiler_path),
                "compiler_sha256": sha256_file(compiler_path),
                "runtime_libraries": libraries,
                "algorithm": value.get("algorithm", "org.atomix.algorithm.saxpy-scalar-loop"),
                "layout": value.get("layout", "org.atomix.layout.separate-contiguous-arrays"),
                "runtime_policy": value.get(
                    "runtime_policy", "org.atomix.runtime.in-process-repetitions"
                ),
            },
        )

    def runtime_libraries(self, artifact: Path) -> list[dict[str, str]]:
        """Record the dynamic libraries selected for this executable, if any."""
        try:
            result = subprocess.run(
                ["ldd", str(artifact)], capture_output=True, text=True,
                timeout=30, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        libraries = []
        for line in result.stdout.splitlines():
            words = line.replace("=>", " ").split()
            path = next((Path(word) for word in words if word.startswith("/") and Path(word).is_file()), None)
            if path is not None:
                libraries.append({"path": str(path), "sha256": sha256_file(path)})
        return sorted(libraries, key=lambda item: item["path"])

    def execute(self, prepared: Prepared, target: dict[str, Any],
                cases: list[dict[str, Any]], workdir: Path, *,
                limit_seconds: float, repetitions: int,
                cancel_after: float | None = None) -> Execution:
        outcomes: list[CaseOutcome] = []
        elapsed_total = 0.0
        for case in cases:
            parameters = case["parameters"]
            case_path = workdir / f"case-{case['name']}.txt"
            case_path.write_text(case_file_text(
                int(parameters["items"]), int(parameters["a"]),
                case["inputs"]["x"], case["inputs"]["y"], repetitions,
            ))
            result = run_bounded(
                [str(prepared.artifact), "--input", str(case_path)],
                limit_seconds, cwd=workdir, cancel_after=cancel_after,
            )
            elapsed_total += result.elapsed_seconds
            checked(result, f"native run of case {case['name']}")
            try:
                report = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise AdapterError(
                    f"native run of case {case['name']} printed no usable report: {exc}"
                ) from exc
            if int(report["items"]) != int(parameters["items"]) or \
                    int(report["a"]) != int(parameters["a"]):
                raise AdapterError(
                    f"native run of case {case['name']} reported different parameters "
                    "than it was given"
                )
            outcomes.append(CaseOutcome(
                name=case["name"],
                outputs={"out": [int(value) for value in report["out"]]},
                repetitions=len(report["elapsed_ns"]),
                elapsed_ns=[int(value) for value in report["elapsed_ns"]],
                detail={"process_elapsed_seconds": result.elapsed_seconds},
            ))
        return Execution(
            cases=outcomes, termination=TERMINATION_COMPLETED,
            elapsed_seconds=elapsed_total,
        )
