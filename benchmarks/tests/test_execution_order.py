from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from benchmarks.harness.suite import SuiteError, load_runtime_suite, load_suite
from benchmarks.harness.selection import select_definitions


SOURCE = Path(__file__).resolve().parents[1] / "suites/repository-intelligence/pilot-v1"


def assignment(row):
    return {
        "task_id": row["task_id"],
        "condition_id": row["condition_id"],
        "replicate_id": row.get("replicate_id", row.get("seed")),
    }


class ExecutionOrderTests(unittest.TestCase):
    def test_optional_frozen_order_conserves_membership_and_changes_plan_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "suite"
            shutil.copytree(SOURCE, root)
            before = load_suite(root).trial_definitions()
            experiment = json.loads((root / "experiment.json").read_text())
            experiment["execution_order"] = [assignment(row) for row in reversed(before)]
            (root / "experiment.json").write_text(json.dumps(experiment))
            after = load_suite(root).trial_definitions()
            self.assertEqual([assignment(row) for row in after], experiment["execution_order"])
            self.assertEqual(len({row["definition_id"] for row in after}), len(before))
            # Declaring a different experimental order changes authority rather
            # than reusing the historical trial identities.
            self.assertTrue({r["definition_id"] for r in before}.isdisjoint(
                {r["definition_id"] for r in after}
            ))
            self.assertEqual(load_suite(root).trial_definitions(), after)
            selected = select_definitions(load_suite(root), tasks=(after[0]["task_id"],))
            self.assertEqual(selected, [row for row in after if row["task_id"] == after[0]["task_id"]])

    def test_invalid_order_fails_runtime_loading_before_task_materialization(self):
        baseline = load_suite(SOURCE)
        complete = [assignment(row) for row in baseline.trial_definitions()]
        invalid = {
            "omitted": complete[:-1],
            "duplicate": complete[:-1] + [complete[0]],
            "added": complete + [{**complete[0], "replicate_id": -99999}],
            "foreign-task": [{**complete[0], "task_id": "foreign"}] + complete[1:],
            "wrong-replicate": [{**complete[0], "replicate_id": -99999}] + complete[1:],
        }
        for name, order in invalid.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "suite"
                shutil.copytree(SOURCE, root)
                experiment = copy.deepcopy(baseline.experiment)
                experiment["execution_order"] = order
                (root / "experiment.json").write_text(json.dumps(experiment))
                shutil.rmtree(root / "tasks")
                with self.assertRaisesRegex(SuiteError, "execution_order|schema validation"):
                    load_runtime_suite(root)


if __name__ == "__main__":
    unittest.main()
