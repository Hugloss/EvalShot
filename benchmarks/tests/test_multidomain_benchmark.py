from __future__ import annotations

import json
import contextlib
import io
import shutil
import subprocess
import tempfile
import unittest
import os
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from benchmarks.__main__ import main as benchmark_main
from benchmarks.evidence import (
    grade_direct_claims,
    load_cases,
    main as evidence_main,
    report,
    review_digest,
    run_case,
)
from benchmarks.harness.admission import TrialAdmissionError, _materialize_fixtures
from benchmarks.harness.suite import SuiteError, load_suite
from benchmarks.harness.source import materialize_repository


ROOT = (
    Path(__file__).resolve().parents[1]
    / "suites/repository-intelligence/multidomain-v2"
)


class MultidomainCorpusTests(unittest.TestCase):
    def test_check_rejects_required_value_missing_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / ".env"
            env_file.write_text(
                f"BENCHMARK_SUITE_PATH={ROOT}\nBENCHMARK_OPENCODE_AGENT=build\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"HASHMARKS_BENCH_SOURCE": "/exported"}):
                with self.assertRaisesRegex(
                    SystemExit,
                    "missing explicit benchmark setting.*HASHMARKS_BENCH_SOURCE",
                ):
                    benchmark_main(
                        [
                            "check",
                            "--suite",
                            str(ROOT),
                            "--env-file",
                            str(env_file),
                        ]
                    )

    def test_frozen_corpus_is_balanced_and_agent_suite_has_216_definitions(
        self,
    ) -> None:
        cases = load_cases(ROOT / "evidence.json")
        suite = load_suite(ROOT)
        self.assertEqual(len(cases), 60)
        self.assertEqual(len(suite.tasks), 36)
        self.assertEqual(len(suite.trial_definitions()), 216)
        self.assertEqual(
            Counter(case["repository_name"] for case in cases),
            {
                "hashmarks": 15,
                "doctor-scheduler-core": 15,
                "doctor-scheduler-frontend": 15,
                "uv-fleet-updater": 15,
            },
        )
        self.assertEqual(
            sum(case["review"]["state"] == "approved" for case in cases), 0
        )
        ambiguous = [
            case
            for case in cases
            if case["family"] == "code_owners"
            and case["expected"]["owner_state"] == "ambiguous"
        ]
        self.assertEqual(len(ambiguous), 2)
        self.assertTrue(all(case["expected"]["path"] is None for case in ambiguous))

    def test_changed_fixture_invalidates_both_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "suite"
            shutil.copytree(ROOT, root)
            fixture = (
                root / load_cases(root / "evidence.json")[0]["fixture"]["artifact"]
            )
            fixture.write_text(
                fixture.read_text(encoding="utf-8") + "changed\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "fixture missing or changed"):
                load_cases(root / "evidence.json")
            with self.assertRaisesRegex(SuiteError, "checksum mismatch"):
                load_suite(root)

    def test_fixture_materialization_checks_bytes_and_workspace_scope(self) -> None:
        case = load_cases(ROOT / "evidence.json")[0]
        fixture = case["fixture"]
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir()
            _materialize_fixtures(ROOT, workspace, [fixture])
            self.assertEqual(
                (workspace / fixture["target"]).read_bytes(),
                (ROOT / fixture["artifact"]).read_bytes(),
            )
            with self.assertRaisesRegex(TrialAdmissionError, "already exists"):
                _materialize_fixtures(ROOT, workspace, [fixture])
            unsafe = {**fixture, "target": "../escaped.log"}
            with self.assertRaisesRegex(TrialAdmissionError, "escapes trial workspace"):
                _materialize_fixtures(ROOT, workspace, [unsafe])

    def test_approval_needs_two_distinct_reviewers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "suite"
            shutil.copytree(ROOT, root)
            path = root / "evidence.json"
            corpus = json.loads(path.read_text(encoding="utf-8"))
            case = corpus["cases"][0]
            digest = review_digest(case)
            case["review"] = {
                "state": "approved",
                "approvals": [{"reviewer": "one", "case_sha256": digest}],
            }
            path.write_text(json.dumps(corpus), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "two independent reviewers"):
                load_cases(path)
            case["review"]["approvals"].append(
                {"reviewer": "two", "case_sha256": digest}
            )
            path.write_text(json.dumps(corpus), encoding="utf-8")
            load_cases(path)
            case["question"] += " changed"
            path.write_text(json.dumps(corpus), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale case review approval"):
                load_cases(path)

    def test_ambiguous_owner_cannot_be_graded_as_unique(self) -> None:
        case = next(
            row
            for row in load_cases(ROOT / "evidence.json")
            if row["id"] == "code_owners-02"
        )
        packet = {
            "schema": "hashmarks.task-evidence.v2",
            "evidence_packet_identity": "packet-id",
            "evidence_receipt": {
                "repository_identity": "repo-id",
                "evidence_identity": "evidence-id",
            },
            "ownership": {
                "owner": {"path": "hashmarks/mcp_surface.py"},
                "ambiguity": {"ambiguous": False},
            },
        }
        conflict = grade_direct_claims(case, "hashmarks", json.dumps(packet))
        self.assertEqual(conflict["answer_correctness"], "FAIL")
        self.assertEqual(conflict["authority_claims"], "CONFLICT")
        packet["ownership"]["ambiguity"]["ambiguous"] = True
        self.assertEqual(
            grade_direct_claims(case, "hashmarks", json.dumps(packet))[
                "authority_claims"
            ],
            "CONFLICT",
        )
        packet["ownership"] = {"owner": None, "ambiguity": {"ambiguous": True}}
        abstained = grade_direct_claims(case, "hashmarks", json.dumps(packet))
        self.assertEqual(abstained["answer_correctness"], "PASS")
        self.assertEqual(abstained["abstention"], "CORRECT")

    def test_missing_direct_results_are_visible(self) -> None:
        cases = load_cases(ROOT / "evidence.json")
        with tempfile.TemporaryDirectory() as temporary:
            outcome = report(cases, Path(temporary), ("hashmarks", "enola"))
        self.assertEqual(outcome["expected"], 120)
        self.assertEqual(outcome["observed"], 0)
        self.assertEqual(len(outcome["missing"]), 120)
        self.assertFalse(outcome["complete"])

    def test_noncomparable_native_probe_is_recorded_without_product_failure(
        self,
    ) -> None:
        cases = load_cases(ROOT / "evidence.json")
        case = next(row for row in cases if row["id"] == "logs-00")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = run_case(
                case,
                corpus_root=ROOT,
                subject_id="enola",
                cache=root / "cache",
                work=root / "work",
                results=root / "results",
            )
            summary = report(cases, root / "results", ("enola",))
        self.assertEqual(receipt["status"], "NOT_COMPARABLE")
        self.assertEqual(receipt["answer_correctness"], "NOT_ASSESSED")
        self.assertEqual(
            summary["subjects"]["enola"]["logs"]["statuses"], {"NOT_COMPARABLE": 1}
        )

    def test_missing_local_hashmarks_setting_stops_before_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = root / ".env"
            env.write_text("BENCHMARK_SUITE_PATH=unused\n", encoding="utf-8")
            stderr = io.StringIO()
            with (
                contextlib.redirect_stderr(stderr),
                self.assertRaises(SystemExit) as raised,
            ):
                evidence_main(
                    [
                        "run",
                        "--corpus",
                        str(ROOT / "evidence.json"),
                        "--env-file",
                        str(env),
                        "--subject",
                        "hashmarks",
                        "--cache",
                        str(root / "cache"),
                        "--work",
                        str(root / "work"),
                        "--results",
                        str(root / "results"),
                        "--case",
                        "logs-00",
                    ]
                )
            self.assertNotEqual(raised.exception.code, 0)
            self.assertIn("HASHMARKS_BENCH_SOURCE", stderr.getvalue())
            self.assertFalse((root / "work").exists())

    def test_pinned_source_cache_does_not_fetch_known_commit_again(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            subprocess.run(("git", "init", "-q", str(source)), check=True)
            (source / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
            subprocess.run(("git", "-C", str(source), "add", "example.py"), check=True)
            subprocess.run(
                (
                    "git",
                    "-C",
                    str(source),
                    "-c",
                    "user.name=Benchmark",
                    "-c",
                    "user.email=benchmark@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ),
                check=True,
            )
            commit = subprocess.check_output(
                ("git", "-C", str(source), "rev-parse", "HEAD"), text=True
            ).strip()
            tree = subprocess.check_output(
                ("git", "-C", str(source), "rev-parse", "HEAD^{tree}"), text=True
            ).strip()
            repository = {"url": str(source), "commit": commit, "tree": tree}
            cache = root / "cache"
            materialize_repository(
                repository=repository, destination=root / "first", cache_root=cache
            )
            from benchmarks.harness import source as source_module

            commands = []
            original = source_module._run_git

            def record(directory, argv, **kwargs):
                commands.append(argv)
                return original(directory, argv, **kwargs)

            with patch.object(source_module, "_run_git", side_effect=record):
                materialize_repository(
                    repository=repository, destination=root / "second", cache_root=cache
                )
            self.assertFalse(any(argv[1] == "fetch" for argv in commands))
            self.assertEqual(
                (root / "second/example.py").read_text(encoding="utf-8"), "VALUE = 1\n"
            )


if __name__ == "__main__":
    unittest.main()
