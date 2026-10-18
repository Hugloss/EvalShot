from __future__ import annotations

import argparse
import copy
import json
import tempfile
from pathlib import Path

from .probe_contract import (
    COMMON_TOP_LEVEL_KEYS,
    SCHEMA_NAME,
    SCHEMA_VERSION,
    validate_probe_contract,
)
from .refactor_focus_p3_qualification import materialize_p3_corpus
from .refactor_focus_workflow import refactor_focus_audit


def _run_probe(
    root: Path,
    *,
    hints: Path | None,
    top_n: int,
    artifact_name: str,
) -> dict[str, object]:
    artifact = root / artifact_name
    exit_codes: list[int] = []

    def emit(_level: str, _event: str, **_payload: object) -> None:
        return None

    def exit_code(code: int) -> None:
        exit_codes.append(code)

    refactor_focus_audit(
        emit=emit,
        exit_code=exit_code,
        source_root=root / "src/samplepkg",
        tests_root=root / "tests",
        repository_root=root,
        artifact_path=artifact,
        package_name="samplepkg",
        tests_package_name="checks",
        file_line_threshold=20,
        function_line_threshold=10,
        top_n=top_n,
        transitive_max_depth=2,
        helper_max_depth=2,
        pytest_max_depth=2,
        ownership_hints_path=hints,
    )
    if exit_codes != [0]:
        raise RuntimeError(f"unexpected probe exit codes: {exit_codes!r}")
    return json.loads(artifact.read_text(encoding="utf-8"))


def _assert_portable_paths(
    payload: dict[str, object], root: Path, failures: list[str]
) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    root_text = root.resolve().as_posix()
    if root_text in rendered:
        failures.append("contract leaks absolute repository root")

    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list):
        return
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            continue
        target = candidate.get("target")
        if isinstance(target, str) and Path(target).is_absolute():
            failures.append(f"candidate {index} target is absolute")
        evidence = candidate.get("evidence", {})
        if not isinstance(evidence, dict):
            continue
        for authority in ("confirmed", "supporting", "candidate"):
            matches = evidence.get(authority, [])
            if not isinstance(matches, list):
                continue
            for match in matches:
                if not isinstance(match, dict):
                    continue
                test_path = match.get("test_path")
                if isinstance(test_path, str) and Path(test_path).is_absolute():
                    failures.append(
                        f"candidate {index} {authority} evidence contains absolute test path"
                    )


