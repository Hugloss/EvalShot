# Behavior-Preserving Split — Agent Economics plan

## Purpose

Generalize a refactoring pattern proven useful during Hashmarks structural-debt cleanup:

1. measure a concrete structural/debt candidate;
2. establish strong behavioral tests and meaningful coverage **before** changing production structure;
3. identify a narrow extraction boundary supported by evidence;
4. split oversized implementation into smaller typed state/configuration objects or focused helpers without intentionally changing behavior;
5. rerun the pre-existing behavioral evidence after every bounded split;
6. run broader repository verification;
7. remeasure current debt and choose the next candidate from fresh evidence.

This is deliberately a **behavior-preserving split** workflow, not a generic "clean up ugly files" command and not automatic refactoring authority.

## Hard invariants

- Debt, size, complexity, churn, or hotspot rank may nominate an investigation target; none authorizes an edit.
- No production split begins until the relevant behavior has strong executable evidence. Missing or weak coverage is a reason to strengthen tests first.
- Tests must capture intended behavior, authority boundaries, failure modes, and important negative cases. Do not add tests that merely mirror implementation structure.
- Pre-edit test evidence must be bound to the exact target and source identity. Passing tests owned by another target cannot satisfy BP1.
- The pre-split behavioral suite becomes the invariant used to detect accidental behavior change during extraction.
- A split must not intentionally change public behavior, acceptance policy, repository authority, or observable failure semantics.
- Prefer small typed state/configuration objects and focused helpers when they make ownership explicit. Do not create parallel code paths, compatibility shims, or abstraction layers solely to reduce a metric.
- Focused PASS is not repository qualification. Broader repository gates remain required.
- After a successful split, remeasure the current repository. Never carry forward a hand-curated hotspot list as authority.
- If a strong pre-existing test fails after the split, fix the implementation unless the test is proven to violate the intended contract. Never weaken a test merely to make the refactor green.
- Agent Economics remains evidence/measurement/verification guidance. The target repository owns behavior and policy.

## Capability shape

Do not begin by adding an autonomous refactor engine. First compose existing evidence:

`quality-debt / hotspot-focus`
→ candidate only
→ `test-focus`
→ behavioral evidence review
→ coverage-strength gate
→ human/agent-owned bounded split
→ focused verification
→ affected/component verification
→ repository gate
→ `quality-debt / hotspot-focus` remeasurement.

A reusable addition is justified only where existing probes cannot represent an important evidence boundary. Any new surface must remain provider-neutral and parameterized.

## Phases

### BP0 — Preserve the proven lesson

Document the behavior-preserving split contract and its non-authority boundary. Add a qualification that prevents future guidance from presenting debt rank as edit authority or focused PASS as completion.

### BP1 — Pre-edit behavioral evidence

Review whether `test-focus`, provider evidence, and repository-declared gates can establish direct owning tests, affected tests, broader gates, and important uncovered behavior. If evidence is insufficient, emit a **test-strengthening requirement**, not an edit recommendation.

Prefer composing existing evidence over inventing a coverage score. If coverage data is admitted later, preserve exact producer/provenance/scope and never equate line coverage with behavioral adequacy.

### BP2 — Split contract

Represent a proposed split as evidence, not source mutation. Bind the target/source identity, candidate definition/range, confirmed tests, affected verification, behavior-preserving invariant, proposed extraction kind, forbidden outcomes, and post-edit verification ladder. Do **not** generate the production edit. Stale packets fail closed.

### BP3 — Post-split verification

After the external agent edits: bind changed source identity; run focused behavioral tests; run affected/component gates; run repository gate; compare failures with the pre-split contract; reject "successful cleanup" when broader verification fails. Verification economics remain separate from correctness.

### BP4 — Fresh remeasurement

Rerun quality/hotspot evidence against the new repository bytes. Compare only semantically compatible measurements. Record resolved/reduced/increased/new debt, file/definition movement, newly exposed candidates, and verification cost. The next target comes from current bytes, never a frozen historical list.

### BP5 — Hashmarks dogfood

Use current Hashmarks `main`, not the historical `ruff-debt` branch.

For each bounded candidate:

1. exact-bootstrap the qualified EvalShot revision;
2. run `doctor` / reviewed profile as needed;
3. measure current quality debt;
4. choose one candidate from current evidence;
5. obtain `test-focus` and Hashmarks repository-intelligence evidence;
6. strengthen behavioral/adversarial tests first when the boundary is not sufficiently protected;
7. freeze the pre-split behavioral evidence;
8. perform one bounded behavior-preserving split;
9. run focused tests, then affected/component tests, then Hashmarks' broader repository gate;
10. remeasure;
11. retain the change only if behavior remains protected and the structural result is actually simpler;
12. repeat from fresh evidence.

