"""Shared stdlib-only shape authority for provisional blinded review returns.

Copied verbatim into the kit. No oracle approval, semantic truth or reviewer
independence is implied by validating this envelope.
"""
from __future__ import annotations
import re
from typing import Any, Mapping

RETURNS_SCHEMA = "evalshot.hashmarks-external-first-pass-returns.v1"
_DECISIONS = frozenset({"unique", "ambiguous", "invalid", "unable_to_review"})
_SYMBOL = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\\Z")


class ReturnError(ValueError):
    pass


def _safe_file(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\\\" in value or ":" in value:
        return False
    if any(c in "*?[]" or ord(c) < 32 or ord(c) == 127 for c in value):
        return False
    parts = value.split("/")
    return not value.startswith("/") and all(p not in ("", ".", "..") for p in parts)


def _required_string(value: object, *, max_length: int = 2000) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= max_length


def _locations(locations: object) -> None:
    if not isinstance(locations, list) or len(locations) > 20:
        raise ReturnError("invalid candidate-location list")
    seen: set[tuple[str, str]] = set()
    for location in locations:
        if (
            not isinstance(location, dict)
            or set(location) != {"path", "symbol"}
            or not _safe_file(location["path"])
            or len(location["path"]) > 1024
            or not isinstance(location["symbol"], str)
            or not _SYMBOL.fullmatch(location["symbol"])
        ):
            raise ReturnError("invalid candidate source location")
        key = (location["path"], location["symbol"])
        if key in seen:
            raise ReturnError("duplicate candidate source location")
        seen.add(key)


def validate_returns(packet: Mapping[str, Any], returns: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    questions = packet.get("packets")
    if not isinstance(questions, list) or len(questions) != 2 or any(
        not isinstance(row, dict) or not isinstance(row.get("packet_id"), str)
        for row in questions
    ):
        raise ReturnError("blinded question population differs from preregistration")
    indexed = {row["packet_id"]: row for row in questions}
    if len(indexed) != 2:
        raise ReturnError("duplicate blinded question identity")
    if (not isinstance(returns, Mapping)
        or set(returns) != {"schema", "blinded_packet_identity", "reviews"}
        or returns.get("schema") != RETURNS_SCHEMA
        or not isinstance(returns.get("reviews"), list)
        or len(returns["reviews"]) > len(questions)):
        raise ReturnError("review returns do not match the exact blinded packet")
    incoming: dict[str, dict[str, Any]] = {}
    for review in returns["reviews"]:
        if not isinstance(review, dict) or set(review) != {
            "packet_id", "task_digest", "reviewer_id", "source_commit", "source_tree",
            "decision", "prior_expected_answer_access", "source_inspected",
            "candidate_locations", "reason", "source_evidence",
        }:
            raise ReturnError("malformed review return envelope")
        packet_id = review["packet_id"]
        question = indexed.get(packet_id) if isinstance(packet_id, str) else None
        if (question is None or packet_id in incoming
            or review["task_digest"] != question["task_digest"]
            or review["source_commit"] != question["repository"]["commit"]
            or review["source_tree"] != question["repository"]["tree"]
            or not _required_string(review["reviewer_id"], max_length=128)
            or type(review["prior_expected_answer_access"]) is not bool
            or type(review["source_inspected"]) is not bool
            or not isinstance(review["decision"], str)
            or review["decision"] not in _DECISIONS
            or not _required_string(review["reason"])):
            raise ReturnError("review return identity, provenance, or decision invalid")
        _locations(review["candidate_locations"])
        evidence = review["source_evidence"]
        if not isinstance(evidence, list) or len(evidence) > 40:
            raise ReturnError("invalid review source evidence")
        for fact in evidence:
            if (not isinstance(fact, dict)
                or set(fact) != {"path", "line", "observation"}
                or not _safe_file(fact["path"])
                or type(fact["line"]) is not int
                or fact["line"] < 1
                or not _required_string(fact["observation"])):
                raise ReturnError("malformed source evidence observation")
        decision = review["decision"]
        locations = review["candidate_locations"]
        if decision == "unique" and len(locations) != 1:
            raise ReturnError("unique claim needs one declared candidate")
        if decision == "ambiguous" and len(locations) < 2:
            raise ReturnError("ambiguous claim needs at least two candidates")
        if decision == "unable_to_review" and (
            review["source_inspected"] is True or locations or evidence
        ):
            raise ReturnError("unable-to-review claim contradicts inspected evidence")
        if decision != "unable_to_review" and (
            review["source_inspected"] is not True or not evidence
        ):
            raise ReturnError("semantic claim lacks declared pinned-source inspection")
        incoming[packet_id] = review
    return incoming
