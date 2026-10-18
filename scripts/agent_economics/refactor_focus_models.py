from collections.abc import Callable
from typing import Literal, TypedDict

Emit = Callable[..., None]
ExitCode = Callable[[int], None]
EvidenceAuthority = Literal["confirmed", "supporting", "candidate"]


class MatchRecord(TypedDict):
    test_path: str
    test_lines: int
    match_type: str
    evidence_authority: EvidenceAuthority
    provenance: str


class FocusRow(TypedDict):
    source_path: str
    source_lines: int
    matches: list[MatchRecord]
    confirmed_matches: list[MatchRecord]
    supporting_matches: list[MatchRecord]
    candidate_matches: list[MatchRecord]
    corresponding_test_count: int
    supporting_test_count: int
    candidate_test_count: int
    has_corresponding_tests: bool
    correspondence_status: str
    test_sync_required_if_split: bool
    max_test_lines: int
    oversized_test_count: int
    dependent_source_count: int
    imports_out_count: int
    function_over_limit_count: int
    largest_function_lines: int
    recommended_test_action: str
    recommended_strategy: str


MATCH_AUTHORITY: dict[str, EvidenceAuthority] = {
    # Explicit repository declarations and concrete load/use paths may establish
    # a source/test relationship.
    "declared_owner": "confirmed",
    "import_exact": "confirmed",
    "inherited_method_call": "confirmed",
    "dynamic_import_literal": "confirmed",
    "support_loader": "confirmed",
    "conftest_fixture": "confirmed",
    "pytest_fixture": "confirmed",
    "pytest_plugin_fixture": "confirmed",
    "pytest_plugin": "confirmed",
    # Conventions and inferred source-dependency paths are useful, but require
    # inspection before they become ownership authority.
    "transitive_owner": "supporting",
    "mirrored_path": "supporting",
    "direct_name": "supporting",
    # Path-token overlap is discovery only.
    "feature_fallback": "candidate",
}

# Prefer stronger evidence when more than one matcher finds the same test path.
MATCH_PRIORITY = {
    "declared_owner": 0,
    "import_exact": 1,
    "inherited_method_call": 2,
    "dynamic_import_literal": 3,
    "conftest_fixture": 3,
    "pytest_fixture": 4,
    "pytest_plugin_fixture": 5,
    "pytest_plugin": 6,
    "support_loader": 7,
    "transitive_owner": 8,
    "mirrored_path": 9,
    "direct_name": 10,
    "feature_fallback": 11,
}

MIN_FEATURE_TOKEN_OVERLAP = 2
