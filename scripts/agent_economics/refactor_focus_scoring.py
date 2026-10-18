import ast
from pathlib import Path

from .refactor_focus_analysis import AnalysisCache
from .refactor_focus_models import MatchRecord


def function_size_summary(
    path: Path,
    *,
    max_function_lines: int,
    analysis_cache: AnalysisCache,
) -> tuple[int, int]:
    tree = analysis_cache.get(path).tree
    if tree is None:
        return 0, 0

    over_limit_count = 0
    largest = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end_lineno = getattr(node, "end_lineno", node.lineno)
        line_count = max(1, end_lineno - node.lineno + 1)
        largest = max(largest, line_count)
        if line_count > max_function_lines:
            over_limit_count += 1
    return over_limit_count, largest


def matches_with_authority(
    matches: list[MatchRecord],
    authority: str,
) -> list[MatchRecord]:
    return [match for match in matches if match["evidence_authority"] == authority]


def confirmed_matches_for(matches: list[MatchRecord]) -> list[MatchRecord]:
    return matches_with_authority(matches, "confirmed")


def supporting_matches_for(matches: list[MatchRecord]) -> list[MatchRecord]:
    return matches_with_authority(matches, "supporting")


def candidate_matches_for(matches: list[MatchRecord]) -> list[MatchRecord]:
    return matches_with_authority(matches, "candidate")


def correspondence_status_for(matches: list[MatchRecord]) -> str:
    if confirmed_matches_for(matches):
        return "confirmed"
    if supporting_matches_for(matches):
        return "supported"
    if candidate_matches_for(matches):
        return "ambiguous"
    return "missing"


def recommended_test_action_for(
    *,
    correspondence_status: str,
    corresponding_test_count: int,
    max_test_lines: int,
    file_line_threshold: int,
) -> str:
    if correspondence_status == "missing":
        return "manual_review_required"
    if correspondence_status in {"supported", "ambiguous"}:
        return "verify_test_correspondence_first"
    if max_test_lines > file_line_threshold:
        return "inspect_test_locality_before_split"
    if corresponding_test_count == 1:
        return "update_existing_test_file"
    return "keep_existing_test_file"


def recommended_strategy_for(
    *,
    source_lines: int,
    dependent_source_count: int,
    imports_out_count: int,
    correspondence_status: str,
    max_test_lines: int,
    file_line_threshold: int,
) -> str:
    # Size, dependency counts, and branch pressure may select an investigation
    # target, but they do not establish that decomposition is an improvement.
    # Keep the signature stable because callers already expose these facts.
    _ = (
        source_lines,
        dependent_source_count,
        imports_out_count,
        max_test_lines,
        file_line_threshold,
    )
    if correspondence_status in {"missing", "supported", "ambiguous"}:
        return "recover_test_correspondence_first"
    return "measure_refactor_locality_before_decomposition"
