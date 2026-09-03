#!/usr/bin/env python3
"""Resolve an atomiX component configuration without third-party packages.

Manifests intentionally carry only enough information to compose a build:
their type, source files, and optional build defaults.  They are not a
description language for an implementation's microarchitecture.  A component
outside this repository can be selected by manifest path, so DIY work never
has to be copied into the atomiX tree merely to try it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "components"
SCHEMA = 1
# Kinds are intentionally not an exhaustive registry: a configuration may
# include an experimental kind for a custom SoC or harness, and the resolver
# will pass it through as COMPONENT_<KIND>_* variables.
SAFE_COMPONENT_KIND = re.compile(r"^[a-z][a-z0-9_]*$")
SAFE_MAKE_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
SAFE_PARAM_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
SAFE_DEFINE_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


class ConfigError(Exception):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ConfigError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: expected a JSON object")
    return value


def validate_manifest(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    for key in ("schema", "id", "kind", "title"):
        if key not in value:
            raise ConfigError(f"{path}: manifest is missing '{key}'")
    if value["schema"] != SCHEMA:
        raise ConfigError(f"{path}: unsupported component schema {value['schema']!r}")
    if not isinstance(value["id"], str) or not value["id"]:
        raise ConfigError(f"{path}: component id must be a non-empty string")
    if not isinstance(value["kind"], str) or not SAFE_COMPONENT_KIND.fullmatch(value["kind"]):
        raise ConfigError(
            f"{path}: component kind must use lowercase letters, digits, and underscores")
    if "sources" in value and (not isinstance(value["sources"], list) or
                              not all(isinstance(item, str) for item in value["sources"])):
        raise ConfigError(f"{path}: sources must be a list of strings")
    if "make" in value and not isinstance(value["make"], dict):
        raise ConfigError(f"{path}: make must be an object")
    if "parameters" in value:
        params = value["parameters"]
        if not isinstance(params, dict):
            raise ConfigError(f"{path}: parameters must be an object")
        for name, spec in params.items():
            if not isinstance(name, str) or not SAFE_PARAM_NAME.fullmatch(name):
                raise ConfigError(
                    f"{path}: parameter name {name!r} must be lowercase "
                    "letters, digits, and underscores")
            if not isinstance(spec, dict) or "default" not in spec or "define" not in spec:
                raise ConfigError(
                    f"{path}: parameter {name!r} needs 'default' and 'define'")
            if not isinstance(spec["define"], str) or \
                    not SAFE_DEFINE_NAME.fullmatch(spec["define"]):
                raise ConfigError(
                    f"{path}: parameter {name!r} define must be an uppercase macro name")
            if not isinstance(spec["default"], int) or isinstance(spec["default"], bool):
                raise ConfigError(
                    f"{path}: parameter {name!r} default must be an integer")
            if "omit_when_zero" in spec and not isinstance(
                    spec["omit_when_zero"], bool):
                raise ConfigError(
                    f"{path}: parameter {name!r} omit_when_zero must be true or false")
    if "defaults" in value:
        defaults = value["defaults"]
        if (not isinstance(defaults, dict) or
                not all(isinstance(kind, str) and SAFE_COMPONENT_KIND.fullmatch(kind) and
                        isinstance(default, str) and default
                        for kind, default in defaults.items())):
            raise ConfigError(f"{path}: defaults must map component kinds to component ids")
    value = dict(value)
    value["_path"] = path.resolve()
    return value


def catalog() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(CATALOG.glob("**/component.json")):
        component = validate_manifest(path, read_json(path))
        if component["id"] in result:
            raise ConfigError(f"duplicate component id {component['id']!r}")
        result[component["id"]] = component
    return result


def component_from_selection(selection: Any, known: dict[str, dict[str, Any]],
                             config_path: Path) -> dict[str, Any]:
    if isinstance(selection, str):
        try:
            return known[selection]
        except KeyError as exc:
            raise ConfigError(f"{config_path}: unknown component id {selection!r}") from exc
    if not isinstance(selection, dict) or set(selection) != {"manifest"}:
        raise ConfigError(
            f"{config_path}: a component selection must be an id string or "
            "{\"manifest\": \"path/to/component.json\"}")
    manifest = selection["manifest"]
    if not isinstance(manifest, str):
        raise ConfigError(f"{config_path}: manifest path must be a string")
    path = Path(manifest)
    if not path.is_absolute():
        path = (config_path.parent / path).resolve()
    return validate_manifest(path, read_json(path))


def resolve_source(component: dict[str, Any], source: str) -> Path:
    path = Path(source)
    if path.is_absolute():
        return path
    # Built-in manifests use repository-relative paths.  External manifests
    # use paths beside their manifest, making a component folder portable.
    if component["_path"].is_relative_to(CATALOG):
        return ROOT / path
    return component["_path"].parent / path


def make_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return " ".join(value)
    raise ConfigError(f"unsupported Make value {value!r}")


def safe_make_value(value: Any) -> str:
    rendered = make_value(value)
    if any(char in rendered for char in "\n\r$#"):
        raise ConfigError("Make values may not contain newline, '$', or '#'")
    return rendered


def resolved_config(path: Path) -> dict[str, Any]:
    config = read_json(path)
    if config.get("schema") != SCHEMA:
        raise ConfigError(f"{path}: configuration schema must be {SCHEMA}")
    if not isinstance(config.get("name"), str) or not config["name"]:
        raise ConfigError(f"{path}: configuration needs a non-empty name")
    selections = config.get("components")
    if not isinstance(selections, dict):
        raise ConfigError(f"{path}: configuration needs a components object")
    known = catalog()
    selected: dict[str, dict[str, Any]] = {}
    for kind, selection in selections.items():
        component = component_from_selection(selection, known, path)
        if component["kind"] != kind:
            raise ConfigError(
                f"{path}: selected {component['id']!r} for {kind}, but it is a {component['kind']}")
        selected[kind] = component
    # Fill unselected kinds from the selected components' declared defaults
    # (the reference core defaults its ALU, mul/div, register file, and MMU),
    # so a profile only names the units it overrides.  Explicit selections
    # always win, a defaulted component's own defaults apply too, and two
    # components disagreeing about an unselected kind is an error the profile
    # must settle by selecting that kind explicitly.
    while True:
        wanted: dict[str, str] = {}
        for component in selected.values():
            for kind, default_id in component.get("defaults", {}).items():
                if kind in selected:
                    continue
                if kind in wanted and wanted[kind] != default_id:
                    raise ConfigError(
                        f"{path}: selected components default {kind!r} to both "
                        f"{wanted[kind]!r} and {default_id!r}; select one explicitly")
                wanted[kind] = default_id
        if not wanted:
            break
        for kind, default_id in wanted.items():
            component = component_from_selection(default_id, known, path)
            if component["kind"] != kind:
                raise ConfigError(
                    f"{path}: default {default_id!r} for {kind} is a "
                    f"{component['kind']}")
            selected[kind] = component
    settings = config.get("settings", {})
    if not isinstance(settings, dict):
        raise ConfigError(f"{path}: settings must be an object")
    parameters = config.get("parameters", {})
    if not isinstance(parameters, dict):
        raise ConfigError(f"{path}: parameters must be an object")
    return {"path": path.resolve(), "raw": config, "components": selected,
            "settings": settings, "parameters": parameters}


# Every setting a profile may name, with the bounds that make it usable.
#
# Settings used to be free-form: an unrecognised key became a make variable
# nothing read, so `task_slot` instead of `task_slots` silently kept the
# default and the profile appeared to work.  That is the failure mode a
# configurable system invites, and the answer is not to stop making things
# configurable but to make an unknown or out-of-range setting say so.
#
# Bounds here are the coarse "this cannot possibly be right" limits.  The
# relationships *between* settings are checked where both are visible --
# _Static_assert in the headers that define them -- because that is the only
# place that knows them.
SETTINGS: dict[str, dict[str, Any]] = {
    # System sizing.
    "ram_bytes": {"type": int, "min": 4096, "max": 1 << 30},
    "caches": {"type": bool},
    "reset_pc": {"type": str},
    "kernel_mode": {"type": str, "choices": ("full", "monitor")},
    # Kernel-owned capacity. Component-owned ones (max_fds, path_max,
    # write_max, io_chunk, role_max_payload, the loader's arg_max) are
    # component `parameters`, not settings: the component that owns a
    # knob declares it, with its default and its documentation.
    "task_slots": {"type": int, "min": 2, "max": 64},
    # Cache geometry.
    "cache_lines": {"type": int, "min": 1, "max": 1 << 16},
    "cache_words_per_line": {"type": int, "min": 1, "max": 256},
    # Place-and-route.
    "pnr_seed": {"type": int, "min": 1, "max": 1 << 16},
    "pnr_extra_args": {"type": str},
}


def check_setting(path: Path, key: str, value: Any) -> None:
    spec = SETTINGS.get(key)
    if spec is None:
        near = [name for name in SETTINGS if name.startswith(key[:4])]
        hint = f" (did you mean {' or '.join(sorted(near))}?)" if near else ""
        raise ConfigError(
            f"{path}: unknown setting {key!r}{hint}. Known settings: "
            f"{', '.join(sorted(SETTINGS))}")
    expected = spec["type"]
    # bool is a subclass of int, so check it first and exactly.
    if expected is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"{path}: setting {key!r} must be true or false")
        return
    if expected is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{path}: setting {key!r} must be an integer")
        low, high = spec.get("min"), spec.get("max")
        if low is not None and value < low:
            raise ConfigError(
                f"{path}: setting {key!r} is {value}, below the minimum {low}")
        if high is not None and value > high:
            raise ConfigError(
                f"{path}: setting {key!r} is {value}, above the maximum {high}")
        return
    if expected is str:
        if not isinstance(value, str):
            raise ConfigError(f"{path}: setting {key!r} must be a string")
        choices = spec.get("choices")
        if choices is not None and value not in choices:
            raise ConfigError(
                f"{path}: setting {key!r} is {value!r}; expected one of "
                f"{', '.join(choices)}")


def source_list(component: dict[str, Any]) -> list[str]:
    sources: list[str] = []
    for source in component.get("sources", []):
        path = resolve_source(component, source).resolve()
        if not path.is_file():
            raise ConfigError(f"{component['_path']}: source does not exist: {path}")
        if any(char.isspace() for char in str(path)):
            raise ConfigError(f"source paths with whitespace are not supported: {path}")
        sources.append(str(path))
    return sources


def to_make(resolved: dict[str, Any]) -> str:
    lines = [
        "# Generated by tools/configure.py; do not edit.",
        f"COMPONENT_CONFIG_NAME := {safe_make_value(resolved['raw']['name'])}",
        f"COMPONENT_CONFIG_PATH := {safe_make_value(str(resolved['path']))}",
        "COMPONENT_SELECTED_MANIFESTS := " + safe_make_value([
            str(component["_path"]) for component in resolved["components"].values()
        ]),
    ]
    make_values: dict[str, Any] = {}
    for kind, component in sorted(resolved["components"].items()):
        prefix = f"COMPONENT_{kind.upper()}"
        lines.append(f"{prefix}_ID := {safe_make_value(component['id'])}")
        sources = source_list(component)
        if sources:
            lines.append(f"{prefix}_SOURCES := {safe_make_value(sources)}")
        for key, value in component.get("make", {}).items():
            upper = key.upper()
            if not SAFE_MAKE_NAME.fullmatch(upper):
                raise ConfigError(f"{component['_path']}: invalid Make key {key!r}")
            # Board constraints and software artifacts are implementation
            # assets. Resolve them relative to a portable external manifest
            # rather than the Makefile that happened to invoke the resolver.
            path_value_keys = {
                "FPGA_LPF", "FPGA_CST", "SOFTWARE_MAKE_DIR", "SOFTWARE_RAM_HEX",
                "SOFTWARE_ROM_HEX", "SOFTWARE_SD_IMAGE", "SOFTWARE_UART_INPUT",
                "KERNEL_CONFIG", "LIBC_INCLUDE",
            }
            if upper in path_value_keys:
                if not isinstance(value, str):
                    raise ConfigError(f"{component['_path']}: {key} must be a path string")
                asset = resolve_source(component, value).resolve()
                if upper in ("FPGA_LPF", "FPGA_CST") and not asset.is_file():
                    raise ConfigError(f"{component['_path']}: constraint file does not exist: {asset}")
                if upper == "SOFTWARE_MAKE_DIR" and not asset.is_dir():
                    raise ConfigError(f"{component['_path']}: software directory does not exist: {asset}")
                if upper == "KERNEL_CONFIG" and not asset.is_file():
                    raise ConfigError(f"{component['_path']}: kernel configuration does not exist: {asset}")
                if upper == "LIBC_INCLUDE" and not asset.is_dir():
                    raise ConfigError(f"{component['_path']}: libc include directory does not exist: {asset}")
                value = str(asset)
            full_key = f"COMPONENT_{upper}"
            if full_key in make_values and make_values[full_key] != value:
                raise ConfigError(f"conflicting component Make value {full_key}")
            make_values[full_key] = value
    # Tunable parameters become preprocessor defines.  A component declares
    # what it exposes and the default that defines its baseline; a profile
    # overrides by name, under the component's kind.  Anything a component did
    # not declare is a configuration error rather than a silent no-op -- the
    # same discipline that makes component selection validated rather than
    # hopeful.
    overrides = resolved["parameters"]
    for kind in overrides:
        if kind not in resolved["components"]:
            raise ConfigError(
                f"{resolved['path']}: parameters given for {kind!r}, which the "
                "configuration does not select")
    defines: list[str] = []
    for kind, component in sorted(resolved["components"].items()):
        declared = component.get("parameters", {})
        given = overrides.get(kind, {})
        if not isinstance(given, dict):
            raise ConfigError(f"{resolved['path']}: parameters.{kind} must be an object")
        for name in given:
            if name not in declared:
                known = ", ".join(sorted(declared)) or "none"
                raise ConfigError(
                    f"{resolved['path']}: {component['id']} has no parameter "
                    f"{name!r} (it declares: {known})")
        for name, spec in sorted(declared.items()):
            value = given.get(name, spec["default"])
            if not isinstance(value, int) or isinstance(value, bool):
                raise ConfigError(
                    f"{resolved['path']}: parameter {kind}.{name} must be an integer")
            # `omit_when_zero` exists because `ifdef` tests whether a macro is
            # defined, not what it is worth, and a parameter that always emits
            # `NAME=0` therefore cannot remove a module port or a port
            # connection -- only the logic behind them.  On a part near its
            # placement limit that distinction is the whole ballgame: gating
            # role.morph's telemetry producers by value still left 1,620 LUT4
            # of perturbation and cost the profile a legal placement, while
            # compiling the port out returns the netlist to what it was.
            # Parameters that only size or enable *logic* must not set this;
            # they stay always-defined so the RTL has one spelling to read.
            if value == 0 and spec.get("omit_when_zero"):
                continue
            defines.append(f"+define+{spec['define']}={value}")
    if defines:
        make_values["COMPONENT_DEFINES"] = " ".join(defines)

    settings = resolved["settings"]
    setting_names = {
        "ram_bytes": "COMPONENT_RAM_BYTES",
        "caches": "COMPONENT_CACHES",
        "reset_pc": "COMPONENT_RESET_PC",
    }
    for key, value in settings.items():
        check_setting(resolved["path"], key, value)
        output = setting_names.get(key, f"COMPONENT_SETTING_{key.upper()}")
        if not SAFE_MAKE_NAME.fullmatch(output):
            raise ConfigError(f"{resolved['path']}: invalid setting name {key!r}")
        make_values[output] = value
    for key, value in sorted(make_values.items()):
        lines.append(f"{key} := {safe_make_value(value)}")
    lines.append("")
    return "\n".join(lines)


def command_list(_: argparse.Namespace) -> int:
    for component in catalog().values():
        print(f"{component['id']:28} {component['kind']:8} {component['title']}")
    return 0


def command_describe(args: argparse.Namespace) -> int:
    known = catalog()
    try:
        component = known[args.component]
    except KeyError:
        raise ConfigError(f"unknown component id {args.component!r}")
    printable = {key: value for key, value in component.items() if key != "_path"}
    print(json.dumps(printable, indent=2, sort_keys=True))
    return 0


def command_resolve(args: argparse.Namespace) -> int:
    path = Path(args.config).resolve()
    output = to_make(resolved_config(path))
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(output)
    else:
        print(output, end="")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("list", help="list built-in components").set_defaults(func=command_list)
    describe = subcommands.add_parser("describe", help="show a component manifest")
    describe.add_argument("component")
    describe.set_defaults(func=command_describe)
    resolve = subcommands.add_parser("resolve", help="validate and turn a configuration into Make variables")
    resolve.add_argument("--config", required=True)
    resolve.add_argument("--output")
    resolve.set_defaults(func=command_resolve)
    args = parser.parse_args()
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"configure.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
