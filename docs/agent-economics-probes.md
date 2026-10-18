# Agent Economics Probes

Agent Economics Probes are optional, portable helpers for deciding where a coding agent should spend its next context, evidence, tool-call, and verification budget.

They are deliberately narrower than an agent framework:

- probes may discover, measure, rank, bound, and explain;
- probes do not edit target repository source code;
- the host runtime still owns execution, permissions, sandboxing, sessions, and persistence;
- repository-specific assumptions should be parameters or unambiguous derivations, not hardcoded policy.

The first reference probe is **`refactor-focus`**. It identifies oversized Python refactoring candidates and separates direct source/test evidence from weaker conventions and heuristics.

## Portable use

The implementation uses only the Python standard library and its own package modules.

From this repository:

```bash
python -m scripts.agent_economics.refactor_focus_cli \
  --source-root src/my_package \
  --tests-root tests \
  --repository-root . \
  --package-name my_package \
  --tests-package-name tests \
  --artifact-path .agent-artifacts/refactor-focus.json
```

If the `agent_economics/` directory is copied into another ChatGPT or coding-agent environment, run it from the directory that contains the package:

```bash
python -m agent_economics.refactor_focus_cli \
  --source-root src/my_package \
  --tests-root verification \
  --repository-root . \
  --package-name my_package \
  --tests-package-name checks
```

`--repository-root`, package names, output path, file/function thresholds, top-N selection, source-transitive depth, test-helper depth, and pytest ownership depth are configurable. `--ownership-hints-path` optionally supplies explicit repository-relative source/test relationships. The source package name can be derived from the source-root basename when that is correct for the target repository.

## Evidence authority

`refactor-focus` does not treat every match as equivalent.

| Authority | Current evidence | May establish corresponding-test authority? |
| --- | --- | --- |
| `confirmed` | exact static import; literal dynamic import; bounded helper chain; active pytest fixture; loaded pytest plugin module-scope dependency; explicit repository ownership declaration | yes |
| `supporting` | mirrored path; same-name convention; bounded transitive owner | no |
| `candidate` | path-feature overlap | no |

When the same test is found by more than one mechanism, stronger evidence wins. For example, an exact import overrides a same-name match for that test file.

Only confirmed evidence can set `has_corresponding_tests`, `test_sync_required_if_split`, confirmed test-size metrics, or actions such as updating/splitting an existing corresponding test. Supporting and candidate evidence instead tell the agent what it should inspect next.

## P2 qualification

Run the built-in stdlib-only adversarial corpus with:

```bash
python -m scripts.agent_economics.refactor_focus_p2_qualification
```

The corpus materializes a temporary repository and exercises:

- exact source imports;
- helper-loader ownership;
- package `__init__.py` identity;
- stronger exact evidence overriding same-name evidence;
- mirrored-path and direct-name conventions without imports;
- bounded transitive ownership;
- heuristic feature matching;
- same-name false-authority pressure;
- unrelated negative cases;
- a literal `importlib.import_module()` relationship;
- non-default source and test package names.

Qualification records `TRUE_RELEVANT`, `FALSE_RELEVANT`, `MISSED_RELEVANT`, `UNKNOWN`, and true-negative counts plus precision, recall, bounded-candidate reduction, evidence reduction, runtime, and exact probe economics.

The gate fails if a non-authoritative signal becomes confirmed authority, if a relationship that the probe claims to support is missed, or if a discovered Python file is read or AST-parsed more than once in a probe run. P2 independently instruments `Path.read_bytes` and `ast.parse`; it does not trust the probe's own counters as proof.

Each probe artifact now includes an `economics` object with exact Python-analysis `files_read`, `bytes_read`, `ast_parses`, `read_failures`, `parse_failures`, `cache_hits`, `unique_files_cached`, `elapsed_ms`, `candidate_reduction`, `evidence_files_selected`, `evidence_lines_selected`, and active depth bounds. Auxiliary ownership-hint reads are reported separately and folded into `total_files_read` / `total_bytes_read`.

## P3 Python/pytest ownership

P3 adds bounded structural ownership recovery without promoting conventions into facts:

