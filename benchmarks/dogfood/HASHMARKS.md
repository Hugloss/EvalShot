# Hashmarks as an EvalShot dogfood corpus

This is a **model-free, read-only corpus maintenance check**, not a replacement
for EvalShot's experiment admission, host delivery, trial qualification,
independent grading, or effectiveness analysis. It interrogates existing frozen
Hashmarks-source tasks without editing historical task definitions or Hashmarks.

It answers: which frozen suites still use Hashmarks as their source repository,
which commits and task families are represented, whether the oracle transcribed
into each reviewed task still matches its independent review, whether a local
Hashmarks checkout contains the frozen commit/tree and cited file, and whether
the same `(commit, task ID)` appears with distinct definitions in multiple suites.

From the EvalShot checkout:

```sh
uv run --no-project python -m benchmarks.dogfood.hashmarks_corpus \
  --suite-root benchmarks/suites/repository-intelligence \
  --output /tmp/evalshot-hashmarks-corpus.json

# Strict reproducibility check, requires historical Git objects locally.
uv run --no-project python -m benchmarks.dogfood.hashmarks_corpus \
  --checkout /path/to/Hashmarks --require-source --require-reviewed \
  --output /tmp/evalshot-hashmarks-source-audit.json

uv run --no-project python -m unittest benchmarks.tests.test_hashmarks_dogfood_corpus
```

## Pinned-repository same-name collision investigation

`hashmarks_collisions` extends the nominated-file AST check by scanning
**all tracked Python files in the exact historical Git tree**, not merely
the oracle-proposed file. It identifies other declarations with the same
spelling as an unassigned candidate's function or method. It never
concludes that same-name functions have the same behavior, nor that
a single observed declaration proves unique semantic ownership.
The agent is not shown these reviewer-only observations.

```sh
uv run --no-project python -m benchmarks.dogfood.hashmarks_collisions \
  --audit /tmp/hashmarks-source-audit.json \
  --checkout /path/to/Hashmarks \
  --output /tmp/hashmarks-collisions.json
```

This module reuses the source audit, pinned-commit Git reading and existing
AST declaration visitor. It verifies that the Git object is a commit and
that its tree matches the frozen task, disables Git replacement refs,
reads tracked blobs only, and never imports repository code. The Python
scan is bounded (at most 1,200 tracked Python files, 24 MiB total
inspected bytes, 1 MiB per file); parse failures, nonregular entries,
missing blobs and exceeded limits force `SCAN_INCOMPLETE` rather than
false evidence of no alternatives. Same-name evidence is retained even
when the rest of the scan is incomplete.

The report distinguishes `SAME_NAME_ALTERNATIVES_OBSERVED`,
`NO_OTHER_STATIC_DECLARATIONS_OBSERVED`,
`NOMINATED_DECLARATION_NOT_OBSERVED`,
`SCAN_INCOMPLETE`, `SOURCE_PRECONDITION_FAILED`, and identity or
candidate-format errors. The word **observed** is deliberate: dynamic
wiring, imported aliases, call graphs and human intent are not resolved.
Every output contains `unique_semantic_owner_proven=false`,
`oracle_approved=false` and `baseline_headroom_known=false`.

The first real run against pinned commit
`85453be578e8ff46b8d79b363f140a915d6804f6` completed a
**528-file Python scan with zero unreadable/unparseable files**. It found
`WorkspaceMapStore.paths_under` only in
`hashmarks/codemap/repository_index_store.py` and
`IndexingLifecycleMixin._sync_remove_stale_paths` only in
`hashmarks/codemap/indexing_lifecycle.py`. Neither had an additional
**exact same-name Python declaration** in that frozen tree. This is
bounded syntax evidence, **not a unique behavioral owner certificate**.
The unassigned `lookup-known-symbol-run-poll-delay` task stayed blocked
by its missing source precondition.

CI publishes `hashmarks-collisions.json` next to the existing source,
coverage, review and nominated-file reports. The independent reviewer
can now inspect alternatives before deciding which candidate, if any,
deserves new independently frozen experiments. Previously frozen tasks,
their oracle expectations and experiment-run behavior remain untouched.

## Pinned-source declaration observations

