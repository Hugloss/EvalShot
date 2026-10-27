# Opt-in finding-quality evidence (v1)

The canonical task-bound independent review gate in `benchmarks.harness.oracle_reviews` accepts an optional `finding_quality` object on a task's oracle-review row. Omitting it preserves the existing `agents-cookbook-oracle-reviews.v1` review behavior. This first checkpoint validates **record shape only**; the recorded claims remain reviewer-supplied observations, **not independent semantic truth or a quality score**.

The exact v1 fields are `schema` (`evalshot-finding-quality.v1`), `verdict` (`FINDING`, `CLEAN`, `LEAVE ALONE`, `INSUFFICIENT EVIDENCE`), and non-empty string `requirement`, `trigger`, `causal_owner`, `supported_path`, `consequence`, `evidence_qualifier`, and `unresolved_scope`. `source_references` is a list of exact `{path, line, quote}` records, with positive integer physical source lines. `FINDING` requires at least one cited source reference; incomplete verdicts may preserve an empty list. Unknown fields and malformed types are rejected with `OracleReviewError`.

Neither source references nor the reviewer's verdict are independently verified by this metadata check. The pre-existing task oracle and independent reviews remain the only governing approval checks. Later work must freeze gold from Hashmarks source snapshots (HUG-145), establish blinded adjudication (HUG-161), validate cited bytes/semantics independently (HUG-143), and calibrate any score (HUG-142), **before** making effectiveness claims. Do not backfill a scored result from a structurally valid record.

Run the focused model-free contract regression with:

```sh
python -m unittest benchmarks.tests.test_finding_quality_contract
```

This changes no existing trial status, score calculation, execution, runtime, or canonical source-intelligence owner.
