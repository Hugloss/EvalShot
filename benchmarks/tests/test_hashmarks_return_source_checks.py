"""Source-citation existence never qualifies untrusted blinded reviewer claims."""

from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stderr
from io import StringIO
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from benchmarks.dogfood.hashmarks_return_source_checks import (
    main, verify_source_references,
)
from benchmarks.tests.test_hashmarks_review_returns import questions, review, responses


class FrozenReviewerCitationChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "Hashmarks"
        self.repo.mkdir()
        self.git("init", "-q")
        (self.repo / "hashmarks").mkdir()
        (self.repo / "hashmarks/a.py").write_text(
            "def a():\n"
            "    return 3\n"
            "# static source content\n"
            "\n\n\n\n\n"
            "def private_helper(): pass\n", encoding="utf-8",
        )
        (self.repo / "hashmarks/b.py").write_text(
            "class Other:\n"
            "    def b(self):\n"
            "        return 2\n",
            encoding="utf-8",
        )
        self.freeze("source")
        self.packet = questions()
        for row in self.packet["packets"]:
            row["repository"]["commit"] = self.commit
            row["repository"]["tree"] = self.tree

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def freeze(self, message):
        self.git("add", "-A")
        self.git("-c", "user.email=case@example.invalid",
                 "-c", "user.name=Case", "commit", "-qm", message)
        self.commit = self.git("rev-parse", "HEAD")
        self.tree = self.git("rev-parse", "HEAD^{tree}")

    def assertion(self, *, decision="unique", index=0):
        data = review(self.packet, decision=decision, index=index)
        data["source_evidence"] = [{
            "path": "hashmarks/a.py", "line": 1,
            "observation": "checked the actual source declaration, meaning still unverified",
        }]
        return data

    def check(self, claims=None):
        return verify_source_references(
            self.packet,
            responses(self.packet, claims) if claims is not None else None,
            checkout=self.repo,
        )

    def status(self, report, index=0):
        return report["tasks"][index]["source_reference_status"]

    def test_real_git_commit_tree_and_lines_exist_without_oracle_approval(self):
        report = self.check([self.assertion()])
        self.assertEqual(
            self.status(report),
            "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF",
        )
        self.assertEqual(report["summary"]["pinned_references_exist"], 1)
        self.assertEqual(report["summary"]["approved_oracles"], 0)
        self.assertEqual(report["summary"]["verified_independent_reviewers"], 0)
        self.assertEqual(report["summary"]["measured_native_headroom"], 0)
        self.assertFalse(report["tasks"][0]["claim_content_verified"])
        self.assertFalse(report["tasks"][0]["semantic_owner_proven"])

    def test_no_private_returns_means_waiting_not_verified_review(self):
        result = self.check()
        self.assertEqual(result["summary"]["responses_received"], 0)
        self.assertEqual(result["summary"]["awaiting_first_pass"], 2)
        self.assertEqual(result["summary"]["pinned_references_exist"], 0)
        self.assertTrue(all(
            row["source_reference_status"] == "AWAITING_FIRST_PASS"
            for row in result["tasks"]
        ))

    def test_missing_git_candidate_file_remains_unavailable(self):
        claim = self.assertion()
        claim["candidate_locations"][0]["path"] = "hashmarks/missing.py"
        result = self.check([claim])
        self.assertEqual(self.status(result), "PINNED_CANDIDATE_FILE_UNAVAILABLE")

    def test_wrong_declared_symbol_cannot_impersonate_python_declaration(self):
        claim = self.assertion()
        claim["candidate_locations"][0]["symbol"] = "not_there"
        report = self.check([claim])
        self.assertEqual(self.status(report), "CANDIDATE_DECLARATION_NOT_OBSERVED")

    def test_missing_line_is_not_a_supported_source_observation(self):
        claim = self.assertion()
        claim["source_evidence"][0]["line"] = 10000
        report = self.check([claim])
        self.assertEqual(self.status(report), "PINNED_EVIDENCE_LINE_ABSENT")

    def test_missing_evidence_file_is_distinct_from_missing_line(self):
        claim = self.assertion()
        claim["source_evidence"][0]["path"] = "hashmarks/never.py"
        report = self.check([claim])
        self.assertEqual(self.status(report), "PINNED_EVIDENCE_FILE_UNAVAILABLE")

    def test_source_symlink_does_not_masquerade_as_regular_python(self):
        (self.repo / "hashmarks/link.py").symlink_to("a.py")
        self.freeze("symlink")
        for row in self.packet["packets"]:
            row["repository"]["commit"] = self.commit
            row["repository"]["tree"] = self.tree
        claim = self.assertion()
        claim["candidate_locations"][0]["path"] = "hashmarks/link.py"
        self.assertEqual(
            self.status(self.check([claim])),
            "PINNED_CANDIDATE_FILE_UNAVAILABLE",
        )

    def test_unparseable_python_cannot_supply_declaration(self):
        (self.repo / "hashmarks/bad.py").write_text("def a( :\n")
        self.freeze("unparseable")
        for row in self.packet["packets"]:
            row["repository"]["commit"] = self.commit
            row["repository"]["tree"] = self.tree
        claim = self.assertion()
        claim["candidate_locations"][0]["path"] = "hashmarks/bad.py"
        self.assertEqual(
            self.status(self.check([claim])),
            "CANDIDATE_PYTHON_NOT_PARSEABLE",
        )

    def test_unrelated_line_in_same_candidate_file_cannot_substitute_for_declaration(self):
        claim = self.assertion()
        claim["source_evidence"][0]["line"] = 9  # actual unrelated private_helper
        result = self.check([claim])
        self.assertEqual(
            self.status(result), "CANDIDATE_WITHOUT_DECLARATION_SPAN_CITATION"
        )
        self.assertEqual(result["summary"]["pinned_references_exist"], 0)
        self.assertEqual(result["summary"]["approved_oracles"], 0)
        self.assertNotIn("hashmarks/a.py", json.dumps(result))

    def _guarded_owner_status(self, source: str, *, line: int = 2) -> str:
        (self.repo / "hashmarks/guarded.py").write_text(source, encoding="utf-8")
        self.freeze("guarded declaration fixture")
        for question in self.packet["packets"]:
            question["repository"]["commit"] = self.commit
            question["repository"]["tree"] = self.tree
        claim = self.assertion()
        claim["candidate_locations"] = [{
            "path": "hashmarks/guarded.py", "symbol": "a"
        }]
        claim["source_evidence"][0].update(
            path="hashmarks/guarded.py", line=line
        )
        report = self.check([claim])
        self.assertEqual(report["summary"]["approved_oracles"], 0)
        self.assertFalse(report["tasks"][0]["semantic_owner_proven"])
        self.assertNotIn("hashmarks/guarded.py", json.dumps(report))
        return self.status(report)

    def test_if_false_declaration_is_not_unconditional_owner_evidence(self):
        self.assertEqual(self._guarded_owner_status(
            "if False:\n"
            "    def a():\n"
            "        return 1\n",
        ), "CANDIDATE_ONLY_GUARDED_DECLARATION")

    def test_type_checking_declaration_is_not_unconditional_owner_evidence(self):
        self.assertEqual(self._guarded_owner_status(
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    class Demo:\n"
            "        def a(self): pass\n",
            line=4,
        ), "CANDIDATE_ONLY_GUARDED_DECLARATION")

    def test_try_except_declaration_does_not_establish_unconditional_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "try:\n"
            "    def a():\n"
            "        return 1\n"
            "except RuntimeError:\n"
            "    pass\n",
        ), "CANDIDATE_ONLY_GUARDED_DECLARATION")

    def test_match_case_declaration_is_guarded_not_verified_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "match 0:\n"
            "    case 0:\n"
            "        def a(): pass\n",
            line=3,
        ), "CANDIDATE_ONLY_GUARDED_DECLARATION")

    def test_conditional_same_name_alternative_blocks_unconditional_unique_claim(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "if should_override:\n"
            "    def a():\n"
            "        return 2\n",
            line=1,
        ), "CANDIDATE_GUARDED_ALTERNATIVE")

    def test_guard_inside_unrelated_function_does_not_hide_plain_declaration(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "def wrapper():\n"
            "    if enabled:\n"
            "        def a(): pass\n",
            line=1,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_prior_module_assignment_is_shadowed_by_unconditional_definition(self):
        self.assertEqual(self._guarded_owner_status(
            "a = 42\n"
            "def a():\n"
            "    return 1\n",
            line=2,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_prior_import_alias_is_overwritten_by_later_definition(self):
        self.assertEqual(self._guarded_owner_status(
            "from module import replacement as a\n"
            "def a():\n"
            "    return 1\n",
            line=2,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_prior_deletion_is_overwritten_by_later_definition(self):
        self.assertEqual(self._guarded_owner_status(
            "del a\n"
            "def a():\n"
            "    return 1\n",
            line=2,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_prior_class_field_is_overwritten_by_later_method(self):
        self.assertEqual(self._guarded_owner_status(
            "class Owner:\n"
            "    a = 0\n"
            "    def a(self):\n"
            "        return 1\n",
            line=3,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_prior_class_statement_is_overwritten_by_later_module_function(self):
        self.assertEqual(self._guarded_owner_status(
            "class a:\n"
            "    pass\n"
            "def a():\n"
            "    return 1\n",
            line=3,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_prior_wildcard_import_is_overwritten_by_later_module_function(self):
        self.assertEqual(self._guarded_owner_status(
            "from package import *\n"
            "def a():\n"
            "    return 1\n",
            line=2,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_later_wildcard_import_is_not_assumed_to_preserve_binding(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "from package import *\n",
            line=1,
        ), "CANDIDATE_WILDCARD_IMPORT_MAY_REBIND")

    def test_guarded_later_wildcard_import_is_still_unresolved(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "if enabled:\n"
            "    from package import *\n",
            line=1,
        ), "CANDIDATE_WILDCARD_IMPORT_MAY_REBIND")

    def test_module_wildcard_import_does_not_masquerade_as_class_method_rebinding(self):
        self.assertEqual(self._guarded_owner_status(
            "class Owner:\n"
            "    def a(self):\n"
            "        return 1\n"
            "from package import *\n",
            line=2,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_conditional_assignment_before_plain_def_is_shadowed_by_def(self):
        self.assertEqual(self._guarded_owner_status(
            "if enabled:\n"
            "    a = 99\n"
            "def a():\n"
            "    return 1\n",
            line=3,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_for_loop_target_after_definition_is_unresolved_binding(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "for a in values:\n"
            "    pass\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_destructured_for_target_after_definition_is_unresolved_binding(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "for unused, (a, extra) in values:\n"
            "    pass\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_prior_for_target_is_superseded_by_later_function(self):
        self.assertEqual(self._guarded_owner_status(
            "for a in items:\n"
            "    pass\n"
            "def a():\n"
            "    return 1\n",
            line=3,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_with_as_target_after_definition_is_unresolved_binding(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "with handle as a:\n"
            "    pass\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_destructured_with_target_is_unresolved_binding(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "with handle as (a, extra):\n"
            "    pass\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_except_as_target_after_definition_is_unresolved_binding(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "try:\n"
            "    dangerous()\n"
            "except RuntimeError as a:\n"
            "    pass\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_match_as_capture_after_definition_is_unresolved_binding(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "match candidate:\n"
            "    case a:\n"
            "        pass\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_match_sequence_star_capture_after_definition_is_unresolved_binding(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "match candidate:\n"
            "    case [head, *a]:\n"
            "        pass\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_match_mapping_rest_capture_after_definition_is_unresolved_binding(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "match candidate:\n"
            "    case {'value': value, **a}:\n"
            "        pass\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_unrelated_loop_with_and_match_targets_do_not_mutate_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "for other in sequence:\n"
            "    pass\n"
            "with resource as other:\n"
            "    pass\n"
            "match candidate:\n"
            "    case {'name': other}:\n"
            "        pass\n",
            line=1,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_loop_and_match_targets_in_unrelated_function_do_not_mutate_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "def other():\n"
            "    for a in sequence:\n"
            "        pass\n"
            "    match candidate:\n"
            "        case a:\n"
            "            pass\n",
            line=1,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_own_function_default_binding_is_superseded_by_its_def(self):
        self.assertEqual(self._guarded_owner_status(
            "def a(value=(a := 3)):\n"
            "    return 1\n",
            line=1,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_own_method_default_binding_is_superseded_by_its_def(self):
        self.assertEqual(self._guarded_owner_status(
            "class Owner:\n"
            "    def a(self, value=(a := 3)):\n"
            "        return 1\n",
            line=2,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_own_definition_header_does_not_hide_subsequent_rebinding(self):
        self.assertEqual(self._guarded_owner_status(
            "def a(value=(a := 3)):\n"
            "    return 1\n"
            "a = None\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_later_lambda_body_walrus_is_local_not_module_rebinding(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "later = lambda: (a := 3)\n",
            line=1,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_later_lambda_default_walrus_rebinds_parent_module_name(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "later = lambda value=(a := 3): value\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_lambda_body_in_later_decorator_does_not_mutate_prior_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "@(lambda: (a := 3))\n"
            "def later():\n"
            "    pass\n",
            line=1,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_later_function_decorator_can_rebind_prior_module_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "@(a := decorator)\n"
            "def other():\n"
            "    pass\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_later_function_default_can_rebind_prior_module_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "def other(arg=(a := 17)):\n"
            "    pass\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_later_function_parameter_annotation_can_rebind_module_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "def other(arg: (a := object)):\n"
            "    pass\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_later_class_decorator_can_rebind_prior_module_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "@(a := class_decorator)\n"
            "class Other:\n"
            "    pass\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_later_class_base_expression_can_rebind_prior_module_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "class Other((a := Base)):\n"
            "    pass\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_later_metaclass_keyword_can_rebind_prior_module_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "class Other(metaclass=(a := Meta)):\n"
            "    pass\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_later_method_decorator_rebinds_earlier_method_in_same_class(self):
        self.assertEqual(self._guarded_owner_status(
            "class Owner:\n"
            "    def a(self):\n"
            "        return 1\n"
            "    @(a := decorator)\n"
            "    def other(self):\n"
            "        pass\n",
            line=2,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_later_method_default_rebinds_earlier_method_in_same_class(self):
        self.assertEqual(self._guarded_owner_status(
            "class Owner:\n"
            "    def a(self):\n"
            "        return 1\n"
            "    def other(self, arg=(a := 3)):\n"
            "        pass\n",
            line=2,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_prior_function_default_is_superseded_by_later_module_definition(self):
        self.assertEqual(self._guarded_owner_status(
            "def early(x=(a := 3)):\n"
            "    pass\n"
            "def a():\n"
            "    return 1\n",
            line=3,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_walrus_in_unrelated_function_body_does_not_rebind_module_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "def other():\n"
            "    x = (a := 3)\n",
            line=1,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_class_member_decorator_does_not_rebind_module_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "class Other:\n"
            "    @(a := decorator)\n"
            "    def method(self):\n"
            "        pass\n",
            line=1,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_class_global_assignment_after_module_function_is_rebinding(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "class Holder:\n"
            "    global a\n"
            "    a = replacement\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_class_global_assignment_before_plain_module_def_is_superseded(self):
        self.assertEqual(self._guarded_owner_status(
            "class Holder:\n"
            "    global a\n"
            "    a = replacement\n"
            "def a():\n"
            "    return 1\n",
            line=4,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_class_global_write_inside_deferred_function_is_deferred(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "def callback():\n"
            "    class Holder:\n"
            "        global a\n"
            "        a = replacement\n",
            line=1,
        ), "CANDIDATE_DEFERRED_GLOBAL_REBINDING")

    def test_prior_nested_class_global_write_remains_possible_after_later_def(self):
        self.assertEqual(self._guarded_owner_status(
            "def callback():\n"
            "    class Holder:\n"
            "        global a\n"
            "        a = replacement\n"
            "def a():\n"
            "    return 1\n",
            line=5,
        ), "CANDIDATE_DEFERRED_GLOBAL_REBINDING")

    def test_class_global_declaration_and_read_without_write_is_not_rebinding(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "class Reader:\n"
            "    global a\n"
            "    result = a\n",
            line=1,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_class_same_named_attribute_without_global_is_not_module_rebinding(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "class Other:\n"
            "    a = replacement\n",
            line=1,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_class_global_with_as_target_after_definition_is_rebinding(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "class Holder:\n"
            "    global a\n"
            "    with handle as a:\n"
            "        pass\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_class_global_match_capture_after_definition_is_rebinding(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "class Holder:\n"
            "    global a\n"
            "    match value:\n"
            "        case a:\n"
            "            pass\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_class_global_definition_is_not_observed_as_regular_class_method(self):
        self.assertEqual(self._guarded_owner_status(
            "class Holder:\n"
            "    global a\n"
            "    def a():\n"
            "        return 1\n",
            line=3,
        ), "CANDIDATE_CLASS_GLOBAL_DEFINITION")

    def test_class_global_function_definition_overrides_earlier_module_candidate(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "class Holder:\n"
            "    global a\n"
            "    def a():\n"
            "        return 2\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_function_method_binding_does_not_inherit_class_global_declaration(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "class Holder:\n"
            "    global a\n"
            "    def method(self):\n"
            "        a = replacement\n",
            line=1,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_deferred_global_assignment_disputes_module_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "def callback():\n"
            "    global a\n"
            "    a = replacement\n",
            line=1,
        ), "CANDIDATE_DEFERRED_GLOBAL_REBINDING")

    def test_deferred_global_delete_disputes_module_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "def callback():\n"
            "    global a\n"
            "    del a\n",
            line=1,
        ), "CANDIDATE_DEFERRED_GLOBAL_REBINDING")

    def test_deferred_global_import_alias_disputes_module_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "def callback():\n"
            "    global a\n"
            "    from package import helper as a\n",
            line=1,
        ), "CANDIDATE_DEFERRED_GLOBAL_REBINDING")

    def test_deferred_global_loop_target_disputes_module_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "def callback():\n"
            "    global a\n"
            "    for a in values:\n"
            "        pass\n",
            line=1,
        ), "CANDIDATE_DEFERRED_GLOBAL_REBINDING")

    def test_potential_global_writer_before_definition_remains_deferred(self):
        self.assertEqual(self._guarded_owner_status(
            "def callback():\n"
            "    global a\n"
            "    a = replacement\n"
            "def a():\n"
            "    return 1\n",
            line=4,
        ), "CANDIDATE_DEFERRED_GLOBAL_REBINDING")

    def test_global_declaration_without_write_does_not_dispute_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "def reader():\n"
            "    global a\n"
            "    return a\n",
            line=1,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_same_name_local_write_without_global_does_not_dispute_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "def callback():\n"
            "    a = replacement\n",
            line=1,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_nested_function_global_declaration_is_not_parent_global_declaration(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "def outer():\n"
            "    def inner():\n"
            "        global a\n"
            "        return a\n"
            "    a = 3\n",
            line=1,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_nested_function_actual_global_assignment_is_observed(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "def outer():\n"
            "    def inner():\n"
            "        global a\n"
            "        a = replacement\n",
            line=1,
        ), "CANDIDATE_DEFERRED_GLOBAL_REBINDING")

    def test_class_body_does_not_inherit_enclosing_function_global_scope(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "def callback():\n"
            "    global a\n"
            "    class Unrelated:\n"
            "        a = 7\n",
            line=1,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_function_global_does_not_rebind_unrelated_class_method(self):
        self.assertEqual(self._guarded_owner_status(
            "class Owner:\n"
            "    def a(self):\n"
            "        return 1\n"
            "def callback():\n"
            "    global a\n"
            "    a = replacement\n",
            line=2,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_function_global_nested_function_definition_is_possible_write(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "def callback():\n"
            "    global a\n"
            "    def a():\n"
            "        return 2\n",
            line=1,
        ), "CANDIDATE_DEFERRED_GLOBAL_REBINDING")

    def test_class_suite_explicit_method_monkeypatch_is_unresolved(self):
        self.assertEqual(self._guarded_owner_status(
            "class Owner:\n"
            "    def a(self):\n"
            "        return 1\n"
            "class Patch:\n"
            "    Owner.a = replacement\n",
            line=2,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_class_suite_explicit_method_delete_is_unresolved(self):
        self.assertEqual(self._guarded_owner_status(
            "class Owner:\n"
            "    def a(self):\n"
            "        return 1\n"
            "class Patch:\n"
            "    del Owner.a\n",
            line=2,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_class_suite_global_does_not_redirect_explicit_method_write(self):
        self.assertEqual(self._guarded_owner_status(
            "class Owner:\n"
            "    def a(self):\n"
            "        return 1\n"
            "class Patch:\n"
            "    global a\n"
            "    Owner.a = replacement\n",
            line=2,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_class_suite_unrelated_receiver_is_not_candidate_mutation(self):
        self.assertEqual(self._guarded_owner_status(
            "class Owner:\n"
            "    def a(self):\n"
            "        return 1\n"
            "class Patch:\n"
            "    Other.a = replacement\n",
            line=2,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_deferred_method_assignment_disputes_class_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "class Owner:\n"
            "    def a(self):\n"
            "        return 1\n"
            "def callback():\n"
            "    Owner.a = replacement\n",
            line=2,
        ), "CANDIDATE_DEFERRED_METHOD_REBINDING")

    def test_deferred_method_write_before_class_is_still_possible(self):
        self.assertEqual(self._guarded_owner_status(
            "def callback():\n"
            "    Owner.a = replacement\n"
            "class Owner:\n"
            "    def a(self):\n"
            "        return 1\n",
            line=4,
        ), "CANDIDATE_DEFERRED_METHOD_REBINDING")

    def test_deferred_nested_method_delete_is_still_possible(self):
        self.assertEqual(self._guarded_owner_status(
            "class Owner:\n"
            "    def a(self):\n"
            "        return 1\n"
            "def callback():\n"
            "    def later():\n"
            "        del Owner.a\n",
            line=2,
        ), "CANDIDATE_DEFERRED_METHOD_REBINDING")

    def test_deferred_async_method_rebind_is_still_possible(self):
        self.assertEqual(self._guarded_owner_status(
            "class Owner:\n"
            "    def a(self):\n"
            "        return 1\n"
            "async def callback():\n"
            "    Owner.a += other\n",
            line=2,
        ), "CANDIDATE_DEFERRED_METHOD_REBINDING")

    def test_deferred_unrelated_method_write_does_not_dispute_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "class Owner:\n"
            "    def a(self):\n"
            "        return 1\n"
            "def callback():\n"
            "    Other.a = replacement\n",
            line=2,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_deferred_method_read_does_not_dispute_owner(self):
        self.assertEqual(self._guarded_owner_status(
            "class Owner:\n"
            "    def a(self):\n"
            "        return 1\n"
            "def callback():\n"
            "    return Owner.a\n",
            line=2,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_method_attribute_write_does_not_rebind_module_function(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "def callback():\n"
            "    Owner.a = replacement\n",
            line=1,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_module_function_reassigned_to_constant_is_not_a_verified_binding(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "a = False\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_module_function_overwritten_by_import_alias_is_unqualified(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "from somewhere import replacement as a\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_deleted_module_function_is_not_declared_available(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "del a\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_conditional_reassignment_remains_an_unresolved_binding_mutation(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "if setting:\n"
            "    a = False\n",
            line=1,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_class_method_rebound_inside_own_class_is_not_qualified(self):
        self.assertEqual(self._guarded_owner_status(
            "class Owner:\n"
            "    def a(self):\n"
            "        return 1\n"
            "    a = property(lambda self: 7)\n",
            line=2,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_monkeypatch_of_named_class_method_is_not_qualified(self):
        self.assertEqual(self._guarded_owner_status(
            "class Owner:\n"
            "    def a(self):\n"
            "        return 1\n"
            "Owner.a = override\n",
            line=2,
        ), "CANDIDATE_BINDING_MUTATION_OBSERVED")

    def test_other_class_binding_cannot_override_unrelated_class_method(self):
        self.assertEqual(self._guarded_owner_status(
            "class Owner:\n"
            "    def a(self):\n"
            "        return 1\n"
            "class Other:\n"
            "    a = 17\n",
            line=2,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_assignment_within_unrelated_function_does_not_rebind_module_symbol(self):
        self.assertEqual(self._guarded_owner_status(
            "def a():\n"
            "    return 1\n"
            "def another():\n"
            "    a = None\n",
            line=1,
        ), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF")

    def test_local_function_with_matching_name_is_not_an_observed_owner(self):
        (self.repo / "hashmarks/local.py").write_text(
            "def wrapper():\n"
            "    def a():\n"
            "        return 3\n"
            "    return a\n", encoding="utf-8",
        )
        self.freeze("local declaration")
        for q in self.packet["packets"]:
            q["repository"]["commit"] = self.commit
            q["repository"]["tree"] = self.tree
        claim = self.assertion()
        claim["candidate_locations"] = [{"path": "hashmarks/local.py", "symbol": "a"}]
        claim["source_evidence"][0].update(path="hashmarks/local.py", line=2)
        report = self.check([claim])
        self.assertEqual(self.status(report), "CANDIDATE_ONLY_LOCAL_DECLARATION")
        self.assertEqual(report["summary"]["pinned_references_exist"], 0)

    def test_two_same_name_declarations_in_one_file_cannot_be_called_unique(self):
        (self.repo / "hashmarks/duplicate.py").write_text(
            "class First:\n"
            "    def a(self): pass\n"
            "class Second:\n"
            "    def a(self): pass\n", encoding="utf-8",
        )
        self.freeze("duplicate declarations")
        for q in self.packet["packets"]:
            q["repository"]["commit"] = self.commit
            q["repository"]["tree"] = self.tree
        claim = self.assertion()
        claim["candidate_locations"] = [{"path": "hashmarks/duplicate.py", "symbol": "a"}]
        claim["source_evidence"][0].update(path="hashmarks/duplicate.py", line=2)
        report = self.check([claim])
        self.assertEqual(self.status(report), "CANDIDATE_MULTIPLE_DECLARATIONS_OBSERVED")
        self.assertEqual(report["summary"]["pinned_references_exist"], 0)
        self.assertEqual(report["summary"]["approved_oracles"], 0)

    def test_method_decorator_line_is_a_valid_syntactic_source_anchor(self):
        (self.repo / "hashmarks/decorated.py").write_text(
            "class Example:\n"
            "    @property\n"
            "    def a(self):\n"
            "        return 1\n", encoding="utf-8",
        )
        self.freeze("decorated method")
        for q in self.packet["packets"]:
            q["repository"]["commit"] = self.commit
            q["repository"]["tree"] = self.tree
        claim = self.assertion()
        claim["candidate_locations"] = [
            {"path": "hashmarks/decorated.py", "symbol": "a"}
        ]
        claim["source_evidence"][0].update(path="hashmarks/decorated.py", line=2)
        result = self.check([claim])
        self.assertEqual(
            self.status(result), "SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF"
        )
        self.assertFalse(result["tasks"][0]["semantic_owner_proven"])

    def test_existing_candidate_without_own_citation_is_not_source_anchored(self):
        claim = self.assertion()
        claim["source_evidence"][0]["path"] = "hashmarks/b.py"
        claim["source_evidence"][0]["line"] = 2  # real but unrelated source
        result = self.check([claim])
        self.assertEqual(
            self.status(result), "CANDIDATE_WITHOUT_CITED_SOURCE_EVIDENCE"
        )
        self.assertEqual(result["summary"]["pinned_references_exist"], 0)
        self.assertEqual(result["summary"]["approved_oracles"], 0)
        self.assertNotIn("hashmarks/b.py", json.dumps(result))

    def test_ambiguous_candidate_without_individual_source_anchor_is_not_positive(self):
        claim = self.assertion(decision="ambiguous")
        result = self.check([claim])
        self.assertEqual(
            self.status(result), "CANDIDATE_WITHOUT_CITED_SOURCE_EVIDENCE"
        )
        self.assertEqual(result["summary"]["pinned_references_exist"], 0)

    def test_non_python_source_cannot_claim_unverified_symbol_ownership(self):
        (self.repo / "README.md").write_text("symbol named pretend\n")
        self.freeze("non-python source")
        for question in self.packet["packets"]:
            question["repository"]["commit"] = self.commit
            question["repository"]["tree"] = self.tree
        claim = self.assertion()
        claim["candidate_locations"] = [
            {"path": "README.md", "symbol": "pretend"}
        ]
        claim["source_evidence"] = [{
            "path": "README.md", "line": 1,
            "observation": "a real line but not a Python declaration",
        }]
        result = self.check([claim])
        self.assertEqual(
            self.status(result), "UNSUPPORTED_NON_PYTHON_CANDIDATE"
        )
        self.assertEqual(result["summary"]["pinned_references_exist"], 0)
        self.assertEqual(result["summary"]["approved_oracles"], 0)

    def test_ambiguity_with_two_pinned_declarations_remains_ambiguous(self):
        claim = self.assertion(decision="ambiguous")
        claim["source_evidence"].append({
            "path": "hashmarks/b.py", "line": 2,
            "observation": "a second privately asserted synthetic owner",
        })
        result = self.check([claim])
        self.assertEqual(result["summary"]["pinned_references_exist"], 1)
        self.assertEqual(result["summary"]["approved_oracles"], 0)
        self.assertNotIn("hashmarks/b.py", json.dumps(result))

    def test_pinned_tree_mismatch_fails_without_private_source_claim(self):
        self.packet["packets"][0]["repository"]["tree"] = "0" * 40
        result = self.check([self.assertion()])
        self.assertEqual(
            self.status(result), "PINNED_SOURCE_IDENTITY_UNAVAILABLE",
        )
        # Both tasks claim one commit but inconsistent frozen trees; neither
        # may inherit a valid identity from the other.
        self.assertEqual(result["summary"]["unavailable_or_disputed_source"], 2)
        self.assertEqual(
            self.status(result, index=1), "PINNED_SOURCE_IDENTITY_UNAVAILABLE"
        )

    def test_conflicting_shared_tree_blocks_all_private_source_claims(self):
        self.packet["packets"][1]["repository"]["tree"] = "0" * 40
        first = self.assertion(index=0)
        second = self.assertion(index=1)
        result = self.check([first, second])
        self.assertEqual(
            [row["source_reference_status"] for row in result["tasks"]],
            ["PINNED_SOURCE_IDENTITY_UNAVAILABLE"] * 2,
        )
        self.assertEqual(result["summary"]["pinned_references_exist"], 0)
        self.assertEqual(result["summary"]["unavailable_or_disputed_source"], 2)
        self.assertEqual(result["summary"]["approved_oracles"], 0)

    def test_absent_frozen_commit_is_not_qualified(self):
        self.packet["packets"][0]["repository"]["commit"] = "f" * 40
        self.assertEqual(
            self.status(self.check([self.assertion()])),
            "PINNED_SOURCE_IDENTITY_UNAVAILABLE",
        )

    def test_worktree_edits_cannot_change_source_claim_status(self):
        before = self.check([self.assertion()])
        (self.repo / "hashmarks/a.py").write_text("def other(): pass\n")
        (self.repo / "hashmarks/new.py").write_text("def a(): pass\n")
        self.assertEqual(before, self.check([self.assertion()]))

    def test_source_answers_and_review_identity_never_appear_in_report(self):
        claim = self.assertion()
        claim["reviewer_id"] = "secret-private-reviewer"
        claim["reason"] = "secret rationale which must never be shown"
        claim["source_evidence"][0]["observation"] = "secret observation"
        result = self.check([claim])
        rendered = json.dumps(result)
        for secret in (
            "hashmarks/a.py", "secret-private-reviewer",
            "secret rationale", "secret observation",
        ):
            self.assertNotIn(secret, rendered)

    def test_unsupported_decision_or_stale_packet_is_rejected_before_git(self):
        wrong = self.assertion()
        wrong["source_commit"] = "f" * 40
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.check([wrong])

    def test_non_git_checkout_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Git working tree"):
            verify_source_references(self.packet, None, checkout=Path(self.temp.name))

    def test_cli_refuses_private_answers_stored_in_evalshot_checkout(self):
        # This is checked before attempting any reviewer or source material.
        root = Path(self.temp.name) / "EvalShot"
        root.mkdir()
        answers = root / "private-reviewer-answers.json"
        answers.write_text('{"schema":"invalid"}')
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
            main([
                "--repository-root", str(root),
                "--checkout", str(self.repo),
                "--responses", str(answers),
            ])
        self.assertEqual(error.exception.code, 2)
        self.assertTrue(answers.exists())

    def test_cli_refuses_writing_source_status_under_evalshot_checkout(self):
        root = Path(self.temp.name) / "EvalShot"
        root.mkdir()
        target = root / "source-status.json"
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
            main([
                "--repository-root", str(root),
                "--checkout", str(self.repo),
                "--output", str(target),
            ])
        self.assertEqual(error.exception.code, 2)
        self.assertFalse(target.exists())

    def test_determinism_and_source_input_non_mutation(self):
        incoming = responses(self.packet, [self.assertion()])
        saved = deepcopy((self.packet, incoming))
        first = verify_source_references(self.packet, incoming, checkout=self.repo)
        self.assertEqual(first, verify_source_references(
            self.packet, incoming, checkout=self.repo,
        ))
        self.assertEqual((self.packet, incoming), saved)


if __name__ == "__main__":
    unittest.main()
