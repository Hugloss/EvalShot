# Agent Economics package

Portable, standard-library-only probes and a constrained capability bridge that help coding agents decide what evidence to inspect and run repository-declared local verification before editing again.

**Runtime:** Python 3.11+ (the command-manifest reader uses stdlib `tomllib`).

Run the refactor probe from an EvalShot checkout:

```bash
python -m scripts.agent_economics.refactor_focus_cli --source-root src/pkg --tests-root tests --repository-root . --package-name pkg
```

Run its adversarial qualification corpora:

```bash
python -m scripts.agent_economics.refactor_focus_p2_qualification
python -m scripts.agent_economics.refactor_focus_p3_qualification
python -m scripts.agent_economics.probe_contract_qualification
```

The directory can also be copied by itself into another environment. From the copied package's parent directory, use `python -m agent_economics.refactor_focus_cli ...`.

### Start in an unfamiliar repository

After materialization, the first command should normally be:

```bash
PYTHONPATH="$PWD/.agent-economics/scripts" python -m agent_economics doctor --repository-root .
```

\`doctor\` is read-only. It reports Git/Python/Ruff availability, detects likely Python source/test/package roots, and marks probe readiness. Detected roots are suggestions only; ambiguous repository structure remains explicit instead of becoming hidden configuration.

To see those observations projected into one reviewable campaign shape without writing anything:

\`\`\`bash
python -m agent_economics doctor --repository-root . --suggest-profile
\`\`\`

The result is always \`REVIEW_REQUIRED\`. It separates package/test roots from quality-analysis roots, preserves unresolved ambiguity and missing tool supply, and is not repository authority. Agent Economics does not automatically write or adopt the suggestion.

The package root is also a real CLI front door:

```bash
python -m agent_economics --help
python -m agent_economics help quality-debt
```

The command catalog is canonical for top-level help and capability reporting, preventing those public surfaces from drifting apart.

### First-class import and exact bootstrap

Agent Economics is a standalone Python package rooted at `scripts/agent_economics`. Consumers do not need repository-specific Python wrappers. Put the parent directory `scripts` on `PYTHONPATH`, then use either the package CLI or normal imports:

```bash
PYTHONPATH=/path/to/EvalShot/scripts python -m agent_economics test-focus ...
```

```python
from agent_economics.test_focus import GateSpec, test_focus_audit
from agent_economics.quality_debt import quality_debt_audit
```

For CI or another repository, materialize an exact EvalShot commit with the fail-closed bootstrap:

```bash
sh /path/to/bootstrap-agent-economics.sh \
  --revision <FULL_COMMIT_SHA> \
  --destination .agent-economics
