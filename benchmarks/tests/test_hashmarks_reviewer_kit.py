"""Blinded reviewer handoff must be source-bound, exclusive and non-authoritative."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from benchmarks.dogfood.hashmarks_blind_review import (
    STAGED_RELATIVE, SOURCE_TASK_ROOT, PARTICIPANT_SOURCE,
    PARTICIPANTS, TASKS,
)
from benchmarks.dogfood.hashmarks_reviewer_kit import (
    ReviewerKitError, prepare_reviewer_kit,
)
from benchmarks.dogfood.hashmarks_reviewer_kit_verify import (
    KitVerificationError, verify_kit,
)

ROOT = Path(__file__).resolve().parents[2]


class PinnedReviewerKitTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        self.evalshot = self.workspace / "EvalShot"
        self.stage = self.evalshot / STAGED_RELATIVE
        shutil.copytree(ROOT / STAGED_RELATIVE, self.stage)
        for task_id in TASKS:
            self._copy(SOURCE_TASK_ROOT / f"{task_id}.json")
        for relative in PARTICIPANTS:
            self._copy(PARTICIPANT_SOURCE / relative)
        self.hashmarks = self.workspace / "Hashmarks"
        self.hashmarks.mkdir()
        self.git("init", "-q")
        (self.hashmarks / "hashmarks").mkdir()
        (self.hashmarks / "hashmarks/store.py").write_text(
            "class Store:\n    def lookup(self):\n        return 7\n"
            "    def alternative(self):\n        return 8\n", encoding="utf-8"
        )
        (self.hashmarks / "README.md").write_text(
            "Pinned source only, not an oracle review.\n", encoding="utf-8"
        )
        (self.hashmarks / "nested").mkdir()
        (self.hashmarks / "nested/data.json").write_text(
            '{"sample":true}\n', encoding="utf-8"
        )
        self.freeze("initial")
        self.update_source_pin()
        self.out = self.workspace / "handoff"

    def _copy(self, relative):
        path = self.evalshot / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, path)

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.hashmarks), *args],
            check=True, capture_output=True, text=True
        ).stdout.strip()

    def freeze(self, message):
        self.git("add", "-A")
        self.git("-c", "user.email=case@example.invalid", "-c",
                 "user.name=Case", "commit", "-qm", message)
        self.commit = self.git("rev-parse", "HEAD")
        self.tree = self.git("rev-parse", "HEAD^{tree}")

    def update_source_pin(self):
        # Synthetic pin mutation is confined to the temporary test repository.
        # Do not rewrite the production frozen task definitions or the PR's
        # staged baseline.
        protocol_file = self.stage / "protocol.json"
        protocol = json.loads(protocol_file.read_text())
        protocol["source"]["commit"] = self.commit
        protocol["source"]["tree"] = self.tree
        for task_id in TASKS:
            for relative in (
                SOURCE_TASK_ROOT / f"{task_id}.json",
                STAGED_RELATIVE / "tasks" / f"{task_id}.json",
            ):
                path = self.evalshot / relative
                task = json.loads(path.read_text())
                task["repository"]["commit"] = self.commit
                task["repository"]["tree"] = self.tree
                raw = (json.dumps(task, indent=2) + "\n").encode()
                path.write_bytes(raw)
            protocol["task_transfers"][task_id]["git_blob_sha1"] = subprocess.run(
                ["git", "hash-object", "--stdin"], input=raw,
                check=True, capture_output=True,
            ).stdout.decode("ascii").strip()
        protocol_file.write_text(json.dumps(protocol, indent=2) + "\n")

    def build(self, destination=None):
        return prepare_reviewer_kit(
            repository_root=self.evalshot,
            checkout=self.hashmarks,
            output=destination or self.out,
        )

    def test_publishes_only_questions_source_and_content_manifest(self):
        result = self.build()
        self.assertEqual(result["source_files"], 3)
        self.assertEqual(result["questions"], 2)
        self.assertFalse(result["owner_oracle_approved"])
        self.assertFalse(result["reviewer_independence_attested"])
        self.assertEqual(result["native_trials_executed"], 0)
        self.assertEqual(
            {p.name for p in self.out.iterdir()},
            {"questions.json", "source", "manifest.json", "verify.py", "start_review.py", "REVIEW.md",
             "review_contract.py", "check_review.py", "source_contract.py", "compare_reviews.py"},
        )
        self.assertFalse((self.out / ".git").exists())
        self.assertFalse((self.out / "source/.git").exists())
        self.assertFalse((self.out / "source/benchmarks").exists())
        packet = json.loads((self.out / "questions.json").read_text())
        self.assertEqual(packet["qualification"], "SOURCE_BLINDED_QUESTION_ONLY")
        self.assertEqual(len(packet["packets"]), 2)
        self.assertFalse(packet["experiment_admitted"])
        self.assertFalse(packet["independent_oracles_approved"])
        for task_id in TASKS:
            task = json.loads((self.stage / "tasks" / f"{task_id}.json").read_text())
            expected = task["oracle"]["configuration"]["expected"]["path"]
            self.assertNotIn(expected, (self.out / "questions.json").read_text())

    def test_tracked_dotfile_is_preserved_and_accounted_for(self):
        (self.hashmarks / ".gitattributes").write_text(
            "# pinned Git attribute source must survive artifact packaging\\n",
            encoding="utf-8",
        )
        self.freeze("tracked dotfile")
        self.update_source_pin()
        report = self.build()
        self.assertEqual(report["source_files"], 4)
        self.assertTrue((self.out / "source/.gitattributes").is_file())
        manifest = json.loads((self.out / "manifest.json").read_text())
        self.assertIn(".gitattributes", manifest["source_sha256_by_path"])
        self.assertNotIn(".git", manifest["source_sha256_by_path"])

    def test_instructions_include_review_procedure_without_oracle_answers(self):
        self.build()
        guidance = (self.out / "REVIEW.md").read_text(encoding="utf-8")
        self.assertIn("Identify other plausible owners", guidance)
        self.assertIn("prior_expected_answer_access", guidance)
        self.assertIn("ambiguous", guidance)
        self.assertIn("unable_to_review", guidance)
        self.assertIn("benchmarks.harness.oracle_reviews", guidance)
        self.assertNotIn("WorkspaceMapStore.paths_under", guidance)
        self.assertNotIn("IndexingLifecycleMixin._sync_remove_stale_paths", guidance)
        self.assertNotIn("hashmarks/codemap/repository_index_store.py", guidance)
        self.assertNotIn("hashmarks/codemap/indexing_lifecycle.py", guidance)
        for task_id in TASKS:
            original = json.loads((self.stage / "tasks" / f"{task_id}.json").read_text())
            expected = original["oracle"]["configuration"]["expected"]
            self.assertNotIn(expected["path"], guidance)
        self.assertEqual(
            (self.out / "REVIEW.md").read_bytes(),
            (ROOT / "benchmarks/dogfood/hashmarks_reviewer_protocol.md").read_bytes(),
        )
        self.assertFalse(json.loads((self.out / "manifest.json").read_text())[
            "canonical_oracle_approved"
        ])

    def test_recipient_rejects_modified_or_missing_reviewer_protocol(self):
        for mutation in ("modify", "remove"):
            with self.subTest(mutation=mutation):
                output = self.workspace / f"handoff-{mutation}"
                self.build(destination=output)
                guide = output / "REVIEW.md"
                if mutation == "modify":
                    guide.write_text(guide.read_text() + "\\n# forged oracle result\\n")
                else:
                    guide.unlink()
                with self.assertRaises(KitVerificationError):
                    verify_kit(output)

    def test_manifest_binds_reviewer_protocol_bytes(self):
        result = self.build()
        self.assertEqual(result["native_trials_executed"], 0)
        manifest = json.loads((self.out / "manifest.json").read_text())
        self.assertEqual(
            hashlib.sha256((self.out / "REVIEW.md").read_bytes()).hexdigest(),
            manifest["review_protocol_sha256"],
        )
        receipt = verify_kit(
            self.out, expected_manifest_sha256=result["manifest_sha256"]
        )
        self.assertEqual(receipt["qualification"], "TRANSPORT_BYTES_VERIFIED_ONLY")
        self.assertFalse(receipt["oracle_approved"])
        self.assertFalse(receipt["reviewer_independence_attested"])

    def _private_claim_check(self, *, path="hashmarks/store.py", symbol="lookup",
                             line=2, decision="unique"):
        producer = self.build()
        target = self.workspace / "private-reviewed-claims.json"
        packet = json.loads((self.out / "questions.json").read_text())
        manifest = json.loads((self.out / "manifest.json").read_text())
        q = packet["packets"][0]
        claim = {
            "packet_id": q["packet_id"],
            "task_digest": q["task_digest"],
            "reviewer_id": "fictional-test-reviewer",
            "source_commit": q["repository"]["commit"],
            "source_tree": q["repository"]["tree"],
            "decision": decision,
            "prior_expected_answer_access": False,
            "source_inspected": True,
            "candidate_locations": [{"path": path, "symbol": symbol}],
            "reason": "synthetic fixture review only",
            "source_evidence": [
                {"path": "hashmarks/store.py", "line": line,
                 "observation": "synthetic test source observation"}
            ],
        }
        if decision == "ambiguous":
            claim["candidate_locations"].append(
                {"path": "hashmarks/store.py", "symbol": "nonexistent"}
            )
        target.write_text(json.dumps({
            "schema": "evalshot.hashmarks-external-first-pass-returns.v1",
            "blinded_packet_identity": manifest["packet_digest"],
            "reviews": [claim],
        }))
        process = subprocess.run([
            sys.executable, str(self.out / "check_review.py"),
            "--root", str(self.out), "--responses", str(target),
            "--expect-manifest-sha256", producer["manifest_sha256"],
        ], cwd=self.workspace, capture_output=True, text=True)
        return process, target

    def test_offline_review_uses_exact_verified_source_after_file_replacement(self):
        original, private = self._private_claim_check()
        self.assertEqual(original.returncode, 0, original.stderr)
        manifest = hashlib.sha256((self.out / "manifest.json").read_bytes()).hexdigest()
        probe = r"""
