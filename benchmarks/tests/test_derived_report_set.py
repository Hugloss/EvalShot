from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks.harness.identity import canonical_json, digest
from benchmarks.harness.derived_report_set import (
    DerivedReportSetError,
    REPORT_SET_ARTIFACTS,
    REPORT_SET_MANIFEST,
    inspect_report_set,
    publish_report_set,
    verified_score_path,
    verify_report_set,
)


class DerivedReportSetTests(unittest.TestCase):
    def _publish(self, root: Path, marker: str) -> dict:
        return publish_report_set(
            run_root=root,
            run_id="000001",
            report={"kind": "report", "marker": marker},
            trace_diagnostics={"kind": "trace", "marker": marker},
            decision_evidence={"kind": "decision", "marker": marker},
            score_artifact="score.json",
            score_bytes=(
                json.dumps({"kind": "score", "marker": marker}, sort_keys=True)
                + "\n"
            ).encode("utf-8"),
        )

    def test_publish_binds_exact_persisted_report_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._publish(root, "one")

            self.assertEqual(
                manifest,
                verify_report_set(
                    run_root=root,
                    expected_run_id="000001",
                ),
            )
            self.assertEqual(
                set(manifest["artifacts"]),
                set(REPORT_SET_ARTIFACTS) | {"score.json"},
            )
            self.assertEqual(manifest["score_artifact"], "score.json")
            self.assertEqual(
                verified_score_path(
                    run_root=root,
                    expected_run_id="000001",
                ),
                root / "reports" / "score.json",
            )
            self.assertTrue((root / "reports" / REPORT_SET_MANIFEST).is_file())

            report = json.loads(
                (root / "reports" / "report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report["marker"], "one")

    def test_mutated_score_fails_generation_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._publish(root, "one")
            (root / "reports" / "score.json").write_text(
                json.dumps({"kind": "score", "marker": "tampered"}) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                DerivedReportSetError,
                "does not match manifest: score.json",
            ):
                verified_score_path(
                    run_root=root,
                    expected_run_id="000001",
                )

    def test_mutated_artifact_fails_manifest_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._publish(root, "one")
            (root / "reports" / "decision-evidence.json").write_text(
                json.dumps({"kind": "decision", "marker": "tampered"}) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                DerivedReportSetError,
                "does not match manifest: decision-evidence.json",
            ):
                verify_report_set(
                    run_root=root,
                    expected_run_id="000001",
                )
            health = inspect_report_set(
                run_root=root,
                expected_run_id="000001",
            )
            self.assertEqual(health["status"], "INVALID")
            self.assertFalse(health["verified"])
            self.assertIn("decision-evidence.json", health["reason"])

    def test_interrupted_republication_cannot_look_like_complete_generation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self._publish(root, "one")
            real_replace = os.replace
            artifact_replacements = 0

            def interrupted_replace(source, destination):
                nonlocal artifact_replacements
                if Path(destination).name in REPORT_SET_ARTIFACTS:
                    artifact_replacements += 1
                    if artifact_replacements == 2:
                        raise OSError("simulated publication interruption")
                return real_replace(source, destination)

            with (
                mock.patch(
                    "benchmarks.harness.derived_report_set.os.replace",
                    side_effect=interrupted_replace,
                ),
                self.assertRaisesRegex(OSError, "simulated publication interruption"),
            ):
                self._publish(root, "two")

            manifest_bytes = (
                root / "reports" / REPORT_SET_MANIFEST
            ).read_bytes()
            self.assertEqual(
                json.loads(manifest_bytes)["generation_id"],
                first["generation_id"],
            )
            with self.assertRaisesRegex(
                DerivedReportSetError,
                "does not match manifest",
            ):
                verify_report_set(
                    run_root=root,
                    expected_run_id="000001",
                )

    def test_v1_report_generation_is_readable_but_score_is_not_authoritative(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reports = root / "reports"
            reports.mkdir()
            artifacts = {}
            for name in REPORT_SET_ARTIFACTS:
                payload = (json.dumps({"artifact": name}) + "\n").encode("utf-8")
                (reports / name).write_bytes(payload)
                artifacts[name] = {
                    "sha256": __import__("hashlib").sha256(payload).hexdigest(),
                    "bytes": len(payload),
                }
            identity = {
                "contract": "agents-cookbook-derived-report-set.v1",
                "run_id": "000001",
                "artifacts": artifacts,
            }
            manifest = {**identity, "generation_id": digest(identity)}
            (reports / REPORT_SET_MANIFEST).write_bytes(canonical_json(manifest))
            (reports / "score.json").write_text(
                json.dumps({"legacy": True}) + "\n",
                encoding="utf-8",
            )

            health = inspect_report_set(
                run_root=root,
                expected_run_id="000001",
            )
            self.assertEqual(health["status"], "VERIFIED_REPORT_ONLY")
            self.assertTrue(health["verified"])
            self.assertFalse(health["score_bound"])
            with self.assertRaisesRegex(
                DerivedReportSetError,
                "score is not generation-bound",
            ):
                verified_score_path(
                    run_root=root,
                    expected_run_id="000001",
                )

    def test_legacy_unbound_reports_are_never_claimed_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reports = root / "reports"
            reports.mkdir()
            (reports / "report.json").write_text("{}\n", encoding="utf-8")

            health = inspect_report_set(
                run_root=root,
                expected_run_id="legacy",
            )
            self.assertEqual(health["status"], "UNBOUND")
            self.assertFalse(health["verified"])
            self.assertEqual(health["artifacts_present"], ["report.json"])

    def test_manifest_is_bound_to_saved_run_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._publish(root, "one")
            with self.assertRaisesRegex(
                DerivedReportSetError,
                "run identity mismatch",
            ):
                verify_report_set(
                    run_root=root,
                    expected_run_id="000002",
                )


if __name__ == "__main__":
    unittest.main()
