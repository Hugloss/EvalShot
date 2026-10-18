from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

MANIFEST_SCHEMA = "agentscookbook-bounded-evidence-manifest/v1"
BATCH_SCHEMA = "agentscookbook-bounded-evidence-batch/v1"
SUBBATCH_SCHEMA = "agentscookbook-bounded-evidence-subbatch/v1"
RECEIPT_SCHEMA = "agentscookbook-bounded-evidence-receipt/v1"
AGGREGATE_SCHEMA = "agentscookbook-bounded-evidence-aggregate/v1"
COMPLETE_PASS = "COMPLETE_PASS"
PRODUCT_FAILURE = "PRODUCT_FAILURE"
INCOMPLETE = "INCOMPLETE"
INCOMPLETE_CONTROLLER_TIMEOUT = "INCOMPLETE_CONTROLLER_TIMEOUT"
INCOMPLETE_BUDGET_EXCEEDED = "INCOMPLETE_BUDGET_EXCEEDED"


def _identity(payload: Mapping[str, object]) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _targets(values: Sequence[object]) -> list[str]:
    targets = [str(value) for value in values]
    if not targets or any(not value.strip() for value in targets):
        raise ValueError("targets must contain non-empty identifiers")
    if len(set(targets)) != len(targets):
        raise ValueError("targets must be unique")
    return targets


def build_manifest(
    *,
    targets: Sequence[object],
    repository_identity: str,
    provider_identity: str,
    operation: str,
    batch_size: int = 10,
    controller_budget_ms: int | None = None,
    batch_timeout_ms: int | None = None,
    minimum_headroom_ms: int = 5_000,
    parent_manifest_identity: str | None = None,
    selection_identity: str | None = None,
) -> dict[str, object]:
    """Freeze one ordered expensive-evidence campaign without executing it."""
    normalized = _targets(targets)
    if any(
        not _nonempty(v) for v in (repository_identity, provider_identity, operation)
    ):
        raise ValueError("repository/provider/operation identities must be non-empty")
    if (
        not isinstance(batch_size, int)
        or isinstance(batch_size, bool)
        or batch_size < 1
    ):
        raise ValueError("batch_size must be a positive integer")
    if (controller_budget_ms is None) != (batch_timeout_ms is None):
        raise ValueError(
            "controller_budget_ms and batch_timeout_ms must be provided together"
        )
    if controller_budget_ms is not None:
        if (
            not isinstance(controller_budget_ms, int)
            or isinstance(controller_budget_ms, bool)
            or controller_budget_ms < 1
            or not isinstance(batch_timeout_ms, int)
            or isinstance(batch_timeout_ms, bool)
            or batch_timeout_ms < 1
            or not isinstance(minimum_headroom_ms, int)
            or isinstance(minimum_headroom_ms, bool)
            or minimum_headroom_ms < 0
        ):
            raise ValueError("execution budgets must be positive integer milliseconds")
        if batch_timeout_ms + minimum_headroom_ms > controller_budget_ms:
            raise ValueError(
                "batch timeout must leave declared headroom below controller budget"
            )
    if (parent_manifest_identity is None) != (selection_identity is None):
        raise ValueError(
            "follow-up manifests require both parent and selection identities"
        )
    if parent_manifest_identity is not None and (
        not _nonempty(parent_manifest_identity) or not _nonempty(selection_identity)
    ):
        raise ValueError("follow-up manifest identities must be non-empty")
    semantic: dict[str, object] = {
        "schema": MANIFEST_SCHEMA,
        "repository_identity": repository_identity,
        "provider_identity": provider_identity,
        "operation": operation,
        "targets": normalized,
        "target_count": len(normalized),
        "batch_size": batch_size,
        "batch_count": (len(normalized) + batch_size - 1) // batch_size,
    }
    if controller_budget_ms is not None:
        semantic["controller_budget_ms"] = controller_budget_ms
        semantic["batch_timeout_ms"] = batch_timeout_ms
        semantic["minimum_headroom_ms"] = minimum_headroom_ms
        semantic["controller_headroom_ms"] = controller_budget_ms - int(
            batch_timeout_ms
        )
    if parent_manifest_identity is not None:
        semantic["parent_manifest_identity"] = parent_manifest_identity
        semantic["selection_identity"] = selection_identity
    return {**semantic, "manifest_identity": _identity(semantic)}


