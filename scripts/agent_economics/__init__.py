"""Portable, stdlib-only agent economics probes.

These probes measure and rank evidence for coding agents. They never edit target
repository source code.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "change_impact_audit",
    "context_focus_audit",
    "coupling_focus_audit",
    "hotspot_focus_audit",
    "refactor_focus_audit",
    "test_focus_audit",
]


def __getattr__(name: str) -> Any:
    if name == "change_impact_audit":
        from .change_impact import change_impact_audit

        return change_impact_audit
    if name == "context_focus_audit":
        from .context_focus import context_focus_audit

        return context_focus_audit
    if name == "coupling_focus_audit":
        from .coupling_focus import coupling_focus_audit

        return coupling_focus_audit
    if name == "hotspot_focus_audit":
        from .hotspot_focus import hotspot_focus_audit

        return hotspot_focus_audit
    if name == "refactor_focus_audit":
        from .refactor_focus_workflow import refactor_focus_audit

        return refactor_focus_audit
    if name == "test_focus_audit":
        from .test_focus import test_focus_audit

        return test_focus_audit
    raise AttributeError(name)
