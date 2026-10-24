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

## Isolated reviewer source kit — no EvalShot oracle files

The `hashmarks_reviewer_kit` exporter now materializes an **external,
answer-blinded review handoff directory**. This is the missing transport
boundary before engaging a genuinely independent source reviewer.

```sh
uv run --no-project python -m benchmarks.dogfood.hashmarks_reviewer_kit \
  --repository-root . \
  --checkout /path/to/Hashmarks-with-frozen-commit \
  --output-dir /tmp/hashmarks-reviewer-kit-unique
```

The destination must be **new**, outside EvalShot and the Hashmarks checkout.
No existing destination is overwritten. The output contains exactly:

| Item | Meaning |
| --- | --- |
| `questions.json` | Existing validated question-only blinded packet |
| `source/` | Read-only-purpose copy of every regular tracked file in the pinned historical Git tree (no `.git`) |
| `manifest.json` | Pinned commit/tree, packet digest, file counts and independent SHA-256 content inventory |

The exporter reuses EvalShot's existing blinded question generator and
historical Git commit/tree verifier. Each regular source entry is checked
against Git's own blob identity; `git archive` omissions or substitutions
are rejected. It refuses symlinks, submodules and unsafe tracked paths,
bounded source/archive sizes, conflicting pins, modified benchmark source
copies, a preexisting output directory, and any output location inside
the source repositories. Failure leaves no published reviewer kit.

**Git source completeness and byte identity do not prove independence.**
The generated handoff can be given to a distinct reviewer with no prior
oracle exposure. It is *not* a sandbox, access-control boundary, reviewer
identity proof, or guarantee against remembering answers from a previous
session. The reviewer should run in a clean environment using **only**
this directory and must declare previous answer access. The exporter
runs no agent and collects no semantic answers. The two tasks remain
`FIRST_PASS_UNREVIEWED`, the planned native A/B trial remains blocked,
and no baseline, oracle approval, owner proof or Hashmarks uplift is
created by generating the kit.

The dedicated GitHub Actions workflow uploads a separate
`hashmarks-blinded-reviewer-kit` artifact with exactly six top-level items: `questions.json`, `manifest.json`, `source/`, `verify.py`, `start_review.py`, and `REVIEW.md`.
Its upload explicitly **includes tracked dotfiles** such as
`.gitattributes` and `.gitignore`: omitting hidden Git-tracked files
would violate the manifest's complete pinned-tree inventory.
The snapshot never contains a `.git` directory.
Do **not** give the reviewer the broader `hashmarks-frozen-source-audit`
artifact, which intentionally contains oracle-proposed locations and other
answer-bearing reports. Keep any reviewer returns private and **outside**
both repositories. The existing `hashmarks_review_returns` and
`hashmarks_return_source_checks` readers can validate return shape and
source-citation existence afterward, but cannot approve the oracle. Only
EvalShot's canonical `benchmarks.harness.oracle_reviews` holds approval.

## Recipient-side verification of the downloaded reviewer kit

The handoff has exactly six top-level components:
`questions.json`, `manifest.json`, `source/`, `verify.py`,
`start_review.py`, and `REVIEW.md`.
`verify.py` is the copied, standard-library-only recipient verifier.
`REVIEW.md` contains the source-only first-pass review protocol.
It does **not** import, install or require EvalShot, Git, Hashmarks or
the native agent. It never executes code from the source snapshot.

After downloading and extracting the artifact to an otherwise empty
directory, the recipient can verify the exact delivered files:

```sh
python3 /path/to/received-kit/verify.py \
  --root /path/to/received-kit \
  --expect-manifest-sha256 <manifest-sha256-from-the-producing-run>
```

The producer outputs the manifest digest. The verifier checks that digest,
the question file digest, the verifier/starter/instructions digests, pinned
question source identities, a **complete** SHA-256 inventory including
tracked dotfiles, and an exact top-level allowlist. It rejects modified
or missing files, unexpected source files, symlinks, unsafe paths,
overly large inputs, changed question status, and false approval flags.
The received kit must not contain an EvalShot checkout or any previous
answer-bearing dogfood reports.

