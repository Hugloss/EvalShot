from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from unittest import mock
from pathlib import Path

from .bounded_process import ProcessLimits, process_tree_capability, run_bounded
from .capabilities import capabilities
from .command_manifest import CommandManifestError, load_command_manifest
from .command_runner import classify_result, run_named_command
from .hotspot_focus import hotspot_focus_audit
from .local_qualify import qualify_local
from .loop_state import LoopBudget, LoopSession
from .refactor_focus_discovery import (
    DiscoveryConfig,
    DiscoveryError,
    discover_python_roots,
)


def _write(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args], check=True, stdout=subprocess.DEVNULL
    )


def _expect(exc_type, fn) -> None:
    try:
        fn()
    except exc_type:
        return
    raise AssertionError(f"expected {exc_type.__name__}")


def _manifest(root: Path) -> Path:
    py = sys.executable.replace("\\", "\\\\")
    path = root / "agent-economics.toml"
    _write(
        path,
        f'''version = 1
[commands.pass]
argv = ["{py}", "-c", "print('ok')"]
stage = "focused"

[commands.importfail]
argv = ["{py}", "-c", "raise ImportError('missing')"]
stage = "affected"

[commands.syntaxfail]
argv = ["{py}", "-c", "compile('x =', 'bad.py', 'exec')"]
stage = "component"

[commands.lintfail]
argv = ["{py}", "-c", "import sys; print('ruff lint failure'); sys.exit(1)"]
stage = "component"

[commands.typefail]
argv = ["{py}", "-c", "import sys; print('mypy incompatible type'); sys.exit(1)"]
stage = "component"

[commands.depfail]
argv = ["{py}", "-c", "import sys; print('dependency not installed'); sys.exit(1)"]
stage = "component"

[commands.stderr]
argv = ["{py}", "-c", "import sys; sys.stderr.write('x'*10000)"]
stage = "component"

[commands.nonutf8]
argv = ["{py}", "-c", "import sys; sys.stdout.buffer.write(bytes([255,254,253]))"]
stage = "component"

[commands.mutate]
argv = ["{py}", "-c", "open('tracked.txt','w').write('changed')"]
stage = "component"
must_not_modify_tracked_files = true

[commands.allowed]
argv = ["{py}", "-c", "open('tracked.txt','w').write('allowed')"]
stage = "component"
must_not_modify_tracked_files = true
allowed_mutation_paths = ["tracked.txt"]

[commands.broadfail]
argv = ["{py}", "-c", "import sys; print('failure'); sys.exit(1)"]
stage = "repository"
''',
    )
    return path


def _loop_budget_cases(root: Path) -> None:
    cases = [
        ("iterations", LoopBudget(max_iterations=1), dict()),
        ("commands", LoopBudget(max_commands=1), dict()),
        ("stdout", LoopBudget(max_stdout_bytes=1), {"stdout_bytes": 1}),
        ("stderr", LoopBudget(max_stderr_bytes=1), {"stderr_bytes": 1}),
        ("wall", LoopBudget(max_wall_seconds=0.000001), {"elapsed_ms": 1}),
        ("files", LoopBudget(max_evidence_files=1), {"evidence_files": 1}),
        ("lines", LoopBudget(max_evidence_lines=1), {"evidence_lines": 1}),
        ("tokens", LoopBudget(max_context_tokens=1), {"context_tokens": 1}),
    ]
    for name, budget, extra in cases:
        path = root / f".agent-artifacts/{name}.json"
        with LoopSession(path, budget=budget, repository_root=root) as session:
            state = session.record(
                source_identity="s",
                command_identity="c",
                failure_identity=name,
                evidence_identity="e",
                stdout_bytes=extra.get("stdout_bytes", 0),
                stderr_bytes=extra.get("stderr_bytes", 0),
                elapsed_ms=extra.get("elapsed_ms", 0),
                evidence_files=extra.get("evidence_files", 0),
                evidence_lines=extra.get("evidence_lines", 0),
                context_tokens=extra.get("context_tokens", 0),
            )
            assert str(state["stop_reason"]).endswith("BUDGET_EXHAUSTED")