def batch_descriptor(
    manifest: Mapping[str, object], batch_index: int
) -> dict[str, object]:
    if manifest.get("schema") != MANIFEST_SCHEMA or not _nonempty(
        manifest.get("manifest_identity")
    ):
        raise ValueError("manifest must be identified")
    targets = _targets(list(manifest.get("targets") or ()))
    size = manifest.get("batch_size")
    count = manifest.get("batch_count")
    if not isinstance(size, int) or not isinstance(count, int):
        raise ValueError("manifest batch bounds are invalid")
    if (
        not isinstance(batch_index, int)
        or isinstance(batch_index, bool)
        or not 0 <= batch_index < count
    ):
        raise ValueError("batch_index is outside manifest bounds")
    start = batch_index * size
    end = min(len(targets), start + size)
    semantic: dict[str, object] = {
        "schema": BATCH_SCHEMA,
        "manifest_identity": manifest["manifest_identity"],
        "repository_identity": manifest["repository_identity"],
        "provider_identity": manifest["provider_identity"],
        "operation": manifest["operation"],
        "batch_index": batch_index,
        "start": start,
        "end": end,
        "targets": targets[start:end],
    }
    for key in (
        "controller_budget_ms",
        "batch_timeout_ms",
        "minimum_headroom_ms",
        "controller_headroom_ms",
    ):
        if key in manifest:
            semantic[key] = manifest[key]
    return {**semantic, "batch_identity": _identity(semantic)}


def subdivide_batch(
    batch: Mapping[str, object], *, subbatch_size: int
) -> list[dict[str, object]]:
    """Split one expensive batch deterministically after a controller timeout."""
    if batch.get("schema") != BATCH_SCHEMA or not _nonempty(
        batch.get("batch_identity")
    ):
        raise ValueError("batch must be identified")
    targets = _targets(list(batch.get("targets") or ()))
    if (
        not isinstance(subbatch_size, int)
        or isinstance(subbatch_size, bool)
        or not 0 < subbatch_size < len(targets)
    ):
        raise ValueError("subbatch_size must be positive and smaller than parent")
    children = []
    for child_index, offset in enumerate(range(0, len(targets), subbatch_size)):
        child_targets = targets[offset : offset + subbatch_size]
        semantic: dict[str, object] = {
            "schema": SUBBATCH_SCHEMA,
            "manifest_identity": batch["manifest_identity"],
            "repository_identity": batch["repository_identity"],
            "provider_identity": batch["provider_identity"],
            "operation": batch["operation"],
            "parent_batch_identity": batch["batch_identity"],
            "batch_index": batch["batch_index"],
            "subbatch_index": child_index,
            "start": int(batch["start"]) + offset,
            "end": int(batch["start"]) + offset + len(child_targets),
            "targets": child_targets,
        }
        for key in (
            "controller_budget_ms",
            "batch_timeout_ms",
            "minimum_headroom_ms",
            "controller_headroom_ms",
        ):
            if key in batch:
                semantic[key] = batch[key]
        children.append({**semantic, "batch_identity": _identity(semantic)})
    return children