The historical Hashmarks cleanup is precedent/evidence for the workflow, not a list of files to refactor again.

### BP6 — Empirical economics

Capture the work as Agent Economics dogfood evidence without confusing cleanup success with empirical proof. Compare evidence/context consumed, files opened, tool calls, verification attempts, failed edits, iterations/time to a correct bounded split, and local-vs-CI agreement. Retain unfavorable outcomes.

### BP7 — Convergence

When the Hashmarks campaign reaches its intended debt target: remove temporary consumer scaffolding; keep only generalized EvalShot capabilities that proved useful; delete experiment-only branches after evidence is preserved; verify Hashmarks main has no Agent Economics policy ownership; rerun current debt and repository qualification from clean bytes.

## Initial Hashmarks execution order

Start with current-main measurement. Do not select a file from memory or the old `ruff-debt` branch.

For the first candidate, prefer a boundary where debt evidence is concrete, behavior already has strong tests (or can be strengthened narrowly), extraction can be done without changing public semantics, and the result can remove complexity rather than redistribute it.

The first Hashmarks iteration also qualifies this workflow. If existing Agent Economics surfaces already provide enough evidence, keep the cookbook change documentation-only. Add code only for a demonstrated reusable gap.


## BP1 review result — smallest demonstrated gap

Existing capabilities already cover most of the workflow:

- `quality-debt` and `hotspot-focus` explicitly make ranking investigation-only rather than edit authority.
- `quality-debt` routes a debt candidate to `test-focus` before editing.
- `test-focus` distinguishes confirmed ownership from naming/path conventions, emits affected tests, requires repository-supplied broader gates, and explicitly states that focused suggestions never prove broader verification unnecessary.
- `working_evidence.evidence_stop_facts` can fail closed when declared risk boundaries lack fresh direct provider proof.
- `repair_packet` keeps verification incomplete and CI separate.

The missing reusable boundary is **pre-edit behavior protection readiness**. Today `test-focus` can say that a source has confirmed owning tests, but confirmed ownership is not the same fact as "the behavior we intend to preserve has been demonstrated by executable passing evidence." A test may import the source yet fail, be stale, cover only a neighboring behavior, or never have been executed in the current source state.

Therefore the next implementation must not add a coverage score or infer semantic adequacy from imports. Add a small provider-neutral **behavior-preservation evidence contract** that can bind:

- exact target/source identity;
- declared behavior/risk boundaries supplied by the caller or evidence provider;
- confirmed owning-test references from `test-focus`;
- a target-bound ownership packet tying those exact test identities to the exact target/source state and provider evidence;
- execution receipts proving the selected pre-edit tests actually passed against the bound source state;
- freshness/provider references when external repository intelligence is used;
- broader repository gates that remain required after the edit.

Its output may be only one of:

- `READY_FOR_BEHAVIOR_PRESERVING_EDIT`: every declared boundary has fresh direct evidence and the required pre-edit test execution passed on the bound source;
- `TEST_STRENGTHENING_REQUIRED`: ownership/tests exist but one or more declared behavior boundaries lack direct executable protection;
- `EVIDENCE_REQUIRED`: source/test identity, execution, freshness, or repository-gate evidence is unresolved.

This status is evidence readiness only. It does not authorize an edit, choose an extraction, or claim coverage completeness.

### BP1 adversarial qualification requirements

Before using the contract on Hashmarks, qualification must prove at least:

1. a matching test import without an execution receipt cannot become READY;
2. a passing receipt for a different source identity cannot become READY;
3. a stale provider proof cannot become READY;
4. an indirect-only proof produces TEST_STRENGTHENING_REQUIRED, not READY;
5. a failed pre-edit test cannot become READY;
6. missing broader repository gates remains explicit even when focused tests pass;
7. line/branch coverage percentages, if supplied as evidence, cannot independently promote readiness;
8. all declared boundaries with fresh direct proof plus passing bound execution can become READY;
9. changing source, boundary declarations, selected tests, or execution receipt changes the evidence identity;
10. no status claims that the proposed refactor itself is safe or behavior-preserving before post-edit verification;
11. passing tests and a passing execution receipt bound to a different target cannot become READY, even when every other field is valid.

### BP2 consequence

Only after BP1 is qualified should a split packet be added. The split packet should reference the BP1 evidence identity rather than copying repository intelligence. That keeps the sequence explicit:

`current debt → target-bound test ownership → pre-edit executable behavior proof → external edit → post-edit verification → fresh debt`.