- exact static imports and literal `importlib.import_module(...)` / `__import__(...)`;
- test-helper to helper traversal bounded by `--helper-max-depth`;
- ancestor `conftest.py` fixtures, autouse fixtures, fixture dependencies, `usefixtures`, and literal `request.getfixturevalue(...)`;
- repository-local `pytest_plugins` traversal bounded by `--pytest-max-depth`;
- plugin fixtures are authoritative only when active; imports inside unused plugin fixtures do not become plugin-wide ownership;
- optional versioned JSON ownership hints using repository-relative paths; stale, absolute, or escaping declarations fail closed;
- provenance is attached to every match so another agent can inspect why evidence was admitted.

Run the P3 qualification with:

```bash
python -m scripts.agent_economics.refactor_focus_p3_qualification
```

Runtime-built dynamic module names remain unknown rather than guessed. Supporting filename/path conventions and candidate token overlap remain non-authoritative.

## Common probe contract

Every probe artifact now uses the same versioned `agent-economics-probe` v1 envelope:

```text
schema
tool
generated_at
repository
configuration
evidence
derived
interpretation
uncertainty
warnings
candidates
required_next_evidence
deferred_evidence
verification_suggestions
economics
```

The contract deliberately separates **measured facts**, **derived state**, **interpretation**, and **recommendations**. A candidate recommendation such as a refactoring strategy cannot appear in its `facts` object. Confirmed source/test evidence can yield focused verification suggestions; supporting or ambiguous evidence yields explicit uncertainty plus the next evidence the agent should obtain. Candidates omitted by a bounded `top_n` budget are preserved as `deferred_evidence` rather than disappearing silently.

Repository identity is a SHA-256 over the analyzed repository inputs and is independent of the checkout path. Configuration identity is a SHA-256 over semantic probe configuration; changing the artifact destination does not change it, while changing a depth/budget/threshold does. Ownership-hint bytes, when configured, participate through their content identity. Paths in the portable contract are repository-relative.

Run the P4 contract qualification with:

```bash
python -m scripts.agent_economics.probe_contract_qualification
```

P4 qualification also reruns the earlier probe behavior indirectly and checks contract validation, identity invalidation/stability, facts-versus-recommendations separation, deferred-evidence accounting, uncertainty requirements, and verification suggestions.

## Current capability map

The probe family is no longer only a refactor experiment. The current public surfaces are:

- evidence selection: `refactor-focus`, `context-focus`, `test-focus`, `change-impact`, `coupling-focus`, and `hotspot-focus`;
- analyzer evidence: `quality-debt`;
- structural-change evidence: `refactor-locality`, which keeps locality dimensions visible and rejects size-only decomposition authority;
- local capability/execution: `capabilities`, `run-command`, and `qualify-local`;
- measurement: `benchmark-outcomes` and `dogfood-corpus`.

All source-analysis probes preserve evidence authority and bounded economics. Execution commands are separately authorized through the manifest and never gain source-edit authority.

See [Agent Economics Probe phases](agent-economics-probe-phases.md) for the completed hardening sequence and [the dogfood gate](agent-economics-dogfood.md) for the remaining empirical validation boundary.


## Repository discovery semantics (P5)

Repository discovery is part of probe evidence, not an invisible filesystem assumption. `refactor-focus` accepts `auto`, `git`, or `filesystem` discovery; tracked/untracked and ignored-file policy; explicit Python-file symlink policy; repeatable repository-relative exclusion globs; optional default exclusions; and a bounded Git command timeout. `auto` prefers Git repository truth and reports when it must fall back to filesystem discovery. The effective discovery policy is included in configuration identity and discovery observations are included in the evidence envelope.


## Context focus (P6)

`context-focus` answers a narrower question than a repo map: **what should the agent inspect next for this task within this explicit budget?** It discovers configurable source/text suffixes through the P5 repository policy, then performs a bounded lexical fallback scan and emits transparent ranking evidence plus explicit uncertainty. The selection budget has independent file, line, byte, and estimated-token ceilings; the repository scan has separate file/byte/per-file ceilings so a large repository cannot silently consume the entire agent budget.

Optional `--repository-intelligence-path` accepts a versioned provider-neutral JSON shortlist. That is the integration seam for Hashmarks or another repository-intelligence producer. External scores are supporting ranking evidence only: they cannot bypass discovery policy, stale paths are ignored with warnings, and the auxiliary JSON itself is excluded from context candidates. The output uses the common Agent Economics Probe contract from P4.


