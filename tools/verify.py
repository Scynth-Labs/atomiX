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
        allowed = {"label", "cwd", "command", "timeout_seconds", "requires", "env"}
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
    if missing:
        message = f"missing required tools: {', '.join(missing)}"
        log_path.write_text(message + "\n", encoding="utf-8")
        print(f"<== [{stage_id}] BLOCKED: {message}", flush=True)
        return {
            "id": stage_id, "label": label, "status": "blocked", "exit_code": None,
            "duration_seconds": round(time.monotonic() - started, 3),
            "started_at": started_at, "completed_at": utc_now(),
            "log": display_path(log_path), "detail": message,
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
        "log": display_path(log_path),
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
        "schema": "org.atomix.verification-result.v1",
        "suite": suite_id,
        "started_at": utc_now(),
        "keep_going": keep_going,
        "status": "running",
        "stages": [],
    }
    write_summary(summary_path, summary)
    for stage_id in suites[suite_id]:
        result = run_stage(stage_id, document["stages"][stage_id], log_dir)
        summary["stages"].append(result)
        write_summary(summary_path, summary)
        if result["status"] != "passed" and not keep_going:
            break
    failed = [item for item in summary["stages"] if item["status"] != "passed"]
    summary["completed_at"] = utc_now()
    summary["status"] = "failed" if failed else "passed"
    write_summary(summary_path, summary)
    print(f"\nVerification suite {suite_id}: {summary['status'].upper()}")
    for item in summary["stages"]:
        print(f"  {item['status'].upper():7} {item['id']:<28} "
              f"{item['duration_seconds']:>9.3f}s")
    print(f"  summary: {display_path(summary_path)}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("validate")
    subparsers.add_parser("list")
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
