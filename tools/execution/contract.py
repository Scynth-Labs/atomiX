"""The execution-target boundary shared by every atomiX experiment adapter.

An adapter answers four questions about one kind of machine, and nothing else
in the platform needs to know how it does so:

1. `describe`  what can this target do, with which tools, under what limits?
2. `prepare`   turn an implementation into a hashed artifact using its own
               toolchain, and report the identities a replay would need.
3. `execute`   run bounded work and hand back exact outputs and measurements.
4. `identity`  everything above, recorded, so a later run can be compared to
               this one rather than merely resembling it.

The rules the boundary enforces are the ones that make a comparison honest:

- A missing tool or device is a *blocker*, not a fallback. No adapter may
  quietly substitute another target when its own is unavailable.
- Semantics a target cannot honour are refused before execution, so an
  unsupported case never turns into a plausible wrong answer.
- Every measurement carries the domain it was taken in. Host elapsed time,
  simulator wall time, and model cycles never merge into one number here,
  because nothing downstream could separate them again.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[2]

TERMINATION_COMPLETED = "org.atomix.completed"
TERMINATION_TIMEOUT = "org.atomix.timeout"
TERMINATION_CANCELLED = "org.atomix.cancelled"
TERMINATION_BLOCKED = "org.atomix.blocked"


class AdapterError(Exception):
    """Base class for every failure an adapter reports about itself."""


class Blocked(AdapterError):
    """A prerequisite is absent: a tool, a device, or a built artifact."""


class Unsupported(AdapterError):
    """The target cannot honour these semantics, and says so before running."""


class Timeout(AdapterError):
    """Bounded work exceeded its limit and the adapter terminated it."""


class Cancelled(AdapterError):
    """Work was cancelled and the adapter terminated what it owned."""


class StaleArtifact(AdapterError):
    """A replay's inputs no longer hash to what the record was made from."""


@dataclass(frozen=True)
class Description:
    """What a target advertises before anything is built or run."""

    adapter: str
    target_class: str
    capabilities: frozenset[str]
    measurement_domain: str
    tools: dict[str, str]
    limits: dict[str, Any]


