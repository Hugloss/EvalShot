from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

from .probe_contract import validate_probe_contract
from .quality_debt import QualityDebtError, baseline_document, quality_debt_audit


def _fake_ruff(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import json, os, sys, time
if '--version' in sys.argv:
    print('ruff 9.9.9'); raise SystemExit(0)
mode=os.environ.get('AE_RUFF_MODE','base')
if mode == 'timeout': time.sleep(2)
if os.path.exists(os.path.join(os.getcwd(),'pyproject.toml')) and '--isolated' not in sys.argv:
    mode='ambient_config'
if mode == 'badjson': print('{'); raise SystemExit(1)
root=os.getcwd()
limit=6 if mode == 'limit6' else 5
value=12 if mode == 'ambient_config' else (9 if mode != 'reduced' else 7)
rows=[{'code':'C901','filename':os.path.join(root,'src','a.py'),'location':{'row':1},'message':f'complexity ({value} > {limit})'}]
if mode == 'detailed':
    rows.append({'code':'PLR0912','filename':os.path.join(root,'src','a.py'),'location':{'row':1},'message':'branches (11 > 8)'})
if mode == 'new':
    rows.append({'code':'C901','filename':os.path.join(root,'src','b.py'),'location':{'row':1},'message':f'complexity (8 > {limit})'})
if mode == 'excluded':
    rows.append({'code':'C901','filename':os.path.join(root,'src','excluded','legacy.py'),'location':{'row':1},'message':f'complexity (99 > {limit})'})
print(json.dumps(rows)); raise SystemExit(1)
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp).resolve()
        (root / "src").mkdir()
        (root / "src/a.py").write_text("def a():\n    pass\n", encoding="utf-8")
        (root / "src/b.py").write_text("def b():\n    pass\n", encoding="utf-8")
        (root / "src/excluded").mkdir()
        (root / "src/excluded/legacy.py").write_text("x = 1\n" * 50, encoding="utf-8")
        (root / "tools").mkdir()
        (root / "tools/large.py").write_text("x = 1\n" * 50, encoding="utf-8")
        fake = root / "ruff"
        _fake_ruff(fake)
        old_path = os.environ["PATH"]
        os.environ["PATH"] = str(root) + os.pathsep + old_path
        try:
            base = quality_debt_audit(
                repository_root=root, roots=("src",), limits={"C901": 5}
            )
            assert validate_probe_contract(base) == []
            assert base["derived"]["summary"]["excess"] == 4
            (root / "pyproject.toml").write_text(
                "[tool.ruff.lint.mccabe]\nmax-complexity=99\n", encoding="utf-8"
            )
            ambient_changed = quality_debt_audit(
                repository_root=root, roots=("src",), limits={"C901": 5}
            )
            assert ambient_changed["derived"]["summary"]["excess"] == 4
            assert (
                "--isolated" in ambient_changed["evidence"]["analyzer"]["analysis_argv"]
            )
            assert (
                ambient_changed["evidence"]["comparable_values"][
                    "analyzer_configuration_mode"
                ]
                == "isolated_explicit"
            )
            assert (
                "lint.mccabe.max-complexity = 5"
                in ambient_changed["evidence"]["analyzer"]["analysis_argv"]
            )
            try:
                quality_debt_audit(
                    repository_root=root, roots=("src",), limits={"UNKNOWN": 5}
                )
            except QualityDebtError:
                pass
            else:
                raise AssertionError("unsupported Ruff debt rules must fail closed")

            assert base["evidence"]["detailed_findings"] == [
                {
                    "path": "src/a.py",
                    "line": 1,
                    "rule": "C901",
                    "observed": 9,
                    "limit": 5,
                    "excess": 4,
                }
            ]
            assert base["candidates"][0]["facts"] == {
                "locations": 1,
                "rule_findings": 1,
                "excess": 4,
            }
            assert base["candidates"][0]["required_next_evidence"] == [
                {
                    "kind": "test_focus",
                    "target": "src/a.py",
                    "reason": (
                        "quality-debt magnitude does not establish edit safety; "
                        "recover confirmed/supporting test ownership and affected "
                        "verification before selecting this target for an edit"
                    ),
                }
            ]
            assert base["candidates"][0]["verification_suggestions"] == []
            os.environ["AE_RUFF_MODE"] = "detailed"
            detailed = quality_debt_audit(
                repository_root=root,
                roots=("src",),
                limits={"PLR0912": 8, "C901": 5},
            )
            assert detailed["evidence"]["detailed_findings"] == [
                {
                    "path": "src/a.py",
                    "line": 1,
                    "rule": "C901",
                    "observed": 9,
                    "limit": 5,
                    "excess": 4,
                },
                {
                    "path": "src/a.py",
                    "line": 1,
                    "rule": "PLR0912",
                    "observed": 11,
                    "limit": 8,
                    "excess": 3,
                },
            ]
            assert detailed["derived"]["summary"]["files"]["src/a.py"] == {
                "locations": 1,
                "rule_findings": 2,
                "excess": 7,
            }
            os.environ.pop("AE_RUFF_MODE", None)
            baseline = root / "baseline.json"
            baseline.write_text(json.dumps(baseline_document(base)), encoding="utf-8")
            same = quality_debt_audit(
                repository_root=root,
                roots=("src",),
                limits={"C901": 5},
                baseline_path=baseline,
            )
            assert same["derived"]["baseline_comparison"]["state"] == "UNCHANGED"
            os.environ["AE_RUFF_MODE"] = "new"
            grown = quality_debt_audit(
                repository_root=root,
                roots=("src",),
                limits={"C901": 5},
                baseline_path=baseline,
            )
            assert grown["derived"]["baseline_comparison"]["state"] == "INCREASED"
            assert (
                grown["derived"]["baseline_comparison"]["files"]["src/b.py"]["state"]
                == "NEW"
            )
            os.environ["AE_RUFF_MODE"] = "reduced"
            reduced = quality_debt_audit(
                repository_root=root,
                roots=("src",),
                limits={"C901": 5},
                baseline_path=baseline,
            )
            assert reduced["derived"]["baseline_comparison"]["state"] == "REDUCED"
            os.environ["AE_RUFF_MODE"] = "excluded"
            scoped = quality_debt_audit(
                repository_root=root,
                roots=("src", "tools"),
                limits={"C901": 5},
                excludes=("src/excluded",),
                max_file_lines=10,
                file_line_roots=("src",),
            )
            assert "src/excluded/legacy.py" not in scoped["derived"]["summary"]["files"]
            assert scoped["derived"]["summary"]["oversized_files"] == {}
            assert scoped["economics"]["files_read"] == 3
            assert scoped["evidence"]["source_universe"] == {
                "configured_roots": ["src", "tools"],
                "root_file_counts": {"src": 2, "tools": 1},
                "excludes": ["src/excluded"],
                "excluded_python_files_by_rule": {"src/excluded": 1},
                "analyzed_python_files": 3,
                "diagnostics": [],
            }
            assert scoped["evidence"]["analyzer"]["resolved_executable"] == str(fake)
            analyzer_evidence = scoped["evidence"]["analyzer"]
            assert analyzer_evidence["version_argv"] == [str(fake), "--version"]
            assert analyzer_evidence["analysis_argv"] == [
                str(fake),
                "check",
                "src",
                "tools",
                "--preview",
                "--select",
                "C901",
                "--isolated",
                "--config",
                "lint.per-file-ignores = {}",
                "--config",
                "lint.mccabe.max-complexity = 5",
                "--exclude",
                "src/excluded",
                "--output-format",
                "json",
            ]
            assert analyzer_evidence["working_directory"] == "."
            absolute_alias = str((root / "src").resolve())
            for bad_roots in (
                ("src", "src"),
                ("src", "./src"),
                ("src", absolute_alias),
                ("src", "src/excluded"),
            ):
                try:
                    quality_debt_audit(
                        repository_root=root, roots=bad_roots, limits={"C901": 5}
                    )
                except QualityDebtError:
                    pass
                else:
                    raise AssertionError(
                        f"ambiguous roots must fail closed: {bad_roots}"
                    )
            for bad_excludes in (("src/excluded", "./src/excluded"),):
                try:
                    quality_debt_audit(
                        repository_root=root,
                        roots=("src",),
                        limits={"C901": 5},
                        excludes=bad_excludes,
                    )
                except QualityDebtError:
                    pass
                else:
                    raise AssertionError(
                        f"duplicate normalized excludes must fail closed: {bad_excludes}"
                    )
            try:
                quality_debt_audit(
                    repository_root=root,
                    roots=("src",),
                    limits={"C901": 5},
                    excludes=("../outside",),
                )
            except QualityDebtError:
                pass
            else:
                raise AssertionError("escaping exclude must fail closed")
            try:
                quality_debt_audit(
                    repository_root=root,
                    roots=("src",),
                    limits={"C901": 5},
                    file_line_roots=("../outside",),
                    max_file_lines=10,
                )
            except QualityDebtError:
                pass
            else:
                raise AssertionError("escaping file-line root must fail closed")
            swallowed = quality_debt_audit(
                repository_root=root,
                roots=("src/excluded",),
                limits={"C901": 5},
                excludes=("src/excluded",),
            )
            assert swallowed["evidence"]["source_universe"]["diagnostics"] == [
                {
                    "code": "configured_root_fully_excluded",
                    "root": "src/excluded",
                    "message": "configured root is fully excluded: src/excluded",
                }
            ]
            assert swallowed["economics"]["files_read"] == 0
            os.environ["AE_RUFF_MODE"] = "limit6"
            changed_limit = quality_debt_audit(
                repository_root=root,
                roots=("src",),
                limits={"C901": 6},
                baseline_path=baseline,
            )
            assert (
                changed_limit["derived"]["baseline_comparison"]["state"]
                == "INCOMPARABLE_BASELINE"
            )
            assert changed_limit["derived"]["baseline_comparison"][
                "changed_fields"
            ] == ["limits"]
            assert (
                baseline_document(base)["comparable_values"]
                == base["evidence"]["comparable_values"]
            )
            assert "timeout_seconds" not in baseline_document(base)["comparable_values"]
            assert (
                "analyzer_executable"
                not in baseline_document(base)["comparable_values"]
            )
            assert base["evidence"]["analyzer"]["resolved_executable"] == str(fake)

            os.environ.pop("AE_RUFF_MODE", None)
            pre_isolation = baseline_document(base)
            pre_isolation_values = dict(pre_isolation["comparable_values"])
            pre_isolation_values.pop("analyzer_configuration_mode")
            from .quality_debt import configuration_identity

            pre_isolation["comparable_values"] = pre_isolation_values
            pre_isolation["comparable_identity"] = configuration_identity(
                pre_isolation_values
            )
            pre_isolation_path = root / "pre-isolation-baseline.json"
            pre_isolation_path.write_text(json.dumps(pre_isolation), encoding="utf-8")
            pre_isolation_result = quality_debt_audit(
                repository_root=root,
                roots=("src",),
                limits={"C901": 5},
                baseline_path=pre_isolation_path,
            )
            assert (
                pre_isolation_result["derived"]["baseline_comparison"]["state"]
                == "INCOMPARABLE_BASELINE"
            )
            assert (
                pre_isolation_result["derived"]["baseline_comparison"]["reason"]
                == "analyzer_configuration_mode_unavailable"
            )

            portable_baseline = baseline_document(base)
            relocated_values = dict(portable_baseline["comparable_values"])
            relocated_values["analyzer_executable"] = "/different/checkout/bin/ruff"
            portable_baseline["comparable_values"] = relocated_values
            portable_baseline["comparable_identity"] = configuration_identity(
                relocated_values
            )
            relocated = root / "relocated-baseline.json"
            relocated.write_text(json.dumps(portable_baseline), encoding="utf-8")
            os.environ.pop("AE_RUFF_MODE", None)
            relocated_result = quality_debt_audit(
                repository_root=root,
                roots=("src",),
                limits={"C901": 5},
                baseline_path=relocated,
            )
            assert (
                relocated_result["derived"]["baseline_comparison"]["state"]
                == "UNCHANGED"
            )

            legacy = baseline_document(base)
            legacy["comparable_identity"] = "sha256:legacy-incompatible"
            legacy.pop("comparable_values", None)
            legacy_path = root / "legacy-baseline.json"
            legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
            legacy_result = quality_debt_audit(
                repository_root=root,
                roots=("src",),
                limits={"C901": 5},
                baseline_path=legacy_path,
            )
            assert (
                legacy_result["derived"]["baseline_comparison"]["state"]
                == "INCOMPARABLE_BASELINE"
            )
            assert (
                legacy_result["derived"]["baseline_comparison"]["reason"]
                == "comparable_values_unavailable"
            )
            assert (
                legacy_result["derived"]["baseline_comparison"]["changed_fields"]
                is None
            )
            os.environ["AE_RUFF_MODE"] = "badjson"
            try:
                quality_debt_audit(
                    repository_root=root, roots=("src",), limits={"C901": 5}
                )
            except QualityDebtError:
                pass
            else:
                raise AssertionError("bad analyzer JSON must fail closed")
            os.environ["AE_RUFF_MODE"] = "timeout"
            try:
                quality_debt_audit(
                    repository_root=root,
                    roots=("src",),
                    limits={"C901": 5},
                    timeout_seconds=0.05,
                )
            except QualityDebtError:
                pass
            else:
                raise AssertionError("analyzer timeout must fail closed")
        finally:
            os.environ.pop("AE_RUFF_MODE", None)
            os.environ["PATH"] = old_path
    print(
        json.dumps(
            {"status": "PASS", "cases": 32, "tool": "quality-debt"}, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