def _process_tree_case(root: Path) -> None:
    if not process_tree_capability()["available"]:
        return
    pidfile = root / "child.pid"
    code = (
        "import subprocess,sys,time,pathlib;"
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']);"
        f"pathlib.Path({str(pidfile)!r}).write_text(str(p.pid));"
        "time.sleep(30)"
    )
    result = run_bounded(
        repository_root=root,
        argv=(sys.executable, "-c", code),
        limits=ProcessLimits(0.3, 4096, 4096),
    )
    assert result.timed_out
    for _ in range(30):
        if pidfile.exists():
            break
        time.sleep(0.01)
    if pidfile.exists() and os.name != "nt":
        pid = int(pidfile.read_text())
        time.sleep(0.05)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        else:
            # A killed child may briefly remain as a zombie; /proc state Z is not alive work.
            stat = Path(f"/proc/{pid}/stat")
            if stat.exists():
                assert ") Z " in stat.read_text(errors="replace")


def _hotspot_identity_case(root: Path) -> None:
    src, tests = root / "src/pkg", root / "tests"
    _write(src / "__init__.py", "")
    _write(src / "a.py", "def f(x):\n    return 1 if x else 0\n")
    _write(tests / "test_a.py", "from pkg.a import f\ndef test_f(): assert f(1)==1\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture")
    first = hotspot_focus_audit(
        repository_root=root,
        source_root=src,
        tests_root=tests,
        package_name="pkg",
        history_policy="required",
        discovery_mode="git",
    )
    first_id = first["repository"]["identity"]
    _write(tests / "test_a.py", "from pkg.a import f\ndef test_f(): assert f(0)==0\n")
    second = hotspot_focus_audit(
        repository_root=root,
        source_root=src,
        tests_root=tests,
        package_name="pkg",
        history_policy="required",
        discovery_mode="git",
    )
    assert second["repository"]["identity"] != first_id
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "test change")
    third = hotspot_focus_audit(
        repository_root=root,
        source_root=src,
        tests_root=tests,
        package_name="pkg",
        history_policy="required",
        discovery_mode="git",
    )
    assert third["repository"]["identity"] != second["repository"]["identity"]


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp).resolve()
        _git(root, "init", "-q")
        _git(root, "config", "user.email", "qualification@example.invalid")
        _git(root, "config", "user.name", "Qualification")
        _write(root / "tracked.txt", "before\n")
        _git(root, "add", "tracked.txt")
        _git(root, "commit", "-qm", "base")
        manifest = _manifest(root)

        # Strict schema and type handling.
        bad = root / "bad.toml"
        _write(bad, 'version=1\nunknown=true\n[commands.x]\nargv=["x"]\n')
        _expect(CommandManifestError, lambda: load_command_manifest(bad))
        _write(
            bad, 'version=1\n[commands.x]\nargv=["x"]\nappend_selected_tests="yes"\n'
        )
        _expect(CommandManifestError, lambda: load_command_manifest(bad))

        # Semantic identities exclude timestamps and checkout paths.
        pass_one = run_named_command(
            repository_root=root, manifest_path=manifest, name="pass"
        )
        time.sleep(0.01)
        pass_two = run_named_command(
            repository_root=root, manifest_path=manifest, name="pass"
        )
        assert pass_one["failure_identity"] == pass_two["failure_identity"]
        assert pass_one["command"]["identity"] == pass_two["command"]["identity"]

        # Failure-class corpus.
        expected = {
            "importfail": "collection_import_failure",
            "syntaxfail": "syntax_compile_failure",
            "lintfail": "lint_static_failure",
            "typefail": "type_check_failure",
            "depfail": "dependency_environment_missing",
        }
        for name, classification in expected.items():
            assert (
                run_named_command(
                    repository_root=root, manifest_path=manifest, name=name
                )["classification"]
                == classification
            )
        assert (
            classify_result(
                return_code=1,
                timed_out=False,
                executable_missing=False,
                stdout="",
                stderr="",
                policy_violation=False,
            )
            == "unknown_failure"
        )

        stderr = run_named_command(
            repository_root=root,
            manifest_path=manifest,
            name="stderr",
            max_stderr_bytes=32,
        )
        assert stderr["classification"] == "output_limit_exceeded"
        nonutf8 = run_named_command(
            repository_root=root, manifest_path=manifest, name="nonutf8"
        )
        assert "\ufffd" in nonutf8["stdout"]

        mutated = run_named_command(
            repository_root=root, manifest_path=manifest, name="mutate"
        )
        assert mutated["classification"] == "policy_mutation_violation"
        _write(root / "tracked.txt", "before\n")
        allowed = run_named_command(
            repository_root=root, manifest_path=manifest, name="allowed"
        )
        assert allowed["classification"] == "pass"
        _write(root / "tracked.txt", "before\n")

        # Focused pass cannot claim repository qualification; broad failure remains visible.
        local = qualify_local(
            repository_root=root,
            manifest_path=manifest,
            command_names=["pass", "broadfail"],
            state_path=Path(".agent-artifacts/staged.json"),
        )
        assert local["status"] == "LOCAL_FAILED"
        assert (
            local["stages"][0]["status"] == "PASS"
            and local["stages"][1]["status"] == "FAIL"
        )
        assert local["ci_status"] == "NOT_RUN"
        assert local["economics"]["commands"] == 2
        assert local["economics"]["by_stage"]["focused"]["commands"] == 1
        assert local["economics"]["by_stage"]["repository"]["commands"] == 1

        # Workspace absence and Git capability honesty.
        missing_root = root / "does-not-exist"
        caps = capabilities(repository_root=missing_root)
        assert {"code": "repository_bytes_unavailable"} in caps["warnings"]
        caps = capabilities(repository_root=root, manifest_path=manifest)
        assert caps["capabilities"]["git"]["worktree"] is True
        with mock.patch(
            "scripts.agent_economics.capabilities.shutil.which", return_value=None
        ):
            no_git = capabilities(repository_root=root)
        assert no_git["capabilities"]["git"]["available"] is False
        assert caps["capabilities"]["network_isolation"]["available"] is False

        # P5 must fail while streaming, not after unbounded buffering.
        for i in range(30):
            _write(root / f"src/p{i:03}.py", "x=1\n")
        _git(root, "add", "src")
        _expect(
            DiscoveryError,
            lambda: discover_python_roots(
                roots={"source": root / "src"},
                repository_root=root,
                config=DiscoveryConfig(mode="git", git_max_stdout_bytes=32),
            ),
        )

        _loop_budget_cases(root)

        # Bounded A -> B -> A oscillation is distinct from immediate repetition.
        osc_path = root / ".agent-artifacts/oscillation.json"
        with LoopSession(osc_path, repository_root=root) as session:
            session.record(
                source_identity="A",
                command_identity="c",
                failure_identity="fa",
                evidence_identity="ea",
                stdout_bytes=0,
                stderr_bytes=0,
            )
            session.record(
                source_identity="B",
                command_identity="c",
                failure_identity="fb",
                evidence_identity="eb",
                stdout_bytes=0,
                stderr_bytes=0,
            )
            oscillated = session.record(
                source_identity="A",
                command_identity="c",
                failure_identity="fa",
                evidence_identity="ec",
                stdout_bytes=0,
                stderr_bytes=0,
            )
            assert oscillated["stop_reason"] == "OSCILLATION"

        _process_tree_case(root)

    # Identity case needs a clean repository so history changes are controlled.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp).resolve()
        _git(root, "init", "-q")
        _git(root, "config", "user.email", "qualification@example.invalid")
        _git(root, "config", "user.name", "Qualification")
        _hotspot_identity_case(root)

    print(
        json.dumps(
            {
                "status": "PASS",
                "cases": 29,
                "authority": "pre-pr-adversarial-regression-only",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
