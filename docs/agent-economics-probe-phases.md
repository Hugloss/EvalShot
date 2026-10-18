# Agent Economics Probes — Hardening and Expansion Phases

## Goal

Make the AgentCookbook probe family portable across ChatGPT coding environments and repositories. Probes should cheaply identify where an agent should spend evidence, context, tool-call, and verification budget. They may discover, measure, rank, bound, and explain; they must not edit production code.

## Portability rule

Repository-specific facts must be supplied as parameters or derived from supplied paths when derivation is unambiguous. Do not hardcode application package names, repository roots, test subdirectories, logging frameworks, or project-specific commands into generic probes.

## Phase 0 — P0 correctness and portability closure

Status: **complete** on the Agent Economics Probes branch.

1. Separate confirmed test evidence from heuristic candidate matches.
   - Heuristic `feature_fallback` matches may suggest what to inspect next.
   - They must not set `has_corresponding_tests`, `test_sync_required_if_split`, confirmed test size, or test-splitting recommendations.
   - Ambiguous-only matches must require correspondence verification before a source/test action is recommended.
2. Correct Python package identity for `__init__.py`.
   - `pkg/__init__.py` maps to `pkg`, not `pkg.__init__`.
   - Relative-import resolution must preserve package semantics for `__init__.py`.
3. Replace guessed reporting anchors with repository-relative reporting.
   - Accept `repository_root` explicitly.
   - If omitted, derive a common ancestor from source/test roots.
   - Use one reporting anchor for source and test paths.
4. Remove application-specific assumptions.
   - No hardcoded `app` package in import analysis.
   - No hardcoded `tests/unit` lookup.
   - No dependency on an application's logging utilities.
   - CLI accepts source root, tests root, repository root, package names, thresholds, output path, top-N, and transitive depth.
5. Add P0 adversarial qualification cases.
   - custom non-`app` package;
   - `__init__.py` imported as its package;
   - weak feature-only test match cannot prescribe a split;
   - source/test report paths use the same repository-relative anchor;
   - same-name tests may live outside `tests/unit`.

Exit criteria: every P0 adversarial case passes and the scripts compile with only Python stdlib plus their own probe modules.

## Phase 1 — Qualification corpus and evidence authority

Status: **complete** on the Agent Economics Probes branch.

Build a repository fixture corpus with known positive, negative, ambiguous, and unsupported relationships. Track `TRUE_RELEVANT`, `FALSE_RELEVANT`, `MISSED_RELEVANT`, `UNKNOWN`, and explicit true negatives. Measure precision, recall, candidate reduction, evidence reduction, runtime, a lower bound on files parsed, and a lower bound on bytes read. Fail qualification when false authority is introduced even if unit tests pass.

P1 also establishes three evidence-authority classes:

- `confirmed`: direct import or explicit helper/loader relationship;
- `supporting`: mirrored/name convention or bounded transitive ownership;
- `candidate`: heuristic path-feature overlap.

Only `confirmed` evidence may set corresponding-test authority or prescribe confirmed-test actions. P1 intentionally deferred exact read/AST invocation accounting to Phase 2.

## Phase 2 — Probe economics and parse-once analysis

Status: **complete** on the Agent Economics Probes branch.

Read and AST-parse each discovered Python file at most once per probe run, then reuse one analysis cache for line counts, import extraction, dynamic-loader inspection, and function-size analysis. Publish exact economics in every probe artifact: files read, bytes read, AST parses, read/parse failures, cache hits, unique cached files, elapsed time, candidate reduction, selected evidence files/lines, and the explicit transitive-depth bound.

Qualification independently instruments `Path.read_bytes` and `ast.parse` so repeated reads/parses fail the gate even if the probe's own counters are wrong. The P2 corpus requires one read and one AST parse per discovered Python file, exact byte accounting, zero read/parse failures on the valid corpus, and exact bounded evidence-line accounting.

## Phase 3 — Python/pytest ownership closure

Status: **complete** on the Agent Economics Probes branch.