@dataclass(frozen=True)
class Prepared:
    """A built implementation and the identities a replay would need."""

    artifact: Path
    artifact_sha256: str
    artifact_bytes: int
    build_sha256: str
    tools: dict[str, str]
    target_build_sha256: str | None = None
    profile_sha256: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CaseOutcome:
    """One workload case as this target actually executed it."""

    name: str
    outputs: dict[str, list[int]]
    repetitions: int
    cycles: dict[str, int] = field(default_factory=dict)
    elapsed_ns: list[int] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Execution:
    """Everything one candidate's run produced, including how it ended."""

    cases: list[CaseOutcome]
    termination: str
    elapsed_seconds: float


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    termination: str


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def tool_version(executable: str, argument: str = "--version") -> str:
    """Identify a tool, or raise Blocked if it is not installed.

    A version string is part of a record's identity, so a tool that cannot be
    identified is treated as absent rather than recorded as an empty string.
    """
    resolved = shutil.which(executable)
    if resolved is None:
        raise Blocked(f"{executable} is not on PATH")
    try:
        completed = subprocess.run(
            [resolved, argument], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Blocked(f"{executable} could not be identified: {exc}") from exc
    text = (completed.stdout or completed.stderr).strip().splitlines()
    if not text:
        raise Blocked(f"{executable} reported no version")
    return text[0].strip()


def run_bounded(command: Sequence[str], limit_seconds: float, *,
                cwd: Path | None = None, cancel_after: float | None = None,
                env: dict[str, str] | None = None) -> CommandResult:
    """Run a command under a wall-clock bound this process actually enforces.

    The child gets its own session so a timeout or cancellation reaches the
    whole process group: a Verilator build spawns make, which spawns compilers,
    and killing only the direct child would leave those running while this
    adapter reported the work terminated.  Cancellation asks first (SIGTERM)
    and the timeout does not (SIGKILL), which is the difference between
    stopping work and abandoning it.
    """
    started = time.monotonic()
    process = subprocess.Popen(
        list(command), cwd=str(cwd) if cwd else None, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    cancelled = threading.Event()

    def cancel() -> None:
        cancelled.set()
        _signal_group(process, signal.SIGTERM)

    timer = threading.Timer(cancel_after, cancel) if cancel_after else None
    if timer:
        timer.daemon = True
        timer.start()
    try:
        stdout, stderr = process.communicate(timeout=limit_seconds)
        termination = (
            TERMINATION_CANCELLED if cancelled.is_set() else TERMINATION_COMPLETED
        )
    except subprocess.TimeoutExpired:
        _signal_group(process, signal.SIGKILL)
        stdout, stderr = process.communicate()
        termination = TERMINATION_TIMEOUT
    finally:
        if timer:
            timer.cancel()
    return CommandResult(
        command=list(command), returncode=process.returncode,
        stdout=stdout or "", stderr=stderr or "",
        elapsed_seconds=time.monotonic() - started, termination=termination,
    )


def _signal_group(process: subprocess.Popen, number: int) -> None:
    try:
        os.killpg(os.getpgid(process.pid), number)
    except (ProcessLookupError, PermissionError):
        process.send_signal(number)


def checked(result: CommandResult, what: str) -> CommandResult:
    """Turn a bounded command's outcome into the adapter's own vocabulary."""
    if result.termination == TERMINATION_TIMEOUT:
        raise Timeout(f"{what} exceeded its time limit")
    if result.termination == TERMINATION_CANCELLED:
        raise Cancelled(f"{what} was cancelled")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise AdapterError(
            f"{what} failed with status {result.returncode}"
            + (f": {detail[-1]}" if detail else "")
        )
    return result


def namespaced_capability(raw: str) -> str:
    """Map a component manifest's capability name into the shared namespace.

    Manifests were written before this contract existed and spell capabilities
    locally ("rv32im", "m/s/u").  Rewriting them would churn every component
    for no gain, so the mapping happens here and stays reversible.
    """
    slug = raw.strip().lower().replace("/", "-").replace("_", "-").replace(" ", "-")
    return f"org.atomix.capability.{slug}"


def case_file_text(items: int, a: int, x: Sequence[int], y: Sequence[int],
                   repetitions: int) -> str:
    """The saxpy-i32 exchange format both legs read.

    Deliberately trivial to parse in C: the native executable and the RTL
    harness must agree on the same logical inputs without either of them
    linking a JSON library or trusting the other's byte order.
    """
    lines = [
        f"items {items}",
        f"a {a}",
        f"repetitions {repetitions}",
        "x " + " ".join(str(int(value)) for value in x),
        "y " + " ".join(str(int(value)) for value in y),
    ]
    return "\n".join(lines) + "\n"


class Adapter:
    """One execution target's mechanics, behind one small interface."""

    adapter_id: str = ""
    target_class: str = ""

    def describe(self, target: dict[str, Any]) -> Description:
        raise NotImplementedError

    def prepare(self, implementation: dict[str, Any], target: dict[str, Any],
                workdir: Path, *, limit_seconds: float,
                cancel_after: float | None = None) -> Prepared:
        raise NotImplementedError

    def execute(self, prepared: Prepared, target: dict[str, Any],
                cases: list[dict[str, Any]], workdir: Path, *,
                limit_seconds: float, repetitions: int,
                cancel_after: float | None = None) -> Execution:
        raise NotImplementedError

    def check_compatibility(self, description: Description,
                            implementation: dict[str, Any],
                            target: dict[str, Any]) -> None:
        """Refuse work the target cannot host, before anything is built.

        Two separate claims are checked.  The plan's declared target
        capabilities must be ones this adapter actually discovered -- a plan
        cannot grant a target an ability by writing it down.  The
        implementation's requirements must then be met by those capabilities.
        """
        declared = set(target["capabilities"])
        undiscovered = declared - description.capabilities
        if undiscovered:
            raise Unsupported(
                f"{target['id']} declares capabilities this adapter did not find: "
                f"{sorted(undiscovered)}"
            )
        missing = set(implementation["requires"]) - description.capabilities
        if missing:
            raise Unsupported(
                f"{target['id']} cannot provide {sorted(missing)} required by "
                f"{implementation['id']}"
            )
