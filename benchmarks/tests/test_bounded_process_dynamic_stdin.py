"""A native protocol may bind later requests to an earlier response's identity."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from scripts.agent_economics.bounded_process import BoundedProcessError, ProcessLimits, run_bounded


class DynamicStdinTests(unittest.TestCase):
    def test_later_request_uses_the_identity_returned_by_the_same_process(self):
        script = """
import json, secrets, sys
assert sys.stdin.readline() == 'start\\n'
identity = secrets.token_hex(8)
print(json.dumps({'identity': identity}), flush=True)
request = json.loads(sys.stdin.readline())
assert request == {'identity': identity, 'operation': 'status'}
print(json.dumps({'status': 'ready'}), flush=True)
assert sys.stdin.read() == ''
"""
        seen = []

        def next_request(stdout):
            prior = json.loads(stdout.splitlines()[0])
            seen.append(prior["identity"])
            return (json.dumps({"identity": prior["identity"], "operation": "status"}) + "\n").encode()

        with tempfile.TemporaryDirectory() as temporary:
            result = run_bounded(
                repository_root=Path(temporary), argv=(sys.executable, "-c", script),
                environment={}, inherit_environment=False,
                stdin_stages=(
                    (b"start\n", lambda raw: b"\n" in raw),
                    (next_request, lambda raw: b'"status": "ready"' in raw),
                ),
            )
        self.assertEqual(result.return_code, 0)
        self.assertFalse(result.timed_out)
        self.assertEqual(len(seen), 1)
        self.assertEqual(json.loads(result.stdout.splitlines()[0])["identity"], seen[0])

    def test_invalid_dynamic_payload_terminates_the_waiting_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            witness = root / "finished"
            script = (
                "import pathlib, sys; print('ready', flush=True); "
                "request=sys.stdin.readline(); "
                "sys.exit(0) if not request else pathlib.Path(sys.argv[1]).write_text('unexpected')"
            )
            with self.assertRaisesRegex(BoundedProcessError, "factory must return bytes"):
                run_bounded(
                    repository_root=root, argv=(sys.executable, "-c", script, str(witness)),
                    stdin_stages=((b"", lambda raw: b"ready" in raw), (lambda raw: "invalid", lambda raw: False)),
                    limits=ProcessLimits(timeout_seconds=2),
                )
            self.assertFalse(witness.exists())

    def test_timed_out_response_does_not_invoke_a_later_request_factory(self):
        calls = []
        with tempfile.TemporaryDirectory() as temporary:
            result = run_bounded(
                repository_root=Path(temporary), argv=(sys.executable, "-c", "import sys; sys.stdin.read()"),
                stdin_stages=(
                    (b"unanswered\n", lambda raw: False),
                    (lambda raw: calls.append(raw) or b"must-not-send\n", lambda raw: True),
                ),
                limits=ProcessLimits(timeout_seconds=0.2),
            )
        self.assertTrue(result.timed_out)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
