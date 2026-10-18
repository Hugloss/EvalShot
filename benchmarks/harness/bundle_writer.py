"""One atomic evidence-bundle publication path for benchmark backends."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

from .bundle import verify_bundle
from .receipt import write_receipt


class BundlePublicationError(RuntimeError):
    pass


def _sync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish_bundle(
    *,
    results_root: Path,
    trial_id: str,
    artifacts: Mapping[str, tuple[str, bytes]],
    receipt: dict[str, Any],
    validate: Callable[[dict[str, Any]], None] | None = None,
) -> Path:
    """Publish a create-once receipt and checksum-bound artifacts."""
    results_root.mkdir(parents=True, exist_ok=True)
    final_dir = results_root / trial_id
    if final_dir.exists():
        valid, reason = verify_bundle(final_dir)
        if valid:
            return final_dir
        raise BundlePublicationError(f"published trial bundle is invalid: {final_dir}: {reason}")
    staged = Path(tempfile.mkdtemp(prefix=f".{trial_id}.bundle-", dir=results_root))
    try:
        artifact_manifest = {}
        names = set()
        for key, (name, payload) in artifacts.items():
            if (
                not name
                or Path(name).name != name
                or name in names
                or name in {"result.json", "result.sha256", "completion.json"}
            ):
                raise BundlePublicationError(f"invalid artifact name: {name!r}")
            names.add(name)
            path = staged / name
            path.write_bytes(payload)
            _sync(path)
            artifact_manifest[key] = {
                "path": name,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        receipt["execution"]["artifacts"] = artifact_manifest
        if validate is not None:
            validate(receipt)
        write_receipt(staged, receipt)
        _sync(staged)
        os.rename(staged, final_dir)
        valid, reason = verify_bundle(final_dir)
        if not valid:
            raise BundlePublicationError(f"published trial bundle failed verification: {reason}")
        _sync(results_root)
        return final_dir
    except BaseException:
        shutil.rmtree(staged, ignore_errors=True)
        raise
