"""Crash-safe, recoverable trial receipt finalization."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .identity import canonical_json


class ReceiptExistsError(RuntimeError):
    pass


def _fsync_directory(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _promote(path: Path, payload: bytes) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def is_complete_receipt(directory: Path) -> bool:
    result = directory / "result.json"
    checksum = directory / "result.sha256"
    completion = directory / "completion.json"
    if not all(path.is_file() for path in (result, checksum, completion)):
        return False
    try:
        payload = result.read_bytes()
        parsed = json.loads(payload)
        if canonical_json(parsed) != payload:
            return False
        actual = hashlib.sha256(payload).hexdigest()
        recorded = checksum.read_text(encoding="utf-8").strip().split()[0]
        complete = json.loads(completion.read_text(encoding="utf-8"))
    except (OSError, ValueError, IndexError, json.JSONDecodeError):
        return False
    return recorded == actual and complete == {"result_sha256": actual}


def write_receipt(directory: Path, receipt: dict[str, Any]) -> tuple[Path, str]:
    directory.mkdir(parents=True, exist_ok=True)
    result = directory / "result.json"
    checksum = directory / "result.sha256"
    completion = directory / "completion.json"

    if is_complete_receipt(directory):
        raise ReceiptExistsError(f"trial receipt already complete: {directory}")

    payload = canonical_json(receipt)
    receipt_sha = hashlib.sha256(payload).hexdigest()
    checksum_payload = f"{receipt_sha}  result.json\n".encode()
    completion_payload = canonical_json({"result_sha256": receipt_sha})

    if completion.exists():
        raise ReceiptExistsError(
            f"invalid completion state already exists: {directory}"
        )

    if result.exists():
        if result.read_bytes() != payload:
            raise ReceiptExistsError(
                f"incomplete receipt belongs to different result payload: {directory}"
            )
    elif checksum.exists():
        raise ReceiptExistsError(f"checksum exists without result payload: {directory}")
    else:
        _promote(result, payload)
        _fsync_directory(directory)

    if checksum.exists():
        if checksum.read_bytes() != checksum_payload:
            raise ReceiptExistsError(
                f"incomplete receipt has conflicting checksum: {directory}"
            )
    else:
        _promote(checksum, checksum_payload)
        _fsync_directory(directory)

    _promote(completion, completion_payload)
    _fsync_directory(directory)

    if not is_complete_receipt(directory):
        raise RuntimeError(f"receipt finalization verification failed: {directory}")
    return result, receipt_sha
