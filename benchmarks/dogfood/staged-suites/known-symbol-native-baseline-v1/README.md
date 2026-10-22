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

## Answer-blinded first-pass semantic review

The proposed two-task cohort now has a separate, model-free first-pass
question exporter. It is **not** the canonical EvalShot oracle review runner,
which may legitimately show a proposed expected owner to a later validator.

```sh
uv run --no-project python -m benchmarks.dogfood.hashmarks_blind_review \
  --repository-root . \
  --suite benchmarks/dogfood/staged-suites/known-symbol-native-baseline-v1 \
  --output /tmp/hashmarks-blind-review.json
```

Only give an independent reviewer the resulting
`hashmarks-blind-review.json` and an independently provisioned checkout
at the **pinned historical Hashmarks commit**. Do **not** mount EvalShot,
its benchmark task JSON files, historical oracle reviews, scores, or prior
answer-bearing dogfood reports into that reviewer workspace. The exporter
itself reads the staging files to enforce integrity, but the **reviewer
packet** contains only each exact prompt, source repository/tree/commit,
task identity hash, and open review questions. It omits expected oracle
paths, prior reasons, previously proposed owner, and approval claims.

This is *answer-blinded packet preparation*, not proof that the reviewer
is independent: someone with access to the EvalShot repository, prior task
answers, or this conversation can still know the expected owner. A truly
independent reviewer must disclose prior exposure, inspect the pinned source
without oracle truth, and return source-backed alternatives or ambiguity.
A different reviewer/validator may later compare findings with the frozen
oracle using EvalShot's **existing** task-bound oracle-review authority.

CI tests fail if the staged or original task bytes/participant definitions
have drifted, if recorded Git blob identities mismatch, if the prompt
accidentally embeds the expected source path, if a placeholder review appears,
or if the output attempts to overwrite staging or active benchmark sources.
Export is deterministic and runs no agent or benchmark trial. Its output
goes into the **review-only** GitHub Actions artifact, never an agent trial
workspace.

All candidates still have `FIRST_PASS_UNREVIEWED` status and
`experiment_admitted=false`. This does not approve source meaning,
headroom, baseline performance, or tool effectiveness.

## External first-pass findings: private answers, sanitized status

The separate `hashmarks_review_returns` consumer accepts **untrusted
reviewer declarations** tied to exact `packet_id`, task digest and pinned
Git commit/tree. It does **not** attest reviewer independence, authenticate
the source, verify semantics, select an owner, or issue an oracle approval.

Without external input CI emits a safe status report showing both tasks
still `AWAITING_FIRST_PASS`:

```sh
uv run --no-project python -m benchmarks.dogfood.hashmarks_review_returns \
  --repository-root . \
  --output /tmp/hashmarks-review-return-status.json
```

For a real review, a distinct reviewer must use only the answer-blinded
packet and pinned source checkout. Keep that reviewer's **private**
response *outside* EvalShot, GitHub artifacts and evaluated agent
workspaces. The handoff schema is
`evalshot.hashmarks-external-first-pass-returns.v1`:

```json
{
  "schema": "evalshot.hashmarks-external-first-pass-returns.v1",
  "blinded_packet_identity": "sha256:<digest of entire blinded packet>",
  "reviews": [
    {
      "packet_id": "<packet ID copied from blinded question>",
      "task_digest": "<frozen task digest from question>",
      "reviewer_id": "<independent reviewer declaration>",
      "source_commit": "<pinned commit from question>",
      "source_tree": "<pinned tree from question>",
      "decision": "unique",
      "prior_expected_answer_access": false,
      "source_inspected": true,
      "candidate_locations": [
        {"path": "<reviewer-discovered path>", "symbol": "<symbol>"}
      ],
      "reason": "<reviewer's confidential semantic reasoning>",
      "source_evidence": [
        {
          "path": "<source path>",
          "line": 1,
          "observation": "<bounded source-backed observation>"
        }
      ]
    }
  ]
}
```

Decisions may be `unique`, `ambiguous`, `invalid`, or
`unable_to_review`. An ambiguous result must declare at least two
distinct locations. A unique *claim* must declare exactly one location.
The consumer rejects duplicate keys, stale task/packet identity, altered
source pins, contradictory observation fields and malformed evidence.
`prior_expected_answer_access=true` remains explicit even if the
reviewer calls the oracle unique. A claim of no prior access is **not
independent attestation**.

The user-controlled offline command is:

```sh
uv run --no-project python -m benchmarks.dogfood.hashmarks_review_returns \
  --repository-root . \
  --responses /private/outside-evalshot/reviewer-answers.json \
  --output /tmp/hashmarks-review-return-status.json
```

Only a **sanitized status** is printed and saved: task/packet IDs,
unverified decision category, exposure declaration, and explicit zero
approval/experiment/headroom counters. The candidate source paths,
reviewer identities, rationales and evidence contents are **never
re-emitted**. CI intentionally provides **no response file**, so no
answer-bearing review is processed or uploaded automatically.

The canonical EvalShot `benchmarks.harness.oracle_reviews` is still
the **only** approval authority. A distinct qualified review process must
independently verify the source, reviewer provenance, alternatives and
semantic truth, and create the real task-bound review evidence through
that existing authority. Do not copy the return-intake results into
`qualification/oracle-reviews.json`, unfreeze the staged cohort or
claim native baseline headroom on their basis.

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