export PYTHONPATH="$PWD/.agent-economics/scripts"
python -m agent_economics test-focus ...
```

The bootstrap never follows a branch tip or silently replaces an existing destination. It fetches the requested commit into a detached checkout, verifies the resolved commit, verifies the package exists, removes a partial destination on failure, and prints `AGENT_ECONOMICS_REVISION`, `AGENT_ECONOMICS_ROOT`, and `AGENT_ECONOMICS_PYTHONPATH` for receipts. `AGENT_ECONOMICS_ROOT` and `AGENT_ECONOMICS_REPOSITORY` can provide default destination/repository values.

CI also emits a standalone qualified artifact named `agent-economics-<full-commit-sha>.zip` from the exact commit exercised by the `agent-economics` qualification job. The archive contains the standalone package, bootstrap, dogfood protocol, and a machine-readable `qualification-receipt.json` binding its file digests to that implementation identity. The workflow publishes the ZIP together with its SHA-256 sidecar. This is transport/supply evidence for restricted consumers; it does not replace the consumer repository's independent qualification authority and is not a PyPI release contract.

A consumer-specific adapter is only needed when the repository must prove parity with an existing repository-owned authority or translate a repository-specific evidence schema. It is not required to run or import Agent Economics.

See `docs/agent-economics-probes.md` in the repository for the evidence-authority contract and portability rules.

The JSON artifact uses the versioned `agent-economics-probe` v1 contract. Facts, derived state, interpretation, recommendations, uncertainty, required/deferred evidence, verification suggestions, and economics are separate sections. Repository and semantic configuration identities are deterministic and portable across checkout locations. Exact per-run Python analysis economics include files/bytes read, AST parses, cache reuse, elapsed time, candidate reduction, and selected evidence lines, plus separately accounted auxiliary ownership-hint reads when configured.


### P5 discovery portability

`refactor-focus` defaults to `--discovery-mode auto`, which uses Git tracked plus non-ignored untracked Python files when the supplied repository root is a Git worktree root. Use `--untracked-policy exclude` for tracked-only analysis, `--ignored-policy include` only when ignored untracked files are intentionally evidence, `--symlink-policy exclude|reject|within-repo`, and repeat `--exclude-path` for repository-specific exclusions. `--no-default-excludes` disables the portable defaults. Explicit `filesystem` mode never pretends to apply `.gitignore`; that limitation is emitted in the probe warnings.


### P6 context-focus

Run `python -m scripts.agent_economics context-focus --task "..." --repository-root .`. The command is stdlib-only and accepts repository-relative roots, configurable suffixes, P5 discovery policy, independent scan/context budgets, and optional provider-neutral repository-intelligence JSON. Relative output and intelligence paths are anchored to the repository root. Existing refactor-focus invocation remains backward compatible: `python -m scripts.agent_economics ...` still routes arguments without a subcommand to refactor-focus.


### P7 test-focus

Run `python -m scripts.agent_economics test-focus --source-root src/pkg --changed-path src/pkg/foo.py --gate 'package=pytest -q'`. Changed paths, source/test roots, package names, ownership hints, discovery policy, reverse-impact depth/source limits, per-stage test limits, changed-input identity byte bounds, and broader gates are all parameters. `test-focus` only suggests verification order; it does not execute tests or waive broader repository validation. Exact first-party static façade re-exports are followed back to one unambiguous top-level defining symbol for direct ownership; ambiguous, cyclic, dynamic, or non-defining re-export chains remain unclaimed.


### P8 change-impact

Run `python -m scripts.agent_economics change-impact --source-root src/pkg --changed-path src/pkg/foo.py`. Source/package roots, changed paths, reverse-depth/source bounds, P5 discovery policy, exclusions, symlink policy, and Git timeout are parameters. Unsupported-only changes deliberately avoid a repository-wide Python scan.


### P8 coupling-focus

Run `python -m scripts.agent_economics coupling-focus --target-path src/pkg/foo.py`. Git history length and output bytes are hard-bounded; mega-commit size, shared-commit threshold, candidate suffixes, exclusions, sample count, top-N, first-parent/all-parent mode, and timeout are parameters. Historical co-change is never dependency authority.


### P9 hotspot-focus

Run `python -m scripts.agent_economics hotspot-focus --source-root src/pkg --tests-root tests --package-name pkg`. Visible dimensions include source size, branch points, largest function, static fan-in/fan-out, bounded Git churn, anonymized author concentration, and confirmed test ownership. Ranking is configurable lexicographic investigation priority only; unavailable history/test evidence remains unknown rather than zero.


### P10 capability bridge

Use `python -m scripts.agent_economics capabilities --repository-root . --manifest agent-economics.toml` to inspect what the local environment can actually execute. Repository commands are declared as argv arrays in a version-1 TOML manifest and run with `run-command`; arbitrary shell strings are not the command authority. The runner hard-bounds time/stdout/stderr, keeps cwd inside the repository, records stable command/failure identities, and compares bounded SHA-256 identities of tracked workspace bytes before and after commands. Runtime environment overrides are explicit command inputs; `TERM=dumb` is the default noninteractive contract unless the manifest overrides it, and the environment identity participates in command identity without exposing raw values. Process outcome, harness/postflight outcome, and external-controller observability are separate. A child process returning zero cannot become authoritative when postflight/workspace checks fail. Timeouts, missing executables, output ceilings, and harness failures are incomplete evidence, not product failures. Unexpected tracked mutation fails policy. `qualify-local` supports staged local verification with exclusive loop state, cumulative budgets, and no-progress/oscillation stops. Local qualification never claims CI or certification authority.

### P11 outcome benchmark

Use `python -m scripts.agent_economics benchmark-outcomes --input outcomes.jsonl` with paired `baseline` and `bridge` JSONL records. The comparator measures correctness plus repository-owned independent-qualification activations, evidence/context consumption, tool calls, commands, iterations, verification attempts, failed edits, no-progress stops, bridge overhead, and local-vs-independent-qualification agreement. Benchmark results are evidence only and never automatically promote a workflow.

For empirical closeout, pre-admit each pair before measurement: independently verify the shared immutable starting authority, use that authority itself as `initial_source_id` (an archive-backed task uses its verified archive SHA-256 rather than a session-local extracted-tree hash), freeze equivalent repository/tool/dependency/network/controller supply into `execution_environment_id`, prove the repository-owned independent qualification authority is obtainable, and materialize the exact bridge implementation plus frozen command manifest before bridge evidence collection. Setup-incomplete attempts remain setup evidence rather than empirical outcomes. The final bridge local-qualification receipt is still produced during the admitted run and remains mandatory for strict closeout. See `docs/agent-economics-dogfood.md`.

For empirical closeout, valid individual pairs are not enough. Run `python -m scripts.agent_economics dogfood-campaign --outcomes outcomes.jsonl --campaign campaign.json`. Campaign schema v2 is repository-neutral: the manifest declares its own scenario IDs/cardinalities, minimum repository diversity, exact task membership, measurement contract, isolation/freeze/oracle receipts, cleanup evidence, and producer qualification. Each pair binds the consumer repository's own independent qualification authority and evidence. EvalShot does not hardcode Hashmarks, Oh-Goon, GitHub CI, `make certify`, or any other consumer authority.


### Trust and isolation

A command manifest is executable-repository configuration. Passing `--manifest` and selecting a command is explicit authorization to execute that argv array. Commands are never parsed as shell strings, cwd is confined to the repository, time/output are bounded, and tracked-byte mutation is observed. The child still inherits whatever filesystem, environment, credentials, and network authority the host gives it. Agent Economics is not a sandbox and never reports network/sandbox isolation unless the host actually provides it.

`qualify-local` distinguishes focused, affected, component, and repository stages. Focused/affected passes remain incomplete; only an executed repository-stage pass can yield `LOCAL_QUALIFIED`, and every local result reports `ci_status=NOT_RUN`.


### quality-debt

`quality-debt` measures configured analyzer debt without making policy decisions. The first adapter consumes Ruff JSON diagnostics through the shared bounded-process primitive; it does not reimplement Ruff's complexity semantics.

```bash
python -m scripts.agent_economics quality-debt \
  --repository-root . \
  --root src --root scripts \
  --exclude scripts/retained \
  --limit C901=10 --limit PLR0912=12 \
  --max-file-lines 1200 --file-line-root src \
  --artifact .agent-artifacts/quality-debt.json