def build_receipt(
    descriptor: Mapping[str, object],
    *,
    results: Sequence[Mapping[str, object]],
    execution_class: str,
    elapsed_ms: int,
    observed_repository_identity: str,
    observed_provider_identity: str,
    observed_operation: str,
    controller_status: str = "COMPLETED",
) -> dict[str, object]:
    """Bind one batch result without calling a timeout a product failure."""
    if descriptor.get("schema") not in {BATCH_SCHEMA, SUBBATCH_SCHEMA} or not _nonempty(
        descriptor.get("batch_identity")
    ):
        raise ValueError("descriptor must be an identified batch/subbatch")
    if (
        not _nonempty(execution_class)
        or not isinstance(elapsed_ms, int)
        or elapsed_ms < 0
    ):
        raise ValueError("execution_class/elapsed_ms are invalid")
    observed = (
        observed_repository_identity,
        observed_provider_identity,
        observed_operation,
    )
    if any(not _nonempty(value) for value in observed):
        raise ValueError(
            "observed repository/provider/operation identities must be non-empty"
        )
    expected_observed = (
        str(descriptor.get("repository_identity") or ""),
        str(descriptor.get("provider_identity") or ""),
        str(descriptor.get("operation") or ""),
    )
    if observed != expected_observed:
        raise ValueError(
            "observed execution identity does not match frozen batch descriptor"
        )
    if controller_status not in {"COMPLETED", "TIMEOUT"}:
        raise ValueError("controller_status must be COMPLETED or TIMEOUT")
    expected = _targets(list(descriptor.get("targets") or ()))
    normalized: list[dict[str, object]] = []
    for row in results:
        target = str(row.get("target") or "")
        status = str(row.get("status") or "")
        if target not in expected or status not in {"PASS", "FAIL"}:
            raise ValueError("results must use batch targets with PASS/FAIL status")
        normalized.append(
            {
                "target": target,
                "status": status,
                "failure_identity": str(row.get("failure_identity") or ""),
                "followup_required": bool(row.get("followup_required", False)),
                "followup_reason": str(row.get("followup_reason") or ""),
            }
        )
    if len({row["target"] for row in normalized}) != len(normalized):
        raise ValueError("results must not repeat targets")
    actual = [row["target"] for row in normalized]
    if controller_status == "COMPLETED":
        if actual != expected:
            raise ValueError("completed receipt must cover every target in order")
        status = (
            PRODUCT_FAILURE
            if any(row["status"] == "FAIL" for row in normalized)
            else COMPLETE_PASS
        )
        batch_timeout_ms = descriptor.get("batch_timeout_ms")
        if (
            isinstance(batch_timeout_ms, int)
            and not isinstance(batch_timeout_ms, bool)
            and elapsed_ms > batch_timeout_ms
        ):
            status = INCOMPLETE_BUDGET_EXCEEDED
    else:
        if actual != expected[: len(actual)]:
            raise ValueError("timeout receipt may contain only completed target prefix")
        status = INCOMPLETE_CONTROLLER_TIMEOUT
    semantic: dict[str, object] = {
        "schema": RECEIPT_SCHEMA,
        "manifest_identity": descriptor["manifest_identity"],
        "repository_identity": descriptor["repository_identity"],
        "provider_identity": descriptor["provider_identity"],
        "operation": descriptor["operation"],
        "batch_identity": descriptor["batch_identity"],
        "batch_index": descriptor["batch_index"],
        "subbatch_index": descriptor.get("subbatch_index"),
        "parent_batch_identity": descriptor.get("parent_batch_identity"),
        "targets": expected,
        "results": normalized,
        "execution_class": execution_class,
        "observed_repository_identity": observed_repository_identity,
        "observed_provider_identity": observed_provider_identity,
        "observed_operation": observed_operation,
        "controller_status": controller_status,
        "elapsed_ms": elapsed_ms,
        "controller_budget_ms": descriptor.get("controller_budget_ms"),
        "batch_timeout_ms": descriptor.get("batch_timeout_ms"),
        "minimum_headroom_ms": descriptor.get("minimum_headroom_ms"),
        "status": status,
        "product_failure": status == PRODUCT_FAILURE,
    }
    return {**semantic, "receipt_identity": _identity(semantic)}


def _receipt_identity_matches(receipt: Mapping[str, object]) -> bool:
    identity = receipt.get("receipt_identity")
    if not _nonempty(identity):
        return False
    semantic = {
        key: value for key, value in receipt.items() if key != "receipt_identity"
    }
    return identity == _identity(semantic)


