"""A shared answer is not a shared task, nor is an unqualified pilot headroom."""

from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.dogfood.hashmarks_headroom import build_readiness, main


def task(name, membership, symbol="paths_under", path="src/store.py", *, commit="a"*40,
         digest="b"*64, source="VERIFIED_PATH", problems=None):
    return {
        "suite": "heldout-v1", "task": name,
        "family": "known-symbol" if membership == "UNASSIGNED" else "python-localization",
        "mode": "read_only", "commit": commit, "tree": "c"*40,
        "task_sha256": "d"*64, "task_semantic_digest": digest,
        "experiment_membership": membership, "source": source,
        "review": "MATCH", "oracle_path": path, "oracle_symbol": symbol,
        "problems": list(problems or []),
    }


def audit():
    data = [
        task("lookup-paths-under", "UNASSIGNED", digest="1"*64),
        task("lookup-stale", "UNASSIGNED", symbol="_sync_remove_stale_paths",
             path="src/lifecycle.py", digest="2"*64),
        task("lookup-missing", "UNASSIGNED", symbol="runPollDelay", path="ux/run.ts",
             source="ORACLE_PATH_MISSING", problems=["ORACLE_PATH_MISSING"]),
        task("locate-prefix", "ASSIGNED", digest="3"*64),
        task("locate-stale", "ASSIGNED", symbol="_sync_remove_stale_paths",
             path="src/lifecycle.py", digest="4"*64),
    ]
    return {
        "schema": "evalshot.hashmarks-dogfood-audit.v1",
        "repository": "github.com/hugloss/hashmarks",
        "qualification": "MODEL_FREE_ONLY", "summary": {"tasks": len(data)},
        "requirements": {"audit_passed": True}, "tasks": data,
    }


def pilot():
    return {
        "schema": "evalshot.native-evidence-pilot-execution-summary.v1",
        "pilot_measurement_qualified": False,
        "campaign_id": "pinned-diagnostic-only",
        "expected_trials": 3,
        "observed_trials": 3,
        "trials": [
            {"task_id": "locate-prefix", "qualified_success": None,
             "status": "INCOMPLETE"},
            {"task_id": "locate-prefix", "qualified_success": None,
             "status": "CONTAMINATED"},
            {"task_id": "other-task", "qualified_success": None,
             "status": "INCOMPLETE"},
        ],
    }


def selected(report, name):
    return next(row for row in report["candidates"] if row["task"] == name)


