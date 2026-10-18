from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path


class CommandManifestError(ValueError):
    pass


@dataclass(frozen=True)
class CommandSpec:
    name: str
    argv: tuple[str, ...]
    cwd: str
    stage: str
    append_selected_tests: bool
    must_not_modify_tracked_files: bool
    allowed_mutation_paths: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CommandManifest:
    version: int
    commands: dict[str, CommandSpec]
    identity: str


_ROOT_FIELDS = {"version", "commands"}
_COMMAND_FIELDS = {
    "argv",
    "cwd",
    "stage",
    "append_selected_tests",
    "must_not_modify_tracked_files",
    "allowed_mutation_paths",
    "environment",
}


def _safe_relative(value: str) -> str:
    candidate = Path(value.replace("\\", "/"))
    if candidate.is_absolute() or any(part in {"", ".."} for part in candidate.parts):
        raise CommandManifestError(f"path must be repository-relative: {value!r}")
    return candidate.as_posix() if candidate != Path(".") else "."


def _environment(value: object, *, field: str) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise CommandManifestError(f"{field} must be a TOML table")
    rows: list[tuple[str, str]] = []
    for key, raw in sorted(value.items()):
        if (
            not isinstance(key, str)
            or not key
            or "=" in key
            or "\x00" in key
            or not isinstance(raw, str)
            or "\x00" in raw
        ):
            raise CommandManifestError(
                f"{field} must contain valid non-NUL string environment pairs"
            )
        rows.append((key, raw))
    return tuple(rows)


def _bool(value: object, *, field: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise CommandManifestError(f"{field} must be boolean")
    return value


def load_command_manifest(path: Path) -> CommandManifest:
    if path.stat().st_size > 1_000_000:
        raise CommandManifestError("command manifest exceeds 1,000,000 byte bound")
    raw = path.read_bytes()
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise CommandManifestError("invalid UTF-8 TOML command manifest") from exc
    if not isinstance(data, dict):
        raise CommandManifestError("command manifest must be a TOML table")
    unknown_root = set(data) - _ROOT_FIELDS
    if unknown_root:
        raise CommandManifestError(f"unknown manifest fields: {sorted(unknown_root)}")
    if data.get("version") != 1:
        raise CommandManifestError("command manifest version must be 1")
    raw_commands = data.get("commands")
    if not isinstance(raw_commands, dict) or not raw_commands:
        raise CommandManifestError("command manifest requires [commands.*] entries")
    commands: dict[str, CommandSpec] = {}
    for name, value in sorted(raw_commands.items()):
        if not isinstance(name, str) or not name or not isinstance(value, dict):
            raise CommandManifestError("invalid command entry")
        unknown = set(value) - _COMMAND_FIELDS
        if unknown:
            raise CommandManifestError(
                f"command {name!r} has unknown fields: {sorted(unknown)}"
            )
        argv = value.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(x, str) and x for x in argv)
        ):
            raise CommandManifestError(
                f"command {name!r} requires non-empty argv string array"
            )
        cwd_raw = value.get("cwd", ".")
        if not isinstance(cwd_raw, str):
            raise CommandManifestError(f"command {name!r} cwd must be a string")
        stage_raw = value.get("stage", "component")
        if not isinstance(stage_raw, str) or stage_raw not in {
            "focused",
            "affected",
            "component",
            "repository",
        }:
            raise CommandManifestError(f"command {name!r} has invalid stage")
        allowed = value.get("allowed_mutation_paths", [])
        if not isinstance(allowed, list) or not all(
            isinstance(x, str) for x in allowed
        ):
            raise CommandManifestError(
                f"command {name!r} allowed_mutation_paths must be strings"
            )
        commands[name] = CommandSpec(
            name=name,
            argv=tuple(argv),
            cwd=_safe_relative(cwd_raw),
            stage=stage_raw,
            append_selected_tests=_bool(
                value.get("append_selected_tests"),
                field=f"{name}.append_selected_tests",
                default=False,
            ),
            must_not_modify_tracked_files=_bool(
                value.get("must_not_modify_tracked_files"),
                field=f"{name}.must_not_modify_tracked_files",
                default=True,
            ),
            allowed_mutation_paths=tuple(_safe_relative(x) for x in allowed),
            environment=_environment(
                value.get("environment"), field=f"{name}.environment"
            ),
        )
    identity = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    return CommandManifest(version=1, commands=commands, identity=identity)
