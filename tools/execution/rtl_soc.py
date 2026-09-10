"""RTL SoC adapter: one payload, several composed machines.

This adapter exists for the claim the component system was built to make --
that a selection is worth something measurable -- so it keeps two identities
apart at every step.  The Verilated model is built from a resolved profile and
hashed; the payload is loaded into it at run time through --ram-image and
hashed separately.  Three candidates that differ only in their core therefore
share one payload hash and carry three model hashes, and a record can say
which of the two changed.

Capabilities are read from the manifests the resolver actually selected, not
from the plan.  That is how a plan claiming `m-mode` for the five-stage core,
whose manifest says `m/s/u`, is caught here instead of being believed.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from typing import Any

from .contract import (
    Adapter, AdapterError, Blocked, CaseOutcome, Description, Execution,
    Prepared, ROOT, TERMINATION_COMPLETED, Unsupported, checked, run_bounded,
    namespaced_capability, sha256_file, sha256_json, tool_version,
)

RISCV_PREFIXES = (
    "riscv64-unknown-elf-", "riscv32-unknown-elf-",
    "riscv64-elf-", "riscv32-elf-", "riscv64-linux-gnu-",
)


def resolver():
    """Reuse the component resolver rather than reimplementing selection."""
    sys.path.insert(0, str(ROOT / "tools"))
    import configure
    return configure


class RtlSocAdapter(Adapter):
    adapter_id = "org.atomix.adapter.rtl-soc-simulator"
    target_class = "org.atomix.rtl-simulator"

    # Facts about the harness rather than about any selected component: the
    # model takes its payload at run time and can be bounded by this process.
    ADAPTER_CAPABILITIES = frozenset({
        "org.atomix.capability.deterministic-model-cycles",
        "org.atomix.capability.process-timeout",
    })

    def profile_path(self, target: dict[str, Any]) -> Path:
        profile = target.get("profile")
        if not profile or profile["kind"] != "org.atomix.component-profile":
            raise Unsupported(
                f"{self.adapter_id} needs an org.atomix.component-profile profile"
            )
        path = ROOT / profile["value"]["config"]
        if not path.is_file():
            raise Blocked(f"profile {profile['value']['config']} does not exist")
        return path

    def riscv_prefix(self) -> str:
        for prefix in RISCV_PREFIXES:
            if shutil.which(f"{prefix}gcc"):
                return prefix
        raise Blocked(
            "no RISC-V toolchain found; tried " + ", ".join(f"{p}gcc" for p in RISCV_PREFIXES)
        )

    def describe(self, target: dict[str, Any]) -> Description:
        if shutil.which("verilator") is None:
            raise Blocked("verilator is not on PATH")
        path = self.profile_path(target)
        configure = resolver()
        try:
            resolved = configure.resolved_config(path)
        except Exception as exc:  # the resolver owns its own error vocabulary
            raise Unsupported(f"profile {path.name} does not resolve: {exc}") from exc
        capabilities = set(self.ADAPTER_CAPABILITIES)
        selected = {}
        for kind, component in resolved["components"].items():
            selected[kind] = component["id"]
            capabilities |= {
                namespaced_capability(name)
                for name in component.get("capabilities", [])
            }
        return Description(
            adapter=self.adapter_id,
            target_class=self.target_class,
            capabilities=frozenset(capabilities),
            measurement_domain="org.atomix.domain.model-cycles",
            tools={"verilator": tool_version("verilator")},
            limits={
                "max_cycles": int(target["profile"]["value"].get("max_cycles", 2000000)),
                "components": selected,
            },
        )

    def prepare(self, implementation: dict[str, Any], target: dict[str, Any],
                workdir: Path, cases: list[dict[str, Any]], *,
                limit_seconds: float, cancel_after: float | None = None) -> Prepared:
        build = implementation["build"]
        if build["kind"] != "org.atomix.riscv-baremetal-make":
            raise Unsupported(f"{self.adapter_id} cannot build {build['kind']}")
        value = build["value"]
        prefix = self.riscv_prefix()
        compiler = tool_version(f"{prefix}gcc")

        checked(
            run_bounded(
                ["make", "-s", "-C", str(ROOT / value["directory"]), value["target"]],
                limit_seconds, cwd=ROOT, cancel_after=cancel_after,
            ),
            f"baremetal build of {value['target']}",
        )
        artifact = ROOT / value["artifact"]
        if not artifact.is_file():
            raise Blocked(f"payload {value['artifact']} was not produced")

        # The loadable image is a $readmemh text file; the payload's size in
        # bytes is the binary beside it, which is what a board would actually
        # carry. Falling back to the text size would report the encoding.
        binary = artifact.with_suffix(".bin")
        artifact_bytes = (binary if binary.is_file() else artifact).stat().st_size

        profile = self.profile_path(target)
        model, model_sha256 = self.build_model(
            target, profile, limit_seconds=limit_seconds, cancel_after=cancel_after
        )
        return Prepared(
            artifact=artifact,
            artifact_sha256=sha256_file(artifact),
            artifact_bytes=artifact_bytes,
            build_sha256=sha256_json({
                "make": [value["directory"], value["target"]],
                "compiler": compiler,
                "image_sha256": sha256_file(artifact),
            }),
            tools={f"{prefix}gcc": compiler},
            target_build_sha256=model_sha256,
            profile_sha256=sha256_file(profile),
            detail={
                "model": str(model),
                "profile": value.get("artifact"),
                "artifact_kind": "RISC-V baremetal image",
            },
        )

    def build_model(self, target: dict[str, Any], profile: Path, *,
                    limit_seconds: float,
                    cancel_after: float | None = None) -> tuple[Path, str]:
        """Build the model once per profile and reuse it for every payload."""
        value = target["profile"]["value"]
        result = checked(
            run_bounded(
                ["make", "-s", "-C", str(ROOT / "sim" / "soc"), "model-path",
                 f"COMPONENT_CONFIG={profile}",
                 f"RESET_PC={value.get('reset_pc', '0x80000000')}",
                 "BUILD_ID=experiment"],
                limit_seconds, cwd=ROOT, cancel_after=cancel_after,
            ),
            f"verilator build of profile {profile.name}",
        )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            raise AdapterError("model-path produced no path")
        model = Path(lines[-1])
        if not model.is_file():
            raise AdapterError(f"model-path named {model}, which does not exist")
        return model, sha256_file(model)

    def execute(self, prepared: Prepared, target: dict[str, Any],
                cases: list[dict[str, Any]], workdir: Path, *,
                limit_seconds: float, repetitions: int,
                cancel_after: float | None = None) -> Execution:
        model = Path(prepared.detail["model"])
        if not model.is_file():
            raise Blocked(f"model {model} is missing; prepare it before executing")
        max_cycles = int(target["profile"]["value"].get("max_cycles", 2000000))
        outcomes: list[CaseOutcome] = []
        elapsed_total = 0.0
        for case in cases:
            result = run_bounded(
                [str(model), "--max-cycles", str(max_cycles),
                 "--ram-image", str(prepared.artifact)],
                limit_seconds, cwd=workdir, cancel_after=cancel_after,
            )
            elapsed_total += result.elapsed_seconds
            checked(result, f"model run of case {case['name']}")
            outcomes.append(self.report(case, result.stdout + result.stderr,
                                        result.elapsed_seconds))
        return Execution(
            cases=outcomes, termination=TERMINATION_COMPLETED,
            elapsed_seconds=elapsed_total,
        )

    def report(self, case: dict[str, Any], text: str,
               elapsed_seconds: float) -> CaseOutcome:
        """Read the payload's own self-check output as this case's result.

        The program reports the workload-only cycle count separately from the
        whole-program count, and the split matters: the second includes UART
        and setup, which are the harness's costs rather than the workload's.
        """
        measured = re.search(
            r"cpu_perf measured: cycles=(\d+) checksum=0x([0-9a-fA-F]+)", text
        )
        whole = re.search(r"\[soc\] exit 0 \(cycles=(\d+)\)", text)
        if not measured or not whole:
            raise AdapterError(
                f"case {case['name']}: the payload printed no measured result"
            )
        if "cpu_perf: PASS" not in text:
            # The payload's own IPC floors failed, which is a real outcome, not
            # a reason to discard the run: the oracle result below still says
            # whether the arithmetic agreed.
            failure = re.search(r"cpu_perf: FAIL [^\n]*", text)
            detail = failure.group(0) if failure else "cpu_perf did not report PASS"
        else:
            detail = "cpu_perf: PASS"
        return CaseOutcome(
            name=case["name"],
            outputs={"checksum": [int(measured.group(2), 16)]},
            repetitions=1,
            cycles={"execute": int(measured.group(1)), "total": int(whole.group(1))},
            detail={
                "self_check": detail,
                "simulator_elapsed_ns": int(elapsed_seconds * 1e9),
            },
        )