class HeadroomReadinessTests(unittest.TestCase):
    def test_related_assigned_target_is_explicitly_not_equivalent_task(self):
        result = build_readiness(audit())
        row = selected(result, "lookup-paths-under")
        self.assertEqual(row["state"], "RELATED_ASSIGNED_TARGET_NOT_BASELINE_EVIDENCE")
        self.assertEqual([r["task"] for r in row["related_assigned_expectations"]],
                         ["locate-prefix"])
        self.assertEqual(row["related_assigned_expectations"][0]["relation"],
                         "SAME_PINNED_EXPECTATION_NOT_EQUIVALENT_TASK")
        self.assertFalse(row["related_assigned_expectations"][0]["same_task_identity"])
        self.assertFalse(row["candidate_has_qualified_native_baseline"])

    def test_second_distinct_owner_expectation_matches_only_its_analog(self):
        row = selected(build_readiness(audit()), "lookup-stale")
        self.assertEqual([r["task"] for r in row["related_assigned_expectations"]],
                         ["locate-stale"])
        self.assertEqual(row["headroom_status"], "UNKNOWN")

    def test_missing_source_rejects_analog_and_stays_blocked(self):
        result = build_readiness(audit())
        row = selected(result, "lookup-missing")
        self.assertEqual(row["state"], "SOURCE_PRECONDITION_FAILED")
        self.assertEqual(row["related_assigned_expectations"], [])
        self.assertIn("ORACLE_PATH_MISSING", row["source_problems"])
        self.assertEqual(result["summary"]["source_blocked"], 1)

    def test_same_symbol_different_commit_is_not_same_expectation(self):
        document = audit()
        document["tasks"][3]["commit"] = "e"*40
        row = selected(build_readiness(document), "lookup-paths-under")
        self.assertEqual(row["state"], "NO_ASSIGNED_SAME_EXPECTATION_ANALOG")
        self.assertFalse(row["candidate_has_qualified_native_baseline"])

    def test_same_symbol_different_path_is_not_same_expectation(self):
        document = audit()
        document["tasks"][3]["oracle_path"] = "src/other.py"
        row = selected(build_readiness(document), "lookup-paths-under")
        self.assertEqual(row["state"], "NO_ASSIGNED_SAME_EXPECTATION_ANALOG")

    def test_semantic_digest_unknown_never_becomes_same_task_identity(self):
        document = audit()
        document["tasks"][0].pop("task_semantic_digest")
        row = selected(build_readiness(document), "lookup-paths-under")
        self.assertIsNone(row["task_semantic_digest"])
        self.assertFalse(row["related_assigned_expectations"][0]["same_task_identity"])

    def test_equal_digest_only_reports_identity_not_headroom(self):
        document = audit()
        document["tasks"][0]["task_semantic_digest"] = "3"*64
        row = selected(build_readiness(document), "lookup-paths-under")
        self.assertTrue(row["related_assigned_expectations"][0]["same_task_identity"])
        self.assertEqual(row["native_baseline_status"], "UNKNOWN_NOT_MEASURED_FOR_EXACT_TASK")
        self.assertFalse(row["candidate_has_qualified_native_baseline"])

    def test_unqualified_historical_pilot_can_be_named_but_not_scored(self):
        report = build_readiness(audit(), historical_pilot=pilot())
        self.assertEqual(report["historical_pilot"]["status"],
                         "UNQUALIFIED_EXECUTION_DIAGNOSTIC_ONLY")
        self.assertIsNone(report["historical_pilot"]["qualified_outcomes"])
        self.assertEqual(report["historical_pilot"]["headroom_evidence"], "UNKNOWN")
        self.assertTrue(selected(report, "lookup-paths-under")[
            "related_assigned_expectations"][0]["historical_pilot_task_present"])
        self.assertFalse(selected(report, "lookup-stale")[
            "related_assigned_expectations"][0]["historical_pilot_task_present"])
        self.assertEqual(report["summary"]["headroom_established"], 0)
        self.assertEqual(report["summary"]["effectiveness_claims"], 0)

    def test_even_qualified_flag_in_summary_does_not_grant_baseline_authority(self):
        history = pilot()
        history["pilot_measurement_qualified"] = True
        report = build_readiness(audit(), historical_pilot=history)
        self.assertEqual(report["historical_pilot"]["status"],
                         "QUALIFIED_CLAIM_REQUIRES_CANONICAL_REPORT")
        self.assertIsNone(report["historical_pilot"]["qualified_outcomes"])
        self.assertEqual(report["summary"]["qualified_exact_task_baselines"], 0)

    def test_pilot_count_mismatch_is_rejected(self):
        history = pilot()
        history["observed_trials"] = 18
        with self.assertRaisesRegex(ValueError, "trial count"):
            build_readiness(audit(), historical_pilot=history)

    def test_unrecognized_pilot_schema_is_rejected(self):
        history = pilot()
        history["schema"] = "forged"
        with self.assertRaisesRegex(ValueError, "summary schema"):
            build_readiness(audit(), historical_pilot=history)

    def test_malformed_summary_cannot_make_unknown_zero(self):
        history = pilot()
        history["pilot_measurement_qualified"] = "false"
        with self.assertRaisesRegex(ValueError, "boolean"):
            build_readiness(audit(), historical_pilot=history)

    def test_source_audit_from_other_repo_is_rejected(self):
        document = audit()
        document["repository"] = "github.com/elsewhere/other"
        with self.assertRaisesRegex(ValueError, "Hashmarks"):
            build_readiness(document)

    def test_deterministic_and_nonmutating(self):
        doc, history = audit(), pilot()
        saved = deepcopy((doc, history))
        report = build_readiness(doc, historical_pilot=history)
        self.assertEqual(report, build_readiness(doc, historical_pilot=history))
        self.assertEqual((doc, history), saved)
        self.assertEqual(report["summary"]["candidate_definitions"], 3)

    def test_cli_preserves_inputs_and_emits_only_derived_report(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = [Path(temp)/name for name in ("audit.json", "pilot.json", "report.json")]
            paths[0].write_text(json.dumps(audit()))
            paths[1].write_text(json.dumps(pilot()))
            first, second = paths[0].read_bytes(), paths[1].read_bytes()
            with redirect_stdout(StringIO()) as out:
                status = main(["--audit", str(paths[0]), "--historical-pilot",
                               str(paths[1]), "--output", str(paths[2])])
            self.assertEqual(status, 0)
            self.assertEqual(paths[0].read_bytes(), first)
            self.assertEqual(paths[1].read_bytes(), second)
            self.assertEqual(json.loads(out.getvalue()), json.loads(paths[2].read_text()))

    def test_cli_prevents_source_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/"audit.json"
            source.write_text(json.dumps(audit()))
            with self.assertRaises(SystemExit) as raised:
                main(["--audit", str(source), "--output", str(source)])
            self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
