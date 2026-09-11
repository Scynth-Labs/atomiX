"""Execution adapters: one interface over host CPUs, RTL models, and beyond.

`registry()` maps the adapter IDs a plan may name to the implementations that
own them.  An adapter absent from this map is an error rather than a silent
substitution: a plan that asks for hardware and gets a simulator has produced
a result about something the user did not ask about.
"""

from __future__ import annotations

from .contract import (
    Adapter, AdapterError, Blocked, Cancelled, CaseOutcome, Description,
    Execution, Prepared, StaleArtifact, Timeout, Unsupported,
)
from .native_cpu import NativeCpuAdapter
from .riscv_models import AxsimAdapter, QemuRiscvAdapter
from .rtl_role import RtlRoleAdapter
from .rtl_soc import RtlSocAdapter

__all__ = [
    "Adapter", "AdapterError", "Blocked", "Cancelled", "CaseOutcome",
    "Description", "Execution", "Prepared", "StaleArtifact", "Timeout",
    "Unsupported", "registry",
]


def registry() -> dict[str, Adapter]:
    return {
        adapter.adapter_id: adapter
        for adapter in (
            NativeCpuAdapter(), RtlRoleAdapter(), RtlSocAdapter(), AxsimAdapter(),
            QemuRiscvAdapter(),
        )
    }