def collapse_subbatch_receipts(
    parent_batch: Mapping[str, object],
    subbatches: Sequence[Mapping[str, object]],
    receipts: Sequence[Mapping[str, object]],
    *,
    execution_class: str,
) -> dict[str, object]:
    """Collapse a complete deterministic subdivision to parent-batch semantics."""
    children = list(subbatches)
    if not children or len(receipts) != len(children):
        raise ValueError("every subbatch receipt is required")
    ordered = sorted(receipts, key=lambda row: int(row.get("subbatch_index", -1)))
    results: list[Mapping[str, object]] = []
    elapsed = 0
    for child, receipt in zip(children, ordered, strict=True):
        if not _receipt_identity_matches(receipt) or any(
            receipt.get(key) != expected
            for key, expected in (
                ("schema", RECEIPT_SCHEMA),
                ("batch_identity", child.get("batch_identity")),
                ("parent_batch_identity", parent_batch.get("batch_identity")),
                ("manifest_identity", parent_batch.get("manifest_identity")),
                ("repository_identity", parent_batch.get("repository_identity")),
                ("provider_identity", parent_batch.get("provider_identity")),
            )
        ):
            raise ValueError("subbatch receipt identity mismatch")
        elapsed += int(receipt.get("elapsed_ms") or 0)
        if receipt.get("status") in {
            INCOMPLETE_CONTROLLER_TIMEOUT,
            INCOMPLETE_BUDGET_EXCEEDED,
        }:
            return build_receipt(
                parent_batch,
                results=results,
                execution_class=execution_class,
                elapsed_ms=elapsed,
                observed_repository_identity=str(parent_batch["repository_identity"]),
                observed_provider_identity=str(parent_batch["provider_identity"]),
                observed_operation=str(parent_batch["operation"]),
                controller_status="TIMEOUT",
            )
        results.extend(list(receipt.get("results") or ()))
    return build_receipt(
        parent_batch,
        results=results,
        execution_class=execution_class,
        elapsed_ms=elapsed,
        observed_repository_identity=str(parent_batch["repository_identity"]),
        observed_provider_identity=str(parent_batch["provider_identity"]),
        observed_operation=str(parent_batch["operation"]),
    )