The dedicated workflow now has a **second, checkout-free recipient job**.
It downloads **only** `hashmarks-blinded-reviewer-kit` and runs the
standalone verifier using a manifest digest passed from the producer job.
This is an independently executed *artifact transport test*, **not an
independently performed semantic source review**. The verifier cannot
authenticate the producer outside the trusted GitHub workflow, prove that
a human/agent has no prior answer exposure, approve an oracle, or prove
native agent headroom. The existing EvalShot canonical reviewer still
owns any future approval.

## Reviewer-facing protocol — evidence before decisions

The kit includes a self-contained `REVIEW.md` that a separate source-review
agent can read **without an EvalShot checkout**. It describes how to challenge
a candidate's claimed semantic ownership: start from each exact blinded
question, inspect the pinned source and relevant callers/tests, actively
seek plausible alternative owners or delegation paths, and cite positive
source line numbers. It distinguishes `unique`, `ambiguous`, `invalid`,
and `unable_to_review` and requires disclosure of any prior answer exposure.

The guide does **not** disclose the frozen expected locations or previous
reviewer rationale, and does not execute the agent or select its tools.
Its original bytes are bound to `manifest.json` by
`review_protocol_sha256`; the downloaded verifier rejects a missing or
modified guide. CI verifies the complete kit after artifact download.

A guide is not a security sandbox or proof that the reviewer is actually
independent. Only the reviewer can truthfully disclose previous exposure,
and only EvalShot's canonical oracle-review authority can decide whether
source evidence is independently sufficient for approval. This guide
does not mark any task or experiment ready.

## Offline private reviewer response starter

The downloaded reviewer kit now includes a standalone
`start_review.py`. It needs **only** `verify.py`, `questions.json`,
`manifest.json`, and the frozen `source/` files already in the kit;
it does not import or check out EvalShot.

After verifying the kit, generate an **incomplete, private** response
file **outside** the downloaded kit:

```sh
python3 /path/to/received-kit/start_review.py \
  --root /path/to/received-kit \
  --expect-manifest-sha256 <digest-from-producing-GitHub-run> \
  --output /private/reviewer-findings.json
```

The starter pre-populates each frozen question's `packet_id`,
`task_digest`, `source_commit`, and `source_tree`, together with
the exact `blinded_packet_identity` required by the existing
EvalShot private-return consumer. It never inserts an expected owner.

For **both** tasks, the other fields deliberately remain invalid and
unreviewed: `reviewer_id=""`, `decision="NOT_REVIEWED_REPLACE_BEFORE_SUBMISSION"`,
`prior_expected_answer_access=null`, `source_inspected=null`,
`candidate_locations=[]`, `reason=""`, `source_evidence=[]`.
The canonical return validator **rejects this skeleton**. The reviewer
must independently inspect `source/`, disclose prior-answer exposure,
record defensible alternatives and precise source citations, and choose
a meaningful decision before submitting privately. Do not treat the
starter as a completed review or as an authenticated reviewer identity.

The starter refuses an output path inside the kit, an existing output
file, or a missing/mismatched producer manifest digest. It writes a
new file with mode `0600` and does not print answer content. The
checkout-free recipient CI job exercises the starter, asserts it is
still incomplete, and deletes its temporary placeholder. No real
responses are produced or uploaded to GitHub Actions.

The proper next step is still an independent semantic review followed
by EvalShot's canonical private-response and oracle validation. This
handoff adds **no oracle approvals, model trials, or native headroom**.

## Portable offline validation of a private reviewer response

The received kit includes `check_review.py` and `review_contract.py`,
copied from EvalShot's **single** stdlib-only return-shape contract. The kit
manifest binds both files by SHA-256, and `verify.py` checks them before use.
A reviewer can validate its privately completed response without any EvalShot
checkout or copy of oracle expectations:

```sh
python3 /path/to/kit/check_review.py \
  --root /path/to/kit \
  --responses /private/completed-review.json \
  --expect-manifest-sha256 <producer-manifest-digest>
```

This is a structural preflight only: exact question/task/source identity,
legal provisional decision types, declaration of prior expected-answer access,
candidate path syntax and required source evidence. An intentionally incomplete
`start_review.py` skeleton must be rejected; `unique` never becomes an
approved oracle. No reviewer identities, paths or rationales are printed by
successful validation, and the response is not written inside the kit.
The same `validate_returns` function is consumed by EvalShot's private intake
and by the offline kit, preventing parallel interpretations.

