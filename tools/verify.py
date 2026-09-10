#!/usr/bin/env python3
"""Run manifest-defined atomiX verification suites with durable stage logs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "tests/verification-suites.json"
DEFAULT_LOG_ROOT = ROOT / "build/verification"
SCHEMA = "org.atomix.verification-suites.v1"


class ManifestError(ValueError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ManifestError(message)


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"{path}: {exc}") from exc
    require(isinstance(document, dict), f"{path}: root must be an object")
    require(set(document) == {"schema", "stages", "suites"},
            f"{path}: expected schema, stages, and suites")
    require(document["schema"] == SCHEMA,
            f"{path}: unsupported schema {document['schema']!r}")
    stages = document["stages"]
    suites = document["suites"]
    require(isinstance(stages, dict) and stages, f"{path}: stages must be non-empty")
    require(isinstance(suites, dict) and suites, f"{path}: suites must be non-empty")

    for stage_id, stage in stages.items():
        prefix = f"{path}: stage {stage_id!r}"
        require(isinstance(stage_id, str) and stage_id, f"{prefix}: invalid ID")
        require(isinstance(stage, dict), f"{prefix}: must be an object")
        allowed = {"label", "cwd", "command", "timeout_seconds", "requires",
                   "env", "profiles"}
        require(set(stage) <= allowed, f"{prefix}: unknown keys {set(stage) - allowed}")
        require(set(stage) >= {"label", "command", "timeout_seconds"},
                f"{prefix}: missing required keys")
        require(isinstance(stage["label"], str) and stage["label"],
                f"{prefix}: label must be non-empty")
        command = stage["command"]
        require(isinstance(command, list) and command and
                all(isinstance(arg, str) and arg for arg in command),
                f"{prefix}: command must be a non-empty string array")
        timeout = stage["timeout_seconds"]
        require(isinstance(timeout, int) and 1 <= timeout <= 14400,
                f"{prefix}: timeout must be 1..14400 seconds")
        cwd = stage.get("cwd", ".")
        require(isinstance(cwd, str) and cwd, f"{prefix}: cwd must be non-empty")
        resolved_cwd = (ROOT / cwd).resolve()
        require(resolved_cwd == ROOT or ROOT in resolved_cwd.parents,
                f"{prefix}: cwd escapes the repository")
        requires = stage.get("requires", [])
        require(isinstance(requires, list) and
                all(isinstance(item, str) and item for item in requires),
                f"{prefix}: requires must be a string array")
        env = stage.get("env", {})
        require(isinstance(env, dict) and
                all(isinstance(key, str) and isinstance(value, str)
                    for key, value in env.items()),
                f"{prefix}: env must map strings to strings")
        # Which machine this stage exercises.  A result that does not say what
        # it ran on is a claim about nothing in particular, so a stage that
        # boots a profile names it here and the runner records what that
        # profile resolved to at the time.
        profiles = stage.get("profiles", [])
        require(isinstance(profiles, list) and
                all(isinstance(item, str) and item for item in profiles),
                f"{prefix}: profiles must be a string array")
        for profile in profiles:
            require(not Path(profile).is_absolute() and ".." not in profile,
                    f"{prefix}: profile {profile!r} must be inside the repository")
            require((ROOT / profile).is_file(),
                    f"{prefix}: profile {profile!r} does not exist")

    for suite_id, stage_ids in suites.items():
        prefix = f"{path}: suite {suite_id!r}"
        require(isinstance(suite_id, str) and suite_id, f"{prefix}: invalid ID")
        require(isinstance(stage_ids, list) and stage_ids,
                f"{prefix}: must contain stages")
        require(all(isinstance(item, str) and item in stages for item in stage_ids),
                f"{prefix}: references an unknown stage")
        require(len(stage_ids) == len(set(stage_ids)),
                f"{prefix}: repeats a stage")
    ci_suites = {"ci-quick", "ci-unit", "ci-integration"}
    if ci_suites <= set(suites) and "nightly-integrated" in suites:
        ci_stages = set().union(*(set(suites[name]) for name in ci_suites))
        nightly_stages = set(suites["nightly-integrated"])
        missing = sorted(ci_stages - nightly_stages)
        require(not missing,
                f"{path}: nightly-integrated omits CI stages {missing}")
    return document


def expand(value: str) -> str:
    return os.path.expandvars(value.replace("{root}", str(ROOT)))


def stage_environment(stage: dict[str, Any]) -> dict[str, str]:
    environment = os.environ.copy()
    # Containers and service sessions commonly inherit a ccache temp path under
    # /run/user that is absent or read-only. Keep temporary compiler output in
    # the workspace while leaving CCACHE_DIR untouched, so hosted cache reuse
    # still works.
    ccache_temp = DEFAULT_LOG_ROOT / ".ccache-tmp"
    ccache_temp.mkdir(parents=True, exist_ok=True)
    environment.setdefault("CCACHE_TEMPDIR", str(ccache_temp))
    configured_cache = Path(environment.get(
        "CCACHE_DIR", str(Path.home() / ".cache/ccache")))
    probe = configured_cache / f".atomix-write-probe-{os.getpid()}"
    try:
        configured_cache.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        workspace_cache = DEFAULT_LOG_ROOT / ".ccache"
        workspace_cache.mkdir(parents=True, exist_ok=True)
        environment["CCACHE_DIR"] = str(workspace_cache)
    for key, value in stage.get("env", {}).items():
        environment[key] = expand(value)
    environment["ATOMIX_VERIFICATION"] = "1"
    return environment


# What the result was produced by.  A verification record that does not name
# its tools is not reproducible by anyone who has different ones, and the
# project has already been bitten once by a result whose machine was not what
# its label said -- see SDRAM gate 1 in docs/design-checklist.md.
VERSION_PROBES = {
    "verilator": ["verilator", "--version"],
    "yosys": ["yosys", "-V"],
    "riscv_gcc": ["riscv64-unknown-elf-gcc", "--version"],
    "clang": ["clang", "--version"],
    "qemu_riscv32": ["qemu-system-riscv32", "--version"],
    "make": ["make", "--version"],
    "node": ["node", "--version"],
}


def tool_version(command: list[str]) -> str | None:
    if shutil.which(command[0]) is None:
        return None
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    text = (result.stdout or result.stderr).strip().splitlines()
    return text[0].strip() if text else None


def environment_record() -> dict[str, Any]:
    """Tools, source revision, and host, recorded once for the whole suite."""
    def git(*args: str) -> str | None:
        try:
            result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                                    text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    dirty = git("status", "--porcelain")
    return {
        "tools": {name: tool_version(command)
                  for name, command in sorted(VERSION_PROBES.items())},
        "python": sys.version.split()[0],
        "platform": f"{os.uname().sysname} {os.uname().release} "
                    f"{os.uname().machine}",
        "git_revision": git("rev-parse", "HEAD"),
        # Recorded rather than refused: running a suite against a working tree
        # is the normal case. It is the reader who needs to know the result
        # does not correspond to a commit.
        "git_worktree_clean": (dirty == "") if dirty is not None else None,
    }


def resolve_profiles(stage: dict[str, Any]) -> list[dict[str, Any]]:
    """What each profile this stage names actually resolves to, right now.

    A profile is a list of component names; what gets built is what the
    resolver makes of those names plus every manifest it reads. Recording the
    resolved identities is what lets a later reader tell whether a result was
    produced by the machine its label claims.
    """
    records = []
    for profile in stage.get("profiles", []):
        entry: dict[str, Any] = {"profile": profile}
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/configure.py"), "resolve",
             "--config", str(ROOT / profile)],
            cwd=ROOT, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            entry["status"] = "unresolvable"
            entry["detail"] = (result.stderr.strip().splitlines() or
                               ["profile does not resolve"])[-1]
            records.append(entry)
            continue
        resolved = {}
        for line in result.stdout.splitlines():
            if ":=" in line:
                key, _, value = line.partition(":=")
                resolved[key.strip()] = value.strip()
        entry["status"] = "resolved"
        for key, field in (("COMPONENT_CONFIG_NAME", "name"),
                           ("COMPONENT_CORE_ID", "core"),
                           ("COMPONENT_MEMORY_ID", "memory"),
                           ("COMPONENT_CACHE_ID", "cache"),
                           ("COMPONENT_ROLE_ID", "role"),
                           ("COMPONENT_HARNESS_ID", "harness"),
                           ("COMPONENT_SIM_TOP", "sim_top"),
                           ("COMPONENT_BOARD_ID", "board"),
                           ("COMPONENT_SCHEDULER_ID", "scheduler"),
                           ("COMPONENT_DEFINES", "defines")):
            if key in resolved:
                entry[field] = resolved[key]
        settings = {key[len("COMPONENT_SETTING_"):].lower(): value
                    for key, value in resolved.items()
                    if key.startswith("COMPONENT_SETTING_")}
        if settings:
            entry["settings"] = settings
        records.append(entry)
    return records


def missing_requirements(stage: dict[str, Any]) -> list[str]:
    return [name for name in stage.get("requires", []) if shutil.which(expand(name)) is None]


def stop_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()


def run_stage(stage_id: str, stage: dict[str, Any], log_dir: Path) -> dict[str, Any]:
    label = stage["label"]
    timeout = stage["timeout_seconds"]
    command = [expand(arg) for arg in stage["command"]]
    cwd = (ROOT / stage.get("cwd", ".")).resolve()
    log_path = log_dir / f"{stage_id}.log"
    missing = missing_requirements(stage)
    started_at = utc_now()
    started = time.monotonic()

    print(f"\n==> [{stage_id}] {label}", flush=True)
    print(f"    cwd={cwd.relative_to(ROOT) if cwd != ROOT else '.'}", flush=True)
    print(f"    command={' '.join(command)}", flush=True)
    machines = resolve_profiles(stage)
    unresolvable = [item["profile"] for item in machines
                    if item["status"] != "resolved"]
    for item in machines:
        detail = (f" -- {item['detail']}" if item["status"] != "resolved"
                  else f" ({item.get('name', '?')}: core={item.get('core', '-')} "
                       f"memory={item.get('memory', '-')} "
                       f"harness={item.get('harness', '-')})")
        print(f"    machine={item['profile']} {item['status']}{detail}",
              flush=True)
    if unresolvable:
        # A stage cannot pass for a configuration that does not exist. Running
        # the command anyway would produce a result labelled with a machine
        # nothing could have built.
        message = ("profiles do not resolve: " + ", ".join(unresolvable))
        log_path.write_text(message + "\n", encoding="utf-8")
        print(f"<== [{stage_id}] FAILED: {message}", flush=True)
        return {
            "id": stage_id, "label": label, "status": "failed", "exit_code": None,
            "duration_seconds": round(time.monotonic() - started, 3),
            "started_at": started_at, "completed_at": utc_now(),
            "log": display_path(log_path), "detail": message,
            "machines": machines,
        }
    if missing:
        message = f"missing required tools: {', '.join(missing)}"
        log_path.write_text(message + "\n", encoding="utf-8")
        print(f"<== [{stage_id}] BLOCKED: {message}", flush=True)
        return {
            "id": stage_id, "label": label, "status": "blocked", "exit_code": None,
            "duration_seconds": round(time.monotonic() - started, 3),
            "started_at": started_at, "completed_at": utc_now(),
            "log": display_path(log_path), "detail": message,
            "machines": machines,
        }

    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"stage: {stage_id}\nlabel: {label}\ncwd: {cwd}\n")
        log.write(f"command: {json.dumps(command)}\nstarted_at: {started_at}\n\n")
        log.flush()
        try:
            proc = subprocess.Popen(
                command, cwd=cwd, env=stage_environment(stage),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                bufsize=1, start_new_session=True,
            )
        except OSError as exc:
            log.write(f"cannot start command: {exc}\n")
            duration = round(time.monotonic() - started, 3)
            print(f"<== [{stage_id}] FAILED: cannot start command: {exc}", flush=True)
            return {
                "id": stage_id, "label": label, "status": "failed",
                "exit_code": None, "duration_seconds": duration,
                "started_at": started_at, "completed_at": utc_now(),
                "log": display_path(log_path), "detail": str(exc),
            }

        def pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                log.write(line)
                log.flush()

        output_thread = threading.Thread(target=pump, daemon=True)
        output_thread.start()
        timed_out = False
        try:
            exit_code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            stop_process(proc)
            exit_code = proc.returncode
        except KeyboardInterrupt:
            stop_process(proc)
            raise
        finally:
            output_thread.join(timeout=10)

    duration = round(time.monotonic() - started, 3)
    status = "timeout" if timed_out else ("passed" if exit_code == 0 else "failed")
    print(f"<== [{stage_id}] {status.upper()} in {duration:.3f}s", flush=True)
    return {
        "id": stage_id, "label": label, "status": status,
        "exit_code": exit_code, "duration_seconds": duration,
        "started_at": started_at, "completed_at": utc_now(),
        "log": display_path(log_path), "machines": machines,
    }


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def run_suite(document: dict[str, Any], suite_id: str, keep_going: bool,
              log_root: Path) -> int:
    suites = document["suites"]
    if suite_id not in suites:
        raise ManifestError(f"unknown suite {suite_id!r}")
    log_dir = log_root / suite_id
    log_dir.mkdir(parents=True, exist_ok=True)
    summary_path = log_dir / "summary.json"
    summary: dict[str, Any] = {
        "schema": "org.atomix.verification-result.v2",
        "suite": suite_id,
        "started_at": utc_now(),
        "keep_going": keep_going,
        "status": "running",
        "environment": environment_record(),
        "requested_stages": list(suites[suite_id]),
        "stages": [],
    }
    write_summary(summary_path, summary)
    for stage_id in suites[suite_id]:
        result = run_stage(stage_id, document["stages"][stage_id], log_dir)
        summary["stages"].append(result)
        write_summary(summary_path, summary)
        if result["status"] != "passed" and not keep_going:
            break
    # Stages the suite asked for and never reached.  Without these the summary
    # is a shorter list that looks complete: a suite that stopped at stage 3 of
    # 10 recorded three results and a failure, and nothing said the other seven
    # were never attempted.  They are not passes, and they are not failures
    # either -- they are unverified, and they say so.
    attempted = {item["id"] for item in summary["stages"]}
    for stage_id in suites[suite_id]:
        if stage_id in attempted:
            continue
        summary["stages"].append({
            "id": stage_id, "label": document["stages"][stage_id]["label"],
            "status": "not-run", "exit_code": None, "duration_seconds": 0.0,
            "started_at": None, "completed_at": None, "log": None,
            "detail": "an earlier stage did not pass and --keep-going was not "
                      "given, so this was never attempted",
            "machines": [],
        })
    counts: dict[str, int] = {}
    for item in summary["stages"]:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    failed = [item for item in summary["stages"] if item["status"] != "passed"]
    summary["completed_at"] = utc_now()
    summary["counts"] = counts
    summary["status"] = "failed" if failed else "passed"
    write_summary(summary_path, summary)
    print(f"\nVerification suite {suite_id}: {summary['status'].upper()}")
    for item in summary["stages"]:
        print(f"  {item['status'].upper():8} {item['id']:<28} "
              f"{item['duration_seconds']:>9.3f}s")
    # Every outcome, named, so "passed" is never read off a count of passes
    # against a list whose length changed.
    print("  outcomes: " + ", ".join(f"{name}={count}"
                                     for name, count in sorted(counts.items())))
    print(f"  summary: {display_path(summary_path)}")
    return 1 if failed else 0


def self_test(log_root: Path) -> int:
    """The two ways a suite could lie, checked against a synthetic manifest.

    A run that cannot happen must not be recorded as one that happened and
    passed.  There are two shapes of that here -- a stage whose tool is not
    installed, and a stage naming a configuration that does not resolve -- and
    a third that is subtler: the stages a suite asked for and never reached,
    which used to vanish from the summary rather than be reported unverified.
    """
    scratch = log_root / "selftest"
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    rel = display_path(scratch)
    marker = scratch / "the-command-ran"

    broken = scratch / "broken-profile.json"
    broken.write_text(json.dumps({
        "schema": 1, "name": "selftest-broken",
        "components": {"core": "core.does-not-exist"},
    }) + "\n", encoding="utf-8")

    manifest = {
        "schema": SCHEMA,
        "stages": {
            "wrong-profile": {
                "label": "names a configuration that does not resolve",
                "command": ["touch", str(marker)],
                "profiles": [f"{rel}/broken-profile.json"],
                "timeout_seconds": 60,
            },
            "missing-tool": {
                "label": "needs a tool that is not installed",
                "command": ["true"],
                "requires": ["atomix-tool-that-does-not-exist"],
                "timeout_seconds": 60,
            },
            "real-machine": {
                "label": "names a configuration that does resolve",
                "command": ["true"],
                "profiles": ["configs/sim-bram.json"],
                "timeout_seconds": 60,
            },
            "never-reached": {
                "label": "the suite asked for this and never got to it",
                "command": ["true"],
                "timeout_seconds": 60,
            },
        },
        "suites": {"selftest": ["real-machine", "missing-tool", "wrong-profile",
                                "never-reached"]},
    }
    manifest_path = scratch / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n",
                             encoding="utf-8")
    document = load_manifest(manifest_path)

    print("[verify-selftest] running a suite designed to go wrong\n")
    run_suite(document, "selftest", keep_going=True, log_root=scratch)
    summary = json.loads((scratch / "selftest/summary.json").read_text())
    by_id = {item["id"]: item for item in summary["stages"]}

    problems = []
    if summary["status"] != "failed":
        problems.append(f"the suite reported {summary['status']!r}, not failed")
    if by_id["missing-tool"]["status"] != "blocked":
        problems.append("a missing tool did not block its stage: "
                        f"{by_id['missing-tool']['status']}")
    if by_id["wrong-profile"]["status"] != "failed":
        problems.append("a configuration that does not resolve did not fail "
                        f"its stage: {by_id['wrong-profile']['status']}")
    if marker.exists():
        problems.append("the stage naming an unresolvable configuration ran "
                        "its command anyway, so its result would have been "
                        "labelled with a machine nothing could build")
    real = by_id["real-machine"]
    if real["status"] != "passed":
        problems.append(f"the resolvable stage did not pass: {real['status']}")
    machines = {item["profile"]: item for item in real["machines"]}
    recorded = machines.get("configs/sim-bram.json", {})
    if recorded.get("memory") != "memory.bram" or recorded.get("core") != "core.pipeline5":
        problems.append("the passing stage did not record the machine it ran "
                        f"on: {recorded}")

    # And the third shape, with keep-going off: what was never attempted has to
    # survive into the summary as its own outcome.
    run_suite(document, "selftest", keep_going=False, log_root=scratch)
    stopped = json.loads((scratch / "selftest/summary.json").read_text())
    stopped_by_id = {item["id"]: item for item in stopped["stages"]}
    if set(stopped_by_id) != set(manifest["suites"]["selftest"]):
        problems.append("stopping early dropped stages from the summary "
                        "instead of recording them: "
                        f"{sorted(set(manifest['suites']['selftest']) - set(stopped_by_id))}")
    elif stopped_by_id["never-reached"]["status"] != "not-run":
        problems.append("a stage that was never attempted is recorded as "
                        f"{stopped_by_id['never-reached']['status']!r}")
    if stopped.get("counts", {}).get("passed", 0) == len(stopped["stages"]):
        problems.append("the outcome counts read as an all-pass run")

    print()
    for problem in problems:
        print(f"[verify-selftest] FAIL {problem}")
    if problems:
        return 1
    print("[verify-selftest] PASS: a missing tool blocks, an unresolvable "
          "configuration fails before its command runs, a passing stage "
          "records the machine it ran on, and stages never attempted are "
          "reported as not-run rather than dropped")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("validate")
    subparsers.add_parser("list")
    selftest_parser = subparsers.add_parser("self-test")
    selftest_parser.add_argument("--log-root", type=Path,
                                 default=DEFAULT_LOG_ROOT)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("suite")
    run_parser.add_argument("--keep-going", action="store_true")
    run_parser.add_argument("--log-root", type=Path, default=DEFAULT_LOG_ROOT)
    args = parser.parse_args()

    try:
        document = load_manifest(args.manifest.resolve())
        if args.action == "validate":
            print(f"verification manifest: PASS ({len(document['stages'])} stages, "
                  f"{len(document['suites'])} suites)")
            return 0
        if args.action == "self-test":
            return self_test(args.log_root.resolve())
        if args.action == "list":
            for suite_id, stages in document["suites"].items():
                print(f"{suite_id:<22} {len(stages):>2} stages  " + " ".join(stages))
            return 0
        return run_suite(document, args.suite, args.keep_going,
                         args.log_root.resolve())
    except ManifestError as exc:
        print(f"verification manifest: ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