Extend test evidence without pretending heuristics are facts. P3 adds parameterized, bounded support for test-helper chains, ancestor `conftest.py` fixtures, autouse fixtures, fixture dependencies, literal `request.getfixturevalue`, repository-local `pytest_plugins`, active plugin fixtures, literal dynamic imports, and versioned repository ownership hints. Every admitted relationship carries provenance. Runtime-computed module names remain unknown. Unused fixtures, depth-overflow helper/plugin chains, and naming/path heuristics cannot become confirmed ownership. Ownership-hint paths must be repository-relative, discovered files; stale, absolute, or escaping declarations fail closed. Auxiliary hint-file reads are reported separately in probe economics and included in total read/byte accounting.

P3 qualification requires all expected structural relationships to be confirmed, zero false authority across bounded/unused negative cases, P2 parse-once qualification to remain green, and all invalid ownership-hint cases to fail closed.

## Phase 4 — Common Agent Economics Probe contract

Status: **complete** on the Agent Economics Probes branch.

All probe artifacts use the versioned `agent-economics-probe` v1 envelope. The common sections are schema/tool identity, portable repository identity, semantic configuration identity, evidence, derived facts, interpretation, uncertainty, warnings, bounded candidates, required next evidence, deferred evidence, verification suggestions, and economics. Candidate facts, derived state, interpretation, and recommendations are separate objects so an agent cannot accidentally treat a recommendation as measured repository truth.

Repository identity is a checkout-location-independent SHA-256 over analyzed repository inputs. Configuration identity is a canonical SHA-256 over semantic probe configuration and excludes the artifact destination. Optional ownership-hint content identity is included in configuration identity. P4 qualification proves schema validity, identity stability and invalidation, repository-relative paths, deferred-evidence accounting, facts/recommendation separation, uncertainty-to-next-evidence linkage, and verification suggestions for confirmed ownership. P1–P3 qualification must remain green under the v1 envelope.

## Phase 5 — Repository discovery semantics

Status: **complete** on the Agent Economics Probes branch.

P5 replaces recursive path-fragment scanning with a parameterized repository discovery authority. `auto` prefers the Git worktree when available; explicit `git` and `filesystem` modes are also supported. Git discovery has separate tracked/untracked and ignored-file policy, lists the repository once for all requested roots, and records the backend and bounded command count in the artifact. Exclusions are normalized repository-relative segment globs, so patterns cannot silently cross directory boundaries and `docs/_build/**` now behaves as written. Python file symlinks have explicit `exclude`, `reject`, or `within-repo` policy; escaping symlinks and discovery roots outside the repository fail closed. Auto mode uses a deterministic filesystem fallback when Git is unavailable and publishes that loss of Git authority as a warning.

P5 qualification covers Git tracked/untracked/ignored semantics, one-listing economics, normalized and anchored exclusions, the historical `docs/_build` case, symlink policy, out-of-repository and nested-root failures, explicit Git failure outside a worktree, filesystem fallback, and discovery-policy/repository-identity invalidation in the common P4 contract. P1–P4 qualification must remain green.

## Phase 6 — `context-focus`

Status: **complete** on the Agent Economics Probes branch.

Given a task/query, P6 returns a bounded ranked evidence set without editing repository files. The stdlib-only fallback is language-neutral across configurable code/text suffixes and reuses P5 Git/filesystem discovery. Ranking components remain visible: path-token overlap, basename overlap, content-token evidence, bounded occurrence evidence, and optional provider-neutral repository-intelligence support. Selection is independently bounded by files, lines, bytes, and an explicitly labeled token estimate; repository scanning is separately bounded by files, bytes, per-file bytes, and evidence anchors.

Provider-neutral intelligence JSON can carry stronger repository facts from systems such as Hashmarks without creating a runtime dependency or granting execution authority. Stale intelligence paths are warnings; auxiliary intelligence and output artifacts cannot become context candidates themselves. Scan truncation, filesystem fallback, no-match results, read/stat failures, and oversized files remain explicit uncertainty/warnings rather than silently widening the scan. P6 also generalizes P5 discovery to configurable suffix sets while preserving the Python-specific wrapper used by refactor-focus.

