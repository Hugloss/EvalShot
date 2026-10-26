"""Native-owned-auth coding through Oh-Goon's original public Run lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.adapters.runtime import observe_executable
from benchmarks.harness.identity import canonical_json
from benchmarks.harness.runtime_authority import oh_goon_temporary_parent
from benchmarks.harness.model import (
    Observation,
    ParticipantIdentity,
    SubjectLifecycleMode,
    TrialContext,
)
from scripts.agent_economics.bounded_process import ProcessLimits, run_bounded

# The selected implementation observes its own public catalog and loaded producer.
# Neither this command nor the adapter opens the native authentication home.
_RUNTIME_PROBE = """
import importlib.metadata, json, stat, sys
import oh_goon
from pathlib import Path
from oh_goon.runtime.agent_providers import load_agent_providers
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime
from oh_goon.integrations.agent_native_execution import native_executable_sha256, require_codex_authentication_home_separation
catalog = load_agent_providers(Path(sys.argv[1]))
profile = catalog.native_profiles[sys.argv[2]]
connection = profile.codex_service
if connection is None:
    raise ValueError('benchmark requires an explicit Codex service connection')
if Path(connection.home).resolve(strict=True) != Path(sys.argv[3]).resolve(strict=True):
    raise ValueError('native home differs from the selected opaque boundary')
definition = catalog.native_agents.get('codex')
if definition is None or definition.executable is None:
    raise ValueError('benchmark requires an explicitly selected public Codex executable')
program = Path(definition.executable).resolve(strict=True)
require_codex_authentication_home_separation(Path(connection.home), (program, *map(Path, definition.runtime_roots)))
metadata = program.stat()
if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
    raise ValueError('benchmark public Codex executable must be a regular single-link file')
native_digest = native_executable_sha256(program)
workspace = Path(sys.argv[4])
workspace_selection = 'metadata_only'
if workspace.is_dir():
    # Actual trial admission reuses the public CLI selection owner. Readiness
    # has no actor candidate and remains a metadata observation, not admission.
    require_codex_authentication_home_separation(Path(connection.home), (workspace,))
    from oh_goon.cli_ai import resolve_native_cli_selection
    selected, isolation, git = resolve_native_cli_selection(agent='codex', profile=sys.argv[2],
        workspace=workspace, config_file=Path(sys.argv[1]))
    if (selected.executable_sha256 != native_digest
            or selected.profile.codex_service != connection):
        raise ValueError('public CLI selection differs from the observed service authority')
    workspace_selection = 'verified'
