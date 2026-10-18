from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from benchmarks.__main__ import _require_analysis_evidence, _validate_score_cli
from benchmarks.harness.report import ReportError


ROOT = Path(__file__).resolve().parents[1]
GENERIC_SCORE_SCRIPTS = (
    ROOT / "suites/repository-intelligence/heldout-v1/score.py",
    ROOT / "suites/repository-intelligence/headroom-v1/score.py",
    ROOT / "suites/repository-intelligence/headroom-v2/score.py",
    ROOT / "suites/repository-intelligence/context-invariance-v1/score.py",
    ROOT / "suites/repository-intelligence/behavioral-v3/score.py",
    ROOT / "suites/repository-intelligence/behavioral-v4/score.py",
    ROOT / "suites/repository-intelligence/multidomain-v2/score.py",
)


class ScoreContractTests(unittest.TestCase):
    def test_generic_score_scripts_accept_exact_frozen_definition_contract(self) -> None:
        for script in GENERIC_SCORE_SCRIPTS:
            with self.subTest(script=script.parent.name):
                _validate_score_cli(script, os.environ)

    def test_analysis_evidence_gate_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            score = Path(temporary) / "score.json"
            score.write_text(
                json.dumps(
                    {
                        "analysis_evidence": {
                            "evidence_state": "minimum-evidence-observed"
                        }
                    }
                ),
                encoding="utf-8",
            )
            _require_analysis_evidence(score)

            score.write_text(
                json.dumps(
                    {
                        "analysis_evidence": {
                            "evidence_state": "below-minimum"
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ReportError, "below the frozen minimum"):
                _require_analysis_evidence(score)

            score.write_text(json.dumps({"analysis_evidence": None}), encoding="utf-8")
            with self.assertRaisesRegex(ReportError, "no analysis_evidence"):
                _require_analysis_evidence(score)


if __name__ == "__main__":
    unittest.main()
