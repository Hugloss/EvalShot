# Independent Hashmarks semantic review — operator execution gate

**Scope:** the two frozen known-symbol questions in
`benchmarks/dogfood/staged-suites/known-symbol-native-baseline-v1/`.
This playbook is an **external, private human/agent coordination procedure**,
not an automated oracle decision, reviewer-identity attestation or permission
to execute a benchmark. It does not change the frozen task population.

## Prerequisites and separation of duties

Assign **two distinct first-pass reviewers** and **one private intake operator**.
A reviewer must not have access to EvalShot, the frozen expected answers, prior
responses, the other reviewer, or an answer-bearing audit artifact. Reviewers
must declare any actual prior expected-answer exposure even if instructed not
to look. If separation cannot be established, record the review as potentially
contaminated; **do not declare independence**.

The operator may inspect EvalShot and the producer job, but must never send
answer-bearing files to reviewers. The independent reviewers receive only
copies of the same `hashmarks-blinded-reviewer-kit` artifact and the expected
SHA-256 digest of its manifest from a separately trusted producer channel.
Use a private channel with explicit access control for each response. The
operator must avoid sharing reviewer A's response with reviewer B until both
are finalized. Keep all response paths **outside both repositories and CI**.

## Phase A — prepare exactly one immutable kit

1. Use the current `Hashmarks Dogfood Source Audit` producer workflow for
   the fixed historical source. Download **only** its
   `hashmarks-blinded-reviewer-kit` artifact, not the broader source-audit
   artifact. Preserve the producing run URL, GitHub run ID, producer commit,
   artifact identity, timestamp, and its printed manifest SHA-256 in a private
   operator log. An artifact name alone is not a custody record.
2. Extract the kit into a clean directory. Check that the kit contains the
   standalone `verify.py`, `start_review.py`, `check_review.py` and
   `compare_reviews.py` helpers together with `questions.json`,
   `manifest.json`, `REVIEW.md`, `source/` and the manifest-bound
   contracts. **Do not infer the inventory from this list:** the manifest
   and verifier enforce the actual complete allowlist.
3. Run the bundled verifier before distribution:

   ```sh
   python3 /private/kit/verify.py \
     --root /private/kit \
     --expect-manifest-sha256 "<producer-manifest-sha256>"
   ```

4. Give each reviewer a separate clean extraction of the *same* immutable
   kit and the independently obtained digest. Preserve the artifact and
   digest reference for private intake; do not publish an answer template
   with prefilled candidate owners.

**Stop:** a missing digest, failed verification, mismatched source identity,
or differing kits blocks review. Reproduce the transport correctly; do not
"repair" the snapshot in place.

## Phase B — independently collect private responses

For each reviewer, in an independent workspace/session:

1. Run `verify.py` with the producer's digest.
2. Read only `REVIEW.md`, `questions.json` and `source/`; inspect the
   historical source as text, not by executing it.
3. Create a fresh incomplete private response **outside** the kit:

   ```sh
   python3 /private/kit/start_review.py \
     --root /private/kit \
     --expect-manifest-sha256 "<producer-manifest-sha256>" \
     --output /private/reviewer-a/findings.json
   ```

4. Investigate **both** questions independently and complete the response
   with a truthful `reviewer_id`, disclosure of prior access, source-backed
   candidate alternatives, positive declaration-span citations, and a
   provisional `unique`, `ambiguous`, `invalid`, or `unable_to_review`
   decision. Do not convert missing evidence into "unique".
5. Run the kit's canonical offline contract and source-evidence check:

   ```sh
   python3 /private/kit/check_review.py \
     --root /private/kit \
     --responses /private/reviewer-a/findings.json \
     --expect-manifest-sha256 "<producer-manifest-sha256>"
   ```

6. Deliver the completed response **directly to the private operator**, not
   a PR, GitHub issue, uploaded workflow artifact, repository file or
   evaluated agent workspace. Record receipt time and a local SHA-256
   digest. Repeat using reviewer B's isolated directory and response path;
   do not send A's contents to B.

**Stop:** a failed review check, an incomplete starter, untruthful or unknown
exposure declaration, reviewer cross-talk, or missing citations must be
preserved as an unresolved intake problem. A successful source check
verifies *syntax and transport*, not semantic correctness.

## Phase C — private comparison and canonical qualification

1. Ensure both received responses refer to the **same** manifest and frozen
   question identities. Keep the unredacted originals private and immutable.
2. Run the **existing** checkout-free private comparator:

   ```sh
   python3 /private/kit/compare_reviews.py \
     --root /private/kit \
     --response-a /private/reviewer-a/findings.json \
     --response-b /private/reviewer-b/findings.json \
     --expect-manifest-sha256 "<producer-manifest-sha256>" \
     --output /private/operator/sanitized-comparison.json
   ```

3. Preserve all disagreements, `unable_to_review`, `invalid`,
   source-reference disputes, declared prior exposure and even
   `SAME_CANDIDATE_UNVERIFIED` as *unqualified*. Do not force agreement,
   select a winning answer by majority vote, or reinterpret a sanitized
   category as a substantive decision.
4. A separate qualified semantic adjudicator must assess reviewer provenance,
   task meaning, the **private** reasoning, alternatives and source evidence.
   Only `benchmarks.harness.oracle_reviews` may admit genuine task-bound
   oracle evidence. Do not copy a comparison report into
   `qualification/oracle-reviews.json`, or unblock a staged experiment on
   the strength of this playbook or the pairwise comparison.
5. If any review remains disputed or insufficient, record it as **blocked**
   and seek genuinely new independent evidence; preserve the original
   findings and separation rather than overwriting them.

## Private custody checklist

Maintain a private operator-only record of: kit provenance and manifest
digest; reviewer assignments and separation declarations; each review's
receipt time, digest, and exposure statement; structural/source-check result;
pairwise comparison status; adjudicator and canonical oracle-review record
(if one is eventually accepted). Never publish unredacted responses or source
owner guesses in a repository, GitHub Actions log, job artifact or PR body.

Safe public status is limited to receipt/check completion, coarse unresolved
categories and a **negative** statement of oracle/experiment qualification.
The presence of this runbook or two matching reviews must never be reported
as two independently verified reviewers.

## Qualification boundary and next gate

A real native trial remains blocked until the **existing canonical**
oracle-review gate accepts the exact frozen task population in a separately
approved executable suite, and native host/runtime and headroom checks pass.
Then perform the preregistered A/B protocol without changing the native agent
harness or inference configuration. Track adoption separately from outcome.

**This document is a procedure only.** No reviewer has been commissioned,
no real first-pass return has been received, no semantic oracle has been
accepted, and no native measurements are asserted by its addition.
