"""Append-only, sealable benchmark event evidence."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .identity import canonical_json


class EventStreamError(RuntimeError):
    pass


def _seal_path(path: Path) -> Path:
    return path.with_name(path.name + ".seal.json")


def append_event(
    path: Path,
    *,
    trial_id: str,
    sequence: int,
    kind: str,
    payload: dict[str, Any],
) -> None:
    if not trial_id or sequence < 0 or not kind:
        raise ValueError(
            "trial_id and kind must be non-empty and sequence must be >= 0"
        )
    if _seal_path(path).exists():
        raise EventStreamError(f"event stream already sealed: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "trial_id": trial_id,
        "sequence": sequence,
        "kind": kind,
        "payload": payload,
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    with os.fdopen(fd, "ab") as stream:
        stream.write(canonical_json(event))
        stream.flush()
        os.fsync(stream.fileno())


def seal_events(
    path: Path,
    *,
    trial_id: str,
    max_bytes: int = 50_000_000,
) -> dict[str, Any]:
    seal = _seal_path(path)
    if seal.exists():
        raise EventStreamError(f"event stream already sealed: {path}")
    raw = path.read_bytes() if path.exists() else b""
    if len(raw) > max_bytes:
        raise EventStreamError("event stream exceeds configured byte bound")

    events = []
    for line_no, line in enumerate(raw.splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EventStreamError(f"invalid event JSON at line {line_no}") from exc
        if event.get("trial_id") != trial_id:
            raise EventStreamError(f"event trial identity mismatch at line {line_no}")
        if event.get("sequence") != len(events):
            raise EventStreamError(f"non-contiguous event sequence at line {line_no}")
        events.append(event)

    evidence = {
        "trial_id": trial_id,
        "event_count": len(events),
        "events_sha256": hashlib.sha256(raw).hexdigest(),
    }
    payload = canonical_json(evidence)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{seal.name}.", dir=seal.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if seal.exists():
            raise EventStreamError(f"event stream already sealed: {path}")
        os.replace(tmp, seal)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return evidence
