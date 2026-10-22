"""Review packets must be source-bound and cannot grant experiment authority."""

from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.dogfood.hashmarks_coverage import build_coverage
from benchmarks.dogfood.hashmarks_review_handoff import (
    HandoffError, build_handoff, main,
)


def _row(suite: str, task: str, family: str, membership: str, *,
         source: str = "VERIFIED_PATH", problems: list[str] | None = None,
         semantic: str | None = "c" * 64) -> dict[str, object]:
    return {
        "suite": suite, "task": task, "family": family, "mode": "read_only",
        "commit": "a" * 40, "task_sha256": "b" * 64,
        "task_semantic_digest": semantic, "experiment_membership": membership,
        "source": source, "review": "MATCH", "oracle_path": "src/owner.py",
        "problems": problems or [],
    }


def _audit() -> dict[str, object]:
    tasks = [
        _row("heldout", "known", "known-symbol", "UNASSIGNED",
             source="ORACLE_PATH_MISSING", problems=["ORACLE_PATH_MISSING"]),
        _row("pilot", "another-known", "known-symbol", "UNASSIGNED"),
        _row("heldout", "owner", "backend", "ASSIGNED"),
        _row("pilot", "owner", "backend", "ASSIGNED"),
    ]
    return {
        "schema": "evalshot.hashmarks-dogfood-audit.v1",
        "repository": "github.com/hugloss/hashmarks",
        "qualification": "MODEL_FREE_ONLY",
        "summary": {"tasks": len(tasks)},
        "requirements": {"audit_passed": True},
        "tasks": tasks,
    }