## Test focus (P7)

`test-focus` answers **what is the cheapest defensible verification ladder for these changed paths?** It reuses confirmed source/test ownership from the refactor-focus analyzers and separates direct owning tests from tests belonging to bounded reverse dependents. Naming/path conventions remain supporting investigation evidence only. Changed test files are directly suggested; non-Python or unresolved changes require repository gates or stronger provider evidence rather than guessed focused tests.

Use repeatable `--changed-path`, optional `--changed-paths-file`, explicit traversal/test-selection bounds, and repeatable `--gate NAME=COMMAND`. Gates are suggestions for the external agent or repository authority to execute; this probe never executes them and never treats a focused green set as proof that broader validation is unnecessary.


## Change impact (P8)

`change-impact` answers **what source files are structurally reachable upstream of these changed Python modules?** The fallback authority is intentionally narrow: current Python import relationships under the configured source/package root. It reports direct and transitive depth, the module chain that produced the relationship, bounded omissions, and uncertainty for non-Python or absent changed source. Static reachability is evidence for investigation scope, not proof of runtime behavior or permission to edit dependent files.


## Coupling focus (P8)

`coupling-focus` answers **what files repeatedly changed with these target paths in bounded Git history?** It is historical correlation only. The artifact keeps shared commit count, target/candidate commit counts, coverage ratios, and Jaccard separate and always marks dependency authority false. First-parent history is the default; merge behavior, history length/output bytes, mega-commit ceiling, suffix filtering, exclusions, support threshold, sample count, top-N, and Git timeout are explicit parameters.


## Hotspot focus (P9)

`hotspot-focus` answers **which source files deserve the next investigation budget, and why?** It keeps source size/control-flow, static fan-in/fan-out, bounded Git churn, anonymized author concentration, and confirmed test ownership as independent facts. Ranking is configurable and lexicographic; there is no hidden composite score and the ordering never authorizes an edit.

Git history can be `auto`, `required`, or `disabled`. Missing history and missing test-tree evidence are represented as unknown, not as reassuring zeros. Use `python -m scripts.agent_economics hotspot-focus --source-root src/pkg --tests-root tests --package-name pkg`.


## Refactor locality preservation

A maintainability hotspot is permission to investigate, not permission to decompose. `refactor-locality` compares explicit pre/post (or pre/proposed) evidence for one semantic authority without an opaque score. It keeps file fan-out, symbol fan-out, navigation depth, forwarding-only layers, context footprint, verifier files, edit surface, evidence surface, and cross-file reach separate. Introduced symbols with zero or one observed exact caller are surfaced as `limited_observed_reuse_structures`: this is a review signal only, because exact callers are a positive lower bound and do not prove global single-use.

The decision contract has four outcomes: `KEEP_COHESIVE_AUTHORITY`, `DECOMPOSITION_JUSTIFIED`, `DECOMPOSITION_LOCALITY_RISK`, and `INSUFFICIENT_LOCALITY_EVIDENCE`. A drop from 225 lines to 16 lines is not justification by itself. New structure must earn its existence: each introduced helper/module/layer carries a structure kind plus an evidence-bound structural value. Wrapper, shim, adapter, facade, proxy, delegate, re-export, manager, service, and forwarding-helper shapes are treated as easy-path structures and remain visible even when justified. Size-only evidence keeps the cohesive authority. A decomposition that increases hard locality costs such as files, forwarding-only layers, context/evidence/edit surface, or cross-file reach is a locality risk even if line/branch counts improve. Same-file semantic extraction may expose a navigation tradeoff and can be justified only by independent non-size evidence such as mixed responsibilities, duplicated authority, ownership ambiguity, hidden side effects, or change-isolation failure, plus explicit evidence accepting the bounded locality tradeoff. Merely moving statements into private helpers without evidence-bound semantic value is classified as unearned structure; a low observed exact-caller count is not used to prove global single-use. Forwarding-only layers must own an evidence-bound external compatibility/protocol boundary; otherwise they are locality regressions. Exact caller evidence and forwarding shape come from Hashmarks, not from agent-authored claims; ambiguous Hashmarks call edges make the locality evidence incomplete rather than being promoted to reuse. Exact caller counts are positive lower-bound evidence, but `shared_reuse` specifically requires `>=2` exact callers outside the Hashmarks verifier set. Test/verifier callers remain verification evidence and cannot manufacture semantic reuse. `0` or `1` observed caller still cannot prove global single-use because aliased/dynamic callers may be outside the exact observed set.

