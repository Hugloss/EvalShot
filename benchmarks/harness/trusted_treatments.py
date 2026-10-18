"""E238/E239: frozen, exactly assigned host-side evaluation treatments.

This module owns *experimental assignment* but does not own Harbor's model
invocations. An externally controlled host must call select_treatment()
before constructing its provider request. No native harness is silently
intercepted and no randomization or preregistration timestamp is claimed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .host_input_attestation import canonical
from .intervention_audit import validate_design

SCHEMA = "agentscookbook.trusted-host-treatment-manifest.v1"
MAX_PAYLOAD_BYTES = 262_144


def sha(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _hex(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _text(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 256


def build_manifest(design: Mapping[str, Any], *, source_contract_identity: str,
                   host_build_sha256: str, assignment_seed_sha256: str) -> dict[str, Any]:
    """Explicitly allocate EVERY arm before execution; deterministic order.

    Seed is a declared input, not proof of randomness. Across replicates the
    order is deterministic and auditable, never adjusted after observing scores.
    """
    plan = validate_design(dict(design))
    if not _text(source_contract_identity) or not _hex(host_build_sha256) or not _hex(assignment_seed_sha256):
        raise ValueError("invalid-manifest-source-or-seed-authority")
    cells = []
    for harness in plan["harnesses"]:
        for task in plan["tasks"]:
            for replicate in range(1, plan["replicates"] + 1):
                pair_id = sha([plan["campaign_id"], harness, task, replicate])[:32]
                flip = int(sha([assignment_seed_sha256, pair_id]), 16) % 2
                for ordinal, arm in enumerate(("control", "variant") if not flip else ("variant", "control"), 1):
                    cell = {
                        "trial_id": f"{pair_id}-{arm}",
                        "pair_id": pair_id,
                        "harness": harness, "task": task,
                        "replicate": replicate, "arm": arm,
                        "execution_ordinal": ordinal,
                    }
                    cells.append(cell)
    body = {
        "schema": SCHEMA, "design": plan, "design_sha256": sha(plan),
        "source_contract_identity": source_contract_identity,
        "host_build_sha256": host_build_sha256,
        "assignment_seed_sha256": assignment_seed_sha256,
        "assignments": cells,
    }
    return {**body, "manifest_sha256": sha(body)}


def verify_manifest(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema", "design", "design_sha256", "source_contract_identity",
        "host_build_sha256", "assignment_seed_sha256", "assignments",
        "manifest_sha256",
    } or value.get("schema") != SCHEMA:
        raise ValueError("invalid-treatment-manifest")
    if not _hex(value["manifest_sha256"]):
        raise ValueError("invalid-manifest-digest")
    expected = build_manifest(
        value["design"],
        source_contract_identity=value["source_contract_identity"],
        host_build_sha256=value["host_build_sha256"],
        assignment_seed_sha256=value["assignment_seed_sha256"],
    )
    if canonical(value) != canonical(expected):
        raise ValueError("treatment-manifest-tampered-or-stale")
    return expected


def assigned_cell(manifest: Mapping[str, Any], *, trial_id: str,
                  harness: str, task: str, replicate: int, arm: str) -> dict[str, Any]:
    frozen = verify_manifest(dict(manifest))
    matches = [x for x in frozen["assignments"] if x["trial_id"] == trial_id]
    if len(matches) != 1 or any(
        matches[0][key] != value for key, value in (
            ("harness", harness), ("task", task), ("replicate", replicate), ("arm", arm)
        )
    ):
        raise ValueError("trial-assignment-mismatch")
    return matches[0]


def _payload(value: object) -> bytes:
    data = canonical(value)
    if not 0 < len(data) <= MAX_PAYLOAD_BYTES:
        raise ValueError("treatment-payload-empty-or-oversized")
    return data


def select_treatment(manifest: Mapping[str, Any], *, trial_id: str,
                     harness: str, task: str, replicate: int, arm: str,
                     current: Mapping[str, Any],
                     replaced: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Fail-before-work pure selection of exactly one model-visible result.

    Caller must enforce the selected result at the host request boundary and
    must not treat this return alone as host delivery evidence.
    Payloads require independently pinned generation and semantic identities.
    """
    frozen = verify_manifest(dict(manifest))
    assigned_cell(frozen, trial_id=trial_id, harness=harness, task=task,
                  replicate=replicate, arm=arm)
    design = frozen["design"]
    def parse_source(item: object) -> tuple[dict[str, Any], bytes]:
        if not isinstance(item, Mapping) or set(item) != {
            "generation_sha256", "semantic_sha256", "content",
        } or not _hex(item["generation_sha256"]) or not _hex(item["semantic_sha256"]):
            raise ValueError("missing-pinned-treatment-content")
        payload = _payload(item["content"])
        if item["semantic_sha256"] != hashlib.sha256(payload).hexdigest():
            raise ValueError("semantic-source-digest-does-not-match-content")
        return dict(item), payload

    primary, primary_bytes = parse_source(current)
    if design["study"] == "presentation":
        if replaced is not None:
            raise ValueError("presentation-uses-only-one-semantic-source")
        chosen, chosen_bytes = primary, primary_bytes
        presentation = "structured" if arm == "control" else "text"
        # Both arms carry semantically identical JSON, differing only in the
        # representation sent by the host, never in task ownership or source.
        visible: object = (
            primary["content"] if presentation == "structured"
            else primary_bytes.decode("utf-8")
        )
    elif design["study"] == "text_presentation":
        if replaced is not None:
            raise ValueError("text-presentation-requires-one-unchanged-source")
        chosen, chosen_bytes = primary, primary_bytes
        presentation = (
            "compact-json-text" if arm == "control" else "indented-json-text"
        )
        visible = (
            primary_bytes.decode("utf-8") if arm == "control"
            else json.dumps(
                primary["content"], sort_keys=True, ensure_ascii=False,
                allow_nan=False, indent=2,
            )
        )
        if (not isinstance(visible, str)
                or visible == primary_bytes.decode("utf-8")
                and arm == "variant"):
            raise ValueError("text-presentation-has-no-distinct-visible-surfaces")
        if json.loads(visible) != primary["content"]:
            raise ValueError("text-presentation-semantic-drift")
    elif design["study"] == "freshness":
        if replaced is None:
            raise ValueError("freshness-requires-explicit-replaced-generation")
        alternate, alternate_bytes = parse_source(replaced)
        if (alternate["generation_sha256"] == primary["generation_sha256"]
                or alternate["semantic_sha256"] == primary["semantic_sha256"]):
            raise ValueError("freshness-control-and-replacement-not-distinct")
        chosen, chosen_bytes = (
            (primary, primary_bytes) if arm == "control"
            else (alternate, alternate_bytes)
        )
        presentation = "structured"
        visible = chosen["content"]
    else:
        if replaced is not None:
            raise ValueError("timing-requires-single-unchanged-semantic-source")
        chosen, chosen_bytes = primary, primary_bytes
        presentation = "structured"
        visible = primary["content"]
    return {
        "trial_id": trial_id,
        "arm": arm,
        "study": design["study"],
        "manifest_sha256": frozen["manifest_sha256"],
        "design_sha256": frozen["design_sha256"],
        "generation_sha256": chosen["generation_sha256"],
        "current_generation_sha256": primary["generation_sha256"],
        "semantic_sha256": chosen["semantic_sha256"],
        "presentation": presentation,
        "surface_sha256": sha(visible),
        "selected_content": visible,
        **({
            "required_request_sequence": 1 if arm == "control" else 2,
            "requires_prior_tool_free_request": arm == "variant",
            "timing_extra_model_request_confound": True,
        } if design["study"] == "timing" else {}),
        "selection_is_host_delivery_proof": False,
    }


