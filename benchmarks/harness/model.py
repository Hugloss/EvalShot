"""Product-neutral benchmark contracts. Standard-library only."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol


class SubjectLifecycleMode(str, Enum):
    ADAPTER = "adapter"
    AGENT_NATIVE = "agent-native"


class TrialStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCOMPLETE = "INCOMPLETE"
    INVALID = "INVALID"
    CONTAMINATED = "CONTAMINATED"
    NO_QUALIFYING_DEFECT = "NO_QUALIFYING_DEFECT"


@dataclass(frozen=True)
class ParticipantIdentity:
    participant_id: str
    kind: str
    version: str
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    payload: dict[str, Any]
    raw: str
    measurements: dict[str, int | float | str | bool] = field(default_factory=dict)


@dataclass(frozen=True)
class TrialContext:
    workspace: Path
    control_root: Path
    environment: dict[str, str]
    admission_scope: str | None = None


@dataclass(frozen=True)
class McpExposure:
    name: str
    command: str
    args: tuple[str, ...]
    cwd: Path
    semantic_identity: dict[str, Any]
    environment: dict[str, str] = field(default_factory=dict)


class SubjectAdapter(Protocol):
    def identity(self) -> ParticipantIdentity: ...
    def prepare(self, context: TrialContext) -> Observation: ...
    def query(self, context: TrialContext, prompt: str) -> Observation: ...
    def post_change(
        self,
        context: TrialContext,
        changed_paths: tuple[str, ...],
    ) -> Observation: ...
    def cleanup(self, context: TrialContext) -> Observation: ...
    def mcp_exposure(self, context: TrialContext) -> McpExposure | None: ...
    def generated_globs(self) -> tuple[str, ...]: ...


class AgentAdapter(Protocol):
    def identity(self) -> ParticipantIdentity: ...
    def subject_lifecycle_mode(self) -> SubjectLifecycleMode: ...
    def prepare(
        self,
        context: TrialContext,
        exposed_subject: SubjectAdapter | None,
    ) -> Observation: ...
    def run(
        self,
        context: TrialContext,
        prompt: str,
        exposed_subject: SubjectAdapter | None,
    ) -> Observation: ...


class OracleAdapter(Protocol):
    def identity(self) -> ParticipantIdentity: ...
    def healthcheck(self, context: TrialContext) -> Observation: ...
    def grade(self, context: TrialContext, observation: Observation) -> Observation: ...