def aggregate_receipts(
    manifest: Mapping[str, object], receipts: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    """Aggregate only complete identity-matched parent receipts; missing stays incomplete."""
    if manifest.get("schema") != MANIFEST_SCHEMA or not _nonempty(
        manifest.get("manifest_identity")
    ):
        raise ValueError("manifest must be identified")
    count = int(manifest.get("batch_count") or 0)
    by_index: dict[int, Mapping[str, object]] = {}
    for receipt in receipts:
        if (
            receipt.get("schema") != RECEIPT_SCHEMA
            or receipt.get("subbatch_index") is not None
            or not _receipt_identity_matches(receipt)
        ):
            raise ValueError("aggregate accepts intact parent batch receipts only")
        if any(
            receipt.get(key) != manifest.get(key)
            for key in (
                "manifest_identity",
                "repository_identity",
                "provider_identity",
                "operation",
            )
        ):
            raise ValueError("receipt identity does not belong to manifest")
        if any(
            receipt.get(observed_key) != manifest.get(expected_key)
            for observed_key, expected_key in (
                ("observed_repository_identity", "repository_identity"),
                ("observed_provider_identity", "provider_identity"),
                ("observed_operation", "operation"),
            )
        ):
            raise ValueError(
                "receipt observed execution identity does not belong to manifest"
            )
        index = receipt.get("batch_index")
        if not isinstance(index, int) or not 0 <= index < count or index in by_index:
            raise ValueError("receipt batch index is invalid/duplicated")
        if (
            receipt.get("batch_identity")
            != batch_descriptor(manifest, index)["batch_identity"]
        ):
            raise ValueError("receipt batch identity does not match manifest")
        by_index[index] = receipt
    missing = [index for index in range(count) if index not in by_index]
    ordered = [by_index[index] for index in range(count) if index in by_index]
    timeouts = [
        int(row["batch_index"])
        for row in ordered
        if row.get("status") == INCOMPLETE_CONTROLLER_TIMEOUT
    ]
    budget_exceeded = [
        int(row["batch_index"])
        for row in ordered
        if row.get("status") == INCOMPLETE_BUDGET_EXCEEDED
    ]
    failed = [
        int(row["batch_index"])
        for row in ordered
        if row.get("status") == PRODUCT_FAILURE
    ]
    status = (
        INCOMPLETE
        if missing or timeouts or budget_exceeded
        else PRODUCT_FAILURE
        if failed
        else COMPLETE_PASS
    )
    semantic: dict[str, object] = {
        "schema": AGGREGATE_SCHEMA,
        "manifest_identity": manifest["manifest_identity"],
        "repository_identity": manifest["repository_identity"],
        "provider_identity": manifest["provider_identity"],
        "operation": manifest["operation"],
        "status": status,
        "complete": status in {COMPLETE_PASS, PRODUCT_FAILURE},
        "product_failure": status == PRODUCT_FAILURE,
        "received_batch_count": len(by_index),
        "expected_batch_count": count,
        "missing_batch_indexes": missing,
        "controller_timeout_batch_indexes": timeouts,
        "budget_exceeded_batch_indexes": budget_exceeded,
        "failed_batch_indexes": failed,
        "passed_targets": sum(
            1
            for row in ordered
            for result in row.get("results", ())
            if isinstance(result, Mapping) and result.get("status") == "PASS"
        ),
        "failed_targets": sum(
            1
            for row in ordered
            for result in row.get("results", ())
            if isinstance(result, Mapping) and result.get("status") == "FAIL"
        ),
    }
    return {**semantic, "aggregate_identity": _identity(semantic)}


def derive_followup_manifest(
    parent_manifest: Mapping[str, object],
    receipts: Sequence[Mapping[str, object]],
    *,
    operation: str,
    batch_size: int = 5,
) -> dict[str, object] | None:
    """Derive an expensive follow-up campaign only from complete parent receipts."""
    aggregate = aggregate_receipts(parent_manifest, receipts)
    if not aggregate["complete"]:
        raise ValueError("cannot derive follow-up targets from incomplete evidence")
    selected: list[dict[str, object]] = []
    receipt_identities: list[str] = []
    for receipt in sorted(receipts, key=lambda row: int(row.get("batch_index", -1))):
        receipt_identities.append(str(receipt["receipt_identity"]))
        for result in receipt.get("results", ()):
            if not isinstance(result, Mapping):
                continue
            if (
                result.get("status") == "FAIL"
                or result.get("followup_required") is True
            ):
                selected.append(
                    {
                        "target": str(result.get("target") or ""),
                        "status": str(result.get("status") or ""),
                        "reason": str(result.get("followup_reason") or ""),
                    }
                )
    if not selected:
        return None
    parent_targets = _targets(list(parent_manifest.get("targets") or ()))
    selected_by_target = {str(row["target"]): row for row in selected}
    ordered_targets = [
        target for target in parent_targets if target in selected_by_target
    ]
    selection_semantic: dict[str, object] = {
        "parent_manifest_identity": parent_manifest["manifest_identity"],
        "parent_aggregate_identity": aggregate["aggregate_identity"],
        "receipt_identities": receipt_identities,
        "selected": [selected_by_target[target] for target in ordered_targets],
    }
    selection_identity = _identity(selection_semantic)
    return build_manifest(
        targets=ordered_targets,
        repository_identity=str(parent_manifest["repository_identity"]),
        provider_identity=str(parent_manifest["provider_identity"]),
        operation=operation,
        batch_size=batch_size,
        controller_budget_ms=(
            int(parent_manifest["controller_budget_ms"])
            if "controller_budget_ms" in parent_manifest
            else None
        ),
        batch_timeout_ms=(
            int(parent_manifest["batch_timeout_ms"])
            if "batch_timeout_ms" in parent_manifest
            else None
        ),
        minimum_headroom_ms=int(parent_manifest.get("minimum_headroom_ms", 5_000)),
        parent_manifest_identity=str(parent_manifest["manifest_identity"]),
        selection_identity=selection_identity,
    )


def next_resume_batch(
    manifest: Mapping[str, object], receipts: Sequence[Mapping[str, object]]
) -> int | None:
    """Return the first parent batch that is missing or did not completely pass."""
    by_index = {
        int(row["batch_index"]): row
        for row in receipts
        if row.get("schema") == RECEIPT_SCHEMA
        and row.get("subbatch_index") is None
        and isinstance(row.get("batch_index"), int)
    }
    for index in range(int(manifest.get("batch_count") or 0)):
        receipt = by_index.get(index)
        if receipt is None or receipt.get("status") != COMPLETE_PASS:
            return index
    return None