After the review has passed structural validation, `check_review.py` also
checks that each claimed source file, Python function/method declaration
and positive evidence line number exists in the **manifest-verified pinned
source snapshot**. It uses the same portable `check_references` implementation
that EvalShot's private, historical Git-backed citation checker uses,
packaged byte-for-byte as `source_contract.py` and bound by
`source_contract_sha256`. The Git-backed caller continues to own
commit/tree authority; the offline caller uses the independently verified
received kit. Neither implementation executes source code.

**Single-read verified source custody:** the standalone recipient verifier
now optionally retains an in-memory snapshot of each source blob **at the
same time it verifies its manifest hash**. It publishes that snapshot to the
private return checker only if the *entire* kit passes verification. The
checker consumes those retained bytes rather than reopening paths after
validation. A post-check filesystem edit or symlink swap cannot change the
meaning of an already-validated source citation; a fresh verification of
the modified kit still fails closed. No source bytes enter the printed status
or reviewer-response files. The standard `verify.py` command continues to
return a small, answer-free transport status.

**Atomic verified review context:** the same recipient verifier now also
publishes the already-validated `questions.json` and `manifest.json` parse
results in memory, only after **all** kit source and metadata checks succeed.
The private response checker, incomplete-review starter, and two-review
comparator use those exact verified question/source identities instead of
reopening the JSON files after verification. Both source and public-metadata
snapshot destinations must begin empty and are not populated on failure.
An on-disk question/manifest swap after verification cannot change a
completed first-pass comparison; a new verification of the tampered kit
still fails. Ordinary command-line reports remain answer-free and do not
export private reviewer content. This is transport/identity consistency,
**not** source semantics, reviewer independence, or oracle approval.

The result is explicitly `UNTRUSTED_FIRST_PASS_SOURCE_EXISTENCE_ONLY`;
`SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF` means the citation bytes
exist, not that the reviewer found a valid behavioral owner. For this
Python-symbol cohort, a *positive source-existence status* now requires
each proposed Python candidate to have a matching AST declaration **and**
at least one cited line in that same candidate file. Additional cited
callers, tests and delegations remain permitted. A claimed symbol in a
non-Python file returns `UNSUPPORTED_NON_PYTHON_CANDIDATE` (no inferred
declaration authority); an otherwise valid candidate with only unrelated
file citations returns `CANDIDATE_WITHOUT_CITED_SOURCE_EVIDENCE`.
Missing source files, absent declarations, invalid syntax, or nonexistent
line numbers retain distinct negative statuses. An unable-to-review
decision cannot be reported as positive source evidence. These checks
still do **not** establish semantic behavior, reviewer independence or
oracle correctness. Status output never includes candidate paths,
reviewer names, rationales or source observations, and always reports
zero oracle approvals.

**Symbol-scoped source anchors:** a positive reference-existence result now
requires a real citation inside the **observed Python function/method
declaration span**, including decorators, rather than an unrelated line in
the same file. Matching symbols observed only inside local nested functions
return `CANDIDATE_ONLY_LOCAL_DECLARATION`; multiple separately observable
declarations sharing a symbol within one candidate file return
`CANDIDATE_MULTIPLE_DECLARATIONS_OBSERVED`; existing declarations cited
only elsewhere in their file return `CANDIDATE_WITHOUT_DECLARATION_SPAN_CITATION`.
All three statuses are unqualified: static syntax does not prove semantics,
runtime reachability, independence, or an oracle. Additional external
callers/tests/delegation evidence remains allowed.

Source existence and semantic ownership remain separate responsibilities,
with independent approval reserved to EvalShot's canonical oracle review
owner.

## Two-return private disagreement review (no semantic promotion)

The kit contains an optional `compare_reviews.py` for comparing **two
distinct private reviewer return files** against the **same** frozen
questions and historical source manifest:

```sh
python3 /path/to/kit/compare_reviews.py \
  --root /path/to/kit \
  --response-a /private/first-review.json \
  --response-b /private/second-review.json \
  --expect-manifest-sha256 <digest-from-producer> \
  --output /private/sanitized-comparison.json
```

Both input files must be complete, independently returned first-pass
declarations, not the intentionally incomplete `start_review.py` skeleton.
The comparator uses `check_review.check_claims` to read, validate and
transport **each private response exactly once**, then compares the validated
in-memory payload. This prevents a second read of a response file after
validation from silently changing the claim being compared. The ordinary
`check_review.check` CLI continues to return only sanitized status without
exposing any private payload.
Each is processed by the **same** `check_review.check` pipeline that already
validates the frozen packet identity, source-reference syntax, and **manifest
bound source existence**. The comparison introduces no second grader and no
new interpretation of source-reference validity.