class ReviewHandoffTests(unittest.TestCase):
    def test_all_review_requests_remain_explicitly_blocked(self):
        audit = _audit()
        result = build_handoff(audit, build_coverage(audit))
        self.assertEqual(result["qualification"], "REVIEW_REQUESTS_ONLY")
        self.assertEqual(result["summary"]["experiment_ready"], 0)
        self.assertEqual(result["summary"]["headroom_measured"], 0)
        self.assertEqual(result["summary"]["independent_oracles_established"], 0)
        self.assertEqual(result["summary"]["review_requests"], 4)
        for packet in result["review_packets"]:
            self.assertFalse(packet["experiment_ready"])
            self.assertEqual(packet["review_status"], "REQUEST_ONLY")
            self.assertEqual(packet["headroom_status"], "UNKNOWN_NOT_MEASURED")
            self.assertEqual(packet["oracle_status"], "NOT_ESTABLISHED")
            self.assertNotIn("expected_owner", packet)
            self.assertNotIn("score", packet)
            self.assertTrue(packet["independent_evidence_required"])
            self.assertTrue(all(
                item["status"] == "NOT_PROVIDED"
                for item in packet["independent_evidence_required"]
            ))

    def test_source_mismatch_stays_blocked_even_when_audit_gate_passes(self):
        audit = _audit()
        packet = next(row for row in build_handoff(audit, build_coverage(audit))["review_packets"]
                      if row["kind"] == "DORMANT_TASK_NEEDS_REVIEW")
        self.assertEqual(packet["source_evidence"][0]["source"], "ORACLE_PATH_MISSING")
        self.assertIn("ORACLE_PATH_MISSING", packet["source_evidence"][0]["source_problems"])
        self.assertEqual(
            packet["independent_evidence_required"][0]["evidence"],
            "resolve_historical_source_discrepancy_without_rewriting_evidence",
        )

    def test_unknown_headroom_cannot_be_inferred_from_source_proof(self):
        audit = _audit()
        result = build_handoff(audit, build_coverage(audit))
        self.assertTrue(result["source_audit_gate_passed"])
        self.assertTrue(all(
            row["headroom_status"] == "UNKNOWN_NOT_MEASURED"
            for row in result["review_packets"]
        ))

    def test_rejects_a_modified_candidate_queue(self):
        audit = _audit()
        coverage = build_coverage(audit)
        coverage["candidate_review_queue"][0]["oracle_status"] = "APPROVED"
        with self.assertRaisesRegex(HandoffError, "coverage does not match"):
            build_handoff(audit, coverage)

    def test_rejects_source_audit_modified_after_coverage(self):
        audit = _audit()
        coverage = build_coverage(audit)
        audit["tasks"][0]["source"] = "VERIFIED_PATH"
        audit["tasks"][0]["problems"] = []
        with self.assertRaisesRegex(HandoffError, "coverage does not match"):
            build_handoff(audit, coverage)

    def test_binds_cross_suite_variant_to_all_conflicting_definitions(self):
        audit = _audit()
        audit["tasks"][3]["task_semantic_digest"] = "d" * 64
        result = build_handoff(audit, build_coverage(audit))
        variants = [item for item in result["review_packets"]
                    if item["kind"] == "CROSS_SUITE_SEMANTIC_VARIANT"]
        self.assertEqual(len(variants), 1)
        self.assertEqual(
            {(item["suite"], item["semantic_task_digest"])
             for item in variants[0]["source_evidence"]},
            {("heldout", "c" * 64), ("pilot", "d" * 64)},
        )
        self.assertIn(
            "reconcile_competing_task_meanings_without_promoting_an_oracle",
            [item["evidence"] for item in variants[0]["independent_evidence_required"]],
        )

    def test_family_packet_references_all_matching_frozen_definitions(self):
        audit = _audit()
        result = build_handoff(audit, build_coverage(audit))
        packet = next(item for item in result["review_packets"]
                      if item["kind"] == "FAMILY_WITHOUT_ASSIGNED_TASK")
        self.assertEqual([r["task"] for r in packet["source_evidence"]],
                         ["known", "another-known"])

    def test_legacy_missing_task_digests_are_not_promoted(self):
        audit = _audit()
        for record in audit["tasks"]:
            record.pop("task_semantic_digest")
        result = build_handoff(audit, build_coverage(audit))
        self.assertTrue(all(
            source["semantic_task_digest"] is None
            for packet in result["review_packets"]
            for source in packet["source_evidence"]
        ))

    def test_deterministic_and_no_source_mutation(self):
        audit = _audit()
        coverage = build_coverage(audit)
        original = (deepcopy(audit), deepcopy(coverage))
        first = build_handoff(audit, coverage)
        self.assertEqual(first, build_handoff(audit, coverage))
        self.assertEqual((audit, coverage), original)
        self.assertEqual(len({row["packet_id"] for row in first["review_packets"]}),
                         first["summary"]["review_requests"])

    def test_invalid_source_audit_is_never_accepted(self):
        audit = _audit()
        coverage = build_coverage(audit)
        audit["schema"] = "unexpected"
        with self.assertRaisesRegex(HandoffError, "invalid source audit"):
            build_handoff(audit, coverage)

    def test_cli_writes_only_derived_report_and_preserves_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            audit_path, coverage_path, output = [
                Path(temp) / name for name in ("audit.json", "coverage.json", "handoff.json")
            ]
            audit = _audit()
            audit_path.write_text(json.dumps(audit), encoding="utf-8")
            coverage_path.write_text(json.dumps(build_coverage(audit)), encoding="utf-8")
            original = (audit_path.read_bytes(), coverage_path.read_bytes())
            with redirect_stdout(StringIO()) as stdout:
                result = main([
                    "--audit", str(audit_path), "--coverage", str(coverage_path),
                    "--output", str(output),
                ])
            self.assertEqual(result, 0)
            self.assertEqual((audit_path.read_bytes(), coverage_path.read_bytes()), original)
            self.assertEqual(json.loads(stdout.getvalue()), json.loads(output.read_text()))

    def test_cli_refuses_replacing_any_input(self):
        with tempfile.TemporaryDirectory() as temp:
            audit, coverage = Path(temp) / "audit.json", Path(temp) / "coverage.json"
            content = _audit()
            audit.write_text(json.dumps(content))
            coverage.write_text(json.dumps(build_coverage(content)))
            for dest in (audit, coverage):
                with self.subTest(dest=dest):
                    with self.assertRaises(SystemExit) as error:
                        main(["--audit", str(audit), "--coverage", str(coverage),
                              "--output", str(dest)])
                    self.assertEqual(error.exception.code, 2)

    def test_cli_rejects_duplicate_input_json_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            audit, coverage = Path(temp) / "audit.json", Path(temp) / "coverage.json"
            audit.write_text('{"schema":"first","schema":"second"}')
            coverage.write_text(json.dumps(build_coverage(_audit())))
            with self.assertRaises(SystemExit) as error:
                main(["--audit", str(audit), "--coverage", str(coverage)])
            self.assertEqual(error.exception.code, 2)

    def test_cli_never_writes_on_coverage_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            audit, coverage, output = [
                Path(temp) / name for name in ("audit.json", "coverage.json", "handoff.json")
            ]
            audit.write_text(json.dumps(_audit()))
            wrong = build_coverage(_audit())
            wrong["source_audit_identity"] = "sha256:" + "0" * 64
            coverage.write_text(json.dumps(wrong))
            with self.assertRaises(SystemExit):
                main(["--audit", str(audit), "--coverage", str(coverage),
                      "--output", str(output)])
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