The `hashmarks_declarations` reader checks the **nominated Python source
file only** for concrete `def` / `async def` nodes in the pinned Git blob.
It uses Python AST, does not import or execute source code, limits blob size,
disables Git replacement refs, and records the source content SHA-256 plus
qualified declaration names and line ranges.

```sh
uv run --no-project python -m benchmarks.dogfood.hashmarks_declarations \
  --audit /tmp/hashmarks-source-audit.json \
  --checkout /path/to/Hashmarks \
  --output /tmp/hashmarks-declarations.json
```

For each unassigned candidate the observation distinguishes
`DECLARATION_OBSERVED`, `MULTIPLE_DECLARATIONS`,
`DECLARATION_ABSENT`, `SOURCE_PRECONDITION_FAILED`,
`SOURCE_UNAVAILABLE`, `SOURCE_NOT_PARSEABLE`, and
`NOT_PYTHON_CANDIDATE`. A missing or invalid claimed symbol is also explicit.
The result cannot establish unique ownership, runtime reachability,
independent oracle validity or agent headroom; every row hardcodes those
approval fields to `false`. In particular, checking the *oracle-nominated*
file against the *oracle-nominated* symbol is a consistency observation,
**not an independent search for the correct owner**. The source might have
another legitimate implementation or a misleading declaration.

The historical known-symbol candidates
`lookup-known-symbol-paths-under` and
`lookup-known-symbol-sync-remove-stale-paths` are the immediate review
subjects. The previously observed `lookup-known-symbol-run-poll-delay`
source mismatch must remain a failed precondition and cannot be repaired by
this tool. CI publishes a separate `hashmarks-declarations.json` artifact
alongside the audit, coverage report and handoff. These reports are
**reviewer-only**; they must not be copied into evaluated agent prompts.
An independent reviewer still owns confirmation of unique behavior,
alternatives, and actual trial eligibility.

## Independent review handoff (not experimental qualification)

The next step consumes **both** saved reports and verifies that the coverage
queue exactly matches the source audit. This prevents a changed nomination,
substituted source record, or incorrect candidate-approval flag from silently
entering an independent review packet.

```sh
uv run --no-project python -m benchmarks.dogfood.hashmarks_review_handoff \
  --audit /tmp/hashmarks-source-audit.json \
  --coverage /tmp/hashmarks-coverage.json \
  --output /tmp/hashmarks-review-handoff.json
```

GitHub's `Hashmarks Dogfood Source Audit` workflow now preserves a third
file, `hashmarks-review-handoff.json`, with the provenance-locked review
requests. Every packet is marked `REQUEST_ONLY`, `experiment_ready=false`,
`headroom_status=UNKNOWN_NOT_MEASURED`, and
`oracle_status=NOT_ESTABLISHED`. Requests list their exact source audit
records and the evidence that a **separate** reviewer must obtain.

The independent reviewer must examine actual pinned repository source,
challenge uniqueness/alternatives, determine whether the task offers
meaningful non-ceiling baseline headroom, and independently qualify oracle
evidence, native runtime comparability, contamination and model-visible
delivery where supported. The `DORMANT_TASK_NEEDS_REVIEW` category requires
source discrepancy resolution **before** task-design considerations;
`CROSS_SUITE_SEMANTIC_VARIANT` requires reconciliation of meaning without
electing an arbitrary canonical owner.

The exporter cannot evaluate those requirements or accept a reviewer
signature. The existing EvalShot suite/experiment admission and independent
oracle validators remain the *only* owners of that authority. Never supply
expected oracle answers, source-review rationales or candidate handoff
metadata to the evaluated agent. An absent or unqualified baseline remains
`UNKNOWN`, not zero headroom or a measured failure. None of these packets
changes the active experiment population.

## Model-free coverage and candidate triage

The separate `hashmarks_coverage` reader derives a deterministic coverage
matrix and a **review-only** candidate queue from a saved dogfood source audit.
It does not execute any benchmark, inspect arbitrary source through an agent,
write a new frozen task, copy independent oracle explanations into prompts,
or make effectiveness claims. It reuses EvalShot's canonical task identity
serializer to bind the input report.

