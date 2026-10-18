from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .context_focus import (
    ContextBudget,
    ContextFocusError,
    ScanBudget,
    context_focus_audit,
)
from .probe_contract import validate_probe_contract
from .refactor_focus_discovery import DiscoveryConfig, discover_repository_roots


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "agent@example.invalid")
    _git(root, "config", "user.name", "Agent Economics Qualification")
    _write(root / ".gitignore", "ignored/\n")
    _write(
        root / "src/payments/retry_policy.py",
        "def should_retry_payment(error):\n"
        '    """Retry idempotent payment failures."""\n'
        "    return error.transient\n",
    )
    _write(
        root / "web/checkout.ts",
        "export function submitPayment() {\n  return retryCheckout();\n}\n",
    )
    _write(
        root / "docs/reliability.md",
        "# Payment reliability\nRetries must preserve idempotency.\n",
    )
    _write(root / "src/security/opaque.rs", "pub fn gate(v: bool) -> bool { v }\n")
    _write(root / "src/other.py", "def unrelated():\n    return 1\n")
    _write(root / "ignored/ignored.py", "payment retry idempotency\n")
    _git(root, "add", ".gitignore", "src", "web", "docs")
    _git(root, "commit", "-qm", "fixture")
    _write(
        root / "src/payments/untracked_guard.py",
        "def payment_retry_guard():\n    return True\n",
    )


def _run(
    root: Path,
    *,
    task: str = "payment retry idempotency",
    context_budget: ContextBudget | None = None,
    scan_budget: ScanBudget | None = None,
    discovery_config: DiscoveryConfig | None = None,
    intelligence: Path | None = None,
) -> dict[str, object]:
    return context_focus_audit(
        task=task,
        repository_root=root,
        context_budget=context_budget
        or ContextBudget(
            max_files=4, max_lines=200, max_bytes=50_000, max_tokens=10_000
        ),
        scan_budget=scan_budget
        or ScanBudget(
            max_files=100,
            max_bytes=500_000,
            max_file_bytes=100_000,
            max_anchors_per_file=4,
        ),
        discovery_config=discovery_config or DiscoveryConfig(mode="git"),
        repository_intelligence_path=intelligence,
    )


