"""Bounded, source-pinned Hashmarks #470 semantic controls (not a scored agent oracle)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


MANIFEST = Path(__file__).resolve().parents[1] / "fixtures" / "hashmarks_dogfood" / "pr470.json"


class DefinitionHeaderDogfoodTests(unittest.TestCase):
    def test_source_pin_and_qualification_remain_explicit(self):
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], "evalshot-hashmarks-dogfood-source-pins.v1")
        self.assertEqual(data["origin"]["pull_request"], 470)
        for key in ("base_commit", "candidate_commit"):
            self.assertRegex(data["origin"][key], r"^[0-9a-f]{40}$")
        self.assertEqual(data["status"], "SOURCE_PINNED_SEMANTIC_GOLD_PENDING")
        self.assertEqual(data["qualification"]["semantic_finding_quality_score"], None)
        self.assertTrue(all(value is False for key, value in data["qualification"].items()
                            if key != "semantic_finding_quality_score"))
        self.assertEqual({s["id"] for s in data["scenarios"]},
                         {"default-argument", "decorator", "class-base", "body-recursion-control"})

    def test_default_argument_uses_previous_binding_then_body_uses_new_binding(self):
        events = []
        def imported():
            events.append("imported")
            return 7
        namespace = {"build": imported}
        exec("def build(value=build()):\n    return value\n", namespace)
        self.assertEqual(events, ["imported"])
        self.assertEqual(namespace["build"](), 7)
        self.assertIsNot(namespace["build"], imported)

    def test_decorator_uses_previous_binding(self):
        events = []
        def imported():
            events.append("imported")
            return lambda function: function
        namespace = {"build": imported}
        exec("@build()\ndef build():\n    return 1\n", namespace)
        self.assertEqual(events, ["imported"])
        self.assertEqual(namespace["build"](), 1)

    def test_class_base_uses_previous_binding(self):
        events = []
        def imported():
            events.append("imported")
            return object
        namespace = {"build": imported}
        exec("class build(build()):\n    pass\n", namespace)
        self.assertEqual(events, ["imported"])
        self.assertEqual(namespace["build"].__bases__, (object,))

    def test_body_recursion_is_not_an_earlier_header_call(self):
        def imported(value):
            raise AssertionError("previous import must not own body recursion")
        namespace = {"build": imported}
        exec("def build(value):\n    return build(value - 1) if value else 0\n", namespace)
        self.assertEqual(namespace["build"](2), 0)


if __name__ == "__main__":
    unittest.main()