```sh
uv run --no-project python -m benchmarks.dogfood.hashmarks_corpus \
  --checkout /path/to/Hashmarks --require-source --gate-assigned-only \
  --output /tmp/hashmarks-source-audit.json

uv run --no-project python -m benchmarks.dogfood.hashmarks_coverage \
  --audit /tmp/hashmarks-source-audit.json \
  --output /tmp/hashmarks-coverage.json
```

The source CI publishes both JSON outputs in the same artifact. The new
derived report preserves the parent audit's exact identity and its pass/fail
signal, but never changes that signal. Its family matrix describes **observed**
coverage only: assigned, unassigned and unknown-membership entries,
distinct (commit, task) identities, modes, and source verification.
The candidate queue uses only these provenance-backed categories:

- `DORMANT_TASK_NEEDS_REVIEW`: an unassigned historical task has a source
  discrepancy; investigate it independently and do not rewrite the fixture.
- `UNASSIGNED_CASE_FOR_TRIAGE`: a dormant definition may warrant independent
  scope, oracle, headroom and contamination review.
- `FAMILY_WITHOUT_ASSIGNED_TASK`: a family appears in the inventory but
  has no currently assigned Hashmarks case; this is a *scope observation*.
- `SINGLE_ASSIGNED_TASK_FAMILY`: the corpus has one distinct assigned case
  in an observed family; investigate whether independent additional cases
  would give useful headroom.

All nominations state `review_status=PROPOSAL_ONLY` and
`oracle_status=NOT_ESTABLISHED`. A family-count observation is **not**
a claim that the product is defective or that a test is needed. Unknown
experiment membership is not silently converted into an unassigned candidate.

Cross-suite candidate nominations are deduplicated by the frozen source
commit and task ID. A definition that is `UNASSIGNED` in one suite but
`ASSIGNED` in another is **not** proposed again as a new task. Existing
source errors remain reportable even for an unassigned duplicate. The
producer also transports EvalShot's canonical `task_semantic_digest`,
which makes equal task meaning distinguishable from byte-only formatting
differences. If the same (commit, task ID) has multiple known semantic
digests, the candidate queue emits `CROSS_SUITE_SEMANTIC_VARIANT`
for independent review and suppresses a misleading single-case-family
suggestion. Neither the name nor the digest resolves which definition is
correct. Old saved audit v1 reports without semantic digests remain readable;
the report explicitly counts their unavailable identities rather than
assuming definitions agree.

The first real CI coverage report over the pinned Hashmarks source recorded
20 task entries, 17 assigned and 3 unassigned across eight observed families.
It generated seven independent **review items**, not seven approved tasks:
three one-case-family observations (`code_owners`, `semantics`, `splunk`),
one family with no assigned case (`known-symbol-localization`), two
unassigned source-verified known-symbol definitions, and the retained dormant
missing-path finding. These numbers describe only this frozen inventory
and do not certify agent effectiveness.

The inventory distinguishes every frozen task definition from tasks actually
selected in the suite's `experiment.json`. `ASSIGNED` tasks participate in the
active experiment; `UNASSIGNED` tasks are available definitions, not missing
campaign assignments. `UNKNOWN` means no usable experiment membership was
read. `unreviewed_assigned_tasks` is therefore different from the inventory-wide
`unreviewed_tasks` count. Only the canonical EvalShot suite/review validator
may approve a campaign, and unassigned definitions are not promoted into
campaign failures.

During the October 22 Hashmarks-source check, the historical `heldout-v1`
source tree `667b7066be9ef6cab6e1ce3a28a603481f07387e` was accessible
in GitHub with the inspected expected Python source paths present. In the same
suite, `lookup-known-symbol-paths-under` and
`lookup-known-symbol-sync-remove-stale-paths` were present as definitions but
absent from `experiment.json`. Their missing review entries must not be
reported as active-campaign qualification defects. This source-tree inspection
is not equivalent to a local `--require-source` audit or independent semantic
oracle review.

The `requirements` object is the machine-readable result for exactly the
checks requested: `source_required`, `canonical_review_required`, and
`audit_passed`. A rejected canonical suite review fails the strict audit even
if all Hashmarks-source definitions in that suite are `UNASSIGNED` or have no
task-local errors. Do not infer experiment qualification from
`audit_passed=true` when source/review checks were not requested; this tool
never establishes model-delivery, grading, or causal effectiveness.