```

Repeat `--exclude` for repository-relative path prefixes that are outside the admitted analyzer evidence. Exclusions apply consistently to analyzer invocation, analyzed-source identity, findings, and file-length evidence. `--file-line-root` independently scopes the line ceiling when only production roots own that policy; without it, the ceiling applies to every `--root`.

A baseline comparison is valid only when analyzer/version, roots, exclusions, limits, and file-line policy have the same comparable identity. Otherwise the result is `INCOMPARABLE_BASELINE`. Per-file increases remain visible even when repository-wide debt falls. Baselines are measurement evidence, not acceptance or certification authority.

The artifact also exposes `evidence.detailed_findings` as deterministic machine-readable rows with `path`, `line`, `rule`, `observed`, `limit`, and `excess`. `derived.summary.files` remains the per-file aggregation, so a low-context consumer can rank current hotspots without reparsing analyzer messages or opening candidate files first. These measurements prioritize investigation; they do not authorize edits or change repository policy. Each debt candidate therefore publishes `required_next_evidence.kind=test_focus` for its exact target instead of pretending that a high debt value is sufficient edit evidence. Run `test-focus` with that target as the changed path and the repository's source/test roots to recover confirmed/supporting test ownership and affected verification before selecting an edit.

Hotspot evidence also reports the largest definitions (functions/classes with qualified names and line spans), while keeping those dimensions independent rather than creating a composite quality score.

`qualify-local` receipts expose cumulative and per-stage execution economics so P11 dogfood can consume actual local command cost rather than reconstructing it manually.


### refactor-locality

Use `refactor-locality` before and after a proposed structural decomposition. It records visible locality dimensions rather than collapsing them into a score: symbol count, file fan-out, navigation depth, forwarding-only symbols, context lines, verifier files, edit files, evidence files, and cross-file symbol count. Structural signals such as authority lines, branches, and nesting remain separate.

A large function is an investigation signal, not decomposition authority. Size-only evidence yields `KEEP_COHESIVE_AUTHORITY`. **Every introduced structural layer must earn its existence**: a new helper, module, wrapper, shim, adapter, facade, manager, service, or forwarding layer must bind a concrete value such as semantic/policy ownership, validation/data-contract ownership, side-effect or resource-lifetime isolation, duplicated-authority removal, shared reuse, a direct test seam, change isolation, or a required external compatibility/protocol boundary. Cross-file fragmentation, extractions without evidence-bound semantic value, forwarding layers, or larger edit/evidence surfaces can yield `DECOMPOSITION_LOCALITY_RISK` even when the public entry point becomes much smaller. Prefer deletion, consolidation, in-place simplification, and removal of alternate paths before adding another abstraction. A pass-through wrapper is not a value by itself; forwarding is accepted only when evidence binds it to a real external compatibility or protocol boundary. Hashmarks exact-caller evidence is a conservative lower bound: two or more exact **non-verifier** callers may substantiate real shared reuse, but verifier/test callers do not count toward `shared_reuse`, and zero or one observed caller must never be treated as proof that a helper is globally single-use. A decomposition can become `DECOMPOSITION_JUSTIFIED` only when non-size evidence identifies a real responsibility/ownership/change-isolation problem and locality evidence does not show a hard regression. The contract never authorizes edits or merges.

`refactor-focus` therefore no longer recommends wrappers or splits from size/dependency counts alone. Its ranking is investigation-only and confirmed candidates require `measure_refactor_locality` as the next evidence before decomposition. For architectural decisions, use `python -m scripts.agent_economics refactor-locality observe-hashmarks path/to/file.py::qualname --repository-root .`. That executes Hashmarks through the bounded process runner, binds the exact command/target/bounds, exact Hashmarks implementation identity, packet identity, and unchanged tracked repository bytes, and returns an observation bundle. When the observed post-edit structure introduced responsibility-owned symbols, pass `--values structural-values.json` on this same live command so repository-bound semantic value evidence is incorporated without losing independent provider-execution authority. `from-hashmarks` can inspect a saved packet/bundle, but saved input remains diagnostic even when it contains a receipt; only the live `observe-hashmarks` path can promote provider execution to independent structural authority.

Introduced symbols with zero or one observed exact caller are explicitly surfaced as limited-observed-reuse structures for review. This must never be described as proven single-use: Hashmarks caller evidence is a positive lower bound. A low-reuse helper may still be valid, but it must earn an independently evidenced semantic boundary instead of existing only to lower Ruff complexity.

A bounded caller-reference observation is authoritative only when every observed symbol reports `caller_reference_bound_complete=true`. A false value is explicit incomplete evidence, even when the bounded sample already contains several exact callers. Raise `--ref-limit` within the producer's supported bounds and re-observe; do not infer reuse, single-use, or decomposition safety from a truncated sample, and do not shrink navigation depth merely to make locality qualify.

### behavior-preservation

Use `behavior_preservation_readiness(...)` before a structural split when the goal is to preserve existing behavior. It binds the exact source identity, declared behavior/risk boundaries, confirmed test references, a PASS execution receipt for those exact source/test identities, provider freshness, and repository-owned broader gates. Every boundary must also name the frozen test evidence identities that prove that boundary; missing bindings or references to tests outside the selected/executed test set fail closed as `EVIDENCE_REQUIRED`.

The result is evidence readiness only: `READY_FOR_BEHAVIOR_PRESERVING_EDIT` never authorizes an edit and never claims that a future refactor is safe. Indirect-only protection yields `TEST_STRENGTHENING_REQUIRED`; stale, failed, mismatched, unexecuted, or incomplete evidence yields `EVIDENCE_REQUIRED`. Coverage percentages may be recorded but cannot independently promote readiness.

After the external edit, rerun the bound focused tests and repository gates, then build `behavior_preservation_receipt(...)`. BP2 still proves behavior preservation only; its post-edit requirements now also call for refactor-locality remeasurement, because preserved behavior plus lower debt does not prove a better architecture. `behavior_preservation_debt_delta(...)` enforces that separation: `VERIFIED` now requires an identity-bound post-edit locality snapshot and locality decision in addition to BP2 and comparable analyzer measurements. A cohesive edit may close with preserved/improved locality and no introduced structure; any introduced helpers/layers require `DECOMPOSITION_JUSTIFIED`. Otherwise the result is `LOCALITY_REVIEW_REQUIRED`, even if Ruff debt decreased. BP2 binds the exact BP1 evidence identity and pre-edit source identity to both a canonical post-edit source-set identity and an explicit post-edit repository identity. Callers must provide every changed/new production source as a `post_edit_source_references` row with its evidence identity, and must separately provide repository-owned `post_edit_change_set_evidence` that attests the complete changed/new production source set against that same post-edit repository identity. BP2 requires the declared source references to match that independent change-set evidence exactly; a self-consistent but incomplete source identity/execution receipt cannot prove completeness by itself. BP2 also requires the frozen test path/evidence identities to remain unchanged, requires a PASS execution receipt for those exact frozen tests against both the exact source set and repository state, and requires PASS receipts for the exact repository gates declared by BP1 bound to that same repository identity. Changed tests, source-set completeness mismatches, stale execution/gate receipts from another repository state, missing/failed executions, or incomplete gate receipts fail closed as `POST_EDIT_EVIDENCE_REQUIRED`.

A successful BP2 receipt reports `BEHAVIOR_PRESERVATION_VERIFIED`, but still does not authorize merge, prove architectural improvement, own repository policy, or treat reduced debt/complexity as proof of preserved behavior. A repository may run additional affected/component verification according to its own policy. Remeasure current debt separately; the next target must come from fresh repository evidence rather than a frozen hotspot list.

Probe artifacts that do not publish their own top-level `evidence_identity` can be bound canonically with `agent_economics.probe_contract.sha256_identity(payload)`. Consumers should not invent repository-local JSON serialization or hashing rules.


### bounded-evidence-batches

Use `bounded_evidence_batches` when a constrained agent or controller cannot safely finish one expensive evidence sweep inside a single execution window. It freezes the exact repository identity, provider/artifact identity, operation, ordered targets, and batch size before work starts. The helper **does not execute Hashmarks, tests, or repository commands**; it only plans and binds externally produced evidence.

For example, a 90-target verification-ownership sweep can be frozen as nine deterministic 10-target batches. A campaign may also freeze an external controller budget, an internal batch timeout, and minimum headroom. Plans that do not leave that headroom are rejected before execution. A completed batch that exceeds its declared internal budget remains `INCOMPLETE_BUDGET_EXCEEDED`; a controller timeout remains `INCOMPLETE_CONTROLLER_TIMEOUT`. Neither becomes `PRODUCT_FAILURE`. Missing or over-budget batches remain `INCOMPLETE`; they can never aggregate to a complete proof, while completed sibling receipts remain independently valid.

The contract also supports deterministic subdivision after timeout (for example 10 targets -> 5 + 5), collapse back to the exact parent-batch semantics, and explicit resume at the first missing/incomplete/failed parent batch. Repository and provider identities are present in every descriptor and receipt, preventing reuse across a changed repository generation or a different Hashmarks wheel/commit.

The same contract is available through the canonical package CLI, so constrained environments do not need repository-specific Python wrappers:

```bash
python -m agent_economics evidence-batches plan \
  --targets-file targets.json \
  --repository-identity sha256:<repo-generation> \
  --provider-identity sha256:<provider-artifact> \
  --operation verification_ownership_graph \
  --batch-size 10 \
  --artifact manifest.json

