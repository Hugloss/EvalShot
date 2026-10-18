"""Local regression suite for the paired outcome gate (no model or network needed)."""
import importlib.util
import pathlib
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "paired_outcome_gate.py"
spec = importlib.util.spec_from_file_location("paired_outcome_gate", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def arm(task, variant, owner, reads, correct=True):
    sha = ("%064x" % (task * 2 + variant + 1))
    identity = f"{task}-{variant}"
    return {
        "first_choice_owner_correct": owner,
        "exploratory_native_source_reads": reads,
        "patch_test_correct": correct,
        "delivery": {
            "host_attested": True, "payload_sha256": sha,
            "visible_payload_sha256": sha,
            "delivery_id": f"delivery-{identity}",
            "host_trial_id": f"trial-{identity}",
        },
    }


def campaign(n=20):
    return {"schema": "agentscookbook.paired-outcome.v1", "pairs": [
        {"task_id": f"task-{i}", "baseline": arm(i, 0, i < 10, 10),
         "treatment": arm(i, 1, i < 12, 8)}
        for i in range(n)
    ]}


class PairedOutcomeGateTests(unittest.TestCase):
    def test_thresholds_at_boundary(self):
        result = module.evaluate(campaign())
        self.assertEqual(result["qualification"], "PASS")
        self.assertEqual(result["first_choice_owner"]["improvement_percentage_points"], 10)
        self.assertAlmostEqual(result["exploratory_native_source_reads"]["reduction_fraction"], .2)
        self.assertEqual(result["host_attested_trials"], 40)

    def test_owner_target_missed(self):
        doc = campaign()
        doc["pairs"][11]["treatment"]["first_choice_owner_correct"] = False
        self.assertEqual(module.evaluate(doc)["qualification"], "FAIL")

    def test_native_reads_target_missed(self):
        doc = campaign()
        doc["pairs"][0]["treatment"]["exploratory_native_source_reads"] = 9
        self.assertEqual(module.evaluate(doc)["qualification"], "FAIL")

    def test_credible_correctness_regression(self):
        doc = campaign()
        for pair in doc["pairs"][:6]:
            pair["treatment"]["patch_test_correct"] = False
        result = module.evaluate(doc)
        self.assertFalse(result["patch_test_correctness"]["no_credible_regression"])
        self.assertEqual(result["qualification"], "FAIL")

    def test_zero_baseline_reads_not_claimed_as_reduction(self):
        doc = campaign()
        for pair in doc["pairs"]:
            pair["baseline"]["exploratory_native_source_reads"] = 0
            pair["treatment"]["exploratory_native_source_reads"] = 0
        self.assertIsNone(module.evaluate(doc)["exploratory_native_source_reads"]["reduction_fraction"])
        self.assertEqual(module.evaluate(doc)["qualification"], "FAIL")

    def test_missing_or_forged_delivery_fails_closed(self):
        for field, value in (("host_attested", False), ("visible_payload_sha256", "0" * 64),
                             ("host_trial_id", "")):
            doc = campaign()
            doc["pairs"][0]["treatment"]["delivery"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                module.evaluate(doc)

    def test_duplicate_pair_or_delivery_fails_closed(self):
        doc = campaign()
        doc["pairs"][1]["task_id"] = doc["pairs"][0]["task_id"]
        with self.assertRaises(ValueError):
            module.evaluate(doc)
        doc = campaign()
        doc["pairs"][1]["baseline"]["delivery"] = dict(doc["pairs"][0]["baseline"]["delivery"])
        with self.assertRaises(ValueError):
            module.evaluate(doc)

    def test_invalid_metric_type_fails_closed(self):
        doc = campaign()
        doc["pairs"][0]["baseline"]["exploratory_native_source_reads"] = True
        with self.assertRaises(ValueError):
            module.evaluate(doc)


if __name__ == "__main__":
    unittest.main()