def load_manifest(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4_194_304:
        raise ValueError("unsafe-manifest-file")
    try:
        return verify_manifest(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError("invalid-or-unreadable-treatment-manifest") from exc


def freeze_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    """Create-only transport: refusing overwrite prevents silent redetermination."""
    data = canonical(verify_manifest(dict(manifest))) + b"\n"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-contract-identity", required=True)
    parser.add_argument("--host-build-sha256", required=True)
    parser.add_argument("--assignment-seed-sha256", required=True)
    args = parser.parse_args()
    try:
        if args.design.is_symlink() or args.design.stat().st_size > 65_536:
            raise ValueError("unsafe-design-file")
        design = json.loads(args.design.read_text(encoding="utf-8"))
        manifest = build_manifest(
            design, source_contract_identity=args.source_contract_identity,
            host_build_sha256=args.host_build_sha256,
            assignment_seed_sha256=args.assignment_seed_sha256,
        )
        freeze_manifest(args.output, manifest)
    except (ValueError, OSError, UnicodeError) as exc:
        parser.exit(2, f"manifest admission failed: {exc}\n")
    print(json.dumps({
        "manifest_sha256": manifest["manifest_sha256"],
        "design_sha256": manifest["design_sha256"],
        "planned_cells": len(manifest["assignments"]),
        "pre_execution_external_timestamp_attested": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