P6 qualification requires multi-language ranking, strict context and scan budgets, external-intelligence vocabulary-mismatch rescue, stale-hint handling, checkout-independent repository path-set identity, task/config invalidation, auxiliary-input self-exclusion, bounded intelligence input, common P4 contract validity, and continued P1–P5 qualification.

## Phase 7 — `test-focus`

Status: **complete** on the Agent Economics Probes branch.

Given changed repository paths, P7 builds a bounded staged verification ladder without executing tests. Stage 1 contains changed tests plus tests with confirmed direct ownership of changed Python source. Stage 2 contains confirmed tests of bounded reverse source dependents. Stage 3 contains explicit repository/component gates supplied by the caller. Mirrored paths and same-name conventions remain supporting evidence only and can never become direct-test authority. Non-Python, deleted, undiscovered, or otherwise unmapped changes publish uncertainty and required next evidence instead of fabricated test ownership.

Focused verification suggestions never assert that broader verification is unnecessary. If no broader gates are supplied, the contract records that missing escalation boundary explicitly. Test selection and reverse-impact traversal have independent bounds; omitted tests/sources are preserved as deferred evidence. P7 reuses the P1–P5 ownership, pytest, hints, discovery, parse-once, and common-contract infrastructure rather than maintaining a second authority model.

P7 qualification requires direct-vs-affected separation, naming-only false-authority prevention, changed-test handling, non-Python and deleted-path uncertainty, bounded omission accounting, broader-gate escalation, repository identity stability/invalidation, fail-closed escaping paths, common P4 contract validity, and continued P1–P6 qualification.

## Phase 8 — `change-impact` and `coupling-focus`

Status: **complete** on the Agent Economics Probes branch.

P8 adds two deliberately separate probes. `change-impact` computes bounded reverse reachability over the current Python import graph for changed paths under a parameterized source root. Direct and transitive source dependents retain their explicit depth and module chain. Deleted/renamed Python paths may seed impact lexically while publishing that the current source bytes are unavailable; unsupported-only changes short-circuit without scanning/parsing the Python tree. Source-depth and source-count bounds preserve omitted dependents as deferred evidence.

`coupling-focus` mines bounded Git history for repeated co-change correlation. It exposes raw shared/target/candidate commit counts separately from derived target coverage, candidate coverage, and Jaccard. First-parent history is the default and is parameterized; mega-commits are suppressed by an explicit file-count ceiling; total history output has a hard byte bound; candidate suffixes, exclusions, history length, shared-commit threshold, top-N, commit samples, and Git timeout are all parameters. Co-change always carries `dependency_authority=false` and requires structural/domain inspection before use.

P8 qualification requires reverse-impact depth/budget correctness, deleted-module recovery, zero Python scans for unsupported-only changes, repository identity stability/invalidation, history correlation counts, mega-commit suppression, bounded-history uncertainty, hard history-byte failure, suffix/exclusion policy, fail-closed paths/Git absence, common P4 contract validity, and continued P1–P7 qualification.

## Phase 9 — `hotspot-focus`

Status: **complete** on the Agent Economics Probes branch.

P9 ranks source files for investigation using separately visible facts: source lines, AST branch points, largest function, static fan-in/fan-out, bounded first-parent Git churn, anonymized author concentration, and confirmed test ownership. It never emits an opaque composite risk score. Ranking is an explicit configurable lexicographic dimension order and is labeled investigation priority only.

History has hard commit, byte, and timeout bounds. In `auto` mode unavailable Git history degrades to explicit unknown values rather than zero churn; `required` fails closed; `disabled` supports a static-only run. Missing test-tree evidence likewise remains unknown rather than becoming a false zero-coverage claim. P9 reuses the P1–P8 discovery, parse-once, import, ownership, and common-contract authorities.

