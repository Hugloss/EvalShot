from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)


def qualify() -> None:
    source = Path(__file__).resolve().parents[2]
    bootstrap = source / "scripts/bootstrap-agent-economics.sh"
    with tempfile.TemporaryDirectory(prefix="agent-economics-bootstrap-") as raw:
        root = Path(raw)
        remote = root / "remote.git"
        work = root / "work"
        _run("git", "init", "--bare", str(remote))
        _run("git", "clone", str(remote), str(work))
        _run("git", "config", "user.email", "qualification@example.invalid", cwd=work)
        _run("git", "config", "user.name", "qualification", cwd=work)
        package = work / "scripts/agent_economics"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "__main__.py").write_text(
            "print('portable-agent-economics')\n", encoding="utf-8"
        )
        _run("git", "add", ".", cwd=work)
        _run("git", "commit", "-m", "fixture", cwd=work)
        _run("git", "push", "origin", "HEAD:main", cwd=work)
        revision = _run("git", "rev-parse", "HEAD", cwd=work).stdout.strip()

        destination = root / "consumer" / ".agent-economics"
        destination.parent.mkdir()
        result = _run(
            "sh",
            str(bootstrap),
            "--repository",
            str(remote),
            "--revision",
            revision,
            "--destination",
            str(destination),
        )
        assert result.returncode == 0, result.stderr
        assert f"AGENT_ECONOMICS_REVISION={revision}" in result.stdout
        assert f"AGENT_ECONOMICS_PYTHONPATH={destination}/scripts" in result.stdout
        resolved = _run(
            "git", "-C", str(destination), "rev-parse", "HEAD"
        ).stdout.strip()
        assert resolved == revision

        imported = _run(
            "python",
            "-c",
            "import sys; sys.path.insert(0, r'"
            + str(destination / "scripts")
            + "'); import agent_economics; print(agent_economics.__name__)",
        )
        assert imported.returncode == 0, imported.stderr
        assert imported.stdout.strip() == "agent_economics"

        duplicate = _run(
            "sh",
            str(bootstrap),
            "--repository",
            str(remote),
            "--revision",
            revision,
            "--destination",
            str(destination),
        )
        assert duplicate.returncode != 0
        assert "destination already exists" in duplicate.stderr

        short_exact = _run(
            "sh",
            str(bootstrap),
            "--repository",
            str(remote),
            "--revision",
            revision[:12],
            "--destination",
            str(root / "short-exact"),
        )
        assert short_exact.returncode == 2
        assert "full 40-character commit id" in short_exact.stderr
        assert not (root / "short-exact").exists()

        short_relaxed = _run(
            "sh",
            str(bootstrap),
            "--repository",
            str(remote),
            "--revision",
            revision[:12],
            "--allow-short-revision",
            "--destination",
            str(root / "short-relaxed"),
        )
        assert short_relaxed.returncode == 0, short_relaxed.stderr
        relaxed_resolved = _run(
            "git", "-C", str(root / "short-relaxed"), "rev-parse", "HEAD"
        ).stdout.strip()
        assert relaxed_resolved == revision

        missing = root / "missing"
        bad = _run(
            "sh",
            str(bootstrap),
            "--repository",
            str(remote),
            "--revision",
            "deadbeef",
            "--destination",
            str(missing),
        )
        assert bad.returncode != 0
        assert not missing.exists()

    print('{"cases":6,"status":"PASS","tool":"agent-economics-bootstrap"}')


if __name__ == "__main__":
    qualify()
