"""RTL role adapter: one accelerator component under Verilator, alone.

The target is the role's own RTL, selected through its component manifest --
not a whole SoC, not a board, and not a payload the CPU happens to be running.
What it measures is model cycles, which are a property of the design.  What it
costs to simulate is a property of Verilator and this laptop, so that number is
recorded in a different domain and never ranked against the design's cycles.

The kernel is data, not part of the machine.  This adapter encodes the SIMT
program with the same reviewed encoder the host tools use, writes it as a hex
image, and hands it to the harness the way the SoC harness takes --ram-image.
That is what keeps the model's build identity independent of the payload's:
changing `a` from 3 to 5 rewrites the program and nothing else, and the record
can say so.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from typing import Any

from .contract import (
    Adapter, AdapterError, Blocked, CaseOutcome, Description, Execution,
    Prepared, ROOT, TERMINATION_COMPLETED, Unsupported, case_file_text,
    checked, namespaced_capability, run_bounded, sha256_bytes, sha256_file,
    sha256_json, tool_version,
)

HARNESS = ROOT / "sim" / "experiment" / "tb_role_saxpy.cpp"
ENCODER = ROOT / "sw" / "host" / "gpu_programs.py"

# The engine sign-extends a 17-bit immediate, which bounds both the scalar and
# the buffer offsets one straight-line kernel can address.
IMMEDIATE_MINIMUM = -65536
IMMEDIATE_MAXIMUM = 65535
PROGRAM_WORDS = 64


def load_encoder():
    """Use the reviewed host encoder rather than a second copy of the ISA.

    A private copy of `gpu_insn` here would be one more place for the encoding
    to drift from the RTL it targets.
    """
    if not ENCODER.is_file():
        raise Blocked(f"instruction encoder {ENCODER} is missing")
    spec = importlib.util.spec_from_file_location("ax_gpu_programs", ENCODER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RtlRoleAdapter(Adapter):
    adapter_id = "org.atomix.adapter.rtl-role-simulator"
    target_class = "org.atomix.rtl-simulator"

    # Discovered from the engine's own definition rather than from the plan:
    # the ISA's MUL/MULI keep the low 32 bits, which is the declared wrap, and
    # a Verilated model runs as an ordinary process this adapter can bound.
    ADAPTER_CAPABILITIES = frozenset({
        "org.atomix.capability.exact-int32-wrap",
        "org.atomix.capability.deterministic-model-cycles",
        "org.atomix.capability.process-timeout",
    })

    def component(self, target: dict[str, Any]) -> dict[str, Any]:
        profile = target.get("profile")
        if not profile or profile["kind"] != "org.atomix.component-selection":
            raise Unsupported(
                f"{self.adapter_id} needs an org.atomix.component-selection profile"
            )
        manifest = ROOT / profile["value"]["component"]
        if not manifest.is_file():
            raise Blocked(f"component manifest {profile['value']['component']} is missing")
        return json.loads(manifest.read_text())

    def describe(self, target: dict[str, Any]) -> Description:
        if shutil.which("verilator") is None:
            raise Blocked("verilator is not on PATH")
        if not HARNESS.is_file():
            raise Blocked(f"role harness {HARNESS} is missing")
        component = self.component(target)
        parameters = dict(target["profile"]["value"].get("parameters", {}))
        unknown = parameters.keys() - component.get("parameters", {}).keys()
        if unknown:
            raise Unsupported(
                f"{component['id']} has no parameters {sorted(unknown)}"
            )
        for name, value in sorted(parameters.items()):
            self.check_range(component["parameters"][name], name, value)
        capabilities = {
            namespaced_capability(name) for name in component.get("capabilities", [])
        } | self.ADAPTER_CAPABILITIES
        data_words = int(parameters.get(
            "data_words", component["parameters"]["data_words"]["default"]
        ))
        return Description(
            adapter=self.adapter_id,
            target_class=self.target_class,
            capabilities=frozenset(capabilities),
            measurement_domain="org.atomix.domain.model-cycles",
            tools={"verilator": tool_version("verilator")},
            limits={
                # Three buffers share the flat global memory, and the kernel
                # reaches the far end of it through a sign-extended immediate.
                "max_items": min(data_words // 3, IMMEDIATE_MAXIMUM // 2),
                "immediate_minimum": IMMEDIATE_MINIMUM,
                "immediate_maximum": IMMEDIATE_MAXIMUM,
                "program_words": PROGRAM_WORDS,
                "data_words": data_words,
            },
        )

    def fingerprint(self, implementation: dict[str, Any], target: dict[str, Any],
                    cases: list[dict[str, Any]]) -> dict[str, Any] | None:
        if implementation["build"]["kind"] != "org.atomix.gpu-compute-program":
            return None
        try:
            component = self.component(target)
        except (Blocked, Unsupported):
            return None
        sources = {
            source: sha256_file(ROOT / source) for source in component["sources"]
            if (ROOT / source).is_file()
        }
        if len(sources) != len(component["sources"]) or not HARNESS.is_file() or \
                not ENCODER.is_file():
            return None
        return {
            "component": component["id"],
            "sources": sources,
            "harness_sha256": sha256_file(HARNESS),
            "encoder_sha256": sha256_file(ENCODER),
            "profile": target["profile"]["value"],
            "verilator": tool_version("verilator"),
        }

    def check_range(self, spec: dict[str, Any], name: str, value: Any) -> None:
        """Enforce whatever range the component declares about its own knob.

        The range lives in the component manifest because the component owns
        it: the engine's lane count is bounded by its own indexing, not by any
        experiment that happens to sweep it. A manifest that declares no range
        is not second-guessed here.
        """
        if not isinstance(value, int) or isinstance(value, bool):
            raise Unsupported(f"parameter {name} must be an integer, not {value!r}")
        limits = spec.get("range")
        if not limits:
            return
        if "minimum" in limits and value < limits["minimum"]:
            raise Unsupported(
                f"{name}={value} is below the component's minimum of {limits['minimum']}"
            )
        if "maximum" in limits and value > limits["maximum"]:
            raise Unsupported(
                f"{name}={value} is above the component's maximum of {limits['maximum']}"
            )
        if limits.get("power_of_two") and (value <= 0 or value & (value - 1)):
            raise Unsupported(f"{name}={value} is not a power of two")

    def kernel(self, encoder, items: int, a: int) -> list[int]:
        """SAXPY as nine straight-line SIMT instructions, one thread per element."""
        return [
            encoder.gpu_insn(encoder.GPU_TID, rd=0),               # r0 = tid
            encoder.gpu_insn(encoder.GPU_LDX, rd=1, ra=0),         # r1 = x[tid]
            encoder.gpu_insn(encoder.GPU_ADDI, rd=2, ra=0, imm=items),
            encoder.gpu_insn(encoder.GPU_LDX, rd=3, ra=2),         # r3 = y[tid]
            encoder.gpu_insn(encoder.GPU_MULI, rd=1, ra=1, imm=a),  # r1 = a*x[tid]
            encoder.gpu_insn(encoder.GPU_ADD, rd=1, ra=1, rb=3),   # r1 += y[tid]
            encoder.gpu_insn(encoder.GPU_ADDI, rd=4, ra=0, imm=2 * items),
            encoder.gpu_insn(encoder.GPU_STX, ra=4, rb=1),         # out[tid] = r1
            encoder.gpu_insn(encoder.GPU_HALT),
        ]

    def prepare(self, implementation: dict[str, Any], target: dict[str, Any],
                workdir: Path, cases: list[dict[str, Any]], *,
                limit_seconds: float, cancel_after: float | None = None) -> Prepared:
        build = implementation["build"]
        if build["kind"] != "org.atomix.gpu-compute-program":
            raise Unsupported(f"{self.adapter_id} cannot build {build['kind']}")
        description = self.describe(target)
        limits = description.limits
        encoder = load_encoder()

        # Semantics this target cannot honour are refused now, with the reason,
        # rather than producing a plausible wrong answer from a truncated
        # immediate. A wider `a` needs a different kernel, not a bigger claim.
        programs: dict[str, list[int]] = {}
        for case in cases:
            items = int(case["parameters"]["items"])
            a = int(case["parameters"]["a"])
            if not IMMEDIATE_MINIMUM <= a <= IMMEDIATE_MAXIMUM:
                raise Unsupported(
                    f"case {case['name']}: a={a} does not fit the engine's 17-bit "
                    "signed immediate"
                )
            if items > limits["max_items"]:
                raise Unsupported(
                    f"case {case['name']}: {items} items exceed this target's "
                    f"limit of {limits['max_items']}"
                )
            words = self.kernel(encoder, items, a)
            if len(words) > PROGRAM_WORDS:
                raise Unsupported("kernel is longer than the engine's program memory")
            programs[case["name"]] = words
            (workdir / f"program-{case['name']}.hex").write_text(
                "".join(f"{word:08x}\n" for word in words)
            )

        # The implementation's identity is the instruction words that actually
        # ran, per case -- not the generator that produced them and not the
        # model that hosted them.
        manifest = {
            "implementation": implementation["id"],
            "encoder": build["value"]["encoder"],
            "programs": {name: [f"{word:08x}" for word in words]
                         for name, words in programs.items()},
        }
        manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        artifact = workdir / "saxpy-simt-programs.json"
        artifact.write_text(manifest_text)
        program_bytes = 4 * sum(len(words) for words in programs.values())

        model, model_sha256 = self.build_model(
            target, workdir, limit_seconds=limit_seconds, cancel_after=cancel_after
        )
        return Prepared(
            artifact=artifact,
            artifact_sha256=sha256_bytes(manifest_text.encode()),
            artifact_bytes=program_bytes,
            build_sha256=sha256_json({
                "encoder_sha256": sha256_file(ENCODER),
                "programs": manifest["programs"],
            }),
            tools={"verilator": description.tools["verilator"]},
            target_build_sha256=model_sha256,
            profile_sha256=sha256_json(target["profile"]["value"]),
            detail={
                "model": str(model),
                "parameters": target["profile"]["value"].get("parameters", {}),
                "artifact_kind": "SIMT kernel instruction words",
            },
        )

    def warning_flags(self, version: str) -> list[str]:
        """Mirror the version guard the repository's Makefiles already use.

        Verilator 5 reports component-API package constants as UNUSEDPARAM and
        makes warnings fatal under -Wall; Verilator 4 does not recognise the
        warning name and fails on the flag itself. CI runs on 4.038, so
        hardcoding either answer breaks one of the two hosts.
        """
        digits = "".join(
            character for character in version.split()[1] if character.isdigit() or
            character == "."
        ) if len(version.split()) > 1 else ""
        major = digits.split(".")[0] if digits else ""
        return ["-Wall", "-Wno-UNUSEDPARAM"] if major.isdigit() and int(major) >= 5 \
            else ["-Wall"]

    def build_model(self, target: dict[str, Any], workdir: Path, *,
                    limit_seconds: float,
                    cancel_after: float | None = None) -> tuple[Path, str]:
        """Verilate the role from its manifest's own source list.

        The sources and top module come from the component manifest, so
        selecting a different role implementation is a profile change rather
        than an edit here.
        """
        component = self.component(target)
        profile = target["profile"]["value"]
        top = profile.get("top", component.get("stock_soc_module", "axrole"))
        parameters = dict(profile.get("parameters", {}))
        defines = []
        for name, value in sorted(parameters.items()):
            spec = component["parameters"][name]
            defines.append(f"+define+{spec['define']}={int(value)}")
        sources = [str(ROOT / source) for source in component["sources"]]
        missing = [source for source in sources if not Path(source).is_file()]
        if missing:
            raise Blocked(f"component sources are missing: {missing}")

        model_dir = workdir / "obj_role_saxpy"
        command = [
            "verilator", *self.warning_flags(tool_version("verilator")),
            "--cc", "-O2",
            "--top-module", top, "--Mdir", str(model_dir), "--exe", "--build",
            "-CFLAGS", "-std=c++17 -O2", "-o", "tb_role_saxpy",
            *defines, *sources, str(HARNESS),
        ]
        checked(
            run_bounded(command, limit_seconds, cwd=ROOT, cancel_after=cancel_after),
            f"verilator build of {component['id']}",
        )
        model = model_dir / "tb_role_saxpy"
        if not model.is_file():
            raise AdapterError("verilator reported success but produced no model")
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
            parameters = case["parameters"]
            case_path = workdir / f"case-{case['name']}.txt"
            case_path.write_text(case_file_text(
                int(parameters["items"]), int(parameters["a"]),
                case["inputs"]["x"], case["inputs"]["y"], 1,
            ))
            result = run_bounded(
                [str(model), "--input", str(case_path),
                 "--program", str(workdir / f"program-{case['name']}.hex"),
                 "--max-cycles", str(max_cycles)],
                limit_seconds, cwd=workdir, cancel_after=cancel_after,
            )
            elapsed_total += result.elapsed_seconds
            checked(result, f"model run of case {case['name']}")
            try:
                report = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise AdapterError(
                    f"model run of case {case['name']} printed no usable report: {exc}"
                ) from exc
            outcomes.append(CaseOutcome(
                name=case["name"],
                outputs={"out": [int(value) for value in report["out"]]},
                # Model cycles are deterministic, so one run is the measurement
                # and repeating it would only measure Verilator.
                repetitions=1,
                cycles={
                    "execute": int(report["execute_cycles"]),
                    "total": int(report["total_cycles"]),
                },
                detail={
                    "simulator_elapsed_ns": int(result.elapsed_seconds * 1e9),
                    "program_words": int(report["program_words"]),
                },
            ))
        return Execution(
            cases=outcomes, termination=TERMINATION_COMPLETED,
            elapsed_seconds=elapsed_total,
        )
