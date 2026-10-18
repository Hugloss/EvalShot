"""Host-observed nested search receipts: matched positive proof and attacks."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.harness.bundle_writer import publish_bundle
from benchmarks.harness.host_input_attestation import canonical
from benchmarks.harness.host_nested_search import (
    SCHEMA,
    audit_attested_nested_pair,
    host_capture_nested_observations,
    verify_nested_observations,
    write_host_nested_receipt,
)
from benchmarks.harness.identity import canonical_json, digest

KEY = b"host-only-independent-boundary-secret-for-tests"
BUILD = "d" * 64
IDENTITY = "verified-provider-host"
CAMPAIGN = "frozen-campaign"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def step(tool: str, call_id: str, **arguments: object) -> dict:
    return {
        "source": "agent",
        "tool_calls": [{
            "function_name": tool,
            "tool_call_id": call_id,
            "arguments": arguments,
        }],
    }


def trace(*, bare: bool = True) -> dict:
    if bare:
        steps = [
            step("rg", "direct-search", pattern="owner"),
            step("functions.exec", "router", action="call"),
            step("read_file", "direct-read", path="src/owner.py"),
        ]
    else:
        steps = [
            step("mcp__hashmarks__task_evidence", "evidence", task="find owner"),
            step("shell", "shell-a", command="rg other"),
        ]
    return {"schema_version": "ATIF-v1.8", "steps": steps}


def capture(parent: str, *children: dict) -> dict:
    return {
        "parent_call_id_sha256": sha(parent),
        "complete": True,
        "nested_calls": list(children),
    }


def child(tool: str, call_id: str, *, nested: list[dict] | None = None) -> dict:
    row = {"call_id_sha256": sha(call_id), "tool": tool, "input": {}}
    if nested is not None:
        row["nested_calls"] = nested
    return row


def sign(path: Path, *rows: dict, trial: str = "trial") -> dict:
    return host_capture_nested_observations(
        path, list(rows), campaign_id=CAMPAIGN, trial_id=trial,
        host_identity=IDENTITY, host_build_sha256=BUILD, key=KEY,
    )


def verified(path: Path, receipt: Path, *, trial: str = "trial") -> dict:
    return verify_nested_observations(
        path, receipt, KEY, campaign_id=CAMPAIGN, trial_id=trial,
        host_identity=IDENTITY, host_build_sha256=BUILD,
    )


class NestedHostBoundaryTests(unittest.TestCase):
    def test_host_signature_binds_expanded_router_and_nested_depth(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            atif = root / "trajectory.json"
            atif.write_bytes(canonical(trace()))
            row = capture(
                "router",
                child("rg", "nested-search-1"),
                child("functions.exec", "inner-router", nested=[
                    child("read_file", "nested-read"),
                    child("rg", "nested-search-2"),
                ]),
            )
            receipt = root / "host.json"
            write_host_nested_receipt(receipt, sign(atif, row))
            value = verified(atif, receipt)
            self.assertTrue(value["qualified"])
            self.assertTrue(value["host_key_authenticated"])
            self.assertEqual(value["verified_opaque_parents"], 1)
            self.assertEqual(value["host_asserted_nested_calls"], 4)
            self.assertEqual(value["explicit_native_search_calls"], 1)
            self.assertEqual(value["host_observed_nested_native_search_calls"], 2)
            self.assertEqual(value["combined_observed_native_search_calls"], 3)
            self.assertFalse(value["independently_proven_complete"])
            self.assertFalse(value["model_attention_proven"])
            self.assertEqual(json.loads(receipt.read_bytes())["schema"], SCHEMA)
            with self.assertRaisesRegex(ValueError, "exists"):
                write_host_nested_receipt(receipt, sign(atif, row))

    def test_missing_parent_coverage_or_unsigned_nesting_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "trace.json"
            path.write_bytes(canonical(trace()))
            for observations in (
                [],
                [capture("wrong", child("rg", "unbound"))],
                [capture("router", child("rg", "c")), capture("router")],
                [capture("router", child("functions.exec", "nested"))],
                [capture("router", child("shell", "nested-shell"))],
                [capture("router", child("rg", "duplicate"), child("rg", "duplicate"))],
                [capture("router", child("rg", "router"))],
                [capture("router", child("rg", "direct-search"))],
                [{**capture("router", child("rg", "c")), "complete": False}],
            ):
                with self.subTest(observations=observations):
                    with self.assertRaises(ValueError):
                        sign(path, *observations)

    def test_signed_host_receipt_not_replayable_to_other_source_or_trial(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "trace.json"
            path.write_bytes(canonical(trace()))
            receipt = root / "nested.json"
            write_host_nested_receipt(
                receipt, sign(path, capture("router", child("rg", "search")))
            )
            for changed in (
                {"trial_id": "other"},
                {"host_identity": "foreign-host"},
                {"host_build_sha256": "a" * 64},
                {"trajectory_sha256": "b" * 64},
            ):
                row = json.loads(receipt.read_bytes())
                row.update(changed)
                receipt.write_bytes(canonical(row))
                with self.subTest(changed=changed), self.assertRaises(ValueError):
                    verified(path, receipt)
            receipt.write_bytes(canonical(
                sign(path, capture("router", child("rg", "search")))
            ))
            path.write_bytes(canonical(trace(bare=False)))
            with self.assertRaisesRegex(ValueError, "trajectory-drift"):
                verified(path, receipt)

    def test_host_key_and_mac_replacement_cannot_be_admitted(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            atif = root / "trace.json"
            atif.write_bytes(canonical(trace()))
            receipt = root / "receipt.json"
            write_host_nested_receipt(receipt, sign(
                atif, capture("router", child("rg", "search")),
            ))
            with self.assertRaisesRegex(ValueError, "authentication"):
                verify_nested_observations(
                    atif, receipt, b"other-test-host-secret-key-for-verification",
                    campaign_id=CAMPAIGN, trial_id="trial",
                    host_identity=IDENTITY, host_build_sha256=BUILD,
                )
            forged = json.loads(receipt.read_bytes())
            forged["captures"][0]["nested_calls"].append(child("rg", "forged"))
            receipt.write_bytes(canonical(forged))
            with self.assertRaisesRegex(ValueError, "authentication"):
                verified(atif, receipt)

    def test_agent_supplied_nested_calls_cannot_be_host_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            atif = Path(folder) / "trajectory.json"
            value = trace()
            value["steps"][1]["tool_calls"][0]["nested_calls"] = [{
                "tool": "rg", "input": {"pattern": "search"},
            }]
            atif.write_bytes(canonical(value))
            with self.assertRaisesRegex(ValueError, "unbound-or-duplicate"):
                sign(atif, capture("router", child("rg", "fake")))

    def test_attested_empty_router_not_fabricated_into_search(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            atif = root / "trace.json"
            atif.write_bytes(canonical(trace(bare=False)))
            receipt = root / "signed.json"
            write_host_nested_receipt(receipt, sign(
                atif, capture("shell-a"), trial="treated",
            ))
            value = verified(atif, receipt, trial="treated")
            self.assertEqual(value["combined_observed_native_search_calls"], 0)
            self.assertTrue(value["host_asserted_complete"])
            self.assertFalse(value["independently_proven_complete"])

    def test_pair_readback_has_strict_immutable_bundle_and_host_identity(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            evidence_paths: dict[str, Path] = {}
            for subject in ("none", "hashmarks"):
                case_id = f"def-{subject}"
                trial_id = digest({"campaign_id": CAMPAIGN, "definition_id": case_id})
                job = f"h{trial_id[:24]}-a000001"
                harbor = {
                    "schema": "agentscookbook.harbor-harness-trial.v1",
                    "trial_id": job,
                    "task": "owner",
                    "harness": "codex",
                    "subject": subject,
                    "attempt": 1,
                    "model": "provider/model",
                    "status": "COMPLETE",
                    "success": True,
                    "reward": 1.0,
                    "duration_ms": 1,
                    "harbor_return_code": 0,
                    "harbor_job_root": f"/jobs/{job}",
                    "reward_path": f"/jobs/{job}/reward.txt",
                    "mcp_exposed": subject == "hashmarks",
                    "stderr_tail": "",
                }
                answer = {
                    "schema": "agentscookbook.harbor-answer-evidence.v1",
                    "observed": {"path": "src/owner.py"},
                    "expected": {"path": "src/owner.py"},
                    "match": True,
                    "tracked_clean": True,
                    "error": None,
                }
                receipt = {
                    "backend": "harbor",
                    "definition_id": case_id,
                    "trial_id": trial_id,
                    "task_id": "owner",
                    "condition_id": f"{subject}-codex",
                    "trial": 0,
                    "replicate_id": 1,
                    "harness": "codex",
                    "subject": subject,
                    "model": "provider/model",
                    "status": "PASS",
                    "harbor": harbor,
                    "execution": {
                        "campaign_id": CAMPAIGN,
                        "launch_attempt": 1,
                        "job_name": job,
                    },
                }
                publish_bundle(
                    results_root=root, trial_id=trial_id,
                    artifacts={
                        "harbor_result": ("harbor-result.json", canonical_json(harbor)),
                        "reward": ("reward.txt", b"1\n"),
                        "trajectory": ("trajectory.json", canonical_json(
                            trace(bare=subject == "none")
                        )),
                        "answer": ("answer.json", canonical_json(answer)),
                    },
                    receipt=receipt,
                )
                bundle = root / trial_id
                observations = (
                    [capture("router", child("rg", "search-a"), child("rg", "search-b"))]
                    if subject == "none" else [capture("shell-a", child("rg", "search-c"))]
                )
                external = root / f"external-{subject}.json"
                write_host_nested_receipt(external, sign(
                    bundle / "trajectory.json", *observations, trial=trial_id,
                ))
                evidence_paths[subject] = bundle
                evidence_paths[f"{subject}-attestation"] = external

            report = audit_attested_nested_pair(
                evidence_paths["none"], evidence_paths["hashmarks"],
                evidence_paths["none-attestation"],
                evidence_paths["hashmarks-attestation"], key=KEY,
                host_identity=IDENTITY, host_build_sha256=BUILD,
            )
            self.assertTrue(report["qualified"])
            self.assertEqual(report["bare"]["combined_observed_native_search_calls"], 3)
            self.assertEqual(report["hashmarks"]["combined_observed_native_search_calls"], 1)
            self.assertEqual(report["augmented_observed_native_search_delta"], -2)
            self.assertFalse(report["native_search_savings_proven"])
            self.assertFalse(report["modified_canonical_harbor_report"])

            with self.assertRaises(ValueError):
                audit_attested_nested_pair(
                    evidence_paths["hashmarks"], evidence_paths["none"],
                    evidence_paths["hashmarks-attestation"],
                    evidence_paths["none-attestation"], key=KEY,
                    host_identity=IDENTITY, host_build_sha256=BUILD,
                )
            forged = json.loads(evidence_paths["hashmarks-attestation"].read_bytes())
            forged["captures"][0]["nested_calls"][0]["tool"] = "read_file"
            evidence_paths["hashmarks-attestation"].write_bytes(canonical(forged))
            with self.assertRaisesRegex(ValueError, "authentication"):
                audit_attested_nested_pair(
                    evidence_paths["none"], evidence_paths["hashmarks"],
                    evidence_paths["none-attestation"],
                    evidence_paths["hashmarks-attestation"], key=KEY,
                    host_identity=IDENTITY, host_build_sha256=BUILD,
                )


if __name__ == "__main__":
    unittest.main()