This evidence is deliberately separate from BP1/BP2 behavior preservation and quality-debt reduction. Passing tests prove behavior; debt delta proves measured debt movement; locality evidence addresses whether the structural change made future agent understanding and safe editing more or less expensive. A decomposition may reach `DECOMPOSITION_JUSTIFIED` only when both pre/post locality snapshots are backed by fresh, identity-valid Hashmarks structural-locality packets **and** each packet is bound to an observed bounded Hashmarks execution. The receipt binds exact argv/target/bounds, Hashmarks producer implementation identity, packet/repository identities, output identities, and unchanged Git-tracked workspace bytes. Manual counts, packet-only JSON, and caller-supplied saved receipt bundles remain useful diagnostics but are not independent structural authority; a saved receipt cannot self-promote.

## Capability bridge boundary

P10 adds a constrained local capability helper for environments such as ChatGPT that have repository bytes but lack convenient command execution/evidence tooling. It requires Python 3.11+. The bridge accepts versioned TOML manifests containing argv arrays, never implicit shell strings. Selecting a manifest/command is explicit execution authorization. Command environment overrides are explicit manifest inputs; the runner owns a noninteractive `TERM=dumb` default unless explicitly overridden. Runtime-environment identity participates in command identity without publishing raw values. Execution receipts keep child-process outcome, harness/postflight outcome, and external-controller observability separate: a child `return_code=0` does not become authoritative evidence when postflight workspace checks fail, and timeout/missing-executable/output-limit/harness failures remain incomplete evidence rather than product failures.

The bridge is not a sandbox: child commands inherit host filesystem/environment/network authority unless the host isolates them. It reports process-tree, mutation-guard, sandbox, and network capabilities truthfully. It never edits source, installs dependencies, promotes focused verification into repository authority, or replaces CI/CD.

Local staged statuses are `FOCUSED_PASS`, `AFFECTED_PASS`, `LOCAL_QUALIFIED`, `LOCAL_INCOMPLETE`, and `LOCAL_FAILED`. Every local receipt keeps CI status separate.


## P12 — Quality Debt Evidence

P12 adds a small analyzer-derived debt probe. Ruff is the first adapter. It consumes the analyzer's JSON rather than reproducing analyzer rules, runs through the shared bounded subprocess primitive, binds source/configuration/analyzer identities, and reports raw observed limits/excess separately from baseline interpretation.

Debt reduction is not architectural closure. The behavior-preservation debt-delta contract requires matching post-edit refactor-locality evidence before it can return `VERIFIED`: an in-place cleanup must preserve/improve locality without introducing structure, while an edit that introduces helpers/layers must carry a `DECOMPOSITION_JUSTIFIED` decision from independent Hashmarks-backed evidence. Missing or unreviewed locality returns `LOCALITY_REVIEW_REQUIRED` even when Ruff debt falls and behavior tests pass.

Baseline states are `NO_BASELINE`, `INCOMPARABLE_BASELINE`, `NEW`, `INCREASED`, `UNCHANGED`, `REDUCED`, and `RESOLVED`. A global reduction never hides a per-file increase. Measurement remains separate from repository policy.

P12 also extends hotspot evidence with largest-definition facts, local qualification with explicit execution economics, repair packets with quality-debt evidence, and P11 records with optional bridge/manifest/local-qualification/final-source/CI receipt identities plus freeze-before-oracle protocol evidence.


## Qualification before contribution

The permanent repository workflow compiles the package and runs the complete deterministic/adversarial qualification chain plus a bounded stress layer. The stress layer repeats stable semantic identities, exercises independent stdout/stderr hard ceilings, non-UTF8 diagnostics, the 128-iteration loop-state boundary, and repeated timeout/process-tree termination. The job has a 20-minute outer safety window; that does not widen individual subprocess limits.

This qualification proves implementation contracts. It does **not** prove that an agent saves time or context in real repair work. That claim requires the paired real-agent dogfood protocol, frozen outcomes, and independent CI comparison described in `agent-economics-dogfood.md`.
