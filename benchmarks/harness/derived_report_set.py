"""Generation-bound publication for canonical derived benchmark reports."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .identity import canonical_json, digest


REPORT_SET_CONTRACT_V1 = "agents-cookbook-derived-report-set.v1"
REPORT_SET_CONTRACT = "agents-cookbook-derived-report-set.v2"
REPORT_SET_MANIFEST = "report-set.json"
REPORT_SET_CORE_ARTIFACTS = (
    "report.json",
    "trace-diagnostics.json",
    "decision-evidence.json",
)
# Kept as the core dependency chain for callers that only need those names.
REPORT_SET_ARTIFACTS = REPORT_SET_CORE_ARTIFACTS


class DerivedReportSetError(ValueError):
    pass


def _reports_dir(run_root: Path) -> Path:
    return run_root / "reports"


def _encoded_json(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")


def _artifact_receipt(payload: bytes) -> dict[str, Any]:
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def _score_artifact_name(value: str) -> str:
    path = Path(value)
    if (
        not value
        or path.is_absolute()
        or len(path.parts) != 1
        or value in set(REPORT_SET_CORE_ARTIFACTS) | {REPORT_SET_MANIFEST, "status.json"}
    ):
        raise DerivedReportSetError("derived report-set score artifact is invalid")
    return value


def _manifest_payload(
    *,
    run_id: str,
    encoded: Mapping[str, bytes],
    score_artifact: str,
) -> dict[str, Any]:
    score_artifact = _score_artifact_name(score_artifact)
    artifact_names = (*REPORT_SET_CORE_ARTIFACTS, score_artifact)
    artifacts = {
        name: _artifact_receipt(encoded[name])
        for name in artifact_names
    }
    identity = {
        "contract": REPORT_SET_CONTRACT,
        "run_id": run_id,
        "score_artifact": score_artifact,
        "artifacts": artifacts,
    }
    return {
        **identity,
        "generation_id": digest(identity),
    }


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish_report_set(
    *,
    run_root: Path,
    run_id: str,
    report: object,
    trace_diagnostics: object,
    decision_evidence: object,
    score_artifact: str,
    score_bytes: bytes,
) -> dict[str, Any]:
    """Publish one canonical report+score generation with the manifest last."""
    directory = _reports_dir(run_root)
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise DerivedReportSetError("reports directory is not a regular directory")

    score_artifact = _score_artifact_name(score_artifact)
    if not isinstance(score_bytes, bytes) or not score_bytes:
        raise DerivedReportSetError("derived report-set score is empty")
    try:
        score_payload = json.loads(score_bytes)
    except ValueError as exc:
        raise DerivedReportSetError("derived report-set score is not JSON") from exc
    if not isinstance(score_payload, dict):
        raise DerivedReportSetError("derived report-set score must be one JSON object")

    encoded = {
        "report.json": _encoded_json(report),
        "trace-diagnostics.json": _encoded_json(trace_diagnostics),
        "decision-evidence.json": _encoded_json(decision_evidence),
        score_artifact: score_bytes,
    }
    manifest = _manifest_payload(
        run_id=run_id,
        encoded=encoded,
        score_artifact=score_artifact,
    )
    manifest_bytes = canonical_json(manifest)
    artifact_names = tuple(manifest["artifacts"])

    staged = Path(tempfile.mkdtemp(prefix=".report-set-stage-", dir=directory))
    try:
        for name in artifact_names:
            destination = staged / name
            with destination.open("wb") as stream:
                stream.write(encoded[name])
                stream.flush()
                os.fsync(stream.fileno())

        staged_manifest = staged / REPORT_SET_MANIFEST
        with staged_manifest.open("wb") as stream:
            stream.write(manifest_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(staged)

        for name in artifact_names:
            os.replace(staged / name, directory / name)
        _fsync_directory(directory)

        # The manifest is the commit record for the generation and must be last.
        os.replace(staged_manifest, directory / REPORT_SET_MANIFEST)
        _fsync_directory(directory)
    finally:
        shutil.rmtree(staged, ignore_errors=True)

    return verify_report_set(run_root=run_root, expected_run_id=run_id)


def inspect_report_set(
    *,
    run_root: Path,
    expected_run_id: str | None = None,
) -> dict[str, Any]:
    """Project report-set health without treating legacy files as verified score."""
    directory = _reports_dir(run_root)
    manifest_path = directory / REPORT_SET_MANIFEST
    if not manifest_path.exists():
        present = [
            name
            for name in (*REPORT_SET_CORE_ARTIFACTS, "score.json")
            if (directory / name).is_file()
        ]
        return {
            "status": "UNBOUND" if present else "NOT_PUBLISHED",
            "verified": False,
            "score_bound": False,
            "artifacts_present": present,
        }
    try:
        manifest = verify_report_set(
            run_root=run_root,
            expected_run_id=expected_run_id,
        )
    except DerivedReportSetError as exc:
        return {
            "status": "INVALID",
            "verified": False,
            "score_bound": False,
            "reason": str(exc),
        }
    score_artifact = manifest.get("score_artifact")
    score_bound = isinstance(score_artifact, str)
    return {
        "status": "VERIFIED" if score_bound else "VERIFIED_REPORT_ONLY",
        "verified": True,
        "score_bound": score_bound,
        "score_artifact": score_artifact,
        "generation_id": manifest["generation_id"],
        "run_id": manifest["run_id"],
        "artifacts": sorted(manifest["artifacts"]),
    }


def _verify_artifact(
    *,
    directory: Path,
    name: str,
    receipt: Any,
) -> None:
    if (
        not isinstance(receipt, dict)
        or set(receipt) != {"sha256", "bytes"}
        or not isinstance(receipt.get("sha256"), str)
        or type(receipt.get("bytes")) is not int
        or receipt["bytes"] < 0
    ):
        raise DerivedReportSetError(
            f"invalid derived report-set artifact receipt: {name}"
        )
    path = directory / name
    if path.is_symlink() or not path.is_file():
        raise DerivedReportSetError(
            f"derived report-set artifact is missing or non-regular: {name}"
        )
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise DerivedReportSetError(
            f"cannot read derived report-set artifact: {name}"
        ) from exc
    if (
        len(payload) != receipt["bytes"]
        or hashlib.sha256(payload).hexdigest() != receipt["sha256"]
    ):
        raise DerivedReportSetError(
            f"derived report-set artifact does not match manifest: {name}"
        )
    try:
        value = json.loads(payload)
    except ValueError as exc:
        raise DerivedReportSetError(
            f"derived report-set artifact is not JSON: {name}"
        ) from exc
    if not isinstance(value, dict):
        raise DerivedReportSetError(
            f"derived report-set artifact must be one JSON object: {name}"
        )


def verify_report_set(
    *,
    run_root: Path,
    expected_run_id: str | None = None,
) -> dict[str, Any]:
    """Verify the canonical derived report generation from exact persisted bytes."""
    directory = _reports_dir(run_root)
    manifest_path = directory / REPORT_SET_MANIFEST
    if not manifest_path.exists():
        raise DerivedReportSetError("canonical derived report set has no manifest")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise DerivedReportSetError("derived report-set manifest is not a regular file")

    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise DerivedReportSetError("cannot read derived report-set manifest") from exc
    if not isinstance(manifest, dict) or canonical_json(manifest) != raw:
        raise DerivedReportSetError("derived report-set manifest identity mismatch")

    contract = manifest.get("contract")
    if contract == REPORT_SET_CONTRACT_V1:
        required = {"contract", "run_id", "artifacts", "generation_id"}
        expected_artifacts = set(REPORT_SET_CORE_ARTIFACTS)
        identity_keys = ("contract", "run_id", "artifacts")
    elif contract == REPORT_SET_CONTRACT:
        required = {
            "contract",
            "run_id",
            "score_artifact",
            "artifacts",
            "generation_id",
        }
        if not isinstance(manifest.get("score_artifact"), str):
            raise DerivedReportSetError(
                "derived report-set score artifact identity mismatch"
            )
        score_artifact = _score_artifact_name(manifest["score_artifact"])
        expected_artifacts = set(REPORT_SET_CORE_ARTIFACTS) | {score_artifact}
        identity_keys = ("contract", "run_id", "score_artifact", "artifacts")
    else:
        raise DerivedReportSetError("derived report-set manifest contract is unsupported")

    if (
        set(manifest) != required
        or not isinstance(manifest.get("run_id"), str)
        or not manifest["run_id"]
        or not isinstance(manifest.get("artifacts"), dict)
        or set(manifest["artifacts"]) != expected_artifacts
        or not isinstance(manifest.get("generation_id"), str)
    ):
        raise DerivedReportSetError("derived report-set manifest identity mismatch")

    if expected_run_id is not None and manifest["run_id"] != expected_run_id:
        raise DerivedReportSetError(
            "derived report-set run identity mismatch "
            f"(expected {expected_run_id}, observed {manifest['run_id']})"
        )

    identity = {key: manifest[key] for key in identity_keys}
    if manifest["generation_id"] != digest(identity):
        raise DerivedReportSetError("derived report-set generation digest mismatch")

    for name, receipt in manifest["artifacts"].items():
        _verify_artifact(directory=directory, name=name, receipt=receipt)

    return manifest


def verified_score_path(
    *,
    run_root: Path,
    expected_run_id: str | None = None,
) -> Path:
    """Return only a score bound into the verified v2 derived generation."""
    manifest = verify_report_set(
        run_root=run_root,
        expected_run_id=expected_run_id,
    )
    score_artifact = manifest.get("score_artifact")
    if not isinstance(score_artifact, str):
        raise DerivedReportSetError(
            "canonical score is not generation-bound; regenerate reports"
        )
    return _reports_dir(run_root) / score_artifact
