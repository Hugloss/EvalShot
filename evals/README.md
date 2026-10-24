# Evaluations

EvalShot owns evaluation execution, corpora, fixtures, and evidence checking.
Canonical skills, agents, and their runtime adapters are maintained in
[agentsCookbook](https://github.com/Hugloss/agentsCookbook). Select that checkout
explicitly with `--cookbook-root`; the evaluators do not search for a sibling
checkout or keep copies of its live skill definitions.

Examples below run from the EvalShot root and use a separate checkout at
`../agentsCookbook`. Substitute the path to the source you intend to evaluate.

The fixed flow benchmark set lives in `scripts/run-opencode-benchmarks.js`. It covers full Ping-Pong planning and one-reviewer routing behavior, records sessions, validates runtime call structure, and checks final-answer contracts.

The sharp specialist benchmark is runtime-neutral:

- `evals/sharp-skill-cases.json` is the canonical positive/control/confusion corpus;
- `scripts/run-skill-benchmarks.js` validates and executes that corpus on OpenCode or Pi;
- `evals/sharp-skill-discrimination.md` documents the behavioral contract;
- `evals/fixtures/` contains bounded repository-backed cases where nearby failure classes must be distinguished from one another.

Loading a skill proves availability, not discrimination quality. A sharp skill is useful only when it finds its own defect and rejects safe or neighboring cases.

When adding evaluations:

- test standalone invocation separately from full-flow composition;
- include routing false positives/negatives for short descriptions;
- give every specialist at least one positive and one control case;
- add confusion cases where two skill families can plausibly inspect the same code;
- require evidence and reject cross-skill drift, not just plausible-sounding prose;
- test 98k context-pressure behavior with bounded evidence packets;
- execute the same behavioral corpus on OpenCode and Pi instead of maintaining two copies;
- distinguish install/discovery proof from model-backed behavioral proof;
- distinguish 12 installable agents from the exact nine mandatory flow reviewers;
- verify failed/skipped reviewer calls cannot be reported as successes.

Structural corpus validation is safe for CI:

```bash
node scripts/run-skill-benchmarks.js --cookbook-root ../agentsCookbook --validate-corpus
```

Model-backed runs belong in local runtime qualification because GitHub CI does not own the local endpoints or model profile.

Do not put canonical agent or skill sources under an evaluation or runtime-specific directory.

## Source-anchored positive fixture qualification

For the existing positive repository-fixture cases, the corpus now pins exact
file/line/quote anchors. A model's `EVAL_EVIDENCE` statement alone is not
sufficient. The evaluator additionally requires:

```text
EVAL_EVIDENCE_LOCATION: relative/path:line
EVAL_EVIDENCE_QUOTE: exact quote on that line
```

For multi-file proof, separate complete locations/quotes using the literal
`|||` separator in both fields. The local file must be a real regular file
under the fixture root, not an out-of-root symlink, and the citation must match
every pinned anchor. A citation to a different existing line is rejected.
Anchors are pinned source observations, **not independent semantic oracle
approval**; a correct citation can still support an incorrect interpretation.

## Explicit skill outcome qualification

Skill result receipts now include a separate `qualification` object. The historical `status=pass` remains the classification-and-evidence-contract result, **not** a claim of independently proven semantic correctness. `capability` distinguishes `SCENARIO_CLASSIFICATION`, `SOURCE_LOCATED_CLASSIFICATION`, and `CLASSIFICATION_ONLY`; failed cases report `UNQUALIFIED`. `semanticProof=NOT_ADJUDICATED`, `repositoryDiscovery=NOT_MEASURED`, and `repair=NOT_MEASURED` deliberately prohibit promoting source citations or plausible claims into benchmark-proven capabilities. Future independently qualified semantic oracles require their own frozen experiment and explicit evidence; they must not guess from the observed verdict or citation.

Model-free quality checks:

```sh
node scripts/run-skill-benchmarks.js --cookbook-root ../agentsCookbook --validate-corpus
node scripts/test-skill-evidence-contract.js
```

The validation summary reports actual confusion-case coverage and the count
of scenario-only cases. Do not infer behavioral skill quality from corpus
validation, a nonempty answer, or a source-citation match. The remaining
uncovered specialists require independent, reviewed neighbor-confusion
fixtures and model-backed qualification on OpenCode/Pi. Those evaluations
are not performed by GitHub CI.
