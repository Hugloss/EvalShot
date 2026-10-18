from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .bounded_process import ProcessLimits, process_tree_capability, run_bounded
from .command_runner import run_named_command
from .loop_state import LoopBudget, LoopSession


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def main() -> None:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="agent-economics-stress-") as temp:
        root = Path(temp).resolve()
        _git(root, "init", "-q")
        _git(root, "config", "user.email", "stress@example.invalid")
        _git(root, "config", "user.name", "Stress")
        _write(root / "tracked.txt", "base\n")
        _git(root, "add", ".")
        _git(root, "commit", "-qm", "base")
        py = sys.executable.replace("\\", "\\\\")
        manifest = root / "agent-economics.toml"
        _write(
            manifest,
            f'''version = 1
[commands.pass]
argv = ["{py}", "-c", "print('ok')"]
stage = "focused"

[commands.stderr_flood]
argv = ["{py}", "-c", "import sys; sys.stderr.buffer.write(b'x'*4000000)"]
stage = "component"

[commands.stdout_flood]
argv = ["{py}", "-c", "import sys; sys.stdout.buffer.write(b'x'*4000000)"]
stage = "component"

[commands.nonutf8]
argv = ["{py}", "-c", "import sys; sys.stdout.buffer.write(bytes(range(256))*100)"]
stage = "component"
''',
        )
        # Repeat stable commands enough to expose FD/thread/state leakage without turning CI into a benchmark.
        identities = set()
        for _ in range(40):
            result = run_named_command(
                repository_root=root,
                manifest_path=manifest,
                name="pass",
                max_stdout_bytes=4096,
                max_stderr_bytes=4096,
            )
            assert result["status"] == "PASS"
            identities.add(result["failure_identity"])
        assert len(identities) == 1

        for name in ("stdout_flood", "stderr_flood"):
            result = run_named_command(
                repository_root=root,
                manifest_path=manifest,
                name=name,
                max_stdout_bytes=1024,
                max_stderr_bytes=1024,
                timeout_seconds=10,
            )
            assert result["classification"] == "output_limit_exceeded"
            assert result["execution"]["stdout_bytes"] <= 1024
            assert result["execution"]["stderr_bytes"] <= 1024

        nonutf8 = run_named_command(
            repository_root=root,
            manifest_path=manifest,
            name="nonutf8",
            max_stdout_bytes=100000,
            max_stderr_bytes=4096,
        )
        assert "\ufffd" in nonutf8["stdout"]

        # Large loop-state history stays bounded by the declared iteration budget.
        state = root / ".agent-artifacts/stress-loop.json"
        with LoopSession(
            state,
            budget=LoopBudget(
                max_iterations=128, max_commands=128, max_wall_seconds=120
            ),
            repository_root=root,
        ) as session:
            for i in range(127):
                current = session.record(
                    source_identity=f"s{i}",
                    command_identity="c",
                    failure_identity=f"f{i}",
                    evidence_identity=f"e{i}",
                    stdout_bytes=1,
                    stderr_bytes=1,
                )
                assert current["stop_reason"] is None
            final = session.record(
                source_identity="s127",
                command_identity="c",
                failure_identity="f127",
                evidence_identity="e127",
                stdout_bytes=1,
                stderr_bytes=1,
            )
            assert final["stop_reason"] == "ITERATION_BUDGET_EXHAUSTED"

        # Repeated timeout/tree termination catches lingering-child regressions.
        if process_tree_capability()["available"] and os.name != "nt":
            for i in range(5):
                pidfile = root / f"child-{i}.pid"
                code = (
                    "import subprocess,sys,time,pathlib;"
                    "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)']);"
                    f"pathlib.Path({str(pidfile)!r}).write_text(str(p.pid));time.sleep(20)"
                )
                result = run_bounded(
                    repository_root=root,
                    argv=(sys.executable, "-c", code),
                    limits=ProcessLimits(0.25, 4096, 4096),
                )
                assert result.timed_out
                if pidfile.exists():
                    pid = int(pidfile.read_text())
                    time.sleep(0.05)
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        pass
                    else:
                        stat = Path(f"/proc/{pid}/stat")
                        assert stat.exists() and ") Z " in stat.read_text(
                            errors="replace"
                        )

    print(
        json.dumps(
            {
                "status": "PASS",
                "cases": {
                    "stable_repeats": 40,
                    "floods": 2,
                    "loop_iterations": 128,
                    "tree_timeouts": 5,
                },
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