print(json.dumps({
    'oh_goon_version': importlib.metadata.version('oh-goon'),
    'oh_goon_module': oh_goon.__file__,
    'hashmarks_version': importlib.metadata.version('hashmarks'),
    'hashmarks_implementation_identity': require_loaded_hashmarks_runtime(),
    'model': connection.model, 'reasoning_effort': connection.reasoning_effort,
    'native_home': connection.home,
    'native_executable': str(program),
    'native_executable_sha256': native_digest,
    'native_runtime_roots': definition.runtime_roots,
    'workspace_git_root': workspace_selection,
}))
"""


def _json_bytes(path: Path, home: Path) -> tuple[bytes, dict[str, Any]]:
    path = _public_file(path, home)
    if path.stat().st_size > 262_144:
        raise ValueError("public benchmark authority exceeds its byte bound")
    with path.open("rb") as stream:
        raw = stream.read(262_145)
    if len(raw) > 262_144:
        raise ValueError("public benchmark authority exceeds its byte bound")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate public benchmark authority field")
            result[key] = value
        return result

    value = json.loads(
        raw,
        object_pairs_hook=unique,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError("non-finite public authority")
        ),
    )
    if not isinstance(value, dict):
        raise ValueError("public benchmark authority must be an object")
    canonical_json(value)
    return raw, value


def _public_path(path: Path, home: Path) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    if resolved.is_relative_to(home):
        raise ValueError(
            "benchmark public input is inside the native authentication home"
        )
    if home.is_relative_to(resolved):
        raise ValueError(
            "benchmark public input contains the native authentication home"
        )
    # Preserve the selected virtual-environment launcher; executing its resolved
    # base interpreter would silently discard native uv dependency authority.
    return path.expanduser().absolute()


def _public_file(path: Path, home: Path) -> Path:
    selected = _public_path(path, home)
    metadata = selected.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError(
            "benchmark public authority must be a regular single-link file"
        )
    return selected


@dataclass(frozen=True)
class OhGoonAgent:
    runtime: str
    hashmarks_version: str
    model: str
    reasoning_effort: str
    profile: str
    workspace_subdirectory: str
    skill_directory: Path | None = None
    timeout_seconds: int = 600
    max_output_bytes: int = 5_000_000

    def identity(self) -> ParticipantIdentity:
        return ParticipantIdentity(
            "oh-goon-codex",
            "coding_agent",
            "public-cli-service-v1",
            {
                "runtime": self.runtime,
                "hashmarks_version": self.hashmarks_version,
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "profile": self.profile,
                "workspace_subdirectory": self.workspace_subdirectory,
                "repository_evidence": "mandatory-runtime-integrated-hashmarks",
            },
        )

    def subject_lifecycle_mode(self) -> SubjectLifecycleMode:
        return SubjectLifecycleMode.ADAPTER

    def _paths(self, context: TrialContext) -> tuple[Path, Path, Path, Path]:
        prefix = "BENCHMARK_OH_GOON_" + self.runtime.upper()
        required = (
            "BENCHMARK_OH_GOON_NATIVE_HOME",
            *(prefix + "_" + name for name in ("PYTHON", "CONFIG", "RECEIPT")),
        )
        if any(not context.environment.get(name) for name in required):
            raise ValueError("explicit Oh-Goon benchmark runtime authority is missing")
        home = (
            Path(context.environment["BENCHMARK_OH_GOON_NATIVE_HOME"])
            .expanduser()
            .resolve(strict=True)
        )
        if not home.is_dir():
            raise ValueError(
                "native authentication home must be an existing directory boundary"
            )
        parent = oh_goon_temporary_parent(context.environment)
        temporary = context.temporary_root
        if temporary is not None:
            _public_path(temporary, home)
            _public_path(temporary / "t", home)
            if (
                temporary.parent != parent
                or temporary.resolve(strict=True) != temporary
                or not temporary.is_dir()
                or (temporary / "t").resolve(strict=True) != temporary / "t"
                or not (temporary / "t").is_dir()
                or any(
                    context.environment.get(name) != str(temporary / "t")
                    for name in ("TMPDIR", "TMP", "TEMP")
                )
            ):
                raise ValueError(
                    "Oh-Goon temporary paths differ from the admitted owned root"
                )
        elif (context.workspace / self.workspace_subdirectory).is_dir():
            raise ValueError("Oh-Goon coding requires an admitted owned temporary root")
        paths = tuple(
            _public_path(Path(context.environment[prefix + "_" + name]), home)
            for name in ("PYTHON", "CONFIG", "RECEIPT")
        )
        _public_path(context.workspace, home)
        _public_path(context.control_root, home)
        if self.skill_directory is not None:
            _public_path(self.skill_directory, home)
        return (*paths, home)

    @staticmethod
    def _environment(context: TrialContext) -> dict[str, str]:
        environment = dict(context.environment)
        for name in (
            "CODEX_HOME",
            "CODEX_API_KEY",
            "OPENAI_API_KEY",
            "CODEX_ACCESS_TOKEN",
        ):
            environment.pop(name, None)
        environment["OH_GOON_DB"] = str(context.control_root / "oh-goon" / "app.sqlite")
        environment["OH_GOON_LEAGUE_FILE"] = str(
            context.control_root / "oh-goon" / "league.yml"
        )
        return environment

    def _command(self, context: TrialContext, argv: tuple[str, ...], *, timeout: float):
        environment = self._environment(context)
        return run_bounded(
            repository_root=context.workspace,
            argv=argv,
            environment=environment,
            inherit_environment=False,
            limits=ProcessLimits(
                timeout_seconds=timeout,
                max_stdout_bytes=self.max_output_bytes,
                max_stderr_bytes=100_000,
            ),
        )

    @staticmethod
    def _completed_json(result, *, allow_failed_run: bool = False) -> dict[str, Any]:
        if (
            result.return_code is None
            or (not allow_failed_run and result.return_code != 0)
            or result.executable_missing
            or result.timed_out
            or result.stdout_truncated
            or result.stderr_truncated
        ):
            raise ValueError(
                "Oh-Goon public command did not complete within its evidence bounds"
            )
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError("Oh-Goon public command did not return one JSON object")
        return value

    def _git(self, context: TrialContext, source: Path, *arguments: str) -> bytes:
        result = self._command(
            context, ("git", "-C", str(source), *arguments), timeout=30
        )
        if (
            result.return_code != 0
            or result.executable_missing
            or result.timed_out
            or result.stdout_truncated
            or result.stderr_truncated
        ):
            raise ValueError("Oh-Goon native Git source authority is unavailable")
        return result.stdout

    def _source_identity(
        self, context: TrialContext, source: Path, home: Path, overrides: Any
    ) -> dict[str, Any]:
        if (
            not isinstance(overrides, list)
            or any(not isinstance(name, str) for name in overrides)
            or len(overrides) != len(set(overrides))
            or any(
                name not in {"backend/pyproject.toml", "backend/uv.lock"}
                for name in overrides
            )
        ):
            raise ValueError(
                "Oh-Goon dependency overrides must name only native dependency authorities"
            )
        # Native Git supplies membership. Refuse untracked members before the
        # shared identity owner can open their contents or follow their aliases.
        if self._git(
            context, source, "ls-files", "--others", "--exclude-standard", "-z"
        ):
            raise ValueError(
                "Oh-Goon source has changes outside the admitted dependency overrides"
            )
        top = Path(
            os.fsdecode(
                self._git(context, source, "rev-parse", "--show-toplevel")
            ).removesuffix("\n")
        )
        if top.resolve(strict=True) != source.resolve(strict=True):
            raise ValueError("Oh-Goon supply must select its exact Git source root")
        members = self._git(context, source, "ls-files", "--cached", "-z").split(b"\0")
        for encoded in members:
            if not encoded:
                continue
            relative = Path(os.fsdecode(encoded))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Oh-Goon source member escapes the supplied root")
            member = _public_file(source / relative, home)
            if member.resolve(strict=True) != source.resolve(strict=True) / relative:
                raise ValueError(
                    "Oh-Goon source member redirects outside its frozen Git authority"
                )
        for name in ("pyproject.toml", "uv.lock"):
            _public_file(source / "backend" / name, home)
        changed = self._git(context, source, "diff", "--name-only", "-z", "HEAD")
        if {os.fsdecode(name) for name in changed.split(b"\0") if name} != set(
            overrides
        ):
            raise ValueError(
                "Oh-Goon source has changes outside the admitted dependency overrides"
            )
        # Reuse the existing native Git-backed identity owner after metadata
        # admission; do not introduce a second source fingerprint or tree walk.
        from benchmarks.harness.admission import harness_identity

        return harness_identity(source, repository="oh-goon/oh-goon")

    def _runtime(self, context: TrialContext) -> dict[str, Any]:
        python, config, receipt_path, home = self._paths(context)
        receipt_raw, receipt = _json_bytes(receipt_path, home)
        config_raw, _ = _json_bytes(config, home)
        if (
            receipt.get("schema") != "evalshot.oh-goon-runtime-supply/v1"
            or receipt.get("hashmarks_version") != self.hashmarks_version
        ):
            raise ValueError("Oh-Goon runtime supply does not match the frozen arm")
        for key in (
            "oh_goon_source_commit",
            "oh_goon_source_tree",
            "pyproject_sha256",
            "uv_lock_sha256",
            "hashmarks_implementation_identity",
        ):
            if not isinstance(receipt.get(key), str) or not receipt[key]:
                raise ValueError("Oh-Goon runtime supply is missing " + key)
        source = _public_path(Path(receipt["oh_goon_source_root"]), home)
        source_identity = self._source_identity(
            context, source, home, receipt.get("dependency_overrides", [])
        )
        if (
            source_identity["commit"] != receipt["oh_goon_source_commit"]
            or source_identity["tree"] != receipt["oh_goon_source_tree"]
        ):
            raise ValueError("Oh-Goon source differs from its supplied commit/tree")
        for name, key in (
            ("pyproject.toml", "pyproject_sha256"),
            ("uv.lock", "uv_lock_sha256"),
        ):
            if (
                hashlib.sha256(
                    _public_file(source / "backend" / name, home).read_bytes()
                ).hexdigest()
                != receipt[key]
            ):
                raise ValueError(
                    "Oh-Goon dependency authority differs from its supplied bytes"
                )
        executable = observe_executable(
            context, str(python), environment=self._environment(context)
        )
        if executable.payload.get("available") is not True:
            raise ValueError(
                "selected Oh-Goon Python executable is unavailable or unstable"
            )
        observed = self._completed_json(
            self._command(
                context,
                (
                    str(python),
                    "-I",
                    "-c",
                    _RUNTIME_PROBE,
                    str(config),
                    self.profile,
                    str(home),
                    str(context.workspace / self.workspace_subdirectory),
                ),
                timeout=30,
            )
        )
        if (
            observed.get("hashmarks_version") != self.hashmarks_version
            or observed.get("hashmarks_implementation_identity")
            != receipt["hashmarks_implementation_identity"]
            or observed.get("model") != self.model
            or observed.get("reasoning_effort") != self.reasoning_effort
            or Path(observed["native_home"]).expanduser().resolve() != home
            or observed.get("workspace_git_root")
            != (
                "verified"
                if (context.workspace / self.workspace_subdirectory).is_dir()
                else "metadata_only"
            )
        ):
            raise ValueError(
                "loaded runtime or service connection differs from frozen authority"
            )
        if (
            not Path(observed["oh_goon_module"])
            .resolve()
            .is_relative_to(source / "backend" / "src")
        ):
            raise ValueError("loaded Oh-Goon is outside the supplied source")
        native_path = _public_path(Path(observed["native_executable"]), home)
        for root in observed["native_runtime_roots"]:
            _public_path(Path(root), home)
        native = observe_executable(
            context, str(native_path), environment=self._environment(context)
        )
        if (
            native.payload.get("available") is not True
            or native.payload.get("executable_sha256")
            != observed["native_executable_sha256"]
            or not str(native.payload.get("version", "")).startswith("codex-cli ")
        ):
            raise ValueError(
                "selected native Codex executable authority is unavailable or unstable"
            )
        return {
            "temporary_parent": str(oh_goon_temporary_parent(context.environment)),
            "source_identity": source_identity,
            "python_sha256": executable.payload["executable_sha256"],
            "supply_receipt_sha256": hashlib.sha256(receipt_raw).hexdigest(),
            "supply": receipt,
            "providers_sha256": hashlib.sha256(config_raw).hexdigest(),
            "python": str(python),
            "providers": str(config),
            "loaded": observed,
            "native_execution": {
                "executable_sha256": native.payload["executable_sha256"],
                "version": native.payload["version"].removeprefix("codex-cli "),
                "runtime_root_bytes": "not independently observed; explicit public roots are resolved by Oh-Goon",
            },
        }

    def prepare(self, context: TrialContext, exposed_subject) -> Observation:
        if (
            exposed_subject is not None
            and exposed_subject.identity().participant_id != "none"
        ):
            raise ValueError(
                "Oh-Goon owns mandatory integrated evidence; extra MCP subjects are unsupported"
            )
        runtime = self._runtime(context)
        with (context.control_root / "oh-goon-prepared.json").open("xb") as stream:
            stream.write(canonical_json(runtime))
        return Observation(
            {
                "available": True,
                "version": runtime["loaded"]["oh_goon_version"],
                "auth_mode": "oh-goon-native-owned-service",
                "provider": "openai",
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "execution_runtime_identity": runtime,
                "repository_evidence": "mandatory-runtime-integrated-hashmarks",
            },
            "",
        )

    def run(self, context: TrialContext, prompt: str, exposed_subject) -> Observation:
        _, _, _, home = self._paths(context)
        _, prepared = _json_bytes(context.control_root / "oh-goon-prepared.json", home)
        if self._runtime(context) != prepared:
            raise ValueError(
                "Oh-Goon public execution authority changed after admission"
            )
        workspace = (context.workspace / self.workspace_subdirectory).resolve(
            strict=True
        )
        if (
            not workspace.is_relative_to(context.workspace.resolve())
            or not workspace.is_dir()
        ):
            raise ValueError("Oh-Goon candidate directory escapes the admitted trial")
        prompt_file = context.control_root / "oh-goon-task.txt"
        with prompt_file.open("x", encoding="utf-8") as stream:
            stream.write(prompt)
        python, config = prepared["python"], prepared["providers"]
        skill_args = (
            ("--skill", str(self.skill_directory)) if self.skill_directory else ()
        )
        result = self._command(
            context,
            (
                python,
                "-I",
                "-m",
                "oh_goon",
                "ai",
                "--agent",
                "codex",
                "--profile",
                self.profile,
                "--config",
                config,
                "--workspace",
                str(workspace),
                "--prompt-file",
                str(prompt_file),
                "--headless",
                "--json",
                *skill_args,
            ),
            timeout=self.timeout_seconds,
        )
        run = self._completed_json(result, allow_failed_run=True)
        run_state = run.get("run")
        if not isinstance(run_state, dict):
            raise ValueError(
                "Oh-Goon coding command did not return its canonical Run detail"
            )
        run_id = run_state.get("fleet_run_id")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError(
                "Oh-Goon coding command did not retain its original Run ID"
            )
        projections = {"run": run}
        errors = []
        if result.return_code != 0:
            errors.append(
                "public coding command returned an unsuccessful process status"
            )
        for command in ("handoff", "tape"):
            try:
                value = self._completed_json(
                    self._command(
                        context,
                        (
                            python,
                            "-I",
                            "-m",
                            "oh_goon",
                            "plan",
                            command,
                            run_id,
                            "--json",
                        ),
                        timeout=30,
                    )
                )
                projections[command] = value
                if value.get("fleet_run_id") != run_id:
                    errors.append("original Run readback identity mismatch")
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
        handoff = projections.get("handoff", {})
        tape = projections.get("tape", {})
        steps = [
            step
            for repository in tape.get("repositories", [])
            for step in repository.get("steps", [])
        ]
        if (
            run_state.get("status") != "COMPLETED"
            or run.get("outcome") != "SUCCESS"
            or handoff.get("status") != run_state.get("status")
            or handoff.get("outcome") != run.get("outcome")
            or len(steps) != 1
            or steps[0].get("status") != "PASSED"
        ):
            errors.append("original coding Run did not complete its one admitted Step")
        observations = steps[0].get("native_agent_observations", []) if steps else []
        native = observations[0] if len(observations) == 1 else {}
        execution = native.get("execution", {})
        if (
            native.get("agent") != "codex"
            or native.get("profile") != self.profile
            or native.get("implementation_identity")
            != "codex:sha256:" + prepared["native_execution"]["executable_sha256"]
            or native.get("settled") is not True
            or native.get("failed") is not False
            or execution.get("authority") != "ORIGINAL_STEP_EXECUTION"
            or execution.get("native_version")
            != prepared["native_execution"]["version"]
            or execution.get("native_state_separation") != "verified"
            or execution.get("process_cleanup") != "verified"
        ):
            errors.append("native service authority or cleanup is incomplete")
        try:
            if self._runtime(context) != prepared:
                errors.append("public execution authority changed during inference")
        except Exception as exc:
            # The effect already has an original Run. A failed external readback
            # remains INCOMPLETE without discarding that original owner result.
            errors.append("post-execution authority unavailable: " + str(exc))
        # Preserve unsuccessful original Runs and readback before independent
        # grading; native completion and benchmark correctness remain distinct.
        return Observation(
            {
                "terminal_event": {
                    "type": "turn.failed" if errors else "turn.completed",
                    "basis": "oh-goon-original-run",
                    "reason": "; ".join(errors) if errors else None,
                },
                "terminal_complete": not errors,
                "final_message": "Oh-Goon coding Run "
                + run_id
                + "; independent grading is separate.",
                "process": result.metrics(),
                "oh_goon_run_id": run_id,
                "execution_runtime_identity": prepared,
                "original_run": run,
                "original_handoff": handoff,
                "original_tape": tape,
                "repository_evidence": "mandatory-runtime-integrated-hashmarks",
                "metrics_observability": {
                    "tokens": "unknown",
                    "tool_calls": "unknown",
                    "context": "unknown",
                },
            },
            canonical_json(projections).decode(),
            {"duration_ms": result.elapsed_ms},
        )