def qualify(artifact_path: Path | None = None) -> dict[str, object]:
    failures: list[str] = []
    observations: dict[str, object] = {}

    with (
        tempfile.TemporaryDirectory(prefix="agent-economics-contract-a-") as tmp_a,
        tempfile.TemporaryDirectory(prefix="agent-economics-contract-b-") as tmp_b,
    ):
        root_a = Path(tmp_a)
        root_b = Path(tmp_b)
        hints_a = materialize_p3_corpus(root_a)
        hints_b = materialize_p3_corpus(root_b)

        bounded_a = _run_probe(
            root_a, hints=hints_a, top_n=3, artifact_name="bounded-a.json"
        )
        bounded_a_other_output = _run_probe(
            root_a,
            hints=hints_a,
            top_n=3,
            artifact_name="bounded-a-other-output.json",
        )
        bounded_b = _run_probe(
            root_b, hints=hints_b, top_n=3, artifact_name="bounded-b.json"
        )
        wider_a = _run_probe(
            root_a, hints=hints_a, top_n=4, artifact_name="wider-a.json"
        )
        all_a = _run_probe(root_a, hints=hints_a, top_n=100, artifact_name="all-a.json")

        contract_errors = validate_probe_contract(bounded_a)
        failures.extend(f"contract: {error}" for error in contract_errors)

        negative_validator_cases: dict[str, bool] = {}
        bad_schema = copy.deepcopy(bounded_a)
        assert isinstance(bad_schema["schema"], dict)
        bad_schema["schema"]["version"] = "999"
        negative_validator_cases["schema_version"] = bool(
            validate_probe_contract(bad_schema)
        )

        bad_config = copy.deepcopy(bounded_a)
        assert isinstance(bad_config["configuration"], dict)
        assert isinstance(bad_config["configuration"]["values"], dict)
        bad_config["configuration"]["values"]["top_n"] = 999
        negative_validator_cases["configuration_identity"] = bool(
            validate_probe_contract(bad_config)
        )

        bad_facts = copy.deepcopy(bounded_a)
        assert isinstance(bad_facts["candidates"], list) and bad_facts["candidates"]
        assert isinstance(bad_facts["candidates"][0], dict)
        assert isinstance(bad_facts["candidates"][0]["facts"], dict)
        bad_facts["candidates"][0]["facts"]["risk_score"] = 99
        negative_validator_cases["fact_recommendation_separation"] = bool(
            validate_probe_contract(bad_facts)
        )

        bad_root = copy.deepcopy(bounded_a)
        assert isinstance(bad_root["repository"], dict)
        bad_root["repository"]["root"] = "/tmp/not-portable"
        negative_validator_cases["portable_repository_root"] = bool(
            validate_probe_contract(bad_root)
        )

        for name, detected in negative_validator_cases.items():
            if not detected:
                failures.append(f"validator failed to reject malformed case: {name}")

        schema = bounded_a.get("schema", {})
        if (
            not isinstance(schema, dict)
            or schema.get("name") != SCHEMA_NAME
            or schema.get("version") != SCHEMA_VERSION
        ):
            failures.append("versioned schema identity missing or incorrect")

        missing = [key for key in COMMON_TOP_LEVEL_KEYS if key not in bounded_a]
        if missing:
            failures.append(f"common contract fields missing: {missing}")

        repo_a = bounded_a.get("repository", {})
        repo_b = bounded_b.get("repository", {})
        config_a = bounded_a.get("configuration", {})
        config_b = bounded_b.get("configuration", {})
        config_output = bounded_a_other_output.get("configuration", {})
        config_wider = wider_a.get("configuration", {})
        if not all(
            isinstance(item, dict)
            for item in (
                repo_a,
                repo_b,
                config_a,
                config_b,
                config_output,
                config_wider,
            )
        ):
            failures.append("repository/configuration blocks missing")
        else:
            if repo_a.get("identity") != repo_b.get("identity"):
                failures.append("repository identity depends on checkout location")
            if config_a.get("identity") != config_b.get("identity"):
                failures.append("configuration identity depends on checkout location")
            if config_a.get("identity") != config_output.get("identity"):
                failures.append(
                    "configuration identity depends on artifact destination"
                )
            if config_a.get("identity") == config_wider.get("identity"):
                failures.append(
                    "configuration identity did not change when top_n changed"
                )

        changed_target = root_b / "src/samplepkg/fixture_target.py"
        changed_target.write_text(
            changed_target.read_text(encoding="utf-8") + "# identity change\n",
            encoding="utf-8",
        )
        changed_b = _run_probe(
            root_b, hints=hints_b, top_n=3, artifact_name="changed-b.json"
        )
        changed_repo = changed_b.get("repository", {})
        if not isinstance(changed_repo, dict) or changed_repo.get(
            "identity"
        ) == repo_a.get("identity"):
            failures.append(
                "repository identity did not change after analyzed source bytes changed"
            )

        _assert_portable_paths(bounded_a, root_a, failures)

        candidates = bounded_a.get("candidates", [])
        derived = bounded_a.get("derived", {})
        deferred = bounded_a.get("deferred_evidence", [])
        if (
            not isinstance(candidates, list)
            or not isinstance(derived, dict)
            or not isinstance(deferred, list)
        ):
            failures.append("candidate/derived/deferred sections malformed")
            candidates = []
            derived = {}
            deferred = []

        oversized = int(derived.get("oversized_source_count", 0))
        selected = int(derived.get("selected_count", 0))
        if len(deferred) != max(0, oversized - selected):
            failures.append(
                "deferred evidence does not account for bounded-out candidates"
            )

        recommendation_keys = {
            "test_action",
            "strategy",
            "recommended_test_action",
            "recommended_strategy",
            "risk_score",
        }
        confirmed_candidate_seen = False
        uncertain_candidate_seen = False
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                continue
            facts = candidate.get("facts", {})
            recommendations = candidate.get("recommendations", {})
            interpretation = candidate.get("interpretation", {})
            evidence = candidate.get("evidence", {})
            uncertainty = candidate.get("uncertainty", [])
            required = candidate.get("required_next_evidence", [])
            verification = candidate.get("verification_suggestions", [])
            if not all(
                isinstance(item, dict)
                for item in (facts, recommendations, interpretation, evidence)
            ):
                failures.append(
                    f"candidate {index} fact/interpretation/recommendation separation malformed"
                )
                continue
            if recommendation_keys & set(facts):
                failures.append(
                    f"candidate {index} facts contain recommendation/interpretation fields"
                )
            if set(recommendations) != {"test_action", "strategy"}:
                failures.append(
                    f"candidate {index} recommendations have unexpected shape"
                )
            if (
                interpretation.get("size_complexity_is_investigation_signal_only")
                is not True
            ):
                failures.append(
                    f"candidate {index} did not mark size/complexity as investigation-only"
                )
            if (
                interpretation.get("decomposition_requires_locality_evidence")
                is not True
            ):
                failures.append(
                    f"candidate {index} did not require locality evidence before decomposition"
                )
            confirmed = evidence.get("confirmed", [])
            if isinstance(confirmed, list) and confirmed:
                confirmed_candidate_seen = True
                if not isinstance(verification, list) or not verification:
                    failures.append(
                        f"candidate {index} confirmed ownership has no verification suggestion"
                    )
            elif isinstance(uncertainty, list) and uncertainty:
                uncertain_candidate_seen = True
                if not isinstance(required, list) or not required:
                    failures.append(
                        f"candidate {index} uncertainty has no required next evidence"
                    )

        if not confirmed_candidate_seen:
            failures.append("qualification did not exercise a confirmed candidate")
        if not uncertain_candidate_seen:
            all_candidates = all_a.get("candidates", [])
            if isinstance(all_candidates, list):
                for candidate in all_candidates:
                    if not isinstance(candidate, dict):
                        continue
                    candidate_uncertainty = candidate.get("uncertainty", [])
                    candidate_required = candidate.get("required_next_evidence", [])
                    if (
                        isinstance(candidate_uncertainty, list)
                        and candidate_uncertainty
                    ):
                        uncertain_candidate_seen = True
                        if (
                            not isinstance(candidate_required, list)
                            or not candidate_required
                        ):
                            failures.append(
                                "uncertain candidate has no required next evidence"
                            )
                        break
        if not uncertain_candidate_seen:
            failures.append("qualification did not exercise an uncertain candidate")

        evidence = bounded_a.get("evidence", {})
        if not isinstance(evidence, dict) or not isinstance(
            evidence.get("records"), list
        ):
            failures.append("top-level evidence records missing")
        verification = bounded_a.get("verification_suggestions", [])
        required = bounded_a.get("required_next_evidence", [])
        uncertainty = bounded_a.get("uncertainty", [])
        warnings = bounded_a.get("warnings", [])
        if (
            not isinstance(verification, list)
            or not isinstance(required, list)
            or not isinstance(uncertainty, list)
            or not isinstance(warnings, list)
        ):
            failures.append("common decision-support lists malformed")

        config_values = config_a.get("values", {}) if isinstance(config_a, dict) else {}
        if isinstance(config_values, dict):
            expected_hints_path = hints_a.relative_to(root_a).as_posix()
            if config_values.get("ownership_hints_path") != expected_hints_path:
                failures.append(
                    "ownership hints path is not repository-relative in configuration"
                )
            hint_identity = config_values.get("ownership_hints_sha256")
            if not isinstance(hint_identity, str) or len(hint_identity) != 64:
                failures.append(
                    "ownership hints content identity missing from configuration"
                )

        observations = {
            "schema": bounded_a.get("schema"),
            "tool": bounded_a.get("tool"),
            "repository_identity": repo_a.get("identity")
            if isinstance(repo_a, dict)
            else None,
            "configuration_identity": config_a.get("identity")
            if isinstance(config_a, dict)
            else None,
            "selected_count": selected,
            "deferred_count": len(deferred),
            "uncertainty_count": len(uncertainty)
            if isinstance(uncertainty, list)
            else None,
            "required_next_evidence_count": len(required)
            if isinstance(required, list)
            else None,
            "verification_suggestion_count": len(verification)
            if isinstance(verification, list)
            else None,
            "contract_errors": contract_errors,
            "negative_validator_cases": negative_validator_cases,
            "uncertain_candidate_exercised": uncertain_candidate_seen,
        }

    result: dict[str, object] = {
        "probe": "agent-economics-probe-contract",
        "qualification_phase": 4,
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
    parser = argparse.ArgumentParser(
        description="Qualify the common Agent Economics Probe v1 contract."
    )
    parser.add_argument(
        "--artifact-path",
        type=Path,
        default=Path(
            ".agent-artifacts/agent-economics-probe-contract-qualification.json"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    result = qualify(args.artifact_path)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