def qualify(artifact_path: Path | None = None) -> dict[str, object]:
    failures: list[str] = []
    observations: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="context-focus-p6-") as tmp:
        root = Path(tmp) / "repo"
        root.mkdir()
        _repo(root)

        baseline = _run(root)
        contract_errors = validate_probe_contract(baseline)
        if contract_errors:
            failures.append(f"baseline common contract invalid: {contract_errors}")
        candidates = baseline.get("candidates", [])
        targets = [item.get("target") for item in candidates if isinstance(item, dict)]
        if not targets or targets[0] != "src/payments/retry_policy.py":
            failures.append(f"expected retry_policy.py first, got {targets}")
        if "web/checkout.ts" not in targets:
            failures.append("TypeScript file was not discovered/ranked")
        if "docs/reliability.md" not in targets:
            failures.append("Markdown evidence was not discovered/ranked")
        if "ignored/ignored.py" in targets:
            failures.append("Git-ignored file entered default context")
        if "src/payments/untracked_guard.py" not in targets:
            failures.append("non-ignored untracked file was not eligible by default")

        econ = baseline.get("economics", {})
        if not isinstance(econ, dict):
            failures.append("economics missing")
            econ = {}
        if int(econ.get("selected_files", 0)) > 4:
            failures.append("selected file budget exceeded")
        if int(econ.get("selected_lines", 0)) > 200:
            failures.append("selected line budget exceeded")
        if int(econ.get("selected_bytes", 0)) > 50_000:
            failures.append("selected byte budget exceeded")
        if int(econ.get("selected_estimated_tokens", 0)) > 10_000:
            failures.append("selected token budget exceeded")
        discovery = econ.get("discovery", {})
        if not isinstance(discovery, dict) or discovery.get("backend") != "git":
            failures.append("context-focus did not use Git discovery")
        if isinstance(discovery, dict) and "tracked_candidates" not in discovery:
            failures.append("generic discovery metrics missing")

        tight = _run(
            root,
            context_budget=ContextBudget(
                max_files=1, max_lines=200, max_bytes=50_000, max_tokens=10_000
            ),
        )
        tight_candidates = tight.get("candidates", [])
        if not isinstance(tight_candidates, list) or len(tight_candidates) != 1:
            failures.append(
                "max_files=1 did not produce exactly one selected candidate"
            )
        deferred = tight.get("deferred_evidence", [])
        if not isinstance(deferred, list) or not any(
            isinstance(item, dict) and "context max_files" in str(item.get("reason"))
            for item in deferred
        ):
            failures.append("context file-budget deferral was not explicit")

        scan_limited = _run(
            root,
            scan_budget=ScanBudget(
                max_files=1,
                max_bytes=500_000,
                max_file_bytes=100_000,
                max_anchors_per_file=4,
            ),
        )
        uncertainty = scan_limited.get("uncertainty", [])
        if not isinstance(uncertainty, list) or not any(
            isinstance(item, dict)
            and item.get("code") == "repository_not_fully_content_scanned"
            for item in uncertainty
        ):
            failures.append("scan truncation did not publish uncertainty")
        scan_econ = scan_limited.get("economics", {})
        if not isinstance(scan_econ, dict) or scan_econ.get("files_read") != 1:
            failures.append("scan max_files bound was not enforced exactly")

        intelligence = root.parent / "context-intelligence.json"
        intelligence.write_text(
            json.dumps(
                {
                    "version": 1,
                    "provider": "hashmarks-test-adapter",
                    "files": [
                        {
                            "path": "src/security/opaque.rs",
                            "score": 95,
                            "reasons": [
                                "ownership/impact evidence for credential lease renewal"
                            ],
                            "symbols": ["gate"],
                        },
                        {
                            "path": "src/stale_missing.py",
                            "score": 100,
                            "reasons": ["stale fixture"],
                        },
                    ],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        external = _run(
            root, task="credential lease renewal", intelligence=intelligence
        )
        ext_targets = [
            item.get("target")
            for item in external.get("candidates", [])
            if isinstance(item, dict)
        ]
        if not ext_targets or ext_targets[0] != "src/security/opaque.rs":
            failures.append(
                f"external intelligence did not rescue vocabulary mismatch: {ext_targets}"
            )
        warnings = external.get("warnings", [])
        if not isinstance(warnings, list) or not any(
            isinstance(item, dict)
            and item.get("code") == "stale_external_intelligence_paths"
            for item in warnings
        ):
            failures.append("stale external intelligence path did not produce warning")
        for candidate in external.get("candidates", []):
            if isinstance(candidate, dict):
                evidence = candidate.get("evidence", {})
                if isinstance(evidence, dict):
                    for anchor in evidence.get("anchors", []):
                        if isinstance(anchor, dict) and "preview" in anchor:
                            failures.append(
                                "source preview leaked into context-focus artifact"
                            )

        tiny_intelligence_limit_failed = False
        try:
            context_focus_audit(
                task="credential lease renewal",
                repository_root=root,
                repository_intelligence_path=intelligence,
                repository_intelligence_max_bytes=1,
                discovery_config=DiscoveryConfig(mode="git"),
            )
        except ContextFocusError:
            tiny_intelligence_limit_failed = True
        if not tiny_intelligence_limit_failed:
            failures.append("repository intelligence byte bound did not fail closed")

        interpretation = external.get("interpretation", {})
        if (
            not isinstance(interpretation, dict)
            or interpretation.get(
                "external_intelligence_is_ranking_support_not_repository_authority"
            )
            is not True
        ):
            failures.append("external intelligence authority boundary missing")

        inside_intelligence = root / "context-intelligence.json"
        inside_intelligence.write_text(
            json.dumps(
                {
                    "version": 1,
                    "provider": "self-exclusion-test",
                    "files": [
                        {
                            "path": "src/security/opaque.rs",
                            "score": 95,
                            "reasons": ["credential lease renewal"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        inside = _run(
            root, task="credential lease renewal", intelligence=inside_intelligence
        )
        inside_targets = [
            item.get("target")
            for item in inside.get("candidates", [])
            if isinstance(item, dict)
        ]
        if "context-intelligence.json" in inside_targets:
            failures.append(
                "repository intelligence auxiliary file became a context candidate"
            )
        inside_econ = inside.get("economics", {})
        if (
            not isinstance(inside_econ, dict)
            or int(inside_econ.get("auxiliary_inputs_excluded_from_context", 0)) < 1
        ):
            failures.append("auxiliary context-input exclusion was not measured")
        inside_intelligence.unlink()

        tracked_only = _run(
            root,
            discovery_config=DiscoveryConfig(mode="git", untracked_policy="exclude"),
        )
        base_repo = baseline.get("repository", {})
        tracked_repo = tracked_only.get("repository", {})
        if not isinstance(base_repo, dict) or not isinstance(tracked_repo, dict):
            failures.append("repository identities missing")
        elif base_repo.get("identity") == tracked_repo.get("identity"):
            failures.append(
                "discovery path-set identity did not change when untracked input was excluded"
            )
        base_cfg = baseline.get("configuration", {})
        tracked_cfg = tracked_only.get("configuration", {})
        if not isinstance(base_cfg, dict) or not isinstance(tracked_cfg, dict):
            failures.append("configuration identities missing")
        elif base_cfg.get("identity") == tracked_cfg.get("identity"):
            failures.append(
                "discovery policy did not invalidate configuration identity"
            )

        task_changed = _run(root, task="checkout payment")
        task_repo = task_changed.get("repository", {})
        task_cfg = task_changed.get("configuration", {})
        if isinstance(base_repo, dict) and isinstance(task_repo, dict):
            if base_repo.get("identity") != task_repo.get("identity"):
                failures.append(
                    "repository path-set identity incorrectly depends on task text"
                )
        if isinstance(base_cfg, dict) and isinstance(task_cfg, dict):
            if base_cfg.get("identity") == task_cfg.get("identity"):
                failures.append("task text did not invalidate configuration identity")

        copy = Path(tmp) / "repo-copy"
        shutil.copytree(root, copy, symlinks=True)
        copied = _run(copy)
        copied_repo = copied.get("repository", {})
        if isinstance(base_repo, dict) and isinstance(copied_repo, dict):
            if base_repo.get("identity") != copied_repo.get("identity"):
                failures.append("repository identity depends on checkout location")

        # Prove generic P5 discovery directly rather than only through context-focus.
        generic = discover_repository_roots(
            roots={"repo": root},
            repository_root=root,
            config=DiscoveryConfig(mode="git"),
            suffixes=(".py", ".ts", ".md", ".rs"),
        )
        generic_paths = {
            path.absolute().relative_to(root).as_posix()
            for path in generic.files_for("repo")
        }
        for expected in {
            "src/payments/retry_policy.py",
            "web/checkout.ts",
            "docs/reliability.md",
            "src/security/opaque.rs",
        }:
            if expected not in generic_paths:
                failures.append(f"generic discovery missed {expected}")

        no_match = _run(root, task="zzzzzz_unmatched_concept")
        if no_match.get("candidates"):
            failures.append("unmatched task unexpectedly selected lexical candidates")
        no_match_uncertainty = no_match.get("uncertainty", [])
        if not isinstance(no_match_uncertainty, list) or not any(
            isinstance(item, dict)
            and item.get("code") == "no_context_candidate_selected"
            for item in no_match_uncertainty
        ):
            failures.append("no-match case did not publish uncertainty")

        try:
            _run(root, task="x", context_budget=ContextBudget(max_files=0))
        except ContextFocusError:
            pass
        else:
            failures.append("invalid context budget did not fail closed")

        observations = {
            "baseline_targets": targets,
            "baseline_repository_identity": base_repo.get("identity")
            if isinstance(base_repo, dict)
            else None,
            "baseline_configuration_identity": base_cfg.get("identity")
            if isinstance(base_cfg, dict)
            else None,
            "baseline_files_read": econ.get("files_read"),
            "baseline_bytes_read": econ.get("bytes_read"),
            "tight_selected_count": len(tight_candidates)
            if isinstance(tight_candidates, list)
            else None,
            "scan_limited_files_read": scan_econ.get("files_read")
            if isinstance(scan_econ, dict)
            else None,
            "external_targets": ext_targets,
            "generic_discovered_count": len(generic_paths),
            "contract_errors": contract_errors,
        }

    result: dict[str, object] = {
        "probe": "context-focus",
        "qualification_phase": 6,
        "passed": not failures,
        "observations": observations,
        "failures": failures,
    }
    if artifact_path is not None:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Qualify Phase 6 context-focus.")
    parser.add_argument(
        "--artifact-path",
        type=Path,
        default=Path(".agent-artifacts/context-focus-p6-qualification.json"),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    result = qualify(args.artifact_path)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
