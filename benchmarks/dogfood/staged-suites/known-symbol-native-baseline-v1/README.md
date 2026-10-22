# Known-symbol native baseline — preregistered, review blocked

This is a **staged experiment design**, not an executable or qualified campaign.
It lives under `benchmarks/dogfood/staged-suites/` intentionally so the active
`benchmarks/suites/repository-intelligence` corpus does **not** count its tasks
as assigned or treat the proposal as an independent benchmark.

## Exact task population

- `lookup-known-symbol-paths-under` — copied byte-for-byte from
  `heldout-v1/tasks/`, with frozen Hashmarks Git identity.
- `lookup-known-symbol-sync-remove-stale-paths` — copied byte-for-byte from
  `heldout-v1/tasks/`, with the same pinned source identity.

The missing-path `lookup-known-symbol-run-poll-delay` is explicitly **not**
included. Its source discrepancy remains unresolved and its historical
definition is unchanged.

`protocol.json` binds every transferred task/subject/agent to its
original Git blob SHA-1 and source path. The paired A/B conditions use the
same native Codex agent definition and host inference settings:

| Arm | Subject | Purpose |
| --- | --- | --- |
| `none-codex-native` | none | Native baseline, optional Hashmarks unavailable |
| `hashmarks-codex-native` | Hashmarks | Unforced, available MCP tool; adoption measured separately |

Each task is scheduled at replicate IDs `9901`, `9902`, and `9903`,
for **12 proposed trial assignments**. These IDs are not model RNG seeds.
A/B order alternates across the six task-replicate blocks (three A-first,
three B-first). This planned counterbalance is not proof of randomized or
independently attested actual launch order.

## Fail-before-work requirement

`experiment.json` intentionally names
`qualification/oracle-reviews.json` **without supplying that file**.
EvalShot's existing canonical `load_suite` and
`validate_oracle_reviews` reject the staged proposal before any model
call. Never add a placeholder approval file, duplicate the inherited
behavioral-task reviews, turn off the review declaration, or bypass the
harness through this staging area.

A distinct independent reviewer must inspect the exact known-symbol task
meaning, challenge alternatives and owner uniqueness, and supply the real
task-bound review through EvalShot's existing review workflow. The
reviewed proposal can then be transferred to a **separately frozen,
approved generation** after canonical admission. The files here are not
an automatic executable suite.

Before any pilot, independently verify native host/tool trust, input
integrity, source and treatment identity, tool exposure, contamination,
actual launch order and complete receipts. First establish baseline
headroom **for these exact prompts**. The October 21 pilot and earlier
behavior-description tasks cannot provide their outcomes, even when the
expected owner is identical. A separate qualified A/B campaign would be
needed to estimate Hashmarks usefulness.

## Model-free validation

```sh
uv run --no-project python -m unittest -q \
  benchmarks.tests.test_hashmarks_dogfood_baseline_staging
```

The focused test validates schema, exact transferred source blobs,
12-assignment A/B parity, execution ordering, absence of other subjects or
model overrides, and the deliberately blocked independent-review gate.
It does not invoke a model or populate grader records.

**Current state:** `BLOCKED_PENDING_INDEPENDENT_ORACLE_REVIEW`.
No campaign, baseline outcomes, measured headroom or causal gain exists.