The comparator retains uncertainty rather than averaging opinions: one
missing response, duplicate self-declared reviewer identity, declared prior
expected-answer access, missing source evidence, ambiguity, invalidity,
unable-to-review, two different proposed owners, and two matching candidate
names all receive **distinct review-only states**. Matching candidates are
reported as `SAME_CANDIDATE_UNVERIFIED`, *not* independent agreement, because
any reviewer can copy another review or reuse earlier answers. No automated
winner, grade or oracle approval is produced.

All proposed owner paths, symbol names, reviewer IDs, rationales and cited
source text stay in the **private input files**. The optional new output
cannot overwrite either response or enter the downloaded source kit. It
contains only task IDs, untrusted review states, coarse counts and explicit
negative approval/independence flags. Never upload either private response
file to GitHub Actions or an evaluated agent workspace. The new comparator
is bound into the kit manifest by `comparison_sha256`, and the standalone
recipient verifier rejects its removal or modification.

Even after two source-checked matching claims, EvalShot's canonical
`benchmarks.harness.oracle_reviews` remains the **only** owner of
independent oracle acceptance. This helper cannot authenticate reviewer
identity, validate semantic uniqueness, unfreeze the staged experiment or
establish native baseline headroom.

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

The CLI also **rejects** response input files located inside the
EvalShot repository. This is a placement guard, not a proof of reviewer
independence: the user must still maintain access controls for the
private responses and isolate the reviewer workspace.

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

## Source-existence checks for private first-pass citations

The optional `hashmarks_return_source_checks` diagnostic provides the next
non-authoritative review backstop. The previous `hashmarks_review_returns`
reader only validates source-citation **shape**. This diagnostic reads
the **frozen historical Git commit** to check exact tracked regular files,
candidate Python declarations, and cited source line existence.

```sh
# CI / no private responses: must retain AWAITING_FIRST_PASS
uv run --no-project python -m benchmarks.dogfood.hashmarks_return_source_checks \
  --repository-root . \
  --checkout /path/to/pinned/Hashmarks \
  --output /tmp/hashmarks-return-source-status.json

# Only on a private reviewer intake machine, never GitHub Actions
uv run --no-project python -m benchmarks.dogfood.hashmarks_return_source_checks \
  --repository-root . \
  --checkout /path/to/pinned/Hashmarks \
  --responses /private/outside-evalshot/reviewer-answers.json \
  --output /tmp/hashmarks-return-source-status.json
```

This uses **the existing strict return validator** for exact blinded packet,
task digest, source commit/tree and declared review decision. Git replacement
objects are disabled, the historical commit must be an actual commit with
the frozen tree, and each referenced path must resolve to the **exact**
regular tracked blob, not a symlink, arbitrary working-tree file or wildcard
pathspec. Python function/method declarations are checked using the existing
AST declaration visitor, while evidence line numbers are bounded by the
pinned blob's line count. Non-Python source file *presence* can be checked,
but non-Python symbol semantics are not inferred. Missing or oversized blobs
and unparseable Python cannot pass a Python declaration check.

A verified Git path and line mean only that **the cited source locations
exist**. A natural-language reviewer's observation might still be wrong;
an observed method spelling is not proof of the runtime target, behavioral
owner, or oracle correctness. `SOURCE_REFERENCES_EXIST_NOT_SEMANTIC_PROOF`
is intentionally not an approval or a "verified independent reviewer" state.
`PINNED_SOURCE_IDENTITY_UNAVAILABLE`, source-file, declaration and line
mismatches retain distinct negative status codes.

**Privacy rule:** Reviewer responses must remain outside EvalShot. No CI run
receives private reviewer answers. The output never contains source paths,
reviewer names, candidate owner strings, or reviewer source observations.
It publishes only coarse status/counters and continues to carry zero
canonical oracle approvals, zero verified independent reviewers, and zero
native baseline measurements.

The existing `benchmarks.harness.oracle_reviews` remains the only canonical
source-review/approval owner; this diagnostic cannot issue or modify its
review records and cannot launch or unblock the staged A/B campaign.

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
