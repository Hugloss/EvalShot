# Independent semantic source review — blinded handoff protocol

**Purpose:** decide whether each frozen *question* has a single defensible
source owner, multiple defensible owners, or insufficient evidence. This is a
first-pass **source review**, not an oracle approval or an agent benchmark.

## What you may inspect

The only authorized inputs for the first pass are `questions.json` and the
frozen repository snapshot under `source/`. `manifest.json` binds the bytes;
`verify.py` checks received integrity. `start_review.py` can create a private,
deliberately incomplete response draft. No model or agent must be invoked by
the kit itself.

**Do not inspect or request** EvalShot source, benchmark task definitions,
historical oracle results, previous reviewer decisions, source-audit reports,
grader outputs, or any supplied expected path/symbol. The reviewer should use
a separate clean agent session and workspace with no prior exposure to the
expected answers. If you already know any expected answer, report that
exposure rather than claiming a blind review.

The snapshot contains historical source, not an executable trust qualification.
Inspect text **read-only**. Do not run repository scripts, install dependencies,
contact external services, modify `source/`, or add new files to the kit.
An agent may use its own ordinary file-search and text-reading tools outside
the kit's content tree; EvalShot does not own the agent loop or tool selection.

## Review each question independently

1. Read exactly one entry in `questions.json`. Preserve its `packet_id`,
   `task_digest`, and pinned `repository.commit` / `repository.tree`.
2. Parse the *behavioral meaning* of `frozen_prompt`. The supplied prompt may
   reveal a symbol spelling; that is question input, **not** evidence that a
   particular file or method is the semantic owner.
3. Search the pinned `source/` text yourself. Inspect definitions, callers,
   aliases, dispatch, delegation, result contracts, and relevant tests where
   visible. Distinguish *identical names* from *identical responsibilities*.
4. Challenge the leading candidate. Identify other plausible owners,
   competing layers or interpretable task boundaries. A single matching
   declaration does **not** establish unique behavioral ownership.
5. Ground findings in repository-relative paths and **positive** line numbers
   from the frozen source. Explain the specific semantic relationship seen
   at each citation. A real file and line prove only source existence, not
   that the interpretation is right.
6. Select one provisional decision:
   - `unique`: exactly one defensible owner, with evidence and alternatives
     explicitly challenged.
   - `ambiguous`: at least two defensible owners; preserve both.
   - `invalid`: the frozen question or proposed owner interpretation is not
     defensible on source evidence; explain the defect.
   - `unable_to_review`: source or evidence is insufficient; do **not**
     fabricate a source-backed decision.
7. Repeat independently for the second question. Do not use an answer from the
   first question as the second question's oracle.

## Returning private findings

After inspection, use the standalone `start_review.py` to create a new
private response file **outside this kit**, using the producer-provided
manifest SHA-256. It carries only task, packet, and source identities. All
decision fields are deliberately invalid/unreviewed until you fill them.

Complete each `reviews[]` record with your actual `reviewer_id`,
`decision`, `prior_expected_answer_access` (boolean, declared truthfully),
`source_inspected` (boolean), `candidate_locations` (`path`, `symbol`),
a concise `reason`, and `source_evidence` entries (`path`, `line`,
`observation`). For `unique` provide **exactly one** candidate location;
for `ambiguous` provide **at least two** distinct locations. Any concrete
decision requires genuine source inspection and source evidence. For
`unable_to_review`, leave candidate locations/evidence empty and mark
`source_inspected=false`.

Do not attach the private answers to a GitHub PR, CI artifact, EvalShot
repository, agent trial workspace, or the source kit. Return them directly
through the designated private review channel. The first-pass return parser
only checks shape and source identity; separate source-citation checks only
verify that cited lines and declarations exist. Neither attests your identity
nor turns a decision into an approved EvalShot oracle.

## Scope and stop conditions

If you recognize a prior expected answer, disclose exposure. If the task is
under-specified, record ambiguity or inability instead of making an answer
look unique. Do not rewrite prompts, frozen source, benchmark oracles, grading
rules, or reviewer records. Do not mark an experiment ready.

**End state:** two source-backed *provisional* first-pass findings, or
explicitly unresolved cases. `benchmarks.harness.oracle_reviews` remains the
only EvalShot authority for independent oracle review, and there is still
**no** native baseline, agent-effectiveness claim, or proof of reviewer
independence from merely preparing this kit.