import json
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import check_review
original_verify = check_review.verify_kit
changed = [False]

def replace_source_after_verification(*args, **kwargs):
    receipt = original_verify(*args, **kwargs)
    source = Path(sys.argv[1]) / "source/hashmarks/store.py"
    source.write_text("def forged(): pass\n")
    changed[0] = True
    return receipt

check_review.verify_kit = replace_source_after_verification
result = check_review.check(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
assert changed == [True]
assert result["references_exist"] == 1, result
assert result["source_check_disagreements"] == 0, result
assert result["review_statuses"][0]["source_reference_status"] == "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF"
assert result["approved_oracles"] == 0
print(json.dumps(result))
"""
        command = subprocess.run([
            sys.executable, "-c", probe, str(self.out), str(private), manifest,
        ], cwd=self.workspace, capture_output=True, text=True)
        self.assertEqual(command.returncode, 0, command.stderr)
        self.assertNotIn("hashmarks/store.py", command.stdout)
        self.assertNotIn("fictional-test-reviewer", command.stdout)
        self.assertFalse((self.out / "__pycache__").exists())
        # Later verifier attempts MUST reject changed disk contents, while
        # the completed check above was bound to already verified bytes.
        with self.assertRaisesRegex(KitVerificationError, "inventory"):
            verify_kit(self.out)

    def test_offline_review_after_verified_source_symlink_swap_is_still_bound(self):
        original, private = self._private_claim_check()
        self.assertEqual(original.returncode, 0, original.stderr)
        manifest = hashlib.sha256((self.out / "manifest.json").read_bytes()).hexdigest()
        probe = r"""
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import check_review
original_verify = check_review.verify_kit

def replace_source_after_verification(*args, **kwargs):
    receipt = original_verify(*args, **kwargs)
    source = Path(sys.argv[1]) / "source/hashmarks/store.py"
    source.unlink()
    source.symlink_to(Path(sys.argv[4]))
    return receipt

check_review.verify_kit = replace_source_after_verification
result = check_review.check(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
assert result["references_exist"] == 1, result
assert result["approved_oracles"] == 0
"""
        command = subprocess.run([
            sys.executable, "-c", probe, str(self.out), str(private), manifest,
            str(self.workspace / "nonexistent-private-file"),
        ], cwd=self.workspace, capture_output=True, text=True)
        self.assertEqual(command.returncode, 0, command.stderr)
        self.assertFalse((self.out / "__pycache__").exists())
        with self.assertRaisesRegex(KitVerificationError, "unsafe"):
            verify_kit(self.out)

    def test_offline_private_citation_exists_but_is_never_semantic_approval(self):
        process, private = self._private_claim_check()
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["status"], "UNTRUSTED_FIRST_PASS_SOURCE_EXISTENCE_ONLY")
        self.assertEqual(result["references_exist"], 1)
        self.assertEqual(result["source_check_disagreements"], 0)
        self.assertEqual(result["review_statuses"][0]["source_reference_status"],
                         "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")
        self.assertFalse(result["semantic_owner_proven"])
        self.assertFalse(result["approved_oracles"])
        self.assertNotIn("hashmarks/store.py", process.stdout)
        self.assertNotIn("fictional-test-reviewer", process.stdout)
        self.assertTrue(private.is_file())
        self.assertFalse((self.out / "__pycache__").exists())
        verify_kit(self.out)

    def test_offline_missing_candidate_source_is_diagnostic_not_success(self):
        process, private = self._private_claim_check(path="hashmarks/missing.py")
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["references_exist"], 0)
        self.assertEqual(result["source_check_disagreements"], 1)
        self.assertEqual(result["review_statuses"][0]["source_reference_status"],
                         "PINNED_CANDIDATE_FILE_UNAVAILABLE")
        self.assertFalse(result["approved_oracles"])

    def test_offline_wrong_python_declaration_is_not_proven(self):
        process, _ = self._private_claim_check(symbol="wrong_name")
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["review_statuses"][0]["source_reference_status"],
                         "CANDIDATE_DECLARATION_NOT_OBSERVED")

    def test_offline_source_line_out_of_bounds_is_not_proven(self):
        process, _ = self._private_claim_check(line=10000)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["review_statuses"][0]["source_reference_status"],
                         "PINNED_EVIDENCE_LINE_ABSENT")

    def test_offline_ambiguous_claim_preserves_unresolved_alternative(self):
        process, _ = self._private_claim_check(decision="ambiguous")
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["review_statuses"][0]["source_reference_status"],
                         "CANDIDATE_DECLARATION_NOT_OBSERVED")
        self.assertFalse(result["reviewer_independence_verified"])
        self.assertFalse(result["semantic_owner_proven"])

    def test_offline_candidate_definition_without_its_cited_source_is_not_positive(self):
        process, private = self._private_claim_check()
        self.assertEqual(process.returncode, 0, process.stderr)
        data = json.loads(private.read_text())
        data["reviews"][0]["source_evidence"][0]["path"] = "README.md"
        data["reviews"][0]["source_evidence"][0]["line"] = 1  # real but unrelated
        private.write_text(json.dumps(data))
        manifest_sha = hashlib.sha256((self.out / "manifest.json").read_bytes()).hexdigest()
        run = subprocess.run([
            sys.executable, str(self.out / "check_review.py"),
            "--root", str(self.out),
            "--responses", str(private),
            "--expect-manifest-sha256", manifest_sha,
        ], cwd=self.workspace, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(run.stdout)
        self.assertEqual(report["references_exist"], 0)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "CANDIDATE_WITHOUT_CITED_SOURCE_EVIDENCE",
        )
        self.assertFalse(report["approved_oracles"])
        self.assertNotIn("hashmarks/store.py", run.stdout)
        self.assertNotIn("fictional-test-reviewer", run.stdout)

    def test_offline_non_python_candidate_never_satisfies_source_owner_check(self):
        process, _ = self._private_claim_check(
            path="README.md", symbol="alternative",
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["references_exist"], 0)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "UNSUPPORTED_NON_PYTHON_CANDIDATE",
        )
        self.assertFalse(report["approved_oracles"])

    def test_offline_same_file_unrelated_line_cannot_anchor_method(self):
        process, private = self._private_claim_check(line=1)  # class, not method
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "CANDIDATE_WITHOUT_DECLARATION_SPAN_CITATION",
        )
        self.assertEqual(report["references_exist"], 0)
        self.assertFalse(report["approved_oracles"])
        self.assertNotIn("hashmarks/store.py", process.stdout)

    def test_offline_local_nested_function_is_not_a_module_owner(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "def unrelated():\n"
            "    def lookup():\n"
            "        return 1\n"
            "    return lookup\n", encoding="utf-8"
        )
        self.freeze("local nested helper")
        self.update_source_pin()
        process, _ = self._private_claim_check(line=2)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "CANDIDATE_ONLY_LOCAL_DECLARATION",
        )
        self.assertFalse(report["semantic_owner_proven"])

    def test_offline_if_false_python_candidate_remains_unqualified(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "if False:\n"
            "    def lookup():\n"
            "        return 1\n", encoding="utf-8"
        )
        self.freeze("guarded if-false fixture")
        self.update_source_pin()
        run, private = self._private_claim_check(line=2)
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(run.stdout)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "CANDIDATE_ONLY_GUARDED_DECLARATION",
        )
        self.assertEqual(report["references_exist"], 0)
        self.assertFalse(report["semantic_owner_proven"])
        self.assertFalse(report["approved_oracles"])
        self.assertNotIn("hashmarks/store.py", run.stdout)
        self.assertNotIn("fictional-test-reviewer", run.stdout)
        self.assertTrue(private.is_file())
        verify_kit(self.out)

    def test_offline_conditional_override_blocks_unique_candidate(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "def lookup():\n"
            "    return 1\n"
            "if ENABLED:\n"
            "    def lookup():\n"
            "        return 2\n", encoding="utf-8"
        )
        self.freeze("conditional alternate owner fixture")
        self.update_source_pin()
        run, _ = self._private_claim_check(line=1)
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(run.stdout)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "CANDIDATE_GUARDED_ALTERNATIVE",
        )
        self.assertEqual(report["source_check_disagreements"], 1)
        self.assertFalse(report["reviewer_independence_verified"])
        self.assertFalse(report["approved_oracles"])
        self.assertNotIn("ENABLED", run.stdout)

    def test_offline_prior_binding_is_overwritten_by_final_declared_method(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "class Store:\n"
            "    lookup = None\n"
            "    def lookup(self):\n"
            "        return 1\n", encoding="utf-8",
        )
        self.freeze("method replaces earlier assigned attribute")
        self.update_source_pin()
        process, _ = self._private_claim_check(line=3)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["references_exist"], 1)
        self.assertEqual(report["source_check_disagreements"], 0)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF",
        )
        self.assertFalse(report["approved_oracles"])
        self.assertNotIn("hashmarks/store.py", process.stdout)

    def test_offline_wildcard_import_after_module_def_is_uncertain(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "def lookup():\n"
            "    return 1\n"
            "from package import *\n", encoding="utf-8",
        )
        self.freeze("possibly overwritten by star import")
        self.update_source_pin()
        process, private = self._private_claim_check(line=1)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["references_exist"], 0)
        self.assertEqual(report["source_check_disagreements"], 1)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "CANDIDATE_WILDCARD_IMPORT_MAY_REBIND",
        )
        self.assertFalse(report["semantic_owner_proven"])
        self.assertFalse(report["approved_oracles"])
        self.assertNotIn("fictional-test-reviewer", process.stdout)
        self.assertTrue(private.exists())
        verify_kit(self.out)

    def test_offline_module_wildcard_import_cannot_override_observed_class_method(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "class Store:\n"
            "    def lookup(self):\n"
            "        return 1\n"
            "from package import *\n", encoding="utf-8",
        )
        self.freeze("star-import does not replace class method")
        self.update_source_pin()
        process, _ = self._private_claim_check(line=2)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["references_exist"], 1)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF",
        )
        self.assertFalse(report["approved_oracles"])

    def test_offline_pair_source_wildcard_dispute_never_becomes_agreement(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "def lookup():\n"
            "    return 1\n"
            "from package import *\n", encoding="utf-8",
        )
        self.freeze("ambiguous module star-import source")
        self.update_source_pin()
        # Private synthetic pair checker uses a class-method fixture citation
        # at line 2, which is a valid body line for the module function.
        run, first, second = self._compare_two_private_reviews()
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(run.stdout)
        self.assertEqual(report["cases"][0]["state"], "SOURCE_REFERENCE_DISPUTED")
        self.assertEqual(report["summary"]["same_candidate_unverified"], 0)
        self.assertEqual(report["summary"]["approved_oracles"], 0)
        self.assertNotIn("hashmarks/store.py", run.stdout)
        self.assertTrue(first.exists() and second.exists())

    def test_offline_for_target_after_declared_owner_is_unqualified(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "def lookup():\n"
            "    return 1\n"
            "for lookup in records:\n"
            "    pass\n", encoding="utf-8",
        )
        self.freeze("for target shadows function")
        self.update_source_pin()
        run, private = self._private_claim_check(line=1)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result["references_exist"], 0)
        self.assertEqual(
            result["review_statuses"][0]["source_reference_status"],
            "CANDIDATE_BINDING_MUTATION_OBSERVED",
        )
        self.assertFalse(result["approved_oracles"])
        self.assertNotIn("hashmarks/store.py", run.stdout)
        self.assertNotIn("fictional-test-reviewer", run.stdout)
        self.assertTrue(private.exists())
        verify_kit(self.out)

    def test_offline_except_alias_can_shadow_class_method(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "class Store:\n"
            "    def lookup(self):\n"
            "        return 1\n"
            "    try:\n"
            "        execute()\n"
            "    except RuntimeError as lookup:\n"
            "        pass\n", encoding="utf-8",
        )
        self.freeze("exception alias may shadow class method")
        self.update_source_pin()
        run, _ = self._private_claim_check(line=2)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(
            result["review_statuses"][0]["source_reference_status"],
            "CANDIDATE_BINDING_MUTATION_OBSERVED",
        )
        self.assertFalse(result["semantic_owner_proven"])

    def test_offline_prior_with_alias_is_overwritten_by_later_method(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "class Store:\n"
            "    with resource as lookup:\n"
            "        pass\n"
            "    def lookup(self):\n"
            "        return 1\n", encoding="utf-8",
        )
        self.freeze("later declaration shadows earlier with alias")
        self.update_source_pin()
        run, _ = self._private_claim_check(line=4)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result["references_exist"], 1)
        self.assertEqual(
            result["review_statuses"][0]["source_reference_status"],
            "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF",
        )
        self.assertFalse(result["approved_oracles"])

    def test_offline_match_capture_disputes_two_matching_private_reviews(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "class Store:\n"
            "    def lookup(self):\n"
            "        return 1\n"
            "    match item:\n"
            "        case lookup:\n"
            "            pass\n", encoding="utf-8",
        )
        self.freeze("class case capture shadows same name")
        self.update_source_pin()
        run, first, second = self._compare_two_private_reviews()
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(run.stdout)
        self.assertEqual(report["cases"][0]["state"], "SOURCE_REFERENCE_DISPUTED")
        self.assertEqual(report["summary"]["same_candidate_unverified"], 0)
        self.assertEqual(report["summary"]["approved_oracles"], 0)
        self.assertNotIn("hashmarks/store.py", run.stdout)
        self.assertTrue(first.exists() and second.exists())

    def test_offline_own_default_binding_is_preceded_by_def_assignment(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "def lookup(value=(lookup := 3)):\n"
            "    return 1\n", encoding="utf-8",
        )
        self.freeze("own signature write is replaced by def")
        self.update_source_pin()
        process, private = self._private_claim_check(line=1)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["references_exist"], 1)
        self.assertEqual(report["source_check_disagreements"], 0)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF",
        )
        self.assertFalse(report["approved_oracles"])
        self.assertNotIn("hashmarks/store.py", process.stdout)
        self.assertNotIn("fictional-test-reviewer", process.stdout)
        self.assertTrue(private.is_file())
        verify_kit(self.out)

    def test_offline_own_default_followed_by_rebinding_is_disputed(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "def lookup(value=(lookup := 3)):\n"
            "    return 1\n"
            "lookup = None\n", encoding="utf-8",
        )
        self.freeze("own signature prebind then later overwrite")
        self.update_source_pin()
        process, _ = self._private_claim_check(line=1)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["references_exist"], 0)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "CANDIDATE_BINDING_MUTATION_OBSERVED",
        )
        self.assertFalse(report["semantic_owner_proven"])
        self.assertFalse(report["approved_oracles"])

    def test_offline_lambda_body_walrus_does_not_create_false_mutation(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "def lookup():\n"
            "    return 1\n"
            "other = lambda: (lookup := None)\n", encoding="utf-8",
        )
        self.freeze("lambda body remains local")
        self.update_source_pin()
        process, _ = self._private_claim_check(line=1)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["references_exist"], 1)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF",
        )
        self.assertFalse(report["semantic_owner_proven"])
        self.assertFalse(report["approved_oracles"])

    def test_offline_module_owner_mutated_by_later_function_default(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "def lookup():\n"
            "    return 1\n"
            "def other(arg=(lookup := 7)):\n"
            "    pass\n", encoding="utf-8",
        )
        self.freeze("function default binding in parent scope")
        self.update_source_pin()
        process, private = self._private_claim_check(line=1)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "CANDIDATE_BINDING_MUTATION_OBSERVED",
        )
        self.assertEqual(report["references_exist"], 0)
        self.assertEqual(report["approved_oracles"], 0)
        self.assertNotIn("hashmarks/store.py", process.stdout)
        self.assertNotIn("fictional-test-reviewer", process.stdout)
        self.assertTrue(private.is_file())
        verify_kit(self.out)

    def test_offline_class_owner_mutated_by_later_method_decorator(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "class Store:\n"
            "    def lookup(self):\n"
            "        return 1\n"
            "    @(lookup := decorator)\n"
            "    def other(self):\n"
            "        pass\n", encoding="utf-8",
        )
        self.freeze("method decorator uses class namespace")
        self.update_source_pin()
        process, _ = self._private_claim_check(line=2)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "CANDIDATE_BINDING_MUTATION_OBSERVED",
        )
        self.assertFalse(report["reviewer_independence_verified"])

    def test_offline_class_header_mutation_disputes_two_untrusted_matching_reviews(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "def lookup():\n"
            "    return 1\n"
            "@(lookup := class_decorator)\n"
            "class Other:\n"
            "    pass\n", encoding="utf-8",
        )
        self.freeze("class decorator rebinding")
        self.update_source_pin()
        run, first, second = self._compare_two_private_reviews()
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result["cases"][0]["state"], "SOURCE_REFERENCE_DISPUTED")
        self.assertEqual(result["summary"]["same_candidate_unverified"], 0)
        self.assertEqual(result["summary"]["approved_oracles"], 0)
        self.assertNotIn("hashmarks/store.py", run.stdout)
        self.assertTrue(first.exists() and second.exists())

    def test_offline_unrelated_class_method_decorator_does_not_mutate_module_owner(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "def lookup():\n"
            "    return 1\n"
            "class Other:\n"
            "    @(lookup := decorator)\n"
            "    def other(self):\n"
            "        pass\n", encoding="utf-8",
        )
        self.freeze("class member decorator remains in class namespace")
        self.update_source_pin()
        process, _ = self._private_claim_check(line=1)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["references_exist"], 1)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF",
        )
        self.assertFalse(report["semantic_owner_proven"])
        self.assertFalse(report["approved_oracles"])

    def test_offline_class_global_write_disputes_module_function(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "def lookup():\n"
            "    return 1\n"
            "class Holder:\n"
            "    global lookup\n"
            "    lookup = replacement\n", encoding="utf-8"
        )
        self.freeze("class global module mutation")
        self.update_source_pin()
        process, private = self._private_claim_check(line=1)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "CANDIDATE_BINDING_MUTATION_OBSERVED",
        )
        self.assertEqual(report["references_exist"], 0)
        self.assertFalse(report["approved_oracles"])
        self.assertNotIn("hashmarks/store.py", process.stdout)
        self.assertNotIn("fictional-test-reviewer", process.stdout)
        self.assertTrue(private.is_file())
        verify_kit(self.out)

    def test_offline_prior_class_global_write_is_superseded_by_module_def(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "class Holder:\n"
            "    global lookup\n"
            "    lookup = replacement\n"
            "def lookup():\n"
            "    return 1\n", encoding="utf-8"
        )
        self.freeze("prior class global binding")
        self.update_source_pin()
        process, _ = self._private_claim_check(line=4)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["references_exist"], 1)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF",
        )
        self.assertFalse(report["semantic_owner_proven"])

    def test_offline_deferred_nested_class_global_write_is_unqualified(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "def lookup():\n"
            "    return 1\n"
            "def callback():\n"
            "    class Holder:\n"
            "        global lookup\n"
            "        lookup = replacement\n", encoding="utf-8"
        )
        self.freeze("nested class global deferred")
        self.update_source_pin()
        process, _ = self._private_claim_check(line=1)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "CANDIDATE_DEFERRED_GLOBAL_REBINDING",
        )
        self.assertEqual(report["references_exist"], 0)
        self.assertFalse(report["approved_oracles"])

    def test_offline_class_global_method_def_cannot_impersonate_regular_method(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "class Holder:\n"
            "    global lookup\n"
            "    def lookup():\n"
            "        return 1\n", encoding="utf-8"
        )
        self.freeze("class global def binds module")
        self.update_source_pin()
        process, _ = self._private_claim_check(line=3)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "CANDIDATE_CLASS_GLOBAL_DEFINITION",
        )
        self.assertFalse(report["semantic_owner_proven"])
        self.assertFalse(report["approved_oracles"])

    def test_offline_class_local_assignment_without_global_is_not_module_mutation(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "def lookup():\n"
            "    return 1\n"
            "class Other:\n"
            "    lookup = replacement\n", encoding="utf-8"
        )
        self.freeze("unrelated class local attribute")
        self.update_source_pin()
        process, _ = self._private_claim_check(line=1)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["references_exist"], 1)
        self.assertFalse(report["approved_oracles"])

    def test_offline_class_global_disputes_two_matching_private_claims(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "def lookup():\n"
            "    return 1\n"
            "class Holder:\n"
            "    global lookup\n"
            "    lookup = replacement\n", encoding="utf-8"
        )
        self.freeze("class global possible source shadow")
        self.update_source_pin()
        run, first, second = self._compare_two_private_reviews()
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(run.stdout)
        self.assertEqual(report["cases"][0]["state"], "SOURCE_REFERENCE_DISPUTED")
        self.assertEqual(report["summary"]["same_candidate_unverified"], 0)
        self.assertEqual(report["summary"]["approved_oracles"], 0)
        self.assertNotIn("hashmarks/store.py", run.stdout)
        self.assertTrue(first.exists() and second.exists())
        verify_kit(self.out)

    def test_offline_deferred_global_write_disputes_module_owner(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "def lookup():\n"
            "    return 1\n"
            "def callback():\n"
            "    global lookup\n"
            "    lookup = replacement\n", encoding="utf-8",
        )
        self.freeze("deferred global assignment")
        self.update_source_pin()
        process, private = self._private_claim_check(line=1)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(
            result["review_statuses"][0]["source_reference_status"],
            "CANDIDATE_DEFERRED_GLOBAL_REBINDING",
        )
        self.assertEqual(result["references_exist"], 0)
        self.assertEqual(result["source_check_disagreements"], 1)
        self.assertFalse(result["approved_oracles"])
        self.assertNotIn("hashmarks/store.py", process.stdout)
        self.assertNotIn("fictional-test-reviewer", process.stdout)
        self.assertTrue(private.is_file())
        verify_kit(self.out)

    def test_offline_global_declaration_without_write_is_not_rebinding(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "def lookup():\n"
            "    return 1\n"
            "def callback():\n"
            "    global lookup\n"
            "    return lookup\n", encoding="utf-8",
        )
        self.freeze("global reference with no mutation")
        self.update_source_pin()
        process, _ = self._private_claim_check(line=1)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["references_exist"], 1)
        self.assertEqual(
            result["review_statuses"][0]["source_reference_status"],
            "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF",
        )
        self.assertFalse(result["semantic_owner_proven"])

    def test_offline_global_name_write_does_not_mutate_unrelated_class_method(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "class Store:\n"
            "    def lookup(self):\n"
            "        return 1\n"
            "def callback():\n"
            "    global lookup\n"
            "    lookup = replacement\n", encoding="utf-8",
        )
        self.freeze("class method not replaced by module global write")
        self.update_source_pin()
        process, _ = self._private_claim_check(line=2)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["references_exist"], 1)
        self.assertEqual(
            result["review_statuses"][0]["source_reference_status"],
            "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF",
        )
        self.assertFalse(result["approved_oracles"])

    def test_offline_deferred_global_write_prevents_false_two_review_agreement(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "# Pinned historical source fixture\n"
            "def lookup():\n"
            "    return 1\n"
            "def callback():\n"
            "    global lookup\n"
            "    del lookup\n", encoding="utf-8",
        )
        self.freeze("deferred global deletion of claimed source owner")
        self.update_source_pin()
        run, first, second = self._compare_two_private_reviews()
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result["cases"][0]["state"], "SOURCE_REFERENCE_DISPUTED")
        self.assertEqual(result["summary"]["same_candidate_unverified"], 0)
        self.assertEqual(result["summary"]["approved_oracles"], 0)
        self.assertNotIn("hashmarks/store.py", run.stdout)
        self.assertTrue(first.is_file() and second.is_file())
        verify_kit(self.out)

    def test_offline_verified_declaration_with_assignment_is_not_owner_proof(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "def lookup():\n"
            "    return 1\n"
            "lookup = None\n", encoding="utf-8",
        )
        self.freeze("reassigned module function")
        self.update_source_pin()
        process, private = self._private_claim_check(line=1)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(
            result["review_statuses"][0]["source_reference_status"],
            "CANDIDATE_BINDING_MUTATION_OBSERVED",
        )
        self.assertEqual(result["references_exist"], 0)
        self.assertEqual(result["source_check_disagreements"], 1)
        self.assertFalse(result["approved_oracles"])
        self.assertNotIn("hashmarks/store.py", process.stdout)
        self.assertNotIn("fictional-test-reviewer", process.stdout)
        self.assertTrue(private.exists())
        verify_kit(self.out)

    def test_offline_class_suite_explicit_monkeypatch_is_disputed(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "class Store:\n"
            "    def lookup(self):\n"
            "        return 1\n"
            "class Patch:\n"
            "    Store.lookup = replacement\n", encoding="utf-8",
        )
        self.freeze("class-suite method replacement")
        self.update_source_pin()
        run, _ = self._private_claim_check(line=2)
        self.assertEqual(run.returncode, 0, run.stderr)
        status = json.loads(run.stdout)
        self.assertEqual(
            status["review_statuses"][0]["source_reference_status"],
            "CANDIDATE_BINDING_MUTATION_OBSERVED",
        )
        self.assertEqual(status["references_exist"], 0)
        self.assertFalse(status["approved_oracles"])

    def test_offline_deferred_method_write_is_disputed(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "class Store:\n"
            "    def lookup(self):\n"
            "        return 1\n"
            "def callback():\n"
            "    Store.lookup = replacement\n", encoding="utf-8",
        )
        self.freeze("deferred class method replacement")
        self.update_source_pin()
        run, _ = self._private_claim_check(line=2)
        self.assertEqual(run.returncode, 0, run.stderr)
        status = json.loads(run.stdout)
        self.assertEqual(
            status["review_statuses"][0]["source_reference_status"],
            "CANDIDATE_DEFERRED_METHOD_REBINDING",
        )
        self.assertEqual(status["references_exist"], 0)
        self.assertFalse(status["reviewer_independence_verified"])

    def test_offline_deferred_method_write_prevents_false_review_agreement(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "class Store:\n"
            "    def lookup(self):\n"
            "        return 1\n"
            "def callback():\n"
            "    del Store.lookup\n", encoding="utf-8",
        )
        self.freeze("deferred method deletion")
        self.update_source_pin()
        run, first, second = self._compare_two_private_reviews()
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result["cases"][0]["state"], "SOURCE_REFERENCE_DISPUTED")
        self.assertEqual(result["summary"]["same_candidate_unverified"], 0)
        self.assertEqual(result["summary"]["approved_oracles"], 0)
        self.assertNotIn("hashmarks/store.py", run.stdout)
        self.assertTrue(first.exists() and second.exists())
        verify_kit(self.out)

    def test_offline_class_method_monkeypatch_is_not_unambiguous_source(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "class Store:\n"
            "    def lookup(self):\n"
            "        return 1\n"
            "Store.lookup = other\n", encoding="utf-8",
        )
        self.freeze("method replacement")
        self.update_source_pin()
        process, _ = self._private_claim_check(line=2)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(
            result["review_statuses"][0]["source_reference_status"],
            "CANDIDATE_BINDING_MUTATION_OBSERVED",
        )
        self.assertFalse(result["reviewer_independence_verified"])
        self.assertFalse(result["semantic_owner_proven"])

    def test_offline_different_class_assignment_does_not_shadow_owner(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "class Store:\n"
            "    def lookup(self):\n"
            "        return 1\n"
            "class Other:\n"
            "    lookup = None\n", encoding="utf-8",
        )
        self.freeze("different class assignment")
        self.update_source_pin()
        process, _ = self._private_claim_check(line=2)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["references_exist"], 1)
        self.assertEqual(
            result["review_statuses"][0]["source_reference_status"],
            "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF",
        )
        self.assertFalse(result["approved_oracles"])

    def test_offline_binding_mutation_prevents_false_review_agreement(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "class Store:\n"
            "    def lookup(self):\n"
            "        return 1\n"
            "    lookup = lambda self: 2\n", encoding="utf-8",
        )
        self.freeze("mutated claimed method")
        self.update_source_pin()
        run, first, second = self._compare_two_private_reviews()
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result["cases"][0]["state"], "SOURCE_REFERENCE_DISPUTED")
        self.assertEqual(result["summary"]["same_candidate_unverified"], 0)
        self.assertEqual(result["summary"]["approved_oracles"], 0)
        self.assertNotIn("hashmarks/store.py", run.stdout)
        self.assertTrue(first.exists() and second.exists())
        verify_kit(self.out)

    def test_offline_multiple_same_named_methods_are_not_qualified_unique(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "class One:\n"
            "    def lookup(self): pass\n"
            "class Two:\n"
            "    def lookup(self): pass\n", encoding="utf-8"
        )
        self.freeze("two distinct methods same name")
        self.update_source_pin()
        process, _ = self._private_claim_check(line=2)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "CANDIDATE_MULTIPLE_DECLARATIONS_OBSERVED",
        )
        self.assertFalse(report["approved_oracles"])

    def test_offline_decorated_method_anchor_remains_valid_syntax_only(self):
        (self.hashmarks / "hashmarks/store.py").write_text(
            "class Store:\n"
            "    @property\n"
            "    def lookup(self):\n"
            "        return 1\n", encoding="utf-8"
        )
        self.freeze("decorated")
        self.update_source_pin()
        process, _ = self._private_claim_check(line=2)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["references_exist"], 1)
        self.assertEqual(
            report["review_statuses"][0]["source_reference_status"],
            "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF",
        )
        self.assertFalse(report["semantic_owner_proven"])
        self.assertFalse(report["approved_oracles"])

    def test_offline_source_contract_matches_canonical_file(self):
        self.build()
        self.assertEqual(
            (self.out / "source_contract.py").read_bytes(),
            (ROOT / "benchmarks/dogfood/hashmarks_review_source_contract.py").read_bytes(),
        )
        self.assertEqual(
            hashlib.sha256((self.out / "source_contract.py").read_bytes()).hexdigest(),
            json.loads((self.out / "manifest.json").read_text())["source_contract_sha256"],
        )
        (self.out / "source_contract.py").write_text("# tampered")
        with self.assertRaises(KitVerificationError):
            verify_kit(self.out)

    def _compare_two_private_reviews(
        self, *, decision_a="unique", decision_b="unique",
        candidate_a=("hashmarks/store.py", "lookup"),
        candidate_b=("hashmarks/store.py", "lookup"),
        exposure_a=False, exposure_b=False,
        reviewer_a="reviewer-one", reviewer_b="reviewer-two",
        subset_b=False,
    ):
        producer = self.build()
        manifest = json.loads((self.out / "manifest.json").read_text())
        packet = json.loads((self.out / "questions.json").read_text())
        question = packet["packets"][0]

        def make_response(reviewer, decision, candidate, exposure, path):
            item = {
                "packet_id": question["packet_id"],
                "task_digest": question["task_digest"],
                "reviewer_id": reviewer,
                "source_commit": question["repository"]["commit"],
                "source_tree": question["repository"]["tree"],
                "decision": decision,
                "prior_expected_answer_access": exposure,
                "source_inspected": decision != "unable_to_review",
                "candidate_locations": (
                    [] if decision == "unable_to_review" else [
                        {"path": candidate[0], "symbol": candidate[1]}
                    ]
                ),
                "reason": "private synthetic test reason",
                "source_evidence": (
                    [] if decision == "unable_to_review" else [{
                        "path": "hashmarks/store.py",
                        "line": 2,
                        "observation": "private synthetic evidence",
                    }]
                ),
            }
            if decision == "ambiguous":
                item["candidate_locations"].append(
                    {"path": "hashmarks/store.py", "symbol": "alternative"}
                )
                item["source_evidence"].append({
                    "path": "hashmarks/store.py",
                    "line": 4,
                    "observation": "private synthetic second declaration evidence",
                })
            elif candidate[1] == "alternative":
                item["source_evidence"][0]["line"] = 4
            path.write_text(json.dumps({
                "schema": "evalshot.hashmarks-external-first-pass-returns.v1",
                "blinded_packet_identity": manifest["packet_digest"],
                "reviews": [item],
            }))
            return path

        first = make_response(
            reviewer_a, decision_a, candidate_a, exposure_a,
            self.workspace / "private-first.json",
        )
        second = make_response(
            reviewer_b, decision_b, candidate_b, exposure_b,
            self.workspace / "private-second.json",
        )
        if subset_b:
            second.write_text(json.dumps({
                "schema": "evalshot.hashmarks-external-first-pass-returns.v1",
                "blinded_packet_identity": manifest["packet_digest"],
                "reviews": [],
            }))
        run = subprocess.run([
            sys.executable, str(self.out / "compare_reviews.py"),
            "--root", str(self.out),
            "--response-a", str(first),
            "--response-b", str(second),
            "--expect-manifest-sha256", producer["manifest_sha256"],
        ], cwd=self.workspace, capture_output=True, text=True)
        return run, first, second

    def test_two_matching_review_claims_do_not_establish_independence(self):
        run, first, second = self._compare_two_private_reviews()
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(run.stdout)
        case = report["cases"][0]
        self.assertEqual(case["state"], "SAME_CANDIDATE_UNVERIFIED")
        self.assertEqual(report["summary"]["same_candidate_unverified"], 1)
        self.assertEqual(report["summary"]["approved_oracles"], 0)
        self.assertFalse(case["reviewer_independence_verified"])
        self.assertFalse(case["semantic_owner_proven"])
        self.assertNotIn("reviewer-one", run.stdout)
        self.assertNotIn("hashmarks/store.py", run.stdout)
        self.assertNotIn("private synthetic", run.stdout)
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        self.assertFalse((self.out / "__pycache__").exists())
        verify_kit(self.out)

    def test_two_competing_claimed_source_owners_preserve_disagreement(self):
        run, _, _ = self._compare_two_private_reviews(
            candidate_b=("hashmarks/store.py", "alternative"),
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(run.stdout)
        self.assertEqual(report["cases"][0]["state"], "COMPETING_CANDIDATES_DISAGREE")
        self.assertEqual(report["summary"]["conflicts_or_incomplete"], 2)
        self.assertFalse(report["cases"][0]["canonical_oracle_approved"])
        self.assertNotIn("alternative", run.stdout)

    def test_ambiguous_review_does_not_get_overridden_by_unique(self):
        run, _, _ = self._compare_two_private_reviews(decision_b="ambiguous")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(
            json.loads(run.stdout)["cases"][0]["state"],
            "AMBIGUITY_REPORTED_NO_AUTOMATIC_RESOLUTION",
        )

    def test_declared_prior_exposure_is_preserved(self):
        run, _, _ = self._compare_two_private_reviews(exposure_b=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(
            json.loads(run.stdout)["cases"][0]["state"],
            "PRIOR_EXPECTED_ANSWER_ACCESS_DECLARED",
        )

    def test_same_declared_reviewer_is_not_counted_twice(self):
        run, _, _ = self._compare_two_private_reviews(reviewer_b="reviewer-one")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(
            json.loads(run.stdout)["cases"][0]["state"],
            "DUPLICATE_DECLARED_REVIEWER",
        )

    def test_incomplete_pair_does_not_manufacture_agreement(self):
        run, _, _ = self._compare_two_private_reviews(subset_b=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(
            json.loads(run.stdout)["cases"][0]["state"],
            "INCOMPLETE_TWO_REVIEWER_COVERAGE",
        )

    def test_invalid_pinned_source_citation_remains_disputed(self):
        run, _, _ = self._compare_two_private_reviews(
            candidate_b=("hashmarks/missing.py", "lookup"),
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(
            json.loads(run.stdout)["cases"][0]["state"],
            "SOURCE_REFERENCE_DISPUTED",
        )

    def test_unable_to_review_remains_unresolved(self):
        run, _, _ = self._compare_two_private_reviews(decision_b="unable_to_review")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(
            json.loads(run.stdout)["cases"][0]["state"],
            "UNABLE_TO_REVIEW_NO_AUTOMATIC_RESOLUTION",
        )

    def test_comparison_consumes_each_private_response_exactly_once(self):
        run, first, second = self._compare_two_private_reviews()
        self.assertEqual(run.returncode, 0, run.stderr)
        manifest = hashlib.sha256((self.out / "manifest.json").read_bytes()).hexdigest()
        probe = r"""
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import check_review
import compare_reviews
original = check_review._read_object
private = {str(Path(value).resolve()): 0 for value in sys.argv[2:4]}

def counting_read(path):
    key = str(Path(path).resolve())
    if key in private:
        private[key] += 1
        if private[key] > 1:
            raise RuntimeError("private source response was read twice")
    return original(path)

check_review._read_object = counting_read
result = compare_reviews.compare_private_reviews(
    kit=Path(sys.argv[1]), response_a=Path(sys.argv[2]),
    response_b=Path(sys.argv[3]), expected_manifest_sha256=sys.argv[4],
)
assert tuple(private.values()) == (1, 1), private
assert result["summary"]["same_candidate_unverified"] == 1
assert result["summary"]["approved_oracles"] == 0
"""
        command = subprocess.run([
            sys.executable, "-c", probe, str(self.out), str(first),
            str(second), manifest,
        ], cwd=self.workspace, capture_output=True, text=True)
        self.assertEqual(command.returncode, 0, command.stderr)
        self.assertFalse((self.out / "__pycache__").exists())
        verify_kit(self.out)

    def test_private_response_replacement_after_read_does_not_change_compared_claim(self):
        run, first, second = self._compare_two_private_reviews()
        self.assertEqual(run.returncode, 0, run.stderr)
        digest = hashlib.sha256((self.out / "manifest.json").read_bytes()).hexdigest()
        probe = r"""
import json
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import check_review
import compare_reviews
original = check_review._read_object
target = Path(sys.argv[2]).resolve()
mutations = [0]

def mutating_read(path):
    payload, raw = original(path)
    if Path(path).resolve() == target:
        mutations[0] += 1
        if mutations[0] > 1:
            raise RuntimeError("private response reread after verification")
        replacement = json.loads(raw)
        replacement["reviews"][0]["candidate_locations"][0]["path"] = "hashmarks/missing.py"
        target.write_text(json.dumps(replacement))
    return payload, raw

check_review._read_object = mutating_read
result = compare_reviews.compare_private_reviews(
    kit=Path(sys.argv[1]), response_a=target,
    response_b=Path(sys.argv[3]), expected_manifest_sha256=sys.argv[4],
)
assert mutations == [1], mutations
assert result["cases"][0]["state"] == "SAME_CANDIDATE_UNVERIFIED", result
assert result["summary"]["approved_oracles"] == 0
"""
        command = subprocess.run([
            sys.executable, "-c", probe, str(self.out),
            str(first), str(second), digest,
        ], cwd=self.workspace, capture_output=True, text=True)
        self.assertEqual(command.returncode, 0, command.stderr)
        self.assertFalse((self.out / "__pycache__").exists())
        verify_kit(self.out)

    def test_comparison_does_not_accept_same_private_file_twice(self):
        run, first, _ = self._compare_two_private_reviews()
        self.assertEqual(run.returncode, 0, run.stderr)
        verified = json.loads((self.out / "manifest.json").read_text())
        bad = subprocess.run([
            sys.executable, str(self.out / "compare_reviews.py"),
            "--root", str(self.out),
            "--response-a", str(first), "--response-b", str(first),
            "--expect-manifest-sha256",
            hashlib.sha256((self.out / "manifest.json").read_bytes()).hexdigest(),
        ], cwd=self.workspace, capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)
        self.assertNotIn("private synthetic", bad.stdout)

    def test_comparison_script_tamper_is_rejected_by_recipient(self):
        self.build()
        comparer = self.out / "compare_reviews.py"
        comparer.write_text(comparer.read_text() + "\n# modified\n")
        with self.assertRaises(KitVerificationError):
            verify_kit(self.out)

    def test_offline_shared_contract_rejects_incomplete_review_skeleton(self):
        producer = self.build()
        draft = self.workspace / "private-review-draft.json"
        subprocess.run([
            sys.executable, str(self.out / "start_review.py"),
            "--root", str(self.out), "--output", str(draft),
            "--expect-manifest-sha256", producer["manifest_sha256"],
        ], cwd=self.workspace, check=True, capture_output=True, text=True)
        output = subprocess.run([
            sys.executable, str(self.out / "check_review.py"),
            "--root", str(self.out), "--responses", str(draft),
            "--expect-manifest-sha256", producer["manifest_sha256"],
        ], cwd=self.workspace, capture_output=True, text=True)
        self.assertNotEqual(output.returncode, 0)
        self.assertNotIn("candidate_locations", output.stdout)
        self.assertNotIn("oracle_approved\": true", output.stdout)
        self.assertFalse((self.out / "__pycache__").exists())
        verify_kit(self.out, expected_manifest_sha256=producer["manifest_sha256"])

    def test_offline_checker_tampering_is_refused(self):
        self.build()
        path = self.out / "review_contract.py"
        path.write_text(path.read_text() + "\\n# altered\\n")
        with self.assertRaises(KitVerificationError):
            verify_kit(self.out)

    def test_verified_source_snapshot_only_publishes_after_complete_validation(self):
        producer = self.build()
        verified: dict[str, bytes] = {}
        receipt = verify_kit(
            self.out, expected_manifest_sha256=producer["manifest_sha256"],
            verified_source_bytes=verified,
        )
        self.assertEqual(receipt["source_files"], 3)
        self.assertEqual(set(verified), {
            "README.md", "hashmarks/store.py", "nested/data.json"
        })
        self.assertEqual(
            verified["hashmarks/store.py"],
            (self.hashmarks / "hashmarks/store.py").read_bytes(),
        )
        (self.out / "source/nested/data.json").write_text('{"altered": true}\n')
        failed_sink: dict[str, bytes] = {}
        with self.assertRaisesRegex(KitVerificationError, "inventory"):
            verify_kit(
                self.out, expected_manifest_sha256=producer["manifest_sha256"],
                verified_source_bytes=failed_sink,
            )
        self.assertEqual(failed_sink, {})

    def test_verified_source_snapshot_rejects_prepopulated_destination(self):
        producer = self.build()
        existing = {"preexisting.py": b"untrusted bytes"}
        with self.assertRaisesRegex(KitVerificationError, "start empty"):
            verify_kit(
                self.out, expected_manifest_sha256=producer["manifest_sha256"],
                verified_source_bytes=existing,
            )
        self.assertEqual(existing, {"preexisting.py": b"untrusted bytes"})

    def test_public_metadata_is_published_atomically_only_after_complete_kit(self):
        receipt = self.build()
        public: dict = {}
        result = verify_kit(
            self.out, expected_manifest_sha256=receipt["manifest_sha256"],
            verified_public=public,
        )
        self.assertEqual(result["qualification"], "TRANSPORT_BYTES_VERIFIED_ONLY")
        self.assertEqual(set(public), {"questions", "manifest"})
        self.assertEqual(public["manifest"]["packet_digest"],
                         json.loads((self.out / "manifest.json").read_text())["packet_digest"])
        self.assertEqual(len(public["questions"]["packets"]), 2)
        self.assertFalse(public["questions"]["experiment_admitted"])
        self.assertFalse(public["manifest"]["canonical_oracle_approved"])

        (self.out / "source/nested/data.json").write_text('{"altered":true}\n')
        stale: dict = {}
        with self.assertRaisesRegex(KitVerificationError, "inventory"):
            verify_kit(
                self.out, expected_manifest_sha256=receipt["manifest_sha256"],
                verified_public=stale,
            )
        self.assertEqual(stale, {})

    def test_public_metadata_rejects_existing_destination(self):
        receipt = self.build()
        stale = {"questions": {"compromised": True}}
        with self.assertRaisesRegex(KitVerificationError, "start empty"):
            verify_kit(
                self.out, expected_manifest_sha256=receipt["manifest_sha256"],
                verified_public=stale,
            )
        self.assertEqual(stale, {"questions": {"compromised": True}})

    def test_offline_checker_ignores_question_and_manifest_changes_after_verification(self):
        run, private = self._private_claim_check()
        self.assertEqual(run.returncode, 0, run.stderr)
        digest = hashlib.sha256((self.out / "manifest.json").read_bytes()).hexdigest()
        probe = r"""
import json
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import check_review
original_verify = check_review.verify_kit
changed = [False]

def mutate_metadata_after_verify(*args, **kwargs):
    result = original_verify(*args, **kwargs)
    kit = Path(sys.argv[1])
    (kit / "questions.json").write_text('{"forged":true}\n')
    (kit / "manifest.json").write_text('{"forged":true}\n')
    changed[0] = True
    return result

check_review.verify_kit = mutate_metadata_after_verify
out = check_review.check(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
assert changed == [True]
assert out["references_exist"] == 1, out
assert out["approved_oracles"] == 0
assert out["review_statuses"][0]["source_reference_status"] == "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF"
print(json.dumps(out))
"""
        result = subprocess.run([
            sys.executable, "-c", probe, str(self.out), str(private), digest
        ], cwd=self.workspace, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("hashmarks/store.py", result.stdout)
        self.assertNotIn("fictional-test-reviewer", result.stdout)
        self.assertFalse((self.out / "__pycache__").exists())
        with self.assertRaisesRegex(KitVerificationError, "producer-supplied digest"):
            verify_kit(self.out, expected_manifest_sha256=digest)

    def test_review_starter_uses_verified_public_metadata_without_reread(self):
        producer = self.build()
        destination = self.workspace / "private-unreviewed-snapshot.json"
        probe = r"""
import json
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import start_review
original_verify = start_review.verify_kit

def mutate_metadata_after_verify(*args, **kwargs):
    result = original_verify(*args, **kwargs)
    kit = Path(sys.argv[1])
    (kit / "questions.json").write_text('{"forged":true}\n')
    (kit / "manifest.json").write_text('{"forged":true}\n')
    return result

start_review.verify_kit = mutate_metadata_after_verify
result = start_review.make_unreviewed_starter(Path(sys.argv[1]), sys.argv[2])
assert result["blinded_packet_identity"].startswith("sha256:")
assert len(result["reviews"]) == 2
assert all(r["decision"] == "NOT_REVIEWED_REPLACE_BEFORE_SUBMISSION" for r in result["reviews"])
assert all(r["source_inspected"] is None for r in result["reviews"])
assert all(r["prior_expected_answer_access"] is None for r in result["reviews"])
print(json.dumps({"tasks": len(result["reviews"]), "approval": False}))
"""
        process = subprocess.run([
            sys.executable, "-c", probe, str(self.out), producer["manifest_sha256"],
        ], cwd=self.workspace, capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertFalse(json.loads(process.stdout)["approval"])
        self.assertFalse(destination.exists())
        self.assertFalse((self.out / "__pycache__").exists())

    def test_two_review_comparison_reuses_verified_question_context(self):
        run, first, second = self._compare_two_private_reviews()
        self.assertEqual(run.returncode, 0, run.stderr)
        digest = hashlib.sha256((self.out / "manifest.json").read_bytes()).hexdigest()
        probe = r"""
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import compare_reviews
original_check = compare_reviews.check_claims
calls = [0]

def mutate_after_second_validated_bundle(*args, **kwargs):
    report = original_check(*args, **kwargs)
    calls[0] += 1
    if calls[0] == 2:
        (Path(sys.argv[1]) / "questions.json").write_text('{"forged":true}\n')
    return report

compare_reviews.check_claims = mutate_after_second_validated_bundle
report = compare_reviews.compare_private_reviews(
    kit=Path(sys.argv[1]), response_a=Path(sys.argv[2]),
    response_b=Path(sys.argv[3]), expected_manifest_sha256=sys.argv[4],
)
assert calls == [2]
assert report["summary"]["same_candidate_unverified"] == 1
assert report["summary"]["approved_oracles"] == 0
assert report["cases"][0]["state"] == "SAME_CANDIDATE_UNVERIFIED"
"""
        result = subprocess.run([
            sys.executable, "-c", probe, str(self.out), str(first), str(second),
            digest,
        ], cwd=self.workspace, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.out / "__pycache__").exists())
        with self.assertRaisesRegex(KitVerificationError, "questions bytes"):
            verify_kit(self.out)

    def test_manifest_file_digests_bind_exported_bytes(self):
        self.build()
        manifest = json.loads((self.out / "manifest.json").read_text())
        self.assertEqual(manifest["pinned_commit"], self.commit)
        self.assertEqual(manifest["pinned_tree"], self.tree)
        self.assertEqual(manifest["source_files"], 3)
        self.assertFalse(manifest["reviewer_independence_attested"])
        self.assertFalse(manifest["canonical_oracle_approved"])
        self.assertFalse(manifest["native_trial_executed"])
        for path, digest in manifest["source_sha256_by_path"].items():
            data = (self.out / "source" / path).read_bytes()
            self.assertEqual(digest, hashlib.sha256(data).hexdigest())
            self.assertEqual(data, (self.hashmarks / path).read_bytes())
        self.assertEqual(
            manifest["questions_sha256"],
            hashlib.sha256((self.out / "questions.json").read_bytes()).hexdigest(),
        )

    def test_standalone_verifier_runs_without_evalshot_checkout(self):
        producer = self.build()
        command = subprocess.run([
            sys.executable, str(self.out / "verify.py"), "--root", str(self.out),
            "--expect-manifest-sha256", producer["manifest_sha256"],
        ], cwd=self.workspace, capture_output=True, text=True,
           env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(command.returncode, 0, command.stderr)
        result = json.loads(command.stdout)
        self.assertEqual(result["qualification"], "TRANSPORT_BYTES_VERIFIED_ONLY")
        self.assertEqual(result["source_files"], 3)
        self.assertEqual(result["blinded_questions"], 2)
        self.assertIs(result["oracle_approved"], False)
        self.assertIs(result["reviewer_independence_attested"], False)

    def test_recipient_rejects_modified_source_bytes(self):
        self.build()
        (self.out / "source/hashmarks/store.py").write_text("def forged(): pass\\n")
        with self.assertRaisesRegex(KitVerificationError, "inventory"):
            verify_kit(self.out)

    def test_recipient_rejects_new_rogue_source_file(self):
        self.build()
        (self.out / "source/surprise.py").write_text("def leaked(): pass\\n")
        with self.assertRaisesRegex(KitVerificationError, "unexpected source"):
            verify_kit(self.out)

    def test_recipient_rejects_missing_tracked_dotfile(self):
        (self.hashmarks / ".gitignore").write_text("secrets/\\n")
        self.freeze("tracked hidden member")
        self.update_source_pin()
        self.build()
        (self.out / "source/.gitignore").unlink()
        with self.assertRaisesRegex(KitVerificationError, "omitted tracked"):
            verify_kit(self.out)

    def test_recipient_rejects_tampered_manifest_with_producer_digest(self):
        result = self.build()
        manifest = self.out / "manifest.json"
        manifest.write_text(manifest.read_text().replace(
            "HANDOFF_SOURCE_SNAPSHOT_ONLY", "OTHER_QUALIFICATION"
        ))
        with self.assertRaisesRegex(KitVerificationError, "producer-supplied digest"):
            verify_kit(self.out, expected_manifest_sha256=result["manifest_sha256"])

    def test_recipient_rejects_modified_blinded_question(self):
        self.build()
        packet = self.out / "questions.json"
        packet.write_text(packet.read_text().replace(
            "FIRST_PASS_UNREVIEWED", "UNVERIFIED_APPROVED"
        ))
        with self.assertRaisesRegex(KitVerificationError, "questions bytes"):
            verify_kit(self.out)

    def test_recipient_rejects_changed_verifier(self):
        self.build()
        script = self.out / "verify.py"
        script.write_text(script.read_text() + "\\n# modified verifier\\n")
        with self.assertRaisesRegex(KitVerificationError, "verifier changed"):
            verify_kit(self.out)

    def test_recipient_rejects_symlink_substitution(self):
        self.build()
        target = self.out / "source/hashmarks/store.py"
        target.unlink()
        target.symlink_to(self.hashmarks / "hashmarks/store.py")
        with self.assertRaisesRegex(KitVerificationError, "unsafe material"):
            verify_kit(self.out)

    def test_recipient_rejects_extra_root_answer_file(self):
        self.build()
        (self.out / "oracle-answers.json").write_text("{}")
        with self.assertRaisesRegex(KitVerificationError, "unexpected"):
            verify_kit(self.out)

    def test_recipient_rejects_bad_manifest_attestation(self):
        self.build()
        manifest_path = self.out / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["canonical_oracle_approved"] = True
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(KitVerificationError, "unsafe kit manifest"):
            verify_kit(self.out)

    def test_offline_review_starter_is_validly_bound_but_cannot_self_approve(self):
        from benchmarks.dogfood.hashmarks_review_returns import (
            ReturnError, project_returns,
        )
        producer = self.build()
        private = self.workspace / "private-review-starter.json"
        completed = subprocess.run([
            sys.executable, str(self.out / "start_review.py"),
            "--root", str(self.out),
            "--expect-manifest-sha256", producer["manifest_sha256"],
            "--output", str(private),
        ], cwd=self.workspace, capture_output=True, text=True,
           env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(private.is_file())
        self.assertFalse((self.out / "__pycache__").exists())
        self.assertEqual(
            verify_kit(self.out, expected_manifest_sha256=producer["manifest_sha256"])["qualification"],
            "TRANSPORT_BYTES_VERIFIED_ONLY",
        )
        self.assertEqual(private.stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.out / "reviewer-answers.json").exists())
        data = json.loads(private.read_text())
        packet = json.loads((self.out / "questions.json").read_text())
        manifest = json.loads((self.out / "manifest.json").read_text())
        self.assertEqual(data["blinded_packet_identity"], manifest["packet_digest"])
        self.assertEqual(len(data["reviews"]), 2)
        self.assertEqual({r["packet_id"] for r in data["reviews"]},
                         {q["packet_id"] for q in packet["packets"]})
        for r in data["reviews"]:
            self.assertEqual(r["decision"], "NOT_REVIEWED_REPLACE_BEFORE_SUBMISSION")
            self.assertIsNone(r["prior_expected_answer_access"])
            self.assertIsNone(r["source_inspected"])
            self.assertEqual(r["candidate_locations"], [])
            self.assertEqual(r["source_evidence"], [])
        with self.assertRaisesRegex(ReturnError, "decision"):
            project_returns(packet, data)
        self.assertFalse(json.loads(completed.stdout)["oracle_approved"])

    def test_starter_rejects_output_inside_received_kit(self):
        producer = self.build()
        destination = self.out / "review.json"
        run = subprocess.run([
            sys.executable, str(self.out / "start_review.py"),
            "--root", str(self.out),
            "--expect-manifest-sha256", producer["manifest_sha256"],
            "--output", str(destination),
        ], cwd=self.workspace, capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(destination.exists())

    def test_starter_rejects_wrong_producer_manifest_digest(self):
        self.build()
        destination = self.workspace / "private-draft.json"
        run = subprocess.run([
            sys.executable, str(self.out / "start_review.py"),
            "--root", str(self.out),
            "--expect-manifest-sha256", "0" * 64,
            "--output", str(destination),
        ], cwd=self.workspace, capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(destination.exists())

    def test_starter_does_not_overwrite_existing_private_review(self):
        producer = self.build()
        destination = self.workspace / "private-draft.json"
        destination.write_text("reviewer-private-contents")
        run = subprocess.run([
            sys.executable, str(self.out / "start_review.py"),
            "--root", str(self.out),
            "--expect-manifest-sha256", producer["manifest_sha256"],
            "--output", str(destination),
        ], cwd=self.workspace, capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(destination.read_text(), "reviewer-private-contents")

    def test_recipient_rejects_changed_or_missing_starter_script(self):
        self.build()
        script = self.out / "start_review.py"
        script.write_text(script.read_text() + "\\n# tampered\\n")
        with self.assertRaisesRegex(KitVerificationError, "starter changed"):
            verify_kit(self.out)

    def test_recipient_rejects_forged_packet_digest_even_with_manifest_rewritten(self):
        self.build()
        manifest = self.out / "manifest.json"
        record = json.loads(manifest.read_text())
        record["packet_digest"] = "sha256:" + "0" * 64
        manifest.write_text(json.dumps(record))
        with self.assertRaisesRegex(KitVerificationError, "packet identity"):
            verify_kit(self.out)

    def test_worktree_modified_and_untracked_files_do_not_enter_kit(self):
        (self.hashmarks / "hashmarks/store.py").write_text("def changed(): pass\n")
        (self.hashmarks / "secrets.txt").write_text("UNTRACKED-SECRET")
        self.build()
        self.assertIn(
            "def lookup", (self.out / "source/hashmarks/store.py").read_text()
        )
        self.assertFalse((self.out / "source/secrets.txt").exists())
        self.assertNotIn(
            "UNTRACKED-SECRET", (self.out / "manifest.json").read_text()
        )

    def test_unapproved_review_gate_remains_blocked_after_export(self):
        from benchmarks.harness.suite import SuiteError, load_suite
        self.build()
        with self.assertRaisesRegex(SuiteError, "oracle review evidence is missing"):
            load_suite(self.stage)
        self.assertFalse((self.stage / "qualification/oracle-reviews.json").exists())

    def test_refuses_missing_or_conflicting_historical_source_commit(self):
        original = self.commit
        self.commit = "f" * 40
        self.update_source_pin()
        with self.assertRaisesRegex(ReviewerKitError, "pinned historical"):
            self.build()
        self.assertFalse(self.out.exists())
        self.assertEqual(original, self.git("rev-parse", "HEAD"))

    def test_refuses_correct_commit_with_wrong_tree(self):
        protocol_file = self.stage / "protocol.json"
        proto = json.loads(protocol_file.read_text())
        proto["source"]["tree"] = "f" * 40
        protocol_file.write_text(json.dumps(proto))
        for task_id in TASKS:
            for relative in (
                SOURCE_TASK_ROOT / f"{task_id}.json",
                STAGED_RELATIVE / "tasks" / f"{task_id}.json",
            ):
                path = self.evalshot / relative
                data = json.loads(path.read_text())
                data["repository"]["tree"] = "f" * 40
                raw = (json.dumps(data, indent=2) + "\n").encode()
                path.write_bytes(raw)
            proto["task_transfers"][task_id]["git_blob_sha1"] = subprocess.run(
                ["git", "hash-object", "--stdin"], input=raw,
                capture_output=True, check=True,
            ).stdout.decode("ascii").strip()
        protocol_file.write_text(json.dumps(proto))
        with self.assertRaisesRegex(ReviewerKitError, "commit/tree"):
            self.build()
        self.assertFalse(self.out.exists())

    def test_refuses_git_symlink_in_historical_tree(self):
        (self.hashmarks / "hashmarks/shortcut.py").symlink_to("store.py")
        self.freeze("symlink")
        self.update_source_pin()
        with self.assertRaisesRegex(ReviewerKitError, "unsupported or unsafe"):
            self.build()
        self.assertFalse(self.out.exists())

    def test_refuses_git_export_ignore_incomplete_snapshot(self):
        (self.hashmarks / ".gitattributes").write_text(
            "nested/data.json export-ignore\n", encoding="utf-8"
        )
        self.freeze("export-ignored")
        self.update_source_pin()
        with self.assertRaisesRegex(ReviewerKitError, "omitted tracked"):
            self.build()
        self.assertFalse(self.out.exists())

    def test_refuses_git_export_subst_modified_source_bytes(self):
        (self.hashmarks / "tracked.txt").write_text(
            "$Format:%H$\n", encoding="utf-8"
        )
        (self.hashmarks / ".gitattributes").write_text(
            "tracked.txt export-subst\n", encoding="utf-8"
        )
        self.freeze("export substitution")
        self.update_source_pin()
        with self.assertRaisesRegex(ReviewerKitError, "differs from frozen Git blob"):
            self.build()
        self.assertFalse(self.out.exists())

    def test_refuses_destination_inside_evalshot(self):
        with self.assertRaisesRegex(ReviewerKitError, "external destination"):
            self.build(self.evalshot / "dangerous-review")
        self.assertFalse((self.evalshot / "dangerous-review").exists())

    def test_refuses_destination_inside_hashmarks(self):
        with self.assertRaisesRegex(ReviewerKitError, "external destination"):
            self.build(self.hashmarks / "review")
        self.assertFalse((self.hashmarks / "review").exists())

    def test_refuses_parent_destination_covering_existing_repository(self):
        with self.assertRaisesRegex(ReviewerKitError, "external destination"):
            self.build(self.workspace)
        self.assertTrue(self.evalshot.exists())

    def test_refuses_existing_destination_without_overwrite(self):
        self.out.mkdir()
        (self.out / "private-note.txt").write_text("preserve")
        with self.assertRaisesRegex(ReviewerKitError, "external destination"):
            self.build()
        self.assertEqual((self.out / "private-note.txt").read_text(), "preserve")

    def test_refuses_changed_staged_oracle_or_prompt(self):
        task_file = self.stage / "tasks" / f"{TASKS[0]}.json"
        task_file.write_text(task_file.read_text() + "\n")
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.out.exists())

    def test_rejects_pending_canonical_oracle_review_file(self):
        file = self.stage / "qualification/oracle-reviews.json"
        file.parent.mkdir()
        file.write_text('{"schema":"agents-cookbook-oracle-reviews.v1","tasks":{}}')
        with self.assertRaises(ValueError):
            self.build()

    def test_rejects_non_git_source_checkout(self):
        empty = self.workspace / "not-git"
        empty.mkdir()
        with self.assertRaises(ReviewerKitError):
            prepare_reviewer_kit(
                repository_root=self.evalshot,
                checkout=empty,
                output=self.out,
            )

    def test_does_not_mutate_staged_or_historical_inputs(self):
        before = {
            p.relative_to(self.evalshot): p.read_bytes()
            for p in self.evalshot.rglob("*.json")
        }
        self.build()
        self.assertEqual(before, {
            p.relative_to(self.evalshot): p.read_bytes()
            for p in self.evalshot.rglob("*.json")
        })


if __name__ == "__main__":
    unittest.main()