python -m agent_economics evidence-batches batch manifest.json \
  --index 0 --artifact batch-0.json

# Execute batch-0.json targets externally, write results-0.json, then bind them:
python -m agent_economics evidence-batches receipt batch-0.json \
  --results-file results-0.json \
  --execution-class hosted-diagnostic \
  --elapsed-ms 1234 \
  --observed-repository-identity sha256:<repo-generation> \
  --observed-provider-identity sha256:<provider-artifact> \
  --observed-operation verification_ownership_graph \
  --artifact receipt-0.json

python -m agent_economics evidence-batches aggregate manifest.json receipt-*.json
python -m agent_economics evidence-batches resume manifest.json receipt-*.json
```

The CLI writes only the requested evidence JSON. It never invokes the provider operation itself. Receipt creation requires the executor-observed repository identity, provider identity, and operation; all three must match the frozen batch descriptor, so a checkout/provider change between batches fails closed instead of being stamped with stale manifest authority.

Typical Python use:

```python
from agent_economics.bounded_evidence_batches import (
    aggregate_receipts,
    batch_descriptor,
    build_manifest,
    build_receipt,
)

manifest = build_manifest(
    targets=qualified_targets,
    repository_identity=repository_identity,
    provider_identity=hashmarks_artifact_identity,
    operation="verification_ownership_graph",
    batch_size=10,
)
batch = batch_descriptor(manifest, 0)

