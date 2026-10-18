# Refactor Focus P2 Qualification

Phase 2 closes parse-once economics for the portable `refactor-focus` probe.

## Result

**PASS**

The adversarial corpus contains 25 Python files and 11 oversized source candidates. The bounded `top_n=3` run selected 3 candidates and 6 total evidence files.

Authority results remain unchanged from P1:

- `TRUE_RELEVANT`: 4
- `FALSE_RELEVANT`: 0
- `MISSED_RELEVANT`: 0
- `UNKNOWN`: 6
- `TRUE_NEGATIVE`: 1
- precision: 1.000
- recall: 1.000

## Parse-once authority

For both the full and bounded probe runs:

- discovered Python files: 25
- files read: 25
- AST parses: 25
- maximum reads for any Python path: 1
- read failures: 0
- parse failures: 0
- unique files cached: 25
- bytes read: 7,699

The qualification runner independently wraps `Path.read_bytes` and `ast.parse`; therefore the parse-once result is not derived only from self-reported probe counters.

## Bounded economics

For the `top_n=3` run:

- oversized candidates: 11
- selected candidates: 3
- candidate reduction: 72.7%
- corpus Python files: 25
- selected evidence files: 6
- evidence-file reduction: 76.0%
- selected evidence lines: 78
- transitive ownership depth bound: 2

Runtime is intentionally recorded by each run but is not used as a fixed qualification threshold because host performance varies between ChatGPT/coding-agent environments.

## Gate

P2 fails if any valid-corpus Python file is reread or reparsed, byte accounting differs from the fixture contents, selected evidence-line accounting is wrong, the transitive bound disappears, or P1 evidence authority regresses.