P9 qualification requires visible independent dimensions, no opaque score, deterministic lexicographic ranking, static fan-in, bounded churn, anonymized author concentration, confirmed test evidence, static-only unknown-not-zero semantics, hard history-byte failure, common P4 contract validity, and continued P1–P8 qualification.

## Phase 10 — Capability bridge and local repair loop

Status: **complete and hardened** on the Agent Economics Probes branch.

P10 compensates for repository-local execution capabilities an agent host may not expose directly. It adds explicit capability discovery, a versioned TOML argv-only command manifest, repository-contained working directories, a bounded subprocess primitive, named-command execution, structured failure classification, repair evidence packets, exclusive persistent loop state, no-progress/oscillation detection, cumulative iteration/command/output/time budgets, and staged local qualification. Missing executables and unenforced network/sandbox isolation are reported as capability facts rather than hidden assumptions. The bridge never installs dependencies, edits production source, or claims CI/certification authority.

Command mutation protection is bound to a bounded SHA-256 identity over the actual tracked workspace bytes, including symlink payloads and missing tracked paths. Pre/post identities and changed tracked paths make unexpected command mutation a policy failure. The earlier P9 identity was also hardened to bind the test/history evidence used by hotspot ranking, and Git discovery now has a hard stdout ceiling in addition to timeout.

P10 qualification requires manifest/cwd fail-closed behavior, literal argv handling, missing-executable and failure classification, stdout and timeout bounds, no repair authority, no-progress and state-lock behavior, capability honesty, tracked-byte identity invalidation, explicit noninteractive runtime environment, environment-bound command identity, and process-vs-harness outcome separation. Local outcomes are explicitly `LOCAL_QUALIFIED`, `LOCAL_FAILED`, or `LOCAL_INCOMPLETE`; they never become `CI_PASS` or `CERTIFIED`. The bounded-evidence campaign layer can additionally freeze an external controller budget, internal batch timeout, and minimum headroom. Plans without headroom fail before execution; controller timeout and completed-over-budget receipts are `INCOMPLETE`, never product failures, and completed sibling receipts remain reusable.

## Phase 11 — Agent outcome benchmark

Status: **measurement infrastructure and immutable dogfood corpus complete; empirical paired agent runs remain pending**.

P11 adds a bounded paired baseline-vs-bridge outcome format and comparator. It records correctness, CI activations, files opened, evidence bytes, estimated context tokens, tool calls, local commands, repair iterations, focused/broad verification attempts, failed edits, no-progress stops, bridge overhead, and local-vs-CI agreement. Inputs are bounded JSONL records and duplicate/unpaired records fail closed.

The comparator exposes metric totals/deltas and explicit promotion evidence, but `automatic_promotion=false`: benchmark output is measurement rather than release authority. The implementation and top-level `benchmark-outcomes` CLI are qualified. The remaining closure is empirical rather than architectural: collect representative real repair tasks, run both baseline and capability-bridge treatments, and use those observations to decide whether any further capability deserves implementation.


## Phase 12 — Analyzer-derived quality debt

Status: **complete** on the Agent Economics Probes branch.

P12 adds bounded analyzer-derived debt evidence without reimplementing analyzer semantics. The Ruff adapter consumes JSON through the shared bounded subprocess primitive, records analyzer/config/source identities, separates observed values from policy interpretation, and supports comparable no-growth baselines. Changed analyzer versions, roots, limits, or line-ceiling policy make the baseline explicitly incomparable rather than silently comparing unlike measurements. Per-file growth cannot be hidden by a repository-wide reduction.

P12 also exposes largest-definition hotspot facts, local verification economics, and quality-debt evidence in repair packets while keeping all of those dimensions separate from edit or release authority.

## Phase 13 — Dogfood protocol and pre-PR stress closure

Status: **deterministic infrastructure complete; empirical agent pairs remain pending**.