# Run batch["targets"] outside Agent Economics, then bind the observed results.
receipt = build_receipt(
    batch,
    results=observed_results,
    execution_class="hosted-diagnostic",
    elapsed_ms=elapsed_ms,
)
aggregate = aggregate_receipts(manifest, [receipt])
```

Use different `execution_class` values for hosted diagnostics versus native qualification. Sharing the same manifest shape does not promote hosted evidence to native authority.

For expensive evidence ladders, the same contract can derive a follow-up manifest from a complete cheaper-stage receipt set. Per-target results may set `followup_required` with a reason; `derive_followup_manifest(...)` preserves original target order and binds the expensive-stage selection to the parent manifest, aggregate, and exact receipt identities. Missing/incomplete parent batches cannot produce a follow-up manifest, and tampering with a target's follow-up flag invalidates the receipt identity. This makes patterns such as “run task-action/evidence broadly, then verification ownership only for ambiguity/disagreement” auditable and resumable rather than ad hoc.


### dogfood-corpus

`dogfood-corpus` emits a built-in adversarial reference fixture set for Agent Economics development. It currently contains 13 failure/authority/bounds cases and a protocol that requires local repair iterations, no repository-owned independent qualification during the repair loop, outcome freeze before opening the oracle, and independent repository qualification only after local evidence is frozen. Consumer campaigns are not required to use these scenario names or this corpus.

```bash
python -m scripts.agent_economics dogfood-corpus \
  --artifact .agent-artifacts/dogfood-corpus.json
```

The corpus generator does not execute or repair tasks. Its identity makes it possible to bind P11 outcome records to an exact experimental task definition rather than a mutable task name.


### Stress qualification

The permanent Agent Economics qualification includes a bounded stress layer after the deterministic phase regressions. It repeatedly exercises semantic identity stability, independent stdout/stderr hard limits, non-UTF8 output, a 128-iteration loop-state boundary, and repeated process-tree timeout termination.

The GitHub Agent Economics job has a 20-minute outer timeout. Individual commands retain much smaller local bounds; the outer timeout is safety headroom for the complete suite, not permission for a single probe or repair command to run unbounded.
