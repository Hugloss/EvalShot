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