Frozen task variation is separated into `BYTE_ONLY_VARIANT` (different
serialized bytes, same EvalShot canonical semantic task identity) and
`SEMANTIC_VARIANT` (different canonical task content). This reuses
`benchmarks.harness.identity.digest`; it does not establish a second
normalization authority or rewrite historical definitions.

An explicitly provided `--checkout` must be a readable Git working tree.
The audit reads historical commit/tree objects, not the current working-tree
file contents: uncommitted changes neither make an unavailable historical
source valid nor make an existing historical source disappear.

The `--gate-assigned-only` option makes experiment membership an explicit
qualification boundary: `ASSIGNED` and `UNKNOWN` membership remain strict,
while `UNASSIGNED` definitions are still scanned and their source errors
remain visible in `tasks_with_problems` and
`ungated_tasks_with_problems`. Only the gate decision uses
`gated_tasks_with_problems`; without this flag **every definition** must
pass. An unknown membership is not silently treated as dormant.

The new source CI demonstrated why this matters. A full advertised-history
Hashmarks clone did not contain the historical commit; fetching its exact
OID made 19 of 20 frozen task source entries verifiable. The remaining
`heldout-v1/lookup-known-symbol-run-poll-delay` is `UNASSIGNED` and
references `ux/src/domain/fleet/runLifecycle.ts`, which is not in the
frozen Hashmarks tree. This is retained as a source-mismatch diagnostic,
not repaired by changing archived tasks or silently blessing the path.
The workflow uses `--require-source --gate-assigned-only` so active
experiments are qualified without suppressing the dormant finding.
A separate all-definitions strict audit intentionally still fails.

The source identity gate checks that each recorded `commit` resolves to an
actual Git **commit object** and that its computed tree equals the frozen
`repository.tree`. A bare tree object or tag cannot masquerade as a commit.
Git replacement objects are disabled during these read-only checks so local
`refs/replace` cannot rewrite historical source provenance. Git's
`ls-tree -z` entries are matched by their **exact** paths and tracked-blob
modes, not merely by Git pathspec membership; wildcard and control-character
oracle paths are rejected. These checks establish Git object/file identity,
not that an expected owner symbol is semantically correct, and they do not
certify the clone's remote origin.

This repository also contains a separate read-only source-qualification
workflow: `.github/workflows/hashmarks-dogfood.yml`. On dogfood changes
(or a manual trigger) it checks out full Hashmarks history, runs focused
regressions, and executes `--require-source` against the full corpus. It
publishes the deterministic JSON audit when generated. It deliberately
does **not** use `--require-reviewed` across every historical suite:
review approval remains the canonical experiment-specific admission gate.
No inference configuration, scorer, or frozen task is modified.

The local tool runtime used for this checkpoint cannot clone GitHub repositories
through the command-line network; the independent CI checkout provides the
full-history source reproduction attempt instead. GitHub commit/tree API
observations remain separate from a successful CI strict audit.

The output is deterministic JSON with explicit `NOT_CHECKED` and
`SOURCE_UNAVAILABLE` states. A missing historical object can mean a shallow
checkout: it is not evidence the task is semantically invalid. `--require-source`
rejects unavailable source instead of silently passing reproducibility. A
verified oracle path must be an ordinary tracked Git blob; directories,
symlinks, and other non-file entries are not accepted as proof that the cited
source file exists. `--require-reviewed` delegates independent oracle review
approval to EvalShot's existing suite loader and review validator. The
inventory's simple transcription match is **not** independent approval.
Without that flag, canonical review qualification remains `NOT_CHECKED`. Review
files are never copied into prompts or materialized agent workspaces by this
command. A file's existence does **not** prove the referenced symbol or oracle
is correct; semantic ownership still requires an independent source review.

Do not edit already-frozen prompts, source hashes, oracle grades, or historical
results to make a report green. Record discovered gaps for a separately frozen
future benchmark. The active EvalShot native qualification and effectiveness
program remains in PR #12; product evidence changes remain in Hashmarks PR #443.
This helper owns neither program, and has no host, adapter, scorer, or subject
changes. It does not report an agent performance gain.
