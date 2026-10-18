"""Configuration transport regressions; no model inference or sandbox claim."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from benchmarks.adapters import opencode_native as native


@unittest.skipIf(os.name == "nt", "Native OpenCode adapter uses POSIX processes")
class NativeConfigurationOwnershipTests(unittest.TestCase):
    def test_skill_launch_configuration_matrix(self) -> None:
        root = Path(__file__).resolve().parents[2]
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required by the OpenCode runtime tests")
        result = subprocess.run(
            [str(node), "--test", "scripts/test-evaluator-config-ownership.js"],
            cwd=root, capture_output=True, text=True, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_native_pruning_and_explicit_config_reach_child_unchanged(self) -> None:
        self.assertIsNotNone(shutil.which("node"), "Node is required by the native adapter")
        with tempfile.TemporaryDirectory(prefix="evalshot-native-config-") as tmp:
            root = Path(tmp).resolve()
            workspace, control = root / "workspace", root / "control"
            workspace.mkdir()
            scratch = control / "_environment" / "tmp"
            scratch.mkdir(parents=True)
            # Process launcher fixture: not a containment backend. The production
            # command is executed after '--' so real child environment is observed.
            launcher = root / "launcher"
            launcher.write_text(
                '#!/bin/sh\nwhile [ "$#" -gt 0 ] && [ "$1" != "--" ]; do shift; done\n'
                '[ "$#" -gt 0 ] || exit 2\nshift\nexec "$@"\n', encoding="utf-8",
            )
            launcher.chmod(0o755)
            probe = root / "probe.js"
            probe.write_text(
                'console.log(JSON.stringify({prune:process.env.OPENCODE_DISABLE_PRUNE??null,'
                'config:process.env.OPENCODE_CONFIG??null,'
                'dir:process.env.OPENCODE_CONFIG_DIR??null,'
                'inline:process.env.OPENCODE_CONFIG_CONTENT??null,'
                'cwd:process.cwd(),home:process.env.HOME}));\n', encoding="utf-8",
            )
            config_file, config_dir = root / "operator.jsonc", root / "operator-config"
            config_dir.mkdir()
            config_bytes = b'// operator-owned\n{"model":"local/org/model"}\n'
            config_file.write_bytes(config_bytes)
            inline = json.dumps({"compaction": {"auto": False, "reserved": 0}})
            for value in (None, "0", "1"):
                with self.subTest(pruning=value):
                    env = {
                        "BENCHMARK_NATIVE_HOME": str(root / "operator-home"),
                        "BENCHMARK_NATIVE_XDG_CONFIG_HOME": str(root / "xdg"),
                        "BENCHMARK_OPENCODE_AGENT": "build",
                        "PATH": os.environ.get("PATH", ""),
                        "TMPDIR": str(scratch), "TMP": str(scratch), "TEMP": str(scratch),
                        "XDG_CACHE_HOME": str(control / "cache"),
                        "XDG_DATA_HOME": str(control / "data"),
                        "XDG_STATE_HOME": str(control / "state"),
                        "OPENCODE_CONFIG": str(config_file),
                        "OPENCODE_CONFIG_DIR": str(config_dir),
                        "OPENCODE_CONFIG_CONTENT": inline,
                        "BENCHMARK_PASSTHROUGH_ENV_KEYS":
                            "OPENCODE_CONFIG,OPENCODE_CONFIG_DIR,OPENCODE_CONFIG_CONTENT,OPENCODE_DISABLE_PRUNE,PATH",
                    }
                    if value is not None:
                        env["OPENCODE_DISABLE_PRUNE"] = value
                    context = SimpleNamespace(workspace=workspace, control_root=control, environment=env)
                    with patch.object(native, "resolve_native_executable", return_value=str(launcher)), \
                         patch.object(native, "_RUNTIME_SCRIPT", probe):
                        prepared = native._native_environment(context)
                        if value is None:
                            self.assertNotIn("OPENCODE_DISABLE_PRUNE", prepared)
                        else:
                            self.assertEqual(prepared["OPENCODE_DISABLE_PRUNE"], value)
                        observed, result = native._runtime_call(
                            context, args=(), environment=prepared,
                            timeout_seconds=15, max_stdout_bytes=100_000,
                        )
                    self.assertEqual(result.return_code, 0, result.stderr)
                    self.assertIsNotNone(observed)
                    self.assertEqual(observed["prune"], value)
                    self.assertEqual(observed["config"], str(config_file))
                    self.assertEqual(observed["dir"], str(config_dir))
                    self.assertEqual(observed["inline"], inline)
                    self.assertEqual(observed["cwd"], str(workspace))
                    self.assertEqual(observed["home"], env["BENCHMARK_NATIVE_HOME"])
                    self.assertEqual(config_file.read_bytes(), config_bytes)


if __name__ == "__main__":
    unittest.main()
