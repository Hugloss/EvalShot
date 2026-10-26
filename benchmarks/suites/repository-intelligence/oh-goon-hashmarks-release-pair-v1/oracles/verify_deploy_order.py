import copy
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

repository = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repository))
from deploy_order.graph import order_services


def cli(path):
    return subprocess.run(
        [sys.executable, "-I", "-B", "-c",
         "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('deploy_order',run_name='__main__')",
         str(repository), str(path)],
        text=True, capture_output=True, timeout=10,
    )


class DeploymentOrderContract(unittest.TestCase):
    def test_empty_and_independent_services(self):
        self.assertEqual(order_services({}), [])
        self.assertEqual(order_services({"z": [], "a": [], "m": []}), ["a", "m", "z"])

    def test_newly_ready_services_compete_with_existing_ready_services(self):
        manifest = {"z": [], "b": ["a"], "a": [], "c": ["b"]}
        self.assertEqual(order_services(manifest), ["a", "b", "c", "z"])

    def test_shared_dependencies_duplicates_permutations_and_nonmutation(self):
        manifest = {"web": ["cache", "db", "db"], "worker": ["db"],
                    "db": [], "cache": ["db"]}
        before = copy.deepcopy(manifest)
        expected = ["db", "cache", "web", "worker"]
        self.assertEqual(order_services(manifest), expected)
        self.assertEqual(manifest, before)
        reordered = {key: list(reversed(manifest[key])) for key in reversed(manifest)}
        self.assertEqual(order_services(reordered), expected)
        self.assertEqual(order_services({"café": [], "api": ["café"]}), ["café", "api"])

    def test_invalid_shapes_and_undeclared_dependencies(self):
        for manifest in (None, [], "api", {"": []}, {1: []}, {"api": "db"},
                         {"api": ()}, {"api": None}, {"api": [1]}, {"api": [""]},
                         {"api": [[]]}, {"api": [{}]}, {"api": [True]},
                         {"api": ["missing"]}, {"ok": [], "z": ["absent"]}):
            before = copy.deepcopy(manifest)
            with self.subTest(manifest=manifest), self.assertRaises(ValueError):
                order_services(manifest)
            self.assertEqual(manifest, before)

    def test_cycles_including_disconnected_components(self):
        for manifest in ({"a": ["a"]}, {"a": ["b"], "b": ["a"]},
                         {"ready": [], "a": ["b"], "b": ["c"], "c": ["a"]}):
            before = copy.deepcopy(manifest)
            with self.subTest(manifest=manifest), self.assertRaises(ValueError):
                order_services(manifest)
            self.assertEqual(manifest, before)

    def test_chain_beyond_recursion_limit(self):
        names = [f"service-{index:05}" for index in range(2500)]
        manifest = {name: [names[index - 1]] if index else []
                    for index, name in reversed(list(enumerate(names)))}
        self.assertEqual(order_services(manifest), names)

    def test_cli_utf8_success_and_atomic_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "deployment manifest.json"
            path.write_text(json.dumps({"api": ["café"], "café": []},
                                       ensure_ascii=False), encoding="utf-8")
            result = cli(path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertTrue(result.stdout.endswith("\n"))
            self.assertEqual(json.loads(result.stdout), ["café", "api"])
            for payload in (b"{broken", b"[]", b'{"ok": [], "bad": ["absent"]}',
                            b'{"ok": [], "a": ["a"]}', b"\xff"):
                path.write_bytes(payload)
                result = cli(path)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertTrue(result.stderr.strip())
            result = cli(path.with_name("missing.json"))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertTrue(result.stderr.strip())


unittest.main(argv=["live-codex-deployment-order-verification"])