P13 freezes a 12-task adversarial dogfood corpus covering assertion, syntax/import, lint/type, focused-pass→broad-fail, missing executable, output flood, timeout/child tree, non-UTF8, forbidden/allowed mutation, no-progress/oscillation, and unavailable repository-byte identity. Each task has a semantic fixture identity. The protocol requires local repair iterations, freezes outcomes before the oracle is opened, forbids CI as a repair executor, and permits one independent final CI comparison after local evidence is frozen.

The permanent qualification also runs a bounded stress layer: repeated stable commands, independent stdout/stderr flood bounds, non-UTF8 decoding, a 128-iteration loop-state ceiling, and repeated process-tree timeout termination. The GitHub job has a 20-minute outer safety timeout while individual subprocess limits remain smaller.

This phase does not claim empirical economics improvement. Closure of that claim requires real baseline-vs-bridge agent runs on identical fixture bytes.

## Phase 14 — Refactor locality preservation

Status: **implementation and deterministic qualification added; real-repository dogfood follows through consumers such as Oh-Goon**.

P14 closes a methodology defect exposed by real Oh-Goon cleanup: reducing function length/branch counts can make code worse for coding agents when the result introduces wrappers, cross-file fragmentation, forwarding-only layers, deeper navigation, or broader edit/evidence surfaces. Refactor size and complexity are therefore investigation signals only.

The new `refactor-locality` contract records transparent pre/post dimensions with no composite score, binds measurements to repository/source/configuration identities, rejects incomplete or incomparable evidence, and distinguishes cohesive authority from justified decomposition, locality regression, and insufficient evidence. Size-only evidence cannot justify a split. Every newly introduced symbol/layer must bind an allowed structural value and its evidence identity. The qualification explicitly rejects pass-through wrappers/shims/delegates and same-file helper extraction without evidence-bound semantic value when those structures exist only to lower measured complexity. `refactor-focus` no longer recommends wrappers/splits from size/dependency counts and no longer ranks by an opaque risk score; it ranks investigations by visible dimensions and requires locality evidence before decomposition. BP2 remains behavior-preservation evidence and now explicitly requires locality remeasurement rather than implying architectural improvement from green tests or debt reduction. The follow-on provider-binding closure makes Hashmarks structural-locality packets the independent authority for caller counts, forwarding shape, navigation/file/context dimensions, verifier paths, ambiguity, and repository/source identities. Independent authority additionally requires an EvalShot-observed bounded Hashmarks execution receipt bound to the exact target, bounds, producer implementation identity, packet identity, and unchanged tracked workspace bytes. Hand-authored locality snapshots, packet-only JSON, and caller-supplied saved receipt bundles cannot justify a decomposition; the live `observe-hashmarks` path owns provider-execution promotion.

The debt-closure contract now consumes that locality result instead of treating remeasurement as advisory. Analyzer/Ruff reduction cannot become `VERIFIED` without an identity-valid, independently Hashmarks-backed post-edit locality snapshot and matching locality decision. A cohesive in-place cleanup can close when locality is preserved or improved and no new structure was introduced. If the edit introduced helpers/layers, closure requires `DECOMPOSITION_JUSTIFIED`; evidence-bound semantic value alone is not enough without decomposition evidence and any required bounded-locality tradeoff receipt. Newly introduced symbols with zero or one **observed exact caller** are surfaced as limited-observed-reuse review evidence, never as proof of global single-use. `shared_reuse` requires at least two exact non-verifier callers so direct tests cannot inflate reuse evidence.

Qualification includes a 225-line-to-16-line size-only case that must remain `KEEP_COHESIVE_AUTHORITY`, cross-file wrapper/shim/delegate fragmentation and same-file helper clutter that must become `DECOMPOSITION_LOCALITY_RISK`, a bounded same-file semantic extraction that can become `DECOMPOSITION_JUSTIFIED` only when each helper owns evidence-bound semantic value plus an explicit bounded-locality-tradeoff receipt, and a compatibility shim that remains visible as a tradeoff even when an external contract justifies it.
