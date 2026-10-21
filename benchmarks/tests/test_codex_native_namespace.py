"""Native path aliases preserve commands while confining writes to trial state.

The kernel test uses only temporary fixtures and fixed system Python. It starts
no native agent, plugin, provider connection or model request.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from benchmarks.adapters import codex_native_namespace as namespace
from benchmarks.harness.model import TrialContext
from benchmarks.harness.workspace import isolated_environment


OBSERVED = {
    "source_home_alias_private": True,
    "descendant_alias_private": True,
    "host_readonly_observed": True,
}


class NativeNamespaceFixtures(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="evalshot-namespace-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.control = self.root / "trial"
        environment = isolated_environment(self.control, source={"PATH": os.defpath})
        self.private = Path(environment["CODEX_HOME"])
        self.source = self.root / "native-source"
        self.source.mkdir()
        (self.source / "config.toml").write_text("host-source-input\n", encoding="utf-8")
        (self.private / "config.toml").write_text("private-trial-input\n", encoding="utf-8")
        self.context = TrialContext(self.workspace, self.control, environment)
        self.fake_bwrap = self.root / "bwrap-fixture"
        self.fake_bwrap.write_bytes(b"fixture-executable-identity")
        self.fake_bwrap.chmod(0o755)

    def build(self, command=("native-codex", "--version"), source=None):
        with patch.object(namespace.shutil, "which", return_value=str(self.fake_bwrap)):
            return namespace.build_native_argv(self.context, command, source or self.source)

    def native_runtime(self):
        executable = self.source / "packages/fixture-version/bin/codex"
        executable.parent.mkdir(parents=True)
        executable.write_text('#!/bin/sh\nexec /usr/bin/python3 "$@"\n', encoding="utf-8")
        sibling = executable.with_name("codex-code-mode-host")
        sibling.write_text("#!/bin/sh\nprintf 'code-mode-fixture\\n'\n", encoding="utf-8")
        for path in (executable, sibling):
            path.chmod(0o755)
        self.context.environment["BENCHMARK_NATIVE_CODEX_EXECUTABLE"] = str(executable)
        return executable, sibling


class NativeNamespaceArgvTests(NativeNamespaceFixtures):
    def test_command_and_literal_source_paths_are_preserved_as_exact_tail(self):
        command = ("native-codex", "--no-daemon", "exec", "--json", "-c",
                   f'plugin_path="{self.source}/plugins/exact/plugin.json"',
                   "--", "preserve literal task $() and whitespace")
        argv = self.build(command)
        self.assertEqual(argv[-len(command):], command)
        self.assertEqual(argv[-len(command) - 1], "--")
        self.assertIn(str(self.source / "plugins/exact/plugin.json"), argv[-3])
        self.assertNotIn(str(self.private / "plugins/exact/plugin.json"), argv[-3])

    def test_readonly_host_and_private_alias_apply_to_entire_process_tree(self):
        argv = self.build()
        triples = {tuple(argv[index:index + 3]) for index in range(len(argv) - 2)}
        self.assertIn(("--ro-bind", "/", "/"), triples)
        self.assertIn(("--bind", str(self.control), str(self.control)), triples)
        self.assertIn(("--bind", str(self.workspace), str(self.workspace)), triples)
        self.assertIn(("--bind", str(self.private), str(self.source)), triples)
        self.assertIn(("--proc", "/proc", "--dev"), triples)
        self.assertIn("--unshare-pid", argv)
        self.assertIn("--die-with-parent", argv)
        self.assertNotIn("--unshare-net", argv)
        self.assertNotIn("--unshare-all", argv)

    def test_system_bwrap_resolution_cannot_select_subject_PATH_wrapper(self):
        self.context.environment["PATH"] = "/operator/untrusted-binaries"
        with patch.object(namespace.shutil, "which", return_value=str(self.fake_bwrap)) as resolve:
            argv = namespace.build_native_argv(self.context, ("native-codex",), self.source)
        resolve.assert_called_once_with("bwrap", path=os.defpath)
        self.assertEqual(argv[0], str(self.fake_bwrap.resolve()))

    def test_missing_system_bwrap_rejects_before_launch(self):
        with patch.object(namespace.shutil, "which", return_value=None), \
             self.assertRaisesRegex(ValueError, "native Codex namespace"):
            namespace.build_native_argv(self.context, ("native-codex",), self.source)

    def test_invalid_source_or_overlap_is_not_rewritten_or_admitted(self):
        linked = self.root / "linked-source"
        linked.symlink_to(self.source, target_is_directory=True)
        file_source = self.root / "source-file"
        file_source.write_text("not-a-directory", encoding="utf-8")
        nested_workspace = self.workspace / "source"
        nested_workspace.mkdir()
        nested_control = self.control / "source"
        nested_control.mkdir()
        for source in (Path("/"), self.root, self.workspace, nested_workspace,
                       self.control, nested_control, self.root / "missing", file_source,
                       linked, Path("relative-source")):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "native Codex namespace"):
                self.build(source=source)
        self.assertEqual((self.source / "config.toml").read_text(), "host-source-input\n")

    def test_private_state_cannot_escape_control_through_path_or_symlink(self):
        outside = self.root / "outside-state"
        outside.mkdir()
        linked = self.control / "linked-state"
        linked.symlink_to(outside, target_is_directory=True)
        for selected in (outside, linked):
            with self.subTest(selected=selected), self.assertRaisesRegex(ValueError, "native Codex namespace"):
                self.context.environment["CODEX_HOME"] = str(selected)
                self.build()

    def test_source_alias_with_intermediate_symlink_or_noncanonical_parent_is_rejected(self):
        linked_parent = self.root / "parent-alias"
        linked_parent.symlink_to(self.root, target_is_directory=True)
        for selected in (linked_parent / self.source.name, self.source / ".." / self.source.name):
            with self.subTest(selected=selected), self.assertRaisesRegex(ValueError, "native Codex namespace"):
                self.build(source=selected)

    def test_invalid_command_never_reaches_a_shell(self):
        for command in ((), ("native-codex", None), ("native-codex", "invalid\0argument")):
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, "native Codex namespace"):
                self.build(command=command)

    def test_native_runtime_files_are_readonly_nested_binds_after_home_alias(self):
        executable, sibling = self.native_runtime()
        command = (str(executable), "--json", "literal-task")
        argv = self.build(command)
        triples = [tuple(argv[index:index + 3]) for index in range(len(argv) - 2)]
        alias_index = triples.index(("--bind", str(self.private), str(self.source)))
        for path in (executable, sibling):
            self.assertGreater(triples.index(("--ro-bind", str(path), str(path))), alias_index)
        self.assertEqual(argv[-len(command):], command)


class NativeNamespaceProbeTests(NativeNamespaceFixtures):
    def result(self, payload=OBSERVED, **overrides):
        return SimpleNamespace(
            **{"return_code": 0, "timed_out": False, "executable_missing": False,
               "stdout_truncated": False, "stderr_truncated": False,
               "stdout": json.dumps(payload).encode(), **overrides},
        )

    def probe(self, result):
        with patch.object(namespace.shutil, "which", return_value=str(self.fake_bwrap)), \
             patch.object(namespace, "run_bounded", return_value=result) as run:
            observed = namespace.probe_native_namespace(self.context, self.source)
        return observed, run

    def test_probe_uses_fixed_system_python_and_only_claims_observed_kernel_facts(self):
        observed, run = self.probe(self.result())
        self.assertEqual(observed["schema"], "evalshot.native-codex-path-namespace.v1")
        self.assertEqual(observed["state"], "observed")
        self.assertEqual({key: observed[key] for key in OBSERVED}, OBSERVED)
        self.assertEqual(observed["bwrap_sha256"], hashlib.sha256(self.fake_bwrap.read_bytes()).hexdigest())
        argv = run.call_args.kwargs["argv"]
        self.assertIn("/usr/bin/python3", argv)
        self.assertNotIn("native-codex", argv)
        self.assertFalse(run.call_args.kwargs["inherit_environment"])
        self.assertEqual((self.source / "config.toml").read_text(), "host-source-input\n")

    def test_partial_false_malformed_or_truncated_probes_never_establish_alias_isolation(self):
        for payload in ({}, {**OBSERVED, "descendant_alias_private": False},
                        {**OBSERVED, "host_readonly_observed": 1},
                        {**OBSERVED, "unsupported_claim": True}, None):
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "native Codex namespace probe failed"):
                self.probe(self.result(payload))
        for fields in ({"return_code": 1}, {"timed_out": True}, {"stdout_truncated": True},
                       {"stderr_truncated": True}, {"executable_missing": True}, {"stdout": b"not JSON"}):
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, "native Codex namespace probe failed"):
                self.probe(self.result(**fields))

    def test_process_TMPDIR_cannot_place_probe_witness_in_native_source_home(self):
        original = tempfile.TemporaryDirectory
        source_before = {p.name: p.read_bytes() for p in self.source.iterdir()}
        with patch.dict(os.environ, {"TMPDIR": str(self.source)}), \
             patch.object(namespace.tempfile, "TemporaryDirectory", wraps=original) as temporary:
            self.probe(self.result())
        temporary.assert_called_once()
        self.assertEqual(Path(temporary.call_args.kwargs["dir"]), Path("/tmp").resolve())
        self.assertEqual({p.name: p.read_bytes() for p in self.source.iterdir()}, source_before)

    def test_invalid_mapping_is_rejected_before_creating_any_witness(self):
        self.context.environment["CODEX_HOME"] = str(self.root / "outside-state")
        with patch.object(namespace.tempfile, "TemporaryDirectory") as temporary, \
             self.assertRaisesRegex(ValueError, "native Codex namespace"):
            namespace.probe_native_namespace(self.context, self.source)
        temporary.assert_not_called()

    def test_witness_parent_inside_source_is_rejected_before_directory_creation(self):
        # Isolate the witness guard from the separately tested mapping guard.
        with patch.object(namespace, "build_native_argv", return_value=(str(self.fake_bwrap),)), \
             patch.object(namespace.tempfile, "TemporaryDirectory") as temporary, \
             self.assertRaisesRegex(ValueError, "native Codex namespace probe witness location"):
            namespace.probe_native_namespace(self.context, Path("/tmp"))
        temporary.assert_not_called()


class NativeNamespaceVerificationTests(NativeNamespaceFixtures):
    def observation(self):
        return {"schema": "evalshot.native-codex-path-namespace.v1", "state": "observed",
                **OBSERVED, "bwrap_sha256": hashlib.sha256(self.fake_bwrap.read_bytes()).hexdigest()}

    def test_observed_wrapper_bytes_remain_admitted_until_content_drift(self):
        observation = self.observation()
        with patch.object(namespace, "_bwrap", return_value=self.fake_bwrap):
            self.assertTrue(namespace.verify_native_namespace(observation))
            self.fake_bwrap.write_bytes(b"changed-wrapper-bytes")
            self.assertFalse(namespace.verify_native_namespace(observation))

    def test_missing_or_unavailable_wrapper_cannot_reuse_old_observation(self):
        observation = self.observation()
        self.fake_bwrap.unlink()
        with patch.object(namespace, "_bwrap", return_value=self.fake_bwrap):
            self.assertFalse(namespace.verify_native_namespace(observation))
        with patch.object(namespace, "_bwrap", side_effect=ValueError("unavailable")):
            self.assertFalse(namespace.verify_native_namespace(observation))

    def test_partial_false_and_wrong_schema_observations_are_rejected(self):
        good = self.observation()
        malformed = [{}, {**good, "schema": "future-schema"}, {**good, "state": "unknown"},
                     {**good, "bwrap_sha256": "0" * 64}]
        for name in OBSERVED:
            malformed.extend(({**good, name: False}, {**good, name: 1},
                              {key: value for key, value in good.items() if key != name}))
        with patch.object(namespace, "_bwrap", return_value=self.fake_bwrap):
            for observation in malformed:
                with self.subTest(observation=observation):
                    self.assertFalse(namespace.verify_native_namespace(observation))

    def test_nonobject_observation_is_unverified_without_throwing(self):
        with patch.object(namespace, "_bwrap", return_value=self.fake_bwrap):
            for observation in (None, [], "unverified", True):
                with self.subTest(observation=observation):
                    self.assertFalse(namespace.verify_native_namespace(observation))

    def test_code_mode_host_bytes_and_mode_are_bound_to_the_namespace_observation(self):
        executable, sibling = self.native_runtime()
        process = SimpleNamespace(return_code=0, timed_out=False, executable_missing=False,
                                  stdout_truncated=False, stderr_truncated=False, stdout=json.dumps(OBSERVED).encode())
        with patch.object(namespace.shutil, "which", return_value=str(self.fake_bwrap)), \
             patch.object(namespace, "run_bounded", return_value=process):
            observation = namespace.probe_native_namespace(self.context, self.source)
        self.assertEqual({row["path"] for row in observation["native_runtime"]}, {str(executable), str(sibling)})
        original = sibling.read_bytes()
        with patch.object(namespace, "_bwrap", return_value=self.fake_bwrap):
            self.assertTrue(namespace.verify_native_namespace(observation))
            sibling.write_bytes(b"changed-code-mode-host")
            self.assertFalse(namespace.verify_native_namespace(observation))
            sibling.write_bytes(original)
            sibling.chmod(0o600)
            self.assertFalse(namespace.verify_native_namespace(observation))


class NativeNamespaceKernelTests(NativeNamespaceFixtures):
    def require_namespace_kernel(self):
        bwrap = shutil.which("bwrap", path=os.defpath)
        if bwrap is None:
            self.skipTest("system bubblewrap unavailable; missing-bwrap rejection is covered separately")
        if not Path("/usr/bin/python3").is_file():
            self.skipTest("fixed system Python unavailable for model-free kernel probe")
        completed = subprocess.run(
            (bwrap, "--ro-bind", "/", "/", "--unshare-pid", "--proc", "/proc", "--dev", "/dev",
             "--", "/usr/bin/python3", "-c", "pass"),
            cwd=self.workspace, env=self.context.environment, capture_output=True,
            timeout=20, check=False,
        )
        if completed.returncode:
            diagnostic = completed.stderr.decode("utf-8", errors="replace")
            if any(reason in diagnostic.lower() for reason in (
                "operation not permitted", "no permissions to create new namespace",
                "no permissions to create a new namespace",
                "unprivileged user namespaces are not enabled",
            )):
                self.skipTest("kernel denies bubblewrap namespace creation: " + diagnostic.strip())
            self.fail("bubblewrap baseline failed for an unexpected reason: " + diagnostic)

    def test_kernel_alias_and_child_writes_reach_only_private_copy_with_network_namespace_retained(self):
        self.require_namespace_kernel()
        source_before = {p.name: p.read_bytes() for p in self.source.iterdir()}
        observed = namespace.probe_native_namespace(self.context, self.source)
        self.assertEqual(observed["state"], "observed")
        self.assertTrue(all(observed[key] is True for key in OBSERVED))
        witness = self.root / "readonly-witness"
        witness.write_text("host-witness", encoding="utf-8")
        program = r'''
import json, os, pathlib, subprocess, sys
source, private, workspace, control, witness = map(pathlib.Path, sys.argv[1:])
assert pathlib.Path(os.environ['CODEX_HOME']) == source
assert (source / 'config.toml').read_text() == 'private-trial-input\n'
(source / 'parent-write').write_text('parent')
subprocess.run([sys.executable, '-c',
    'import pathlib, sys; pathlib.Path(sys.argv[1]).write_text("child")',
    str(source / 'child-write')], check=True)
(workspace / 'workspace-write').write_text('workspace')
(control / 'control-write').write_text('control')
readonly = False
try:
    witness.write_text('must-not-write')
except OSError:
    readonly = True
assert readonly
print(json.dumps({'same_file': os.path.samefile(source, private),
    'child_value': (private / 'child-write').read_text(),
    'network_namespace': os.stat('/proc/self/ns/net').st_ino}))
'''
        command = ("/usr/bin/python3", "-c", program, str(self.source), str(self.private),
                   str(self.workspace), str(self.control), str(witness))
        completed = subprocess.run(
            namespace.build_native_argv(self.context, command, self.source),
            cwd=self.workspace, env=self.context.environment, capture_output=True,
            timeout=20, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))
        result = json.loads(completed.stdout)
        self.assertIs(result["same_file"], True)
        self.assertEqual(result["child_value"], "child")
        self.assertEqual(result["network_namespace"], os.stat("/proc/self/ns/net").st_ino)
        self.assertEqual((self.private / "parent-write").read_text(), "parent")
        self.assertEqual((self.workspace / "workspace-write").read_text(), "workspace")
        self.assertEqual((self.control / "control-write").read_text(), "control")
        self.assertEqual(witness.read_text(), "host-witness")
        self.assertEqual({p.name: p.read_bytes() for p in self.source.iterdir()}, source_before)

    def test_kernel_literal_runtime_paths_inside_source_home_execute_but_cannot_be_mutated(self):
        self.require_namespace_kernel()
        executable, sibling = self.native_runtime()
        original = {str(path): path.read_bytes() for path in (executable, sibling)}
        program = r'''
import json, os, pathlib, subprocess, sys
executable, sibling, source, private = map(pathlib.Path, sys.argv[1:])
readonly = []
for path in (executable, sibling):
    try:
        path.write_text('must-not-mutate-runtime')
    except OSError:
        readonly.append(True)
    else:
        readonly.append(False)
child = subprocess.check_output([str(sibling)], text=True).strip()
print(json.dumps({'readonly': readonly, 'child': child,
    'same_home': os.path.samefile(source, private),
    'config': (source / 'config.toml').read_text()}))
'''
        command = (str(executable), "-c", program, str(executable), str(sibling), str(self.source), str(self.private))
        completed = subprocess.run(
            namespace.build_native_argv(self.context, command, self.source),
            cwd=self.workspace, env=self.context.environment, capture_output=True, timeout=20, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))
        result = json.loads(completed.stdout)
        self.assertEqual(result["readonly"], [True, True])
        self.assertEqual(result["child"], "code-mode-fixture")
        self.assertIs(result["same_home"], True)
        self.assertEqual(result["config"], "private-trial-input\n")
        self.assertEqual({str(path): path.read_bytes() for path in (executable, sibling)}, original)


if __name__ == "__main__":
    unittest.main()
