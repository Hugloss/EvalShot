# Harbor cross-harness repository-intelligence matrix

This directory is an **execution projection**, not a second benchmark authority.

The canonical repository task, pinned repository commit/tree, prompt, mutation,
and oracle remain owned by the referenced EvalShot suite. The bridge
projects both bounded read-only `repository-location-json` tasks and frozen
behavioral command-oracle tasks into disposable Harbor tasks without becoming a
second benchmark authority.

The initial matrix compares the same model across OpenCode, Codex, and Claude
Code with two arms:

- `none`: native harness tools only;
- `hashmarks`: the identical task image plus one trial-scoped Hashmarks stdio
  MCP exposure.

Hashmarks is installed into **both** images. Only MCP exposure varies, avoiding a
package/image confound. No user/global MCP registration is benchmark authority.

## Run

Set the existing Hashmarks source authority and the one model to compare in
`.env`:

```dotenv
HASHMARKS_BENCH_SOURCE=/absolute/path/to/Hashmarks
BENCHMARK_HARBOR_MODEL=<provider/model>
BENCHMARK_HARBOR_EXECUTABLE=harbor
BENCHMARK_PASSTHROUGH_ENV_KEYS=OPENAI_API_KEY,ANTHROPIC_API_KEY
```

Only list credential variable **names** in the file. Their values must already
exist in the host environment. The bridge writes them to a run-local mode-0600
Harbor env file and never includes values in EvalShot receipts.

Model-free readiness for the full matrix:

```sh
make benchmark-check MATRIX=harbor-full
```

Fast one-task matrix:

```sh
make benchmark-new MATRIX=harbor-smoke
```

Full three-task / three-attempt matrix:

```sh
make benchmark-new MATRIX=harbor-full
```

List runs, resume an interrupted run, and report without invoking a model:

```sh
make benchmark-runs MATRIX=harbor-full
make benchmark-resume MATRIX=harbor-full RUN_ID=000001
make benchmark-status MATRIX=harbor-full RUN_ID=000001
make benchmark-report MATRIX=harbor-full RUN_ID=000001
```

### Read-only workspace integrity

The repository-location verifier uses one bounded native Git porcelain
observation to qualify the workspace, including staged/unstaged tracked edits,
new untracked files, and ignored files. The sole exception is the required
`.agentscookbook-answer.json` transport file, which must be an ordinary file
of at most 64 KiB. A symlink answer, extra generated file, failed Git status,
or unbounded status output denies the read-only reward even when the reported
owner/path is correct. The verifier keeps `tracked_clean` for compatible
answer-evidence readers and now exposes `workspace_status_error`,
`unexpected_change_count`, and bounded `unexpected_change_paths`.

This check detects current worktree changes; it is **not** proof that the agent
never modified and restored a file or changed Git metadata. The source
checkout and experiment task authorities are still independently frozen
before execution.

The report publishes per-harness bare/Hashmarks success rates,
`hashmarks_uplift`, and the bare-vs-Hashmarks `harness_spread`. A positive
spread reduction is evidence that the portable repository-intelligence layer
reduced harness sensitivity.

### Explain the mechanism, not only the score

New Harbor runs also freeze two bounded artifacts into each immutable
EvalShot result bundle when Harbor provides them:

- the root ATIF `trajectory.json`;
- `answer.json` from the verifier, containing only the observed
  `path`/`symbol` answer, expected answer, match result, and tracked-tree
  cleanliness.

The analyzer intentionally ignores ATIF `reasoning_content` and conversational
message text. It consumes only structured tool calls, linked observations, and
token metrics, then maps host-specific tools through EvalShot's existing
tool-routing vocabulary.

A captured ATIF trajectory is not automatically complete tool-order evidence.
Malformed steps, non-list `tool_calls`, malformed call records, missing function
names, and empty trajectories retain explicit `tool_order_issue_codes` and
`tool_order_issue_count`. Valid observed calls remain visible, but a partial
trajectory has `tool_order_complete=false`, unknown routing and treatment,
and cannot prove `NEVER_INVOKED`, fewer tool calls, native discovery displacement,
or a positive paired mechanism attribution. The component ablation analyzer
likewise excludes those quartets from qualified aggregate denominators.
ATIF steps with no `tool_calls` field are valid: the field is optional.

This is *parser completeness* for the captured trace, not cryptographic
attestation that a host emitted every call. Host exposure and delivery
authenticity must still be established separately.

For a completed bare/Hashmarks pair with the same campaign, task, harness,
model, and replicate, the mechanism report records independent dimensions such
as:

- `FAIL_TO_PASS`, `PASS_TO_PASS`, `PASS_TO_FAIL`, or `FAIL_TO_FAIL`;
- `FIRST_CHOICE`, `LATE_RESCUE`, or `NEVER_INVOKED`;
- observed successful treatment, no usable result, never invoked, or unknown;
- native discovery/search/read displacement;
- tool-call and token deltas;
- wrong-to-correct versus same-answer transitions;
- whether a repository path returned by Hashmarks was later used by a native
  read.

Inspect the latest full Harbor run with:

```sh
make benchmark-harness-explain
```

or a specific run:

```sh
make benchmark-harness-explain RUN_ID=000001
```

The default for this convenience target is `HARBOR_MATRIX=harbor-full`; override
it explicitly when needed.

Positive mechanism labels are **supported associations**, not causal proof.
`NEVER_INVOKED` is stronger in the opposite direction: EvalShot records
the pair as `NOT_ATTRIBUTABLE` and refuses to credit Hashmarks for that outcome
delta. Partial campaigns may be inspected with `./benchmark explain
--allow-incomplete`, but their mechanism evidence is marked
`INSPECTION_ONLY` and is not persisted as a qualified mechanism report.

### Controlled component ablations

A Harbor ablation matrix declares one Hashmarks MCP `component` and four
explicit arm roles:

- `bare`: native harness tools only;
- `full`: the complete canonical Hashmarks MCP contract;
- `remove`: the complete contract with exactly that component withheld;
- `only`: only that component advertised.

The matrix freezes the role-to-subject mapping and requires the remove/only
tool projections to match the declared component exactly. Hashmarks owns the
generic server-side projection mechanism; EvalShot owns which projection
constitutes an experimental arm.

Model-free preflight calls Hashmarks readiness for every restricted catalog and
freezes the canonical source-contract identity, exact projected tools,
independently observed catalog, projection identity, and the ablation contract
before model work. That same ablation contract is transported into every trial
receipt. Generated Harbor MCP config bytes are checksum-bound into campaign
authority, so same-path replacement or projection drift fails resume before
another model trial.

For every completed matched quartet, the report checks observed Hashmarks
invocations from the ATIF trace against the frozen tools in each arm. A bare arm
calling Hashmarks, a removal arm invoking the withheld component, or an only
arm invoking a different Hashmarks operation invalidates treatment
qualification. Unknown tool spellings or incomplete tool-order evidence remain
explicitly unqualified.

These are negative checks on observable calls, not proof of which MCP tools the
host advertised. The report therefore keeps
`observed_catalog_advertisement_proven=false`. Positive necessity/sufficiency
classification additionally requires observable invocation of the declared
component in the relevant full or only arm.

The report uses one generic schema for every component:

- **necessity-style:** full Hashmarks versus full Hashmarks minus the component;
- **sufficiency-style:** bare versus the component as the only Hashmarks tool.

Possible classifications include
`NECESSARY_AND_SUFFICIENT_CONTRAST`, `NECESSITY_SIGNAL`,
`SUFFICIENCY_SIGNAL`, `REDUNDANT_OR_OTHER_HASHMARKS_PATH`, and
`NO_ISOLATED_COMPONENT_SIGNAL`. The report never upgrades these replicate-level
contrasts into universal causal proof and always keeps
`positive_causal_proof_claimed=false`.

Qualified component campaigns persist `reports/ablation.json`.

**Aggregate component effects use a stricter denominator than campaign
completion.** Per-harness success rates and component contrasts are computed
only from fully matched quartets where all four outcomes are PASS/FAIL and the
frozen treatment passes ATIF call-projection qualification. A reward-only,
partial-trajectory, treatment-unqualified, or incomplete quartet remains
inspectable but cannot contribute to apparent component uplift.

The report explicitly counts `matched_quartets`,
`qualified_complete_quartets`, `treatment_unqualified_quartets`,
`incomplete_outcome_quartets`, and `excluded_matched_quartets`; each
`by_harness` entry exposes its own matched/qualified denominator. When no
qualified quartet exists, aggregate success rates and contrasts remain `null`
rather than treating missing evidence as failure.

#### Semantic localization: `task_evidence`

The existing `harbor-ablation-*` matrices use prompts where the implementation
owner is not already known. This matches the Hashmarks MCP contract: semantic
localization should route to `task_evidence` before exploratory native search.

Run smoke then full:

```sh
make benchmark-check MATRIX=harbor-ablation-smoke
make benchmark-new MATRIX=harbor-ablation-smoke

make benchmark-check MATRIX=harbor-ablation-full
make benchmark-new MATRIX=harbor-ablation-full
```

Inspect without another model invocation:

```sh
make benchmark-harness-ablation
make benchmark-harness-ablation RUN_ID=000001
```

#### Known exact symbol: `find`

The `harbor-find-ablation-*` matrices use a distinct task population where the
exact symbol name is explicitly supplied and only its path is unknown. This
matches the Hashmarks MCP contract's routing rule for `find`; reusing the
semantic-localization prompts would confound the component test with the wrong
routing intent.

Three exact-symbol task definitions live beside the held-out suite for Harbor
projection only:

- `lookup-known-symbol-paths-under`;
- `lookup-known-symbol-sync-remove-stale-paths`;
- `lookup-known-symbol-run-poll-delay`.

They are intentionally **not** listed in the canonical held-out
`experiment.json`, so adding the `find` experiment does not expand or mutate
the native 12-task benchmark population.

Run smoke then full:

```sh
make benchmark-check MATRIX=harbor-find-ablation-smoke
make benchmark-new MATRIX=harbor-find-ablation-smoke

make benchmark-check MATRIX=harbor-find-ablation-full
make benchmark-new MATRIX=harbor-find-ablation-full
```

Inspect the latest qualified `find` run:

```sh
make benchmark-harness-find-ablation
make benchmark-harness-find-ablation RUN_ID=000001
```

The generic command remains available for future components:

```sh
make benchmark-harness-ablation \
  HARBOR_ABLATION_MATRIX=harbor-find-ablation-full
```

#### Known edit, downstream consequences: `change_impact`

The `harbor-change-impact-ablation-*` matrices reuse the frozen
`behavioral-v4` mutation corpus. Each task starts from a checksum-bound
post-mutation repository baseline, asks the agent to make one narrowly allowed
edit, and grades both the edited bytes and the downstream implementation /
verification evidence with the suite's existing independent command oracle.

This is intentionally different from semantic localization: the changed owner
is already known, and the question is what else the change affects.

```sh
make benchmark-check MATRIX=harbor-change-impact-ablation-smoke
make benchmark-new MATRIX=harbor-change-impact-ablation-smoke

make benchmark-check MATRIX=harbor-change-impact-ablation-full
make benchmark-new MATRIX=harbor-change-impact-ablation-full

make benchmark-harness-change-impact-ablation
```

The full campaign is 4 tasks × 3 harnesses × 4 arms × 3 replicates = 144 trials.

#### Existing change, evidence revalidation: `post_change`

The `harbor-post-change-ablation-*` matrices reuse the four
`behavioral-v4/post_change` cases. These cover both edit and read-only
freshness/reuse decisions after a changed path already exists. This isolates
whether `post_change` contributes beyond the rest of Hashmarks when the agent
must decide what evidence is stale, reusable, or newly relevant.

```sh
make benchmark-check MATRIX=harbor-post-change-ablation-smoke
make benchmark-new MATRIX=harbor-post-change-ablation-smoke

make benchmark-check MATRIX=harbor-post-change-ablation-full
make benchmark-new MATRIX=harbor-post-change-ablation-full

make benchmark-harness-post-change-ablation
```

The full campaign is also 144 trials.

#### External/derived observations: `correlate_evidence`

The `harbor-correlate-evidence-ablation-*` matrices reuse the four
`behavioral-v4/correlation` cases. Their prompts are deliberately not owner
localization or changed-path questions. They provide bounded external or
derived observations and ask the agent to preserve evidence semantics:

- runtime-path to repository-source mapping without inventing causation;
- conflicting line/symbol claims without silently selecting an owner;
- ambiguous symbol evidence and a known-missing repository member as separate
  states;
- independent revision comparison while retaining runtime provenance.

That matches Hashmarks's `correlate_evidence` contract: correlate bounded
observations while preserving ambiguity, provenance, completeness, and source
equivalence.

```sh
make benchmark-check MATRIX=harbor-correlate-evidence-ablation-smoke
make benchmark-new MATRIX=harbor-correlate-evidence-ablation-smoke

make benchmark-check MATRIX=harbor-correlate-evidence-ablation-full
make benchmark-new MATRIX=harbor-correlate-evidence-ablation-full

make benchmark-harness-correlate-evidence-ablation
```

The full campaign is 4 tasks × 3 harnesses × 4 arms × 3 replicates = 144 trials.
As with the other component experiments, positive attribution requires an
observable `correlate_evidence` invocation in the relevant full/only arm and
never becomes a universal causal claim.

#### Correlated provider declarations: `repository_declarations`

The `harbor-repository-declarations-ablation-*` matrices reuse the four
`behavioral-v4/declarations` cases. They test whether the agent preserves
declaration authority rather than collapsing independently sourced provider
claims into one synthetic truth:

- equivalent declarations retain both provider identities rather than picking
  a winner;
- differing declarations remain explicitly differing without selecting an
  authoritative provider;
- declared A↔B and B↔C relationships do not silently prove A↔C or merge
  provider namespaces;
- incomplete provider coverage does not become proof of absence or
  equivalence.

That matches Hashmarks's `repository_declarations` contract: project
correlated repository declarations while preserving provenance, ambiguity,
coverage, and freshness.

```sh
make benchmark-check MATRIX=harbor-repository-declarations-ablation-smoke
make benchmark-new MATRIX=harbor-repository-declarations-ablation-smoke

make benchmark-check MATRIX=harbor-repository-declarations-ablation-full
make benchmark-new MATRIX=harbor-repository-declarations-ablation-full

make benchmark-harness-repository-declarations-ablation
```

The full campaign is again 144 trials. Positive attribution requires an
observable `repository_declarations` invocation in the relevant full/only
arm; incomplete or treatment-unqualified quartets remain outside aggregate
component-effect denominators.

#### Frozen dependency evidence: `dependency_codemap`

The `harbor-dependency-codemap-ablation-*` matrices reuse the four
`behavioral-v4/dependency_delta` cases. These deliberately present already
materialized dependency evidence; the agent is not asked to execute uv, Maven,
or another package manager. The cases test whether dependency semantics are
projected correctly rather than reconstructed from tool execution:

- uv component selection moving from one version to another without inferring
  why it changed;
- Maven relationship/scope changes that leave component selection unchanged;
- same-version uv source selection separated from marker-only relationship
  change;
- Maven classifier selection distinguished from effective-scope change.

That matches Hashmarks's `dependency_codemap` contract: project dependency
evidence as observation, explanation, or endpoint comparison without executing
a package manager.

```sh
make benchmark-check MATRIX=harbor-dependency-codemap-ablation-smoke
make benchmark-new MATRIX=harbor-dependency-codemap-ablation-smoke

make benchmark-check MATRIX=harbor-dependency-codemap-ablation-full
make benchmark-new MATRIX=harbor-dependency-codemap-ablation-full

make benchmark-harness-dependency-codemap-ablation
```

The full campaign is 4 tasks × 3 harnesses × 4 arms × 3 replicates = 144
trials. Positive attribution requires an observable `dependency_codemap`
invocation in the relevant full/only arm; missing tool-order evidence or
treatment drift leaves the quartet outside qualified component aggregates.

#### Query-facet ablation: `verification-explanation`

`repository_intelligence_query` is a multi-facet facade, so EvalShot
does **not** treat the whole tool as one mechanism. A selector-aware ablation
can bind one structured call argument to one product-owned facet. The first
such matrix uses:

```text
component: repository_intelligence_query
selector:  surface_name = verification-explanation
```

and the four `behavioral-v4/verification` cases. Their oracle asks for a
focused repository-native verifier, runner, argv/scope, or closely related
verification evidence.

The treatment arms are:

- `none`: no Hashmarks MCP;
- `hashmarks`: full Hashmarks tools and all canonical query surfaces;
- `hashmarks-no-verification-explanation`: full tool catalog, but exactly
  that query facet withheld;
- `hashmarks-verification-explanation-only`: only
  `repository_intelligence_query`, restricted to exactly that facet.

The facet catalog is not duplicated in EvalShot. Model-free preflight
reads the canonical surface list from Hashmarks readiness, derives the two
subsets, asks Hashmarks to qualify each projected server, and freezes selected
plus independently observed surfaces into treatment authority. Generated MCP
config then carries exact repeated `--query-surface` arguments.

ATIF attribution is selector-aware. A call to
`repository_intelligence_query(surface_name="freshness")` does not count as
invoking `verification-explanation`; a call to a facet withheld by the frozen
treatment disqualifies that quartet. Only bounded structured
`tool + surface_name` evidence is retained for this gate—model reasoning and
unrelated tool arguments are not consumed.

```sh
make benchmark-check MATRIX=harbor-verification-explanation-ablation-smoke
make benchmark-new MATRIX=harbor-verification-explanation-ablation-smoke

make benchmark-check MATRIX=harbor-verification-explanation-ablation-full
make benchmark-new MATRIX=harbor-verification-explanation-ablation-full

make benchmark-harness-verification-explanation-ablation
```

The full campaign is again 144 trials. The same generic ablation report remains
the authority; it records both `component` and `selector` and still keeps
`positive_causal_proof_claimed=false`.

### Behavioral Harbor projection authority

Behavioral command-oracle tasks do not treat the mutation fixture as agent
work. EvalShot applies the existing suite mutation through the canonical
mutation helper, verifies its SHA-256 and exact changed-path set, and then
freezes a post-mutation workspace manifest before Harbor launches a model.

The Harbor verifier compares the final workspace against that frozen manifest.
Only task-declared `allowed_change_globs` and
`allowed_generated_globs` may differ. It then feeds the agent's bounded JSON
answer to the existing suite `oracle.py` / `cases.json` command oracle and
publishes reward only when both contamination and oracle checks pass.

The projected task bundle therefore binds:

- the mutated repository bytes;
- the mutation identity and changed paths;
- the post-mutation baseline manifest;
- the suite command oracle and cases;
- allowed edit/generated globs;
- the Hashmarks source and projected MCP catalog.

No second behavioral oracle is introduced by Harbor.

### Command-oracle lifecycle qualification

Before applying the frozen mutation or publishing the projected task, the
bridge invokes the suite-owned `oracle.py health <task-id>` using its current
Python interpreter. The bounded 10-second health check must exit zero and
leave workspace bytes unchanged. Failure, timeout, missing executable or
unexpected workspace mutation blocks fixture preparation before model work.
The frozen behavioral projection records `oracle_health` with the successful
operation and time limit; health is not itself a correctness score.

During Harbor verification, the existing suite-owned `oracle.py grade`
receives a distinct 40-second command timeout inside Harbor's 60-second
verifier budget. Missing oracle executables, timeouts, malformed responses,
or stdout/stderr above 64 KiB fail reward closed and leave explicit verifier
evidence. The `oracle.json` diagnostic retains bounded 8 KiB previews,
truncation flags and timeout status. A hung grader can no longer consume the
entire verifier budget without a diagnostic reward. This checks execution
liveness and response integrity; it does not replace the existing lexigram
oracle or prove that the external model saw a specific MCP catalog.

## Boundary

Harbor uses the same numbered run store, durable launch claims, checksum-bound
receipts, and interruption recovery as native benchmarks. Its trial executor
and reward report are backend-specific. A Harbor reward does not certify
Hashmarks subject-tool invocation. Read-only localization keeps the frozen
repository-location verifier; behavioral tasks reuse the suite's frozen
command oracle and mutation authority. Completed `INCOMPLETE` receipts are immutable; resume only executes
pending trials and launches interrupted before receipt publication. Start a
new run after repairing an operational failure.

`harbor-smoke` and `harbor-full` have separate run roots under
`.benchmark-runs/harbor-harness-v1/`. The selected matrix, trial population,
model, Harbor/Docker versions, projected task bytes, and Hashmarks source
identity are frozen before model work. A changed authority requires a new run.

The preflight fails before model work unless the Hashmarks checkout is clean, its
`doctor --mcp` diagnostic reports the current canonical MCP contract, Harbor is
available, Docker is available, all selected tasks satisfy the projection
contract, and explicitly selected credential variables exist.


### Information timing and oracle-aligned evidence

The Harbor mechanism report now exposes a second, **non-causal information
timeline** for completed bare/Hashmarks pairs. It is a richer explanatory
projection than a headline pass rate or a raw tool-call count; it does not
replace the frozen suite oracle or the generic component-ablation evaluator.

Inspect with the existing model-free entrypoint:

```sh
make benchmark-harness-explain RUN_ID=000001
```

Each pair has `information_evidence` with:

- `qualified` and `reason`: explicit observability admission/exclusion;
- `target_alignment`: `ORACLE_TARGET_ONLY`, `ALTERNATE_TARGETS_ONLY`,
  `MIXED_TARGETS`, `NO_STRUCTURED_PATH_TARGETS`, or `UNKNOWN`;
- `arrival_timing`: `BEFORE_NATIVE_DISCOVERY`, `AFTER_NATIVE_DISCOVERY`,
  `NO_NATIVE_DISCOVERY`, `NO_SUBJECT_RESULT`, or `UNKNOWN`;
- `native_read_followthrough`: whether a later *observed native read* named
  the oracle path, another returned path, both, or neither;
- tool-call ordinals, plus the linked ATIF observation step for information
  arrival (which can be later than the tool invocation); native discovery and
  subsequent path-aligned reads are kept separate.

The report summary groups **only qualified pairs** by outcome transition,
arrival timing, target alignment, and follow-through under
`information_outcome_cross_tab`. It separately counts
`information_qualified_pairs` and `information_exclusion_reasons`. Thus
`FAIL_TO_PASS | BEFORE_NATIVE_DISCOVERY | ORACLE_TARGET_ONLY |
ORACLE_PATH_READ` is directly inspectable, while `PASS_TO_FAIL` with an
alternate target is a signal to investigate, **not** proof the target harmed
the agent. Also inspect `FAIL_TO_FAIL` with a correct target: the information
may have been returned too late, left unused, or been insufficient.

The expected path comes only from the frozen verifier's answer evidence,
never from the model's answer or its reasoning. The observation projection
parses bounded structured tool results and tool-call arguments, including JSON
inside MCP text blocks. It compares complete repository-relative paths, never
fuzzy substrings, and never persists raw tool results or agent messages in
this derived report. Duplicate call/observation IDs, missing linked results,
unparseable subject results or MCP text blocks, malformed tool order,
ambiguous same-step result/call ordering, and missing path oracles fail closed to an explicit unqualified/unknown state.

This experiment does **not** measure when the agent mentally adopted a
belief or whether the tool output was attended to. A later native read is
observable follow-through only. Opaque native read arguments are explicitly
unknown rather than evidence that follow-through never occurred. Agents can use information without a native
read, and a native read does not establish causation. For behavioral command
oracles without a path oracle, information alignment remains unqualified
rather than inventing a path-correctness grade. Use the existing
`full/remove/only/bare` component ablations for stronger controlled contrasts.


### Semantic evidence: what information arrived, not just which file

The post-run Harbor mechanism report now adds `semantic_information_evidence`
for the frozen behavioral-v4 **command oracle** tasks (dependency transitions,
change impact, declarations, correlation, freshness, negative evidence, and
verification). This complements the path-specific `information_evidence`
added in PR #211. It does not change the canonical task prompts, experiment
arms, oracle correctness, execution, reward, or component ablation.

After the existing behavioral oracle runs, its verifier writes the **same**
canonical `cases.json` expected atoms into its verifier-only `answer.json`
artifact. Those atoms are never placed in the agent workspace or supplied
to the agent during execution. The mechanism evaluator reads this frozen
bundle and the existing oracle's field-level rubric; it **does not** call
an oracle or derive a second answer key from agent output.

For each treated pair, it records:
- `claim_alignment`: `ALIGNED_ONLY`, `DIVERGENT_ONLY`,
  `MIXED_OR_CONFLICTING`, `NO_COMPARABLE_CLAIMS`, or `UNKNOWN`;
- exact expected-field **names** (not values) found in structured
  Hashmarks responses, separated into aligned, divergent, and conflicted;
- `first_subject_observation_step`, `first_aligned_observation_step`,
  `first_divergent_observation_step`, and `first_native_discovery_step`;
- arrival `BEFORE_NATIVE_DISCOVERY`, `AFTER_NATIVE_DISCOVERY`,
  `NO_NATIVE_DISCOVERY`, or `UNKNOWN_SAME_STEP`;
- `final_answer_overlap`: whether correct or divergent returned claim
  values also appeared in the verifier-observed final answer;
- qualification/exclusion codes and the explicit
  `agent_attention_proven=false` and `causal_influence_claimed=false`.

The existing `make benchmark-harness-explain` target exposes
`summary.semantic_outcome_cross_tab` keyed by paired outcome transition,
arrival timing, alignment, and final-answer overlap. Only fully completed
PASS/FAIL pairs with qualified structured evidence contribute. The
`semantic_exclusion_reasons` denominator separately counts legacy bundles
without frozen semantic expected atoms, incomplete traces, unlinked/ambiguous
observations, invalid rubrics, and incomplete outcomes. The output contains
no expected or observed atom **values** and no model reasoning/messages.

An example pattern worth investigating is `FAIL_TO_FAIL` with an
early, oracle-aligned claim that the final answer did not repeat. Another is
`PASS_TO_FAIL` with a divergent claim repeated in the final answer.
Neither is proof the model read or adopted the evidence; tool output
availability and answer co-occurrence are not agent cognition.
`ALIGNED_ONLY` also does not imply the entire tool response is accurate:
only fields named by the frozen task oracle can be compared. Opaque
narrative output, mixed values for the same field, and uncertain call/result
ordering do not silently turn into an absence or a positive attribution.

Use the existing bare/full/remove/only ablations for stronger component
contrasts. These new post-run projections explain the **mechanism to inspect**,
not universal causal effectiveness. Historical Harbor bundles without the
new expected-atom artifact remain inspectable but do not retroactively
acquire semantic qualification.


### Component-isolated semantic evidence for controlled ablations

The `ablation` report now carries a **separate diagnostic denominator** for
oracle-aligned semantic evidence in the four-arm `bare/full/remove/only`
experiments. This builds on the post-grade semantic evidence projection rather
than introducing a second oracle or a competing ablation scorer.

For each matched quartet, `semantic_component_evidence.full` and
`semantic_component_evidence.only` examine **only returned observations from
the exact frozen component**, including the exact `surface_name` for
selector-based experiments. Evidence from `find`, `task_evidence`, or another
Hashmarks tool cannot be credited to `repository_declarations` merely because
it was in the same full-arm trajectory. A result containing the expected
semantic atom is available evidence; a repeated value in the final answer is
co-occurrence, **not proof of the agent attending to or adopting it**.

The existing command:

```sh
make benchmark-harness-ablation RUN_ID=000001
```

now exposes:

- Per-quartet `semantic_component_evidence` with full/only claim alignment,
  arrival relative to native discovery, final-answer overlap, and a qualification
  reason. Only expected **field names** appear; values and agent messages are not
  reproduced in the report.
- `summary.semantic_qualified_quartets` and
  `summary.semantic_exclusion_reasons`.
- `summary.semantic_outcome_cross_tab`, grouping qualified contrasts by
  existing classification, full/only outcomes, alignment, arrival, and repeated
  claims. This helps distinguish *correct information exposed before discovery
  and associated with a successful only-arm outcome* from *late, conflicting,
  or unused information associated with a failure*.
- Per-harness `semantic_qualified_quartets` alongside the **unchanged**
  `qualified_complete_quartets` and success-rate contrasts.

This diagnostic denominator is deliberately stricter: a quartet must already
be treatment-qualified and have four complete PASS/FAIL outcomes, both
`full` and `only` arms must have invoked the declared component/selector,
and both component-filtered semantic ATIF projections must be qualified.
Missing post-grade oracle atoms, partial ATIF, unlinked observations, opaque
results, non-invocation, and treatment violations remain **excluded**, not
converted to failed/negative information exposure. Historical bundles without
semantic post-grade receipts stay inspectable but do not qualify retroactively.

A component may still help through non-atom mechanisms (e.g., better discovery
or smaller search space); `NO_COMPARABLE_CLAIMS` is not an ineffectiveness
verdict. The control experiment's existing necessity/sufficiency-style
contrasts remain descriptive replicate-level evidence, never universal
causal proof. No models are invoked by this report.

### Scoped SCIP/LSP relationship evidence timing

After Hashmarks PRs #409–#413, the same immutable ATIF tool results can
contain qualified `task_evidence.semantic_relationships` records. The Harbor
mechanism evaluator now adds `relationship_scope_evidence` to each treated
bare/Hashmarks pair without changing reward, oracle, task, prompts, or execution.

The projection requires a linked structured `task_evidence` tool result with
a resolved owner and self-consistent count scopes. It now rejects any orphan
observation, malformed ATIF observation, or incomplete call/result linkage
before declaring the semantic timeline qualified. When multiple scoped returns
occur in the same trace, their admitted ownership and full captured semantic
records must agree. Disagreement (including equal counts with different
producer evidence, or different owner subjects) is excluded as
`conflicting-scoped-semantic-returns` instead of cherry-picking a convenient
first result. Matching returns record `scoped_return_count` and a
`captured_semantic_record_sha256` diagnostic. This digest identifies the
**captured tool-return record**, not the producer's source authority, a model
input, or evidence of use. It preserves the difference
between the direct SCIP **outgoing definition count** and the wider
**associated direct producer-claim count**, which may include incoming SCIP or
request-local LSP observations. A zero outgoing count is not evidence of
absent incoming relationships. No additional Hashmarks requests are made.

The returned report describes:
- `SCOPED_SEMANTIC_RETURN`, `NO_SCOPE_RECORD`, `NEVER_INVOKED`, or
  unqualified/unknown evidence with explicit exclusion reasons;
- `BEFORE_NATIVE_DISCOVERY`, `AFTER_NATIVE_DISCOVERY`,
  `NO_NATIVE_DISCOVERY`, or `SAME_STEP_UNORDERED` based on the *linked
  result step*, not the earlier invocation step;
- an **exact** subsequent `structural_locality` request for the same
  `path::qualname` with `result_mode="relationships"`, separate from model
  attention or actual evidence use;
- native search calls observed before and after the semantic return.

**Opaque tool-boundary qualification:** A `functions.exec`-style router may hide
nested repository calls. When such a call could have preceded the scoped
result, `arrival_timing=UNKNOWN_OPAQUE_ROUTER` replaces an unsupported
`BEFORE_NATIVE_DISCOVERY` or `NO_NATIVE_DISCOVERY` assertion. An opaque
router or shell at/after the return without a visible exact detail request
produces `detail_followthrough=UNKNOWN_OPAQUE_DETAIL_REQUEST`, not a
confident negative. A separately visible exact follow-up is still positive.
The report retains directly observed native-search counts while labeling
`native_search_observability=PARTIAL_OPAQUE_TOOL` (otherwise
`EXPLICIT_TOOL_CALLS_ONLY`); neither label certifies full host tool capture.
Same-step searches are counted in `native_search_same_step` rather than
incorrectly ordered before or after the result. All linked ATIF results,
including unrelated tools, must occur no earlier than their invocation.
These flags preserve exposure data without treating hidden work as avoided.

The summary includes `relationship_scope_outcome_cross_tab` combining the
qualified return timing, explicit detail-request state, semantic count scope,
paired outcome, and `native_search_comparison.qualified_delta`, or an explicit
`UNQUALIFIED` label when the search comparison is not admitted.

### Paired native-search comparison qualification

A raw `native_search_delta` is **only a difference in explicitly visible ATIF
native-search call counts**; it does not establish work avoided inside shell or
tool-router calls. The pair now also emits `native_search_comparison` with
`observed_delta` (retained for diagnosis), `qualified_delta` (otherwise null),
and a machine-readable qualification reason. Both bare and treated traces must
be available, tool-order complete, and independently classified as
`EXPLICIT_TOOL_CALLS_ONLY`. A shell, `functions.exec`, another generic router,
or a historical trace with missing observability produces a qualified
exclusion, never an invented zero. These qualifications are **descriptive
explicit-call comparability**, not independently host-attested complete nested
execution or causal proof of savings.

`summary.evaluation_assurance.economics.native_search_delta` now aggregates
**only admitted comparisons**, separately reporting
`native_search_comparable_pairs` and
`native_search_comparison_exclusions`. Opaque comparisons do not create
`NATIVE_DISCOVERY_AVOIDED`, `NATIVE_DISCOVERY_REDUCED`,
`SAME_ANSWER_FEWER_READS`, or `FEWER_TOOL_CALLS` mechanism labels. Existing
outcome, task, model, harness, oracle, and paired-report denominators remain
unchanged. `native_search_savings_proven` remains false: an observed reduction
is not a controlled causal effect, and missing nested-call evidence must not
be presented as a verified efficiency gain. Incomplete
outcomes, missing/duplicated ATIF links, malformed producer claims, and opaque
results are excluded rather than treated as evidence absence. The paired bare
arm remains the same harness/model/task/replicate. This is **descriptive
correlation**, not a claim that Hashmarks reduced exploration or caused a pass.

Inspect with `make benchmark-harness-explain RUN_ID=000001`. A real A/B
measurement still needs paired runs against explicitly selected Hashmarks
source commits, with identical task/agent/model conditions and successful
campaign admission. These post-run projections run model-free against the
frozen bundles; historical bundles without the new fields are not silently
upgraded. `observed_use_proven`, `agent_attention_proven` and
`causal_influence_claimed` remain false.

## Integrated evidence/evaluation assurance (PR #217)

The model-free Harbor mechanism report now includes
`summary.evaluation_assurance`. It is derived from the same verified
immutable receipts, not a second scorer, second oracle, or another campaign
authority.

- **E217 / E222**: `delivery_evidence` binds each subject invocation to its
  ATIF source-call-linked packet digest, return step, bounded byte count, and
  structural/text presentation parity. `RETURNED` means tool result observed;
  `delivery_state=UNKNOWN` and `application_state=UNKNOWN` remain honest:
  ATIF does **not** independently attest which bytes entered the model input
  context. Tool follow-up is observable behavior, not proof of model cognition.
- **E218**: model-free adversarial tests reject orphan/duplicate/early results,
  forged semantic claims, malformed calls, tool-error envelopes, contradictory
  JSON views, incomplete observations, and unsupported causal upgrades.
- **E219**: headroom-v2 keeps semantic grading separate from output format.
  An isolated bare-agent failure remains `observed` headroom but is no longer
  mislabeled *reproducible*: `reproducibility_state` requires at least two
  semantic failures among three or more gradeable replicates for the same
  task/agent condition. No historical v2 oracle or receipt is regraded.
- **E221**: exact within-harness paired outcome transitions receive a
  deterministic task/harness/model-cluster bootstrap interval only when at
  least eight independent task clusters are observed. Fewer groups show
  `INSUFFICIENT_EVIDENCE` and a null interval; this is **not** a p-value,
  posterior probability, or claim of statistical significance.
- **E223 / E224**: JSON structured-vs-text parity is checked when both
  presentations exist. A matching dual presentation is not a controlled
  presentation experiment; frozen model-backed native/compact/text/structured
  contrasts require a separate admitted intervention campaign.
- **E227 / E228**: the single JSON report provides exact denominators,
  exclusions, headroom, per-harness effects, observed packet states, and paired
  native-search/token deltas. Missing monetary/provider/catalog accounting
  remains unavailable instead of becoming zero.

### E225: two-component tool-catalog factorial

The frozen matrix isolates the interaction of Hashmarks `task_evidence` and
`find` while holding the rest of the catalog unchanged. It has four
factorial tool configurations plus the ordinary bare arm:

| Factorial role | `task_evidence` | `find` |
| --- | --- | --- |
| neither | excluded | excluded |
| a_only | present | excluded |
| b_only | excluded | present |
| both | present | present |

It reuses Harbor's model execution, immutable bundles, treatment identities,
independent answer grading, and ATIF call projection. The new report checks
the exact per-arm tool catalog, source-contract identity, observed forbidden
calls, complete four-way outcomes, and matched task/harness/model/replicate
before emitting the descriptive interaction
`both - a_only - b_only + neither`. Incomplete or contaminated groups are
excluded. Catalog exposure is not inferred from absence of calls. Factorial
records never enter the legacy single-component ablation fallback.

Model-free admission and an explicitly funded run:

```sh
make benchmark-check MATRIX=harbor-factorial-smoke
make benchmark-new MATRIX=harbor-factorial-smoke
make benchmark-check MATRIX=harbor-factorial-full
make benchmark-new MATRIX=harbor-factorial-full
make benchmark-report MATRIX=harbor-factorial-full
```

A qualified full run persists `reports/factorial.json` and the corresponding
`tool_factorial` section in `reports/report.json`. An unqualified run
cannot publish a positive interaction claim. These commands involve model
calls after the model-free checks; CI does not execute them.

### E226 and outstanding empirical boundaries

The existing `context-invariance-v1` suite already runs neutral/placebo/
unsupported-authority/misleading-hint contrasts. Harbor's generic pair report
does not silently call this a bound freshness intervention. A new frozen,
independently reviewed stale/current/replaced-evidence task population is
needed before context/freshness effect claims.

`multidomain-v2` also has 60 pending independent reviews. This PR does not
self-approve them. Presentation interventions need source/runtime support for
changing only the selected result representation; host-level model-input
delivery proofs need a host-owned capture boundary. Until those authorities
exist, the report explicitly records `NOT_ASSESSED`, `NOT_IDENTIFIABLE`,
or `UNKNOWN`, not spurious success.


## E229–E232: factorial campaign completeness and independent-review handoff

The E217–E228 factorial matrix is **a defined experiment, not a measured
effect**. E229–E232 add two read-only qualification surfaces without creating
a new benchmark runner, another oracle, or a Hashmarks implementation.

### E229–E230: exact campaign grid, verified bundles, and arm consistency

Run the existing model-backed campaign explicitly (this requires configured
Harbor, model credentials, runtime dependencies, and a pinned Hashmarks build):

```sh
make benchmark-check MATRIX=harbor-factorial-smoke
make benchmark-new MATRIX=harbor-factorial-smoke
make benchmark-check MATRIX=harbor-factorial-full
make benchmark-new MATRIX=harbor-factorial-full
```

Then audit **one actual saved campaign** (do not use the sample paths as claims
of a completed run):

```sh
make benchmark-factorial-campaign-audit \
  RESULTS_ROOT=.benchmark-runs/harbor-harness-v1/factorial-full \
  CAMPAIGN_ID=<actual-execution-campaign-id> MODE=matrix
```

For smoke use `MODE=smoke` and the smoke run root. The audit walks immediate
bundle directories and invokes the repository's existing immutable Harbor
bundle verifier; unreadable or corrupt bundles are counted as blockers.
It insists on every `harness × task × replicate × subject` cell: **15 smoke
cells/3 quartets**, **135 full cells/27 quartets**, including the bare control
for each matched quartet. No duplicate, missing, foreign-campaign, out-of-grid,
ungradeable, tool-order-incomplete, source-contract-drift, mixed-model, or
contaminated-bare cell is silently removed from the denominator. Each four-arm
factorial must pass the existing exact tool-catalog/trace treatment audit.

`coverage_state=COMPLETE` is limited to exact, internally consistent run
coverage. It does **not** authorize `empirical_effect_qualified`,
`model_input_delivery_attested` or
`independent_oracle_review_attested`; all remain false without distinct
external authority. A failed `--require-complete` exits nonzero. The audit
does not alter existing scoring or rerun a model.

### E231–E232: independently reviewable case work, not synthetic approval

```sh
make benchmark-eval-readiness
make benchmark-eval-review-queue > /tmp/agentscookbook-independent-review-queue.json
```

The queue enumerates each multidomain case still requiring a review, with a
SHA-256 of the frozen case excluding its mutable review field, plus every
skill without a distinct confusion case. Every queue entry is explicitly
non-approved. For multidomain tasks an independent reviewer must inspect the
pinned repository, fixture digest, source ownership, counterexample and oracle
expectation; for confusion cases, create and independently validate genuine
repository fixtures and a negative oracle, not just an ungrounded scenario.
Reviewer-generated evidence must be checked against the frozen identity in a
separate approval workflow. This PR does not self-approve 60 multidomain cases
or fabricate 54 confusion cases.

### Boundary

Hashmarks owns repository evidence; EvalShot owns descriptive
measurement and intervention qualification. ATIF-linked returns are not proof
of visibility or cognition. Presentation parity is not a randomized
representation experiment. The optional campaign audit never runs in CI
against synthetic results to assert actual uplift; tests exercise only
model-free adversarial qualification rules. Independent review and
model-backed intervention campaigns remain explicit follow-on actions.


## E233–E236: externally attested model-input boundary and intervention audit

These read-only qualification tools do **not** turn Harbor's ATIF trace into a
model-input log or add a model-input interception hook to Codex, OpenCode, or
Claude Code. Today the standard Harbor backend **does not** generate the
external signed receipts specified below. They can only be produced by a
separately trusted, instrumented model-invoking host. Until such a host is
integrated, delivery and intervention results remain **UNKNOWN / unrun**.

### E233: independently keyed host input receipt

A Hashmarks tool return is `RETURNED`, not `DELIVERED`. To attest the latter,
a model-invoking **host** must inspect the actual outgoing model request
*after* any result transformation and emit a receipt through a channel
unavailable to the agent and MCP server. It must sign the binding to the
tool packet only when that packet actually appears in the request input.

The receipt schema is
`agentscookbook.host-model-input-attestation.v1`:

- Envelope: `schema`, `campaign_id`, `trial_id`,
  `trajectory_sha256` (SHA-256 of exact ATIF bytes), `deliveries`.
- Every delivery: `host_identity`, `host_build_sha256`,
  `model_request_sha256`, `model_input_sha256`,
  `model_message_sha256`, `packet_sha256`, `call_id_sha256`,
  `model_request_sequence`, `boundary="host-model-request-input"`, and
  `mac_sha256`. All `*_sha256` values are lowercase 64-digit hex.
  `packet_sha256` and `call_id_sha256` must match a **qualified ATIF
  Hashmarks result** rather than a free-text claim.
- MAC: `HMAC-SHA256(key, b"agentscookbook:host-model-input-attestation:v1\\x00"
  + canonical_json({"context": envelope_without_deliveries,
  "delivery": delivery_without_mac}))`. Canonical JSON uses sorted keys,
  compact separators, UTF-8, and rejects nonfinite numbers. Optional
  `intervention` metadata is part of the MAC, never an unsigned annotation.

The trust key **must** be independently generated and inaccessible to trial
workspaces, agent tools, and Hashmarks. The verifier rejects symlinked keys
and group/world-readable key files. Do not put keys, raw prompts, credentials,
or raw model messages into the repository, benchmark outputs, or PRs.

```sh
make benchmark-host-delivery-audit \
  TRAJECTORY=/immutable/run/trial/trajectory.json \
  HOST_ATTESTATION=/external-host/receipts/trial.json \
  HOST_KEY_FILE=/trusted-host-only/host-hmac.key \
  CAMPAIGN_ID=real-campaign-id TRIAL_ID=real-trial-id
```

Invalid/stale/foreign/duplicated/orphaned or unauthenticated receipts never
become delivery proof. Complete coverage yields `delivery_state=PROVEN`
**only in the limited sense of a keyed host assertion about model-request
input**: it does not prove the provider processed the request, the model
noticed the information, or the information influenced an answer. Partial
packet coverage stays `PARTIAL`; no key or receipt leaves it `UNKNOWN`.
The ordinary mechanism and assurance reports intentionally retain their
existing `UNKNOWN` delivery state unless an independent verification
integration supplies qualified proof.

### E234–E235: paired presentation and freshness protocols

`benchmark-intervention-audit` consumes **verified Harbor result bundles**
plus independently signed receipts for those exact trials; it does not create
experimental subjects or synthesize agent traces. A design JSON has precisely:

```json
{
  "schema": "agentscookbook.host-attested-intervention-design.v1",
  "study": "presentation",
  "campaign_id": "replace-with-actual-campaign-id",
  "model": "replace-with-exact-provider-model",
  "harnesses": ["codex"],
  "tasks": ["locate-prefix-path-enumerator"],
  "replicates": 3,
  "arms": {"control": "structured", "variant": "text"}
}
```

For `study="freshness"`, the exact arms are `control="current-generation"`
and `variant="replaced-generation"`. Both require two model-backed,
matched, graded, full-contract `hashmarks` trials per
harness × task × replicate. The externally signed host receipt adds
`intervention` with exactly:

`study`, `arm`, `design_sha256`, `presentation`,
`generation_sha256`, `current_generation_sha256`,
`semantic_sha256`, `catalog_sha256`, `prompt_sha256`,
`oracle_sha256`, `workspace_sha256`, `surface_sha256`.

The design digest uses the canonical JSON above. Every signed packet
in a trial must report the **same** treatment. Presentation comparisons
require identical semantic/generation/catalog/prompt/oracle/workspace
identities, with structured vs text as the only modeled difference and
different surface digests. Freshness comparisons keep the presentation and
current-target generation fixed but deliberately differ in exposed
generation and semantic response identity. The trial source contract
identity must also be unchanged. These are host-attested invariants, not
independently reproduced source-byte proofs.

```sh
make benchmark-intervention-audit \
  DESIGN=/trusted-evaluator/design.json \
  RESULTS_ROOT=/immutable/harbor/run/bundles \
  ATTESTATIONS_ROOT=/external-host/receipts \
  HOST_KEY_FILE=/trusted-host-only/host-hmac.key
```

The receipt for each bundle directory `trial-name` lives at
`ATTESTATIONS_ROOT/trial-name.json`. Its signed `trial_id` must equal
that directory name; its signed campaign must match the frozen design.
Missing/duplicate/foreign/ungradeable trials and uncontrolled changes
block coverage, not merely disappear from denominators. This is **not a
working treatment injector**: implementing verified transformations at each
harness's host boundary remains external work.

### E236: conclusions and safety of interpretation

Only matched `PASS/FAIL` outcomes admitted through the exact host-input
boundary enter the per-harness descriptive contrast (variant minus control).
Task/harness/model-cluster bootstrap intervals are suppressed below eight
independent task clusters, and harnesses are never pooled. Even a complete,
host-attested grid is **not** evidence of true randomization, pre-registered
timestamp authority, independent oracle review, provider consumption,
model attention, or causal product uplift. Those require separately
qualified evidence.

Model-free QA (no model, key, external host or trial needed):

```sh
uv run --no-project python -m unittest benchmarks.tests.test_host_attested_interventions -v
uv run --no-project python -m unittest discover -s benchmarks/tests
make benchmark-eval-readiness
```

**Open external dependencies:** actual host-side model-request instrumentation,
key custody and external durable signatures; real matched interventions and
frozen randomized assignments; independent review of oracle cases. Never
upgrade these to complete merely because the code and tests pass.


## E237–E240: frozen host execution and independent campaign qualification

This is the follow-on to E233–E236. The shipped Python adapter can be
integrated into a **privileged provider-invoking host** using the exact
request bytes and the original Hashmarks return. It is *not* installed into
the current third-party Codex/OpenCode/Claude Code hosts or silently injected
by Harbor. Until a genuine host invokes it on its actual outbound request,
there is **no real host-delivery observation** and no qualifying campaign.

### E237: provider host adapter — actual request, exact message, no forge CLI

`benchmarks/harness/trusted_host_capture.py` exposes
`TrustedModelRequestCapture`. A privileged host must initialize it
**outside the evaluated workspace**, with the frozen manifest, an
owner-only external key, and the exact planned trial. The host then calls
`observe_outbound_request(...)` on the *serialized provider request*
at its outbound request boundary, supplying the corresponding original
Hashmarks return, selected treatment input, and host-observed
catalog/prompt/oracle/workspace identities.

Only the explicit JSON provider envelope with `model` and
`messages=[{"role":"tool","tool_call_id":...,"content":...}]` is
supported. Unknown provider formats reject instead of guessing.
The adapter checks the exact designated tool-call message content,
provider model, returned-packet/source consistency, immutable
manifest assignment, and hashes the actual request bytes.
`prompt_sha256` must match the observed request's non-tool messages
and request settings. The selection must already be reflected in the
request; simply requesting a treatment does not mark it delivered.

After final ATIF capture, the trusted host invokes `finalize()`.
This requires complete immutable ATIF call/result bindings, then
creates an external HMAC receipt without overwriting existing files.
There is **no executable for signing agent-provided data**. The adapter
cannot independently prove that an arbitrary caller is a real
provider host, that provider dispatch succeeded, or that the model
attended to or used the information. Those facts remain separate.

### E238: plan all controlled treatments before work

`benchmarks/harness/trusted_treatments.py` deterministically fixes the
entire `harness × task × replicate × arm` assignment grid under the
original intervention design, one declared seed digest, frozen host
build digest, and source-contract identity. The manifest includes an
identity for each planned trial, paired experiment identity, and explicit
within-pair execution ordinal. It is create-only, and changed arm
assignments, source, design, or missing cells reject on admission.

```sh
make benchmark-treatment-freeze \
  DESIGN=/external/design.json \
  MANIFEST_OUTPUT=/external/pre-work/frozen-manifest.json \
  SOURCE_CONTRACT_ID=sha256:pinned-hashmarks-source \
  HOST_BUILD_SHA256=<64-hex-host-build-digest> \
  ASSIGNMENT_SEED_SHA256=<64-hex-declared-seed-digest>
```

Selection is pure and fail-before-work. Presentation uses **one**
pinned semantic/generation source, returning the same JSON facts
structured or as canonical JSON text. Freshness requires explicit
current and replaced source identities with unequal generations and
semantic digests. Every host must actually install the selected
message into the provider request. The deterministic seed **does
not prove randomized assignment** or externally timestamped preregistration.

### E239: tie Harbor execution back to the frozen assignments

`validate_trial_alignment` enumerates each planned trial and rejects
absent, foreign, duplicate, symlink, mismatched model/harness/task/replicate,
missing full Hashmarks contract, changed contract identity, substituted
host build, wrong arm, or unbound host receipt. The existing intervention
audit additionally verifies external host MACs and exact input facets.
A complete generic Harbor pair report cannot replace these checks.
Identity is resolved once, transported once, executed once, reported once.

### E240: separate custodial seal and conservative decision

`benchmarks/harness/independent_campaign_qualification.py` accepts a
separately signed `agentscookbook.independent-campaign-seal.v1` JSON,
bound to the frozen campaign, manifest digest, design digest, external
anchor digest, review-corpus digest, custodian ID, and boolean assertions
`pre_work_frozen` and `independent_oracle_review_completed`.
Its `mac_sha256` is
`HMAC-SHA256(independent_key, b"agentscookbook:independent-campaign-seal:v1\\x00"
+ canonical_json(seal_without_mac))`.

The review/anchor authority must generate and custody its seal and
secret independently of the host, agent, and Hashmarks. No self-approval
signing command is provided. The qualifier rejects an unverified,
stale, absent or host-key-reused seal, incomplete ATIF delivery,
missing pairs, corrupt/unmatched immutable bundles, and missing
review assertions. Real reviewer labor and externally established
pre-work chronology **must still be independently checked**: a
custodian's signed assertion alone is not a timestamp, proof of
independence, a randomization certificate, or causal evidence.

```sh
make benchmark-independent-campaign-qualify \
  MANIFEST=/external/pre-work/frozen-manifest.json \
  RESULTS_ROOT=/immutable/run/bundles \
  ATTESTATIONS_ROOT=/external-host/receipts \
  HOST_KEY_FILE=/trusted-host/host.key \
  SEAL=/external-reviewer/seal.json \
  INDEPENDENT_KEY_FILE=/external-reviewer/review.key
```

`descriptive_campaign_admitted=true` means that the immutable grid,
host-input boundary receipts, source identities, and externally
key-authenticated custodian claims are consistent. It is not a
causal-impact qualification. Reports always reserve
`causal_effect_proven=false`, `model_attention_proven=false`,
`real_randomization_proven=false`, and
`trusted_pre_work_timestamp_proven=false`.

Regression and model-free evidence:

```sh
uv run --no-project python -m unittest benchmarks.tests.test_trusted_host_campaign -v
uv run --no-project python -m unittest discover -s benchmarks/tests
make benchmark-eval-readiness
```

**Remaining operational work**: add a native provider-host SDK integration
which invokes the boundary adapter on real outgoing requests, independently
custody and anchor the pre-work manifest, verify source and oracle reviews
through actual people, and run the controlled model trials. CI passing
does not close those dependencies.


## E241–E244: controlled HTTPS provider dispatch and strict submission assurance

The E237–E240 host adapter checked a declared outbound request but did not
own the transport. E241 introduces a **runnable** HTTPS transport with a
narrow protocol and explicit host custody, rather than pretending Harbor
can intercept requests made inside native third-party agent harnesses.

### E241: actual outbound body, one attempt, no redirect

A privileged host imports
`benchmarks.harness.trusted_http_provider.dispatch_verified_chat_request`
and passes an E237 `TrustedModelRequestCapture` plus the **actual serialized**
OpenAI-compatible Chat Completions JSON body. The host still owns:
the frozen manifest, model credentials, full original tool response, its
real task/prompt/oracle/workspace identities, and external evidence storage.

For this limited adapter the HTTP endpoint must be pinned to the host-owned
`approved_origin`, use TLS, have exactly the path
`/v1/chat/completions`, and have no embedded URL credentials/query/fragment.
The transport suppresses environment-provided proxy routes, disables
redirect following, sends **one** JSON POST with its original request bytes,
and accepts only a bounded 2xx JSON object containing a nonempty
`choices` array. The host must supply a syntactically correct provider
conversation; this function does not construct agent reasoning, generate a
prior tool call, implement all provider message shapes, or retry on error.
An invalid envelope/unsupported provider remains inadmissible.

The receipt is emitted only when `TrustedModelRequestCapture.finalize()`
matches every source call and packet to the immutable completed ATIF.
The transport must have observed a successful provider HTTP response before
`finalize()` can publish an attestation. Failed/ambiguous requests cannot
retry within the same capture and cannot produce a successful
transport-submission receipt. Raw request and response bodies are not
persisted by this transport.

### E242: verify the selected treatment actually reached the transport

Before HTTP work the host checks the frozen trial identity, single selected
Hashmarks packet, pinned semantic/generation source, specific model and tool
call, one and only one tool-message slot, and a prompt digest derived from
the actual request settings and non-tool messages. These are observable
body-level invariants, not inferred from returned tool packets.

Every successful host-submission record includes a MAC-protected
`transport` object:

```json
{
  "boundary": "https-response",
  "endpoint_sha256": "<64-hex>",
  "http_status": 200,
  "response_sha256": "<64-hex>",
  "response_bytes": 1024
}
```

The verifier distinguishes `delivery_state=PROVEN` (key-holder asserts
tool-message inclusion) from `provider_submission_state=SUBMITTED`
(key-holder asserts one successful HTTPS response from its pinned
transport). Both remain host assertions: neither is proof of model
attention, provider inference, or a causal outcome. A receipt without
the signed transport object keeps submission `UNKNOWN`. Partial coverage
does not become complete.

### E243–E244: audit every arm under one external authority

A stricter qualification entrypoint re-checks every planned trial's
immutable ATIF and independent host HMAC, requires `SUBMITTED` for
*all* planned cells, verifies the existing frozen source/assignment and
paired intervention constraints, and separately validates the review
custodian's signed seal. No completed cell is inferred from a missing
bundle, no replayed request is a new observation, and one valid arm is
not sufficient for a paired claim.

```sh
make benchmark-provider-campaign-qualify \
  MANIFEST=/external/pre-work/frozen-manifest.json \
  RESULTS_ROOT=/immutable/run/bundles \
  ATTESTATIONS_ROOT=/external-host/receipts \
  HOST_KEY_FILE=/trusted-host/host.key \
  SEAL=/external-reviewer/seal.json \
  INDEPENDENT_KEY_FILE=/external-reviewer/review.key
```

Descriptive campaign admission requires every independent check to pass.
The report explicitly withholds provider-processing proof, real randomization,
trusted pre-work chronology, model attention, and causal attribution.
An externally signed pre-work or human-review *assertion* must still be
supported by independent operational evidence.

Model-free attack regressions (faked HTTPS responses, **no network traffic**):

```sh
uv run --no-project python -m unittest benchmarks.tests.test_trusted_http_provider -v
uv run --no-project python -m unittest discover -s benchmarks/tests
```

**Not shipped:** native OpenCode/Codex/Claude Code transport interception,
real verified provider trials, independently timestamped preregistration,
or completed independent oracle review. No experimental result is inferred
from mocks or a successful CI run.


## E245–E248: native-host feasibility and independently verified campaign provenance

These checkpoints deliberately distinguish a **working first-party HTTPS
provider transport** from the opaque model-request boundaries of native
Codex, OpenCode, and Claude Code. They do not create another coding agent
or transplant Hashmarks search into EvalShot.

### E245: explicit native-harness boundary register

`make benchmark-native-host-readiness DESIGN=/external/design.json`
inspects the frozen design without contacting a model. The shipped
integration status is:

| Harness | What can be observed | Native model-input capture qualified? |
|---|---|---|
| Codex / codex-native | Agent trace and tool results | **No** |
| OpenCode / opencode-native | Agent trace and tool results | **No** |
| Claude Code | Harbor trace and tool results | **No** |
| OpenAI Responses routing probe | Provider-managed MCP outputs and direct API response | **No** |
| trusted-http-chat | First-party controlled HTTPS body and submission receipt | **Not a native agent harness** |

The existing OpenAI Responses probe may truthfully claim routed
`mcp_call` evidence, but provider-managed MCP output is not proof of
exact model-input packet inclusion. Independent host HMAC keys and a
synthetic provider response likewise do not magically identify a
native third-party provider boundary.

No `native-host-supported=true` user-supplied field can override this.
Adding a genuine native integration requires a separately reviewed
version-bound adapter and observed provider-request tests before changing
the register.

### E246–E247: cross-trial transport provenance

`make benchmark-host-transport-provenance` re-verifies the signed host
input AND successful HTTPS response binding for **every planned arm**,
against exact frozen trial IDs and expected host identity, host build,
`model_request_sequence=1`, selected arm and design, and the exact
approved SHA-256 endpoint identity. Within each pair the host build,
host identity, endpoint, catalog, prompt, oracle and workspace hashes
must all be stable. One missing, foreign, tampered, re-signed-but-drifted
or symlinked trial fails the population. This is read-only and cannot
produce its own receipts.

```sh
make benchmark-host-transport-provenance \
  MANIFEST=/external/pre-work/frozen-manifest.json \
  RESULTS_ROOT=/immutable/run/bundles \
  ATTESTATIONS_ROOT=/external-host/receipts \
  HOST_KEY_FILE=/trusted-host/host.key \
  APPROVED_ENDPOINT_SHA256=<sha256-of-approved-full-provider-url> \
  EXPECTED_HOST_IDENTITY=<independent-registered-host-name>
```

These identities are host assertions verified with an independently
custodied key, not remote provider-signed statements. A TLS 2xx response
is evidence of bounded host submission, not reasoning or attention.

### E248: descriptive empirical decision gate

`make benchmark-empirical-campaign-decision` jointly requires
authenticated provider submission, the independently sealed campaign,
exact immutable Harbor bundles, complete intervention pair coverage,
and cross-trial stable transport provenance. It also requires observed
control failures (task headroom), and at least **eight distinct task
clusters within each harness** before a descriptive interval population
is admitted. Missing or unknown grades never enter an effect estimate.
Each harness is assessed separately; heterogeneous model/harness
populations are never pooled into a product-winner claim.

```sh
make benchmark-empirical-campaign-decision \
  MANIFEST=/external/pre-work/frozen-manifest.json \
  RESULTS_ROOT=/immutable/run/bundles \
  ATTESTATIONS_ROOT=/external-host/receipts \
  HOST_KEY_FILE=/trusted-host/host.key \
  SEAL=/external-reviewer/seal.json \
  INDEPENDENT_KEY_FILE=/external-reviewer/review.key \
  APPROVED_ENDPOINT_SHA256=<64-hex> \
  EXPECTED_HOST_IDENTITY=<registered-host>
```

The strongest possible state from these tools is
`submission_bounded_descriptive_population_qualified`. The following
are **always explicitly unproven**: true native-harness capture, actual
independent human case reviews, externally trusted preregistration
chronology, genuine assignment randomness, provider consumption,
model attention, and causal Hashmarks uplift. Those require
**new observed external evidence**, not another reporting boolean.

Run the model-free attack ring:

```sh
uv run --no-project python -m unittest benchmarks.tests.test_native_provenance_and_empirical_decision -v
uv run --no-project python -m unittest discover -s benchmarks/tests
```

The additional tests include valid-but-synthetic HMAC receipts,
substituted endpoint/host-build identities, duplicate trial replay,
missing control, cross-arm prompt drift, modified signed transport,
unreviewed host status and insufficient control headroom.
They are **not** a completed empirical benchmark.

**Pending after this bundle:** wire a genuine instrumented host boundary
for at least one native agent/version, execute controlled matched
provider-backed tasks with independent run custody, and obtain real
independent oracle reviews. Do not describe provider-owned MCP routing
as the missing native transport hook.


## E249–E252: opt-in pinned OpenCode custom-provider gateway

This is the **first executable trial-scoped native OpenCode provider route**
in the evaluator. It does not replace the existing native OpenCode
benchmark adapter and is not enabled implicitly. It is a standalone
one-shot OpenAI-compatible route for controlled evaluation.

Official OpenCode V1 provider documentation describes
`provider.<name>.npm="@ai-sdk/openai-compatible"` with
`options.baseURL` for a custom provider and `enabled_providers` for
provider restriction. See
[OpenCode provider configuration](https://opencode.ai/docs/providers/)
and [OpenCode config](https://opencode.ai/docs/config/).
The opt-in runner freezes those values in a *private, create-only*
`opencode.json` and selects `agentscookbook-captured/<model>`.
It also isolates HOME/XDG config/data/cache and suppresses unrelated
LLM credentials in the launched child environment.

### E249 — exact executable and provider-config admission

A trial needs an already-frozen E238 assignment for
`harness="opencode-native"` (or `"opencode"`), pinned OpenCode
executable SHA-256, exact observed `opencode --version`, a model
ID, and one independently pinned source payload
(`generation_sha256`, `semantic_sha256`, `content`).
The semantic digest MUST match the canonical JSON content.
The executable is checked before and after running, and config
files are create-only under a fresh private run root.

**Do not reuse an existing OpenCode configuration** as gateway authority.
OpenCode can merge managed, global, and project configuration, so the
generated allowlist is an intended constraint—not proof of the
runtime's final effective provider selection. Any foreign/native
requests must still be rejected by the isolated gateway. The native
provider package, its effective settings, other plugin hooks, and
process egress are not yet independently attested.

### E250 — actual loopback request boundary

The runner launches the pinned native OpenCode process directly,
not through a shell, and points its configured OpenAI-compatible
provider to a loopback-only HTTP gateway with a per-trial bearer
token. The token is not proof of OS process identity: an evaluated
child could read its own environment. The upstream API credential
stays with the privileged host and is **not** passed to OpenCode.

A maximum of four pre-tool provider requests can pass through the
host-owned TLS client for native tool selection. These are
**unattested** and never count as Hashmarks delivery. When exactly
one tool-result message appears, the gateway compares it byte-for-byte
by JSON value against the frozen selected Hashmarks result. It then
uses the existing E241 signed transport to validate and dispatch the
actual request exactly once. Its bounded response is returned
unchanged to the native client, without a second provider call.
Wrong model, missing/duplicate tool message, changed selected
content, auth failure, over-budget requests, and retries fail closed.
Only OpenAI Chat Completions JSON with the currently supported
single `role="tool"` message form is supported. Both bounded nonstream
JSON responses and OpenAI-style `text/event-stream` responses are supported.
An SSE response must contain valid `choices` chunks and a terminal
`data: [DONE]` event. Truncated/malformed streaming responses cannot
generate a signed successful transport receipt. The full bounded SSE response
is relayed without a second provider call.

### E251 — fail-before-work qualification and observed execution

Use a **fresh** `RUN_ROOT`, a git-bound disposable `WORKSPACE`,
a frozen original subject `CURRENT_SOURCE` JSON and, for freshness,
the explicit `REPLACED_SOURCE` JSON. Specify the OpenCode binary and
observed SHA/version; every authority is a required argument.

```sh
export BENCHMARK_UPSTREAM_API_KEY=<privileged-host-only-provider-secret>
make benchmark-opencode-native-gateway \
  MANIFEST=/external/prework/frozen-manifest.json \
  TRIAL_ID=<frozen-native-trial-id> \
  OPENCODE_BIN=/trusted/bin/opencode \
  OPENCODE_SHA256=<sha256-executable> \
  OPENCODE_VERSION=<exact-version-output> \
  RUN_ROOT=/private/new-trial-dir \
  WORKSPACE=/disposable/git-workspace \
  PROMPT_FILE=/external/frozen-prompt.txt \
  CURRENT_SOURCE=/external/verified-hashmarks-packet.json \
  HOST_KEY_FILE=/trusted-host/host.key \
  HOST_IDENTITY=<trusted-provider-host-id> \
  UPSTREAM=https://api.openai.com/v1/chat/completions \
  APPROVED_ORIGIN=https://api.openai.com \
  CATALOG_SHA256=<64-hex> ORACLE_SHA256=<64-hex> \
  WORKSPACE_SHA256=<64-hex> \
  ALLOW_UNCONFINED_NATIVE_TEST=1
```

**Safety:** This opt-in standalone runner does **not** use the existing
bubblewrap-backed native benchmark sandbox. By default the Make target and
Python CLI **refuse** to execute the native child. The
`ALLOW_UNCONFINED_NATIVE_TEST=1` acknowledgement deliberately permits an
unconfined agent process and must only be used in a disposable, isolated
test host with no secrets or valuable workspace content. The runner also
rejects evaluated-workspace-local secrets, trial authority placed inside
that workspace, and project `opencode.json`, `opencode.jsonc` or
`.opencode` configuration that could override provider selection.
The upstream API key remains in the trusted parent, but these checks do not
constitute OS isolation or guaranteed exclusive network egress.

The native process must successfully exit AND the gateway must have
relayed exactly one selected tool-result request before
`native_gateway_route_completed` can become true. A normal
`opencode run` exit on its own is not evidence of native model-input
delivery. Invalid or unobserved runs return nonzero status without
upgrading evidence. This launcher does **not** infer a tool result
from agent prose or synthesize a host-signed ATIF.

### E252 — what is and isn't empirically qualified

The gateway can prove to its own key holder which HTTP request body it
received and submitted. **It cannot prove that only the real OpenCode
process could speak on loopback.** OS-bound peer identity/egress
confinement, immutable native OpenCode session export, ATIF binding,
real task oracle review, and externally anchored campaign authority
are required before claiming native execution provenance or a
model-backed Hashmarks effect. The result intentionally reports:

- `host_receipt_finalized=false`
- `native_process_origin_proven=false`
- `native_model_input_delivered_proven=false`
- `causal_improvement_proven=false`

The earlier `TrustedModelRequestCapture.finalize` can only sign when
**real immutable ATIF** is separately available and exactly matches
the observed packet; never create an ATIF from the gateway request
to manufacture delivery proof. The E248 empirical gate remains
conservative, and no campaign has been run here.

Model-free attack regressions cover SHA/version drift, fail-before-work
admission, exact gateway model/packet matching, unknown messages,
pre-evidence budgets, one-shot response relay, no retries, create-only
config, child credential isolation and native-exit-without-evidence:

```sh
uv run --no-project python -m unittest benchmarks.tests.test_opencode_native_gateway -v
uv run --no-project python -m unittest discover -s benchmarks/tests
```


## E253–E256: genuine native-session read-back and cohort verification

The E249–E252 OpenCode trial-scoped gateway captures one selected provider
request and relays the upstream response. That **alone** does not prove a
native OpenCode tool call, and the prior runner discarded the native session.
This bundle adds a separate native session owner and refuses to qualify an
unmatched provider request.

### E253 — select and capture the precise OpenCode native session

The pinned launcher now starts `opencode run` with a unique
`--title agentscookbook:<trial>:<nonce> --format json`, records the
start epoch, and reads `opencode session list --format json --max-count 20`
under the **same isolated home and configuration** after the run. It
requires exactly one session with that title, workspace, and update
epoch, then captures `opencode export <session-id>`.

Exports use host-owned bounded output files and an explicit Linux
`prlimit` file-size owner (16 MiB). The capture substrate is admitted
*before* native model work and uses no threaded `preexec_fn`; no unbounded stdout pipe, 'most recent session' fallback,
agent-supplied session ID, or arbitrary prior session can qualify. A
missing, ambiguous, oversized, timed-out, or failed native export blocks
the next admission step. This is an observation of OpenCode's
*local session store*, not a remotely attested event.

### E254 — bind the native tool result to the real provider submission

`opencode_native_session.inspect_native_export` independently checks the
native session ID, exact title, repository directory, provider and model
IDs. The exported assistant parts must contain **exactly one** completed,
recognized Hashmarks tool result with a call ID and JSON packet identical
to the one observed by the trusted gateway; a final assistant text part
must occur **after** that tool completion. Missing or duplicate subject calls, changed packets, unknown Hashmarks
operations, malformed or unfinished native tool parts, and missing nonempty post-last-tool assistant text fail before any host
receipt is written. Text after a tool is not independent proof of a
native terminal stop. Other completed native tools
are allowed and remain part of the observed call order.

Only after that exact native tool observation does the host preserve the
raw `native-session.json`, derive `trajectory.json` **from the native
export** (not from a gateway request or agent prose), and create
`host-attestation.json` using the existing E233 host-key signing
boundary. The independent host verifier must then prove complete
ATIF packet coverage and successful HTTPS submission for the same
call/packet binding. Artifacts are create-only, stored outside the
evaluated workspace, and never overwritten. The result retains exact
SHA-256 digests for the raw export, derived ATIF, and signed host receipt.

This does **not** independently authenticate the native session contents:
a locally readable export is observational data, even if its bytes match
the gateway request. The host token and receipt signing key are not
kernel-level proof of the identity of the process that initiated the
loopback request.

### E255 — admission boundary remains explicit

The standalone launcher is still **unconfined** unless the caller opts in;
this bundle does not claim that HOME/XDG isolation provides a secure
network or filesystem sandbox. It continues refusing execution without
`ALLOW_UNCONFINED_NATIVE_TEST=1`, with project OpenCode configuration,
or with trusted host authority inside the evaluated workspace.

The practical remaining security requirement is **OS-backed process
identity and exclusive network egress**: ensure that only the pinned
OpenCode child may connect to the gateway and that it cannot access an
alternate external provider. Do not change
`native_process_origin_proven=false` until that property is actually
enforced and tested with an isolation backend. Native session parity
alone is not a substitute for this boundary.

### E256 — re-evaluate every trial, not just the signed receipt

The separate read-only verifier, `native_session_cohort_audit`, checks
each frozen trial directory against its saved raw export, derived ATIF,
externally HMAC-authenticated provider receipt, and separately supplied
pinned current/replaced source JSON. It re-derives the ATIF from native
tool observations and rejects changes to model, tool-call ID, original
packet, selected treatment facets, provider host, endpoint, session
workspace or title. Missing controls, foreign trial directories,
symlinked evidence, duplicate/replayed receipts, and cross-arm
prompt/catalog/oracle/workspace or generation drift all block a cohort.

```sh
make benchmark-native-session-cohort-audit \
  MANIFEST=/external/pre-work/frozen-manifest.json \
  NATIVE_RESULTS_ROOT=/private/native-results \
  SOURCES_ROOT=/external/frozen-trial-sources \
  WORKSPACE=/disposable/git-workspace \
  HOST_KEY_FILE=/trusted-host/host.key \
  HOST_IDENTITY=<registered-provider-host> \
  APPROVED_ENDPOINT_SHA256=<sha256-of-full-approved-HTTPS-url>
```

Every `SOURCES_ROOT/<trial-id>.json` file must contain exactly
`{"current":{...},"replaced":null}` (or an explicit pinned replacement)
and must be independently provisioned, not written by the evaluated
agent. The validator does not establish that an external timestamp or
human approved those values.

`native_session_cohort_observationally_consistent=true` is the
maximum claim this verifier makes; **not** independent OS process
provenance, restricted egress, independently reviewed oracle,
provider cognition, true randomization or causal Hashmarks improvement.
Real external campaigns still require independently controlled
assignments, native process isolation and grading.

Model-free regressions:

```sh
uv run --no-project python -m unittest benchmarks.tests.test_opencode_native_session -v
uv run --no-project python -m unittest benchmarks.tests.test_native_session_cohort_audit -v
uv run --no-project python -m unittest discover -s benchmarks/tests
```

## E257–E260: truthful native tool-order evaluation

The E253–E256 binding intentionally projected only the one Hashmarks
tool result into ATIF. That bound an exact packet to the gateway, but if
the native agent also ran `grep`, `read`, `bash` or other navigation,
the generated ATIF omitted those calls. Treating that reduced ATIF as
complete would falsely turn a *late rescue* into *first-choice* usage,
hide native read follow-through, and undercount tool calls.

The versioned native projection now reads **every completed assistant tool
part** in its observed export order. All native call IDs must be unique
and bounded, with completed state, valid name and map input. Unknown
Hashmarks tool spellings, duplicate call IDs, unfinished native tools,
fake user-authored tool parts, foreign MCP calls, and apparently final
text followed by another tool call fail before ATIF publication. The
single selected Hashmarks packet remains exactly linked to its host
provider submission; non-subject calls carry only operation identity
and bounded navigation locators. Their source contents, raw commands,
and full results remain in the separately sealed native export rather
than being copied into model-visible attribution output.

The resulting ATIF records `projection_origin=opencode-native-export`,
`projection_scope=all-native-completed-tool-parts`, and
`source_capture_exhaustiveness=UNKNOWN`. This is **completeness of the
projected tool parts within the supplied export**, not proof that a
runtime-independent observer captured every possible tool interaction.
Generic ATIF `tool_order_complete` continues to mean successful parsing
of the **captured** ordered calls, not OS-level trace attestation.

The native cohort audit independently reparses saved exports, reprojects
ATIF byte-semantics and validates tool-call count, subject-call count,
tool order and original host-signed input/transport. Its
`native_tool_order_evidence` now records native read/search counts and
first-choice versus late-rescue routing by experimental arm. It retains
`host_wide_capture_exhaustiveness_proven=false` and does not elevate
observed search deltas into causal efficiency gains.

The projection and read-back schema are advanced to version 2. Existing
version-1 native artifacts are historical; they are not silently
reinterpreted as newly complete capture. For a directly comparable run
under the v2 projection, use a new frozen campaign and new native session
receipts. This PR does not execute models or remedy the unconfined native
process / exclusive egress prerequisite.

Model-free regression ring:

```sh
uv run --no-project python -m unittest benchmarks.tests.test_opencode_native_tool_order -v
uv run --no-project python -m unittest benchmarks.tests.test_opencode_native_session benchmarks.tests.test_native_session_cohort_audit -v
uv run --no-project python -m unittest discover -s benchmarks/tests -v
```


### Claim-specific arrival versus controlled-ablation outcomes

The component semantic evaluator also now distinguishes the **first tool return**
from the **first actual oracle-aligned or divergent claim**. An early
`repository_declarations` response with unrelated metadata does not make a
correct `winner` claim that arrived three steps later "early."

The existing `make benchmark-harness-ablation RUN_ID=000001` JSON includes:

- Per component result, `aligned_claim_arrival`,
  `divergent_claim_arrival`, and `claim_arrival_order`, derived from the
  first *unconflicted* aligned/divergent field observation. Each is compared
  against the first native discovery step, retaining
  `UNKNOWN_SAME_STEP`, `NO_NATIVE_DISCOVERY`, and `NOT_OBSERVED` rather
  than inventing a sequence.
- Per complete qualified quartet,
  `semantic_information_outcomes`: two comparisons, `full_vs_remove`
  and `only_vs_bare`, with the matched control/observed outcomes and
  component-specific exposure profiles. Profiles separate aligned, divergent
  and conflicted field counts, final-answer value overlap, and claim arrival.
- `summary.semantic_information_outcomes`: qualification/exclusion counts
  and `conditional_outcome_cells`, grouping the observed exposure signature
  with `FAIL_TO_PASS`, `PASS_TO_FAIL`, `PASS_TO_PASS` or
  `FAIL_TO_FAIL`. Each cell includes **replicate count** and a distinct
  task/harness/model-cluster count; no regression coefficient, p-value or
  apparent timing-caused uplift is produced.
- Explicit exclusion reasons if the semantic projection is missing or
  inconsistent, the result is incomplete, or a component-specific return
  cannot be compared against the post-grade frozen oracle. Old v2 ablation
  reports remain readable: this is an additive diagnostic, not a new score
  or reward authority.

For example, a `PASS_TO_FAIL` full-versus-remove contrast with
`DIVERGENT_CLAIM_REPEATED` means an incorrect field was *observable in
the component output* and the verifier-observed final answer also contained
that exact field value. It **does not prove** the model copied it or that
the component caused failure. Conversely, `ALIGNED_AVAILABLE_NOT_REPEATED`
separates correct available evidence from missing final-answer overlap,
without claiming what the model read or mentally used. Conflicting values for
a single field remain `CONFLICTED_FIELDS`; an evaluator cannot cherry-pick
the later correct value to erase the earlier contradictory observation.

**Experimental interpretation:** these groups are post-treatment strata
created by the agent's choice of tool calls and by observed information.
They are **not independently randomized information treatments**. The
unchanged four-arm contrast remains the experiment; conditioned cells help
form follow-up hypotheses about information quality and timing. To test
whether delivering a claim at a particular time *causes* improved outcomes,
design a separate admitted experiment with the timing/presentation treatment
assigned in advance, with matched model/repository/task/attempt controls and
run-level completeness gates. Never convert unknown, opaque, or unavailable
tool returns into negative information evidence.

## Native Linux network namespace provider relay (PR #227; E257–E260 companion)

This bundle adds a **new confined native OpenCode execution mode** while
preserving the earlier E249 unconfined launcher only as an explicitly
authorized diagnostic. `main` contained E253–E256 before this work.

### E257 — fail-before-work namespace and executable admission

`benchmark-opencode-native-netns` requires Linux, an available `bwrap`
executable, a supported Python runtime path, the exact
SHA-256/version-pinned OpenCode executable, an exact SHA-256-pinned
standalone Hashmarks MCP executable, the frozen E238 trial manifest, a
private host run root, and the same pinned source/oracle/workspace evidence
used by E253. Missing or stale authority rejects **before** model work.
No fallback to the host network is permitted.

The native child executes under `bwrap` with
`--unshare-user --unshare-pid --unshare-net --clearenv`, readonly
system-runtime mounts, an explicit writable **disposable workspace**,
isolated home/XDG directories, readonly OpenCode provider config and
Hashmarks executable, and only one host-owned private Unix socket
directory. It does **not** bind mount host `/`, an arbitrary user home,
or the host's `/etc`/network namespace. The pinned Hashmarks MCP
command is configured explicitly as
`[<hashmarks>, "--workspace", <workspace>, "mcp"]` in the OpenCode
local MCP V1 configuration.

This is an intentionally narrow Linux implementation. Missing kernel
user/network namespace support, inaccessible interpreter/runtime
libraries, unsupported OpenCode config, or inability of bubblewrap to establish
isolated loopback must fail rather than silently escaping containment.
A passing argv unit test is not evidence of a functioning sandbox on
a particular deployment host.

### E258 — inside-namespace loopback to outside-namespace provider

The child network namespace has no host-network interfaces. Bubblewrap
brings up its isolated `lo` interface as part of `--unshare-net`
(or fails before execution); a small stdlib worker serves the configured
OpenAI-compatible loopback HTTP endpoint, and forwards bounded request
frames through `AF_UNIX` to the independently held gateway on the
host. The host owns the external TLS client, URL pin, and API credential.
Only the gateway response bytes return to the child. No child can
directly ask the host transport to send an arbitrary URL.

The host Unix gateway validates `SO_PEERCRED` when Linux provides it.
In confined mode it checks the peer process's **kernel**
`/proc/<pid>/ns/net` inode against its own and rejects a peer still
in the host network namespace. This proves a distinct network
namespace at the connection boundary, not which specific OpenCode
binary produced the packet. The gateway still verifies one frozen
Hashmarks packet, single original call ID, arm and prompt and refuses
retries or extra post-treatment requests.

### E259 — same containment for native session read-back

The exact post-run `opencode session list` and `opencode export`
commands run under fresh deny-egress bwrap namespaces using the same
isolated config/data mounts. They cannot regain ambient host network
access merely because the provider call finished. The E253 raw
OpenCode export is still required to match the exact provider request
before ATIF or a host receipt is published.

A successful confined run writes a create-only
`trial-execution.json` outside the evaluated workspace, preserving
its observed gateway namespace peers, bwrap binary SHA-256, Hashmarks
SHA-256, session/ATIF/host-receipt result and any failure state.
Qualified confined trials additionally receive a create-only
`trial-execution.attestation.json` with a separate host-key MAC
binding the exact execution JSON bytes, trial/manifest identity,
SHA-pinned binaries, observed namespace peer count and SHA-256 of the
existing signed provider input receipt. The E260 verifier checks this
signature with externally custodied host-key bytes and rejects
unsigned replacement of either artifact. The HMAC authenticates the
privileged host's observation, not a remote provider or an independent
kernel auditor.

### E260 — paired pilot readiness; no invented causal result

`benchmark-netns-pilot-readiness` reads every planned trial's host
execution report and calls the separate E256 native-session cohort
verifier. Admission requires both arms, exact source treatment,
native export, HMAC-signed host packet delivery, complete provider
submission, a kernel-distinct namespace peer, single response relay,
pinned bubblewrap/Hashmarks executables and unchanged campaign identity.
Missing arms, altered/unsigned reports, forged kernel peer counts,
replayed provider receipts, source changes, or failed transport cannot
become a completed paired pilot.

```sh
export BENCHMARK_UPSTREAM_API_KEY=<privileged-host-secret>
make benchmark-opencode-native-netns \
  MANIFEST=/frozen/manifest.json TRIAL_ID=<frozen-opencode-trial-id> \
  OPENCODE_BIN=/trusted/opencode OPENCODE_SHA256=<64-hex> \
  OPENCODE_VERSION=<exact-version> \
  HASHMARKS_BIN=/trusted/hashmarks HASHMARKS_SHA256=<64-hex> \
  RUN_ROOT=/isolated/results/<trial-id> WORKSPACE=/disposable/workspace \
  PROMPT_FILE=/frozen/prompt.txt CURRENT_SOURCE=/frozen/source.json \
  HOST_KEY_FILE=/trusted-host/host.key HOST_IDENTITY=<host-id> \
  UPSTREAM=https://api.openai.com/v1/chat/completions \
  APPROVED_ORIGIN=https://api.openai.com \
  CATALOG_SHA256=<64-hex> ORACLE_SHA256=<64-hex> \
  WORKSPACE_SHA256=<64-hex>

make benchmark-netns-pilot-readiness \
  MANIFEST=/frozen/manifest.json \
  NATIVE_RESULTS_ROOT=/isolated/results SOURCES_ROOT=/frozen/sources \
  WORKSPACE=/disposable/workspace HOST_KEY_FILE=/trusted-host/host.key \
  HOST_IDENTITY=<host-id> APPROVED_ENDPOINT_SHA256=<64-hex> \
  BWRAP_SHA256=<64-hex> HASHMARKS_SHA256=<64-hex>
```

**Operational limitation:** The current OpenCode V1 Chat Completions
tool-result envelope and the model presentation treatment must be
verified with the exact pinned OpenCode/provider version before
interpreting a live treatment contrast. These APIs do not establish
the true information retained or used by the model. This feature also
does not solve independent timestamp anchoring, execution filesystem
snapshot parity, reviewer-oracle custody or statistically valid paired
grading. The strongest result is
`paired_pilot_observations_ready_for_independent_grading`, *not*
provider cognition or causal Hashmarks improvement.

The new attack ring is entirely model-free:

```sh
uv run --no-project python -m unittest benchmarks.tests.test_native_netns_relay -v
uv run --no-project python -m unittest benchmarks.tests.test_native_netns_pilot_readiness -v
uv run --no-project python -m unittest discover -s benchmarks/tests
```

The static tests verify bwrap flags/mounts and fail-before-work, and the
socket tests perform real local TCP/Unix traffic with a mocked upstream
provider. No running provider-backed or independently graded matched
campaign is claimed by CI.


## E261–E264: effective netns kernel admission and HMAC-bound diagnostic

PR #227 introduced an explicit `bwrap --unshare-net` configuration and
Unix-socket provider relay. **A command line is not evidence that this
Linux host can create the requested kernel boundary.** The next bundle
adds a point-in-time executable preflight before any native trial run
root, socket or model request is created.

```sh
make benchmark-native-kernel-probe
```

This performs no agent/model run. It SHA-pins the discovered bwrap and
Python executables, runs a fixed Python probe under bwrap with the same
single-owner readonly runtime-root allowlist, and independently compares
host vs guest `/proc/self/ns/net` inode. The probe must observe **only
`lo`**, successfully bind a loopback socket, and receive a definite
no-route-to-host/network error for an attempted TCP connection to the
reserved TEST-NET-2 address `198.51.100.1:443`. A timeout, ambiguous
return, host namespace reuse, additional guest interface, absent
kernel permission or changed bwrap/Python bytes rejects the probe.
The check occurs before trial work; there is no automatic unconfined
fallback.

Qualified preflight diagnostics are attached to each new
`trial-execution.json` as `native_kernel_preflight` and are covered
by #227's privileged host MAC over the execution bytes and the original
provider submission receipt. The separate paired pilot audit requires
the exact probe schema, a distinct guest namespace, verified bwrap
SHA, a pinned probe-program digest, and explicit **false** values for
independent egress and process-origin claims. Old reports missing that
field are not silently treated as qualified. No historical campaign
receipts are overwritten.

The Unix gateway now increments observed namespace-peer counts only
after a complete authenticated request and validated response. An
unauthorized peer, malformed request, rejected Hashmarks packet, or
failed upstream dispatch must not increase this diagnostic.

**Scope and nonclaims:** The preflight observes a *separate test*
namespace on the same Linux host, not an independent kernel audit of
the subsequently spawned OpenCode process. One denied external TCP
route is not exhaustive egress analysis; it also cannot certify process
origin, external-clock preregistration, independent oracle review,
provider cognition or Hashmarks uplift. The signed receipt authenticates
what the privileged host recorded, not whether an external auditor
witnessed it.

Model-free regressions:

```sh
uv run --no-project python -m unittest benchmarks.tests.test_native_netns_kernel_probe -v
uv run --no-project python -m unittest benchmarks.tests.test_native_netns_pilot_readiness benchmarks.tests.test_native_netns_relay -v
uv run --no-project python -m unittest discover -s benchmarks/tests
```

## E265–E268: native mount isolation / real worker pre-model admission

An operating kernel `--unshare-net` boundary alone is not proof that
the **full** OpenCode execution container has the intended file mounts.
After E261's point-in-time kernel check, the privileged launcher now
executes a second, fixed, bounded stdlib preflight using precisely the
same `build_isolated_argv` worker wrapper (including the native Python
relay, guest loopback server and provider Unix-socket mount) that is
used for the actual OpenCode trial. A sandbox probe failure is terminal
**before the first model/provider invocation**. It leaves host-side
diagnostics possible without any model or native-agent work.

The guest checks that the bound Git workspace, pinned executables, and
selected Hashmarks MCP command are visible; provider configuration has
the exact captured-provider allowlist and **cannot be opened for writing**;
the five isolated HOME/XDG roots support create/delete; and the Unix
provider socket appears as a real socket at `/run/gateway/upstream.sock`.
The host secret path must **not** exist inside the guest and the child's
environment must not expose provider API-key names. The probe itself does
not connect to an upstream provider, execute Hashmarks, or issue a
model request. No alternative unconfined launch is attempted.

Before creating run-root or gateway, host admission also rejects
workspaces/run roots or the privately held host key under any readonly
runtime root (`/usr`, merged system roots), under the evaluated workspace,
or under publicly mounted per-trial outputs. It rejects symlinked aliases,
and refuses OpenCode/Hashmarks binaries inside agent-writable workspaces
or XDG/output roots. This stops a trusted signing key from being
accidentally included in the broad system runtime bind even when it is
not explicitly mounted as a secret.

The privileged runner writes `native_mount_preflight` into the
create-only execution report **only after** the probe actually exits
successfully. The existing independent host MAC covers those exact
bytes together with the signed model-input/provider-submission receipt.
The pilot read-back must validate this new fixed probe program digest,
worker source SHA-256, bwrap identity, gateway config digest, and every
true/false claim **for every frozen arm**; old reports without it cannot
qualify. Both trial and isolation receipt schemas advance to v2.

These are **local host observations**, not independent live
process-origin attestations, full network-egress proof, MCP semantic
parity, model attention, preregistration, independent oracle review,
or causal uplift. Real target-host bwrap/OpenCode/Hashmarks and
model-backed paired pilots still need independent field validation.

Model-free tests:

```sh
uv run --no-project python -m unittest benchmarks.tests.test_native_mount_preflight -v
uv run --no-project python -m unittest benchmarks.tests.test_native_netns_pilot_readiness -v
uv run --no-project python -m unittest discover -s benchmarks/tests
```


## E265–E268: Native OpenAI tool-message parity and intervention admissibility

The earlier E257–E264 work introduces a host-owned provider gateway, exact
native OpenCode session export, Linux netns isolation, and a host HMAC on
observed tool submission. These prove bounded **observations**, but they
do not by themselves prove that the purported intervention could be carried
by the real provider request's *wire representation*.

### E265 — typed, actual provider envelope rather than equivalent meaning

The existing presentation treatment declares a **structured JSON object**
for its control and a **canonical JSON string** for its variant.
OpenAI Chat Completions `role: tool` message content is text (or
an explicitly typed collection of content parts), not an arbitrary JSON
object as the `content` value. A JSON object may be a legitimate Hashmarks
source, but using it directly as native tool message content is not a
supported OpenAI tool-message request. Converting it to a JSON string,
or to a single text part, would *change the declared structured surface*.
It is not valid to claim the original structured arm was delivered
because both formats decode to the same semantic JSON object.

The versioned `native_chat_transport_parity.py` contract therefore
accepts **only exact plain text** for the declared `text` presentation
on this Chat Completions transport. It refuses JSON objects, arrays,
stringified/coerced structured objects, differently formatted but
semantically equal JSON, and extra content parts. It does not change
the original source corpus or intervention definitions to make a trial
pass. No new adapter syntax or artificial 'structured' transport is
silently invented.

### E266 — fail before native model work

For `--require-netns-isolation`, the pinned launcher now validates its
selected frozen intervention **before creating the run root, opening
the gateway, starting OpenCode, or issuing a model request**. If the
selected structured treatment does not fit the Chat Completions tool
wire contract, admission raises
`native-chat-tool-content-shape-not-supported-before-model-work`.
The existing unconfined controlled-host fixture path retains its
distinct legacy contract rather than mislabeling it native OpenCode.

### E267 — observe exact outbound bytes

The isolated gateway separately checks the **real request's** linked
tool message. Only the original frozen UTF-8 text surface can pass;
`json.loads()`, whitespace reformatting, a nested object, arrays, or
an alternate but semantically equivalent representation are not
acceptable substitutes. The gateway's HTTPS owner still forwards
exactly the submitted request and captures the bounded response once.
No fake session or new model-input receipt is minted from preflight.

### E268 — study-wide feasibility before running a pilot

Both tools read a frozen manifest and one independently provisioned
`SOURCES_ROOT/<trial-id>.json` per cell, with exactly `current` and
`replaced` keys:

```sh
make benchmark-native-chat-parity \
  MANIFEST=/frozen/manifest.json SOURCES_ROOT=/frozen/sources

make benchmark-native-chat-parity-qualify \
  MANIFEST=/frozen/manifest.json SOURCES_ROOT=/frozen/sources
```

The descriptive command emits per-arm compatibility and counts without
claiming execution. The strict command exits nonzero unless **both arms
of every frozen pair** have supported, unchanged native transport shapes.
For the existing structured-object versus canonical-text
presentation treatment, the control is currently **unsupported**.
A freshness trial with `presentation=structured` is likewise not
automatically upgraded by stringifying its JSON source. Thus the
current native Chat Completions path is **not qualified for the planned
paired empirical comparison**, despite passing its netns and receipt
tests. This is a compatibility finding, not a result about Hashmarks.

Before a live paired campaign, the study author must explicitly freeze
a revised presentation design grounded in supported real native formats,
or implement and verify a distinct provider format that genuinely
preserves the structured type. Such a change requires its own
source/design manifest identity and adversarial native end-to-end proof;
never patch one arm mid-campaign.

The attack ring includes semantic-equal/byte-different JSON, nested
values, content-part arrays, false structured parity, omitted/extra
source authorities, actual gateway request mutation, and fail-before-work
without run-root or model attempts:

```sh
uv run --no-project python -m unittest benchmarks.tests.test_native_chat_transport_parity -v
uv run --no-project python -m unittest discover -s benchmarks/tests
```

No real OpenCode/provider campaign, independent oracle review, process
exclusivity proof, model attention, or causal uplift is asserted.


### Host-attested direct-vs-deferred information delivery pilot

The `benchmarks/harbor/trusted-delivery-timing-v1.json` sample freezes a
third **external-host** intervention alongside the existing presentation and
freshness studies. It is **not runnable with unmodified Codex/OpenCode/Claude
Code**: those harnesses do not yet expose trustworthy provider-request
boundaries to this evaluation code. The sample model string is a placeholder;
choose a real, pinned model and the same frozen task/repository/oracle for
both arms before freezing a manifest.

Study `timing` uses two assigned arms (the existing deterministic,
seed-bound `trusted_treatments.build_manifest`):

- `control = direct-first-request`: send the identical structured,
  pinned Hashmarks packet in **provider request 1**, with no earlier
  model invocation.
- `variant = deferred-second-request`: send **provider request 1**
  with the same model, settings and non-tool prompt but *no tool message*,
  call `TrustedModelRequestCapture.begin_pre_exposure_transport()` and
  `record_pre_exposure_submission()` after observing a bounded 2xx response;
  send the identical structured tool result in **provider request 2**,
  with the identical non-tool prompt/settings. The second request also
  requires `begin_provider_transport()` and
  `record_provider_submission()`.

Only the host possessing the independent key may capture actual outbound
request bytes and write the create-only, MAC-bound model-input receipts;
agent messages, MCP outputs and ATIF text cannot attest delivery. Both
requests must use the same canonical settings/non-tool prompt; the first
request must contain no tool message; the final delivered packet must be
bound to the actual returned Hashmarks packet and the frozen task identity.
Skipping the prior request, omitting a provider submission, changing a
model/prompt/packet, using the wrong sequence, or repeating a transport
attempt **fails before a delivery receipt can be published**. The
host-input verifier checks both request-sequence and authenticated
intervention facets; the live intervention audit requires complete,
signed **final provider submission** for timing, with an authenticated
digest of the prior request and its successful response.

The trusted assignment manifest can be frozen using the existing CLI:

```sh
uv run --no-project python -m benchmarks.harness.trusted_treatments \
  --design benchmarks/harbor/trusted-delivery-timing-v1.json \
  --output /secure-external-host/timing-manifest.json \
  --source-contract-identity sha256:<reviewed-source-contract> \
  --host-build-sha256 <64-lowercase-hex> \
  --assignment-seed-sha256 <64-lowercase-hex>
```

The named secure output directory, pinned digests and host-side integration
must be supplied by the independent operator; these placeholders are not
working execution credentials. The existing
`benchmarks.harness.intervention_audit` and
`independent_campaign_qualification` paths analyze immutable Harbor
bundles plus external host attestations. The model-free regression ring is:

```sh
uv run --no-project python -m unittest \
  benchmarks.tests.test_trusted_host_campaign \
  benchmarks.tests.test_host_attested_interventions -v
```

**Interpretation boundary:** the variant issues one extra provider request,
and the model may acquire extra conversation state before evidence arrives.
That is a **request-count and model-state confound**, not isolated clock
timing; no pure timing causal claim is made. Source selection, frozen
assignment, signed transport observations and descriptive matched outcomes
make this design auditable, but they do not prove randomization,
pre-registration time, provider cognition, or causal uplift. A genuine
single-variable timing study would require a host capable of enforcing
delivery delay without adding a provider invocation, with separately
attested model-input ordering and equivalent model state.


### Executable trusted-provider transport for the deferred first request

The trusted-host timing pilot has a **real transport adapter** for both provider
requests (still host-integrated, not a modification to native Codex/OpenCode
or Claude Code). The new
`dispatch_verified_pre_exposure_chat_request` and
`dispatch_verified_pre_exposure_chat_response` from
`benchmarks.harness.trusted_http_provider` accept the exact serialized
OpenAI-compatible **tool-free** provider request bytes. Only the assigned
deferred arm can invoke them. The host must own the independent credential,
host key, request construction, and the final model invocation / ATIF capture.

Before network I/O, the first-stage adapter checks the frozen arm, model,
request number, permitted non-tool message roles, approved HTTPS origin,
exact provider path, credential shape and request size. It then starts the
**single allowed provider transport attempt**, uses the same proxy-disabled,
redirect-denying TLS helper as the evidence-bearing request, and records the
prior submission only after a bounded, nonempty, valid 2xx chat-completions
response. A 429, redirect, truncated body, invalid JSON, empty choices,
transport exception, oversized response, or retry never produces a signed
delivery receipt. Provider errors and response contents are not logged.

For request 2 use the existing `dispatch_verified_chat_request` (or its
privileged response-relay variant), with the original returned packet,
`request_sequence=2` and the same prompt/settings. It now checks that
the previously observed provider origin matches the current one **before**
performing network work. The existing `TrustedModelRequestCapture.finalize`
requires both the prior submission and the evidence-bearing 2xx response
before MAC-signing the create-only receipt against immutable ATIF.

The adapter returns submission metadata to regular callers. The
`..._chat_response` variant returns bounded upstream bytes to an
independently trusted host for provider relay; those bytes must never enter
the benchmark results or a model-visible evidence packet. The signed
`prior_transport_sha256` is a host-key-holder assertion tied to the
submitted request/response identity, **not provider processing or model
attention proof**. No live credential or model is supplied here.

Model-free regression:

```sh
uv run --no-project python -m unittest \
  benchmarks.tests.test_trusted_prior_request_transport \
  benchmarks.tests.test_trusted_http_provider \
  benchmarks.tests.test_trusted_host_campaign -v
```

## E269–E272: paired native transport and tool-call ancestry

The OpenAI-compatible Chat Completions boundary only admits **text**
`role=tool` content. The frozen presentation intervention currently
contrasts a nested structured JSON control against a plain-text variant;
the structured control is not representable on that boundary without
changing the preregistered treatment. An isolated run now checks **both**
frozen arms before constructing a gateway or executing an agent. If only
the selected text variant is wire-compatible, the paired study fails
before any model work by default.

`--allow-unpaired-native-diagnostic` is an explicit opt-in **for a
compatible single text arm only**. It permits a plumbing/transport
diagnostic without inventing a compatible paired control. Its
`native_chat_pair_preflight` receipt is labeled `diagnostic_only=true`
and `paired_transport_compatible=false`; downstream paired pilot
read-back refuses to grade it as a randomized contrast. This is
not a workaround for the missing structured transport.

In isolated mode the gateway now enforces the strict native Chat
tool-content wire contract rather than the permissive legacy host
fixture path. The host-observed request must also contain an actual
assistant `tool_calls` entry for the known Hashmarks function and
exact `tool_call_id` immediately preceding the sole corresponding
`role=tool` result. Orphaned, foreign, duplicated, or parallel
tool calls are rejected before any privileged provider dispatch. The
original request bytes are relayed unchanged; no automatic conversion
of structured JSON into strings is accepted.

The independent paired pilot verifier reopens the frozen source files
to check the entire population's wire compatibility and compares each
signed `native_chat_pair_preflight` against the source representation
and assigned pair identity. Its signer and verifier both require the
strict gateway enforcement flag, refuse missing or modified receipts,
and never treat diagnostic-only trials as eligible paired trials.

**Limits:** Assistant-to-tool ancestry is syntactic in a captured
request; it is not proof of actual MCP execution, provider attention,
independent process origin, or causal improvement. The current
structured-versus-text design remains incompatible with a *native*
Chat Completions paired experiment. Change the preregistered
representation design or use an independently validated transport;
never silently rewrite the frozen intervention.

Model-free regressions:

```sh
uv run --no-project python -m unittest benchmarks.tests.test_native_chat_pair_integrity -v
uv run --no-project python -m unittest benchmarks.tests.test_native_chat_transport_parity benchmarks.tests.test_native_netns_pilot_readiness -v
uv run --no-project python -m unittest discover -s benchmarks/tests
```


## E269–E272: Fresh frozen compact-vs-indented Chat tool text study

**E269 — a genuinely new design, not a rewritten older result.**
The original `presentation` study remains `structured` versus `text`
and is still **not** admissible on the native Chat Completions wire.
This bundle adds a separate `text_presentation` study to the same
strict manifest author, with exactly these new arms:

```json
{
  "schema": "agentscookbook.host-attested-intervention-design.v1",
  "study": "text_presentation",
  "campaign_id": "new-independent-campaign-identity",
  "model": "pinned-provider-model",
  "harnesses": ["opencode-native"],
  "tasks": ["source-owner"],
  "replicates": 1,
  "arms": {
    "control": "compact-json-text",
    "variant": "indented-json-text"
  }
}
```

Only the independent study author supplies the campaign ID, assignment
seed, model, source-contract identity and input sources **before** any run.
The original study's frozen authority is never retagged, migrated or
overwritten. The manifest SHA-256 and design SHA-256 change by construction.

Both new treatments carry *actual UTF-8 JSON text*, not a JSON object in
the provider tool message. The control uses exact canonical compact
JSON; the variant uses `sort_keys=True, ensure_ascii=False, indent=2`
JSON. The source is still an independently pinned exact Hashmarks
packet with one semantic digest and one repository generation.
The selector rejects replaced sources, source drift, missing/invalid
content, and any variant whose pretty and compact strings are equal
(e.g. empty JSON object). Exact content bytes, presentation labels,
source identities and surface SHA-256 remain explicit.

**E270 — same safety property at the actual host boundary.**
The isolated native gateway admits these two new text presentations,
but no nested-object coercion, alternate JSON indentation or content
arrays. The trusted request recorder stores the **observed original**
tool-message digest for exactly one real linked call, and signs the
study/arm/presentation/generation/semantic/surface identities.
The independent HMAC verifier now explicitly enforces the legal
arm label for **each** study: the old `presentation` controls retain
`structured`, `text_presentation` uses compact versus indented,
and timing/freshness keep their original constraints.

**E271 — independent native request parity readback.**
`native_text_presentation_readback.py` first rechecks the entire
E260–E264 native kernel/isolation/session/transport population and the
new frozen per-trial source list. For every cell it extracts the
Hashmarks call ID from the saved native OpenCode session, rebuilds
the exact expected `{"role":"tool","tool_call_id":...,"content":<text>}`
field and checks its canonical SHA-256 against the independently
host-HMAC-verified `model_message_sha256` in the actual transport
receipt. A forged call ID, altered string, switched arm, missing
partner or modified host receipt fails the whole readback. It does
not fabricate a replacement request or infer attention from a hash.

**E272 — pure pair qualification before any kernel/model work.**
The confined launcher validates **both** text surfaces and their
semantic/generation match before the kernel probe, gateway startup,
run-root creation or model dispatch. The separate full-cohort
feasibility preflight also requires externally provisioned,
per-trial frozen source files and verifies complete pair coverage.

```sh
# Freeze a NEW design and seed. Use real SHA-256 inputs, no fallback secrets.
uv run --no-project python -m benchmarks.harness.trusted_treatments \
  --design /frozen/new-text-presentation-design.json \
  --output /frozen/new-text-presentation-manifest.json \
  --source-contract-identity sha256:pinned-hashmarks-contract \
  --host-build-sha256 <64-hex> \
  --assignment-seed-sha256 <64-hex>

# Read-only syntax/semantic pair feasibility: this runs NO agent.
make benchmark-native-chat-parity-qualify \
  MANIFEST=/frozen/new-text-presentation-manifest.json \
  SOURCES_ROOT=/frozen/new-per-trial-sources

# Only after actual native runs and host-signed receipts exist:
make benchmark-native-text-readback \
  MANIFEST=/frozen/new-text-presentation-manifest.json \
  NATIVE_RESULTS_ROOT=/private/native-results \
  SOURCES_ROOT=/frozen/new-per-trial-sources \
  WORKSPACE=/disposable/identical-workspace \
  HOST_KEY_FILE=/privileged/host.key \
  HOST_IDENTITY=<registered-provider-host> \
  APPROVED_ENDPOINT_SHA256=<64-hex> \
  BWRAP_SHA256=<64-hex> HASHMARKS_SHA256=<64-hex>
```

**Current readiness boundary:** Both new arm *wire types* can be
represented correctly by Chat Completions, unlike the original study,
and model-free transport fixtures validate byte-for-byte host capture.
This does **not** establish that unmodified OpenCode/Hashmarks emits
the selected compact or indented result on demand. No automatic MCP
response interception, covert provider-request rewriting, or agent-output
patching has been introduced. A live native run still fails closed if
the chosen presentation is not exactly observed from the real adapter.
The new readback remains unqualified without actual native + provider
execution, external host custody and separate grading. In particular,
the new experiment measures **JSON text formatting**, not
structured-object-versus-text representation, and any eventual
performance contrast must be described accordingly.

Model-free test ring:

```sh
uv run --no-project python -m unittest benchmarks.tests.test_native_text_presentation -v
uv run --no-project python -m unittest benchmarks.tests.test_native_text_presentation_readback -v
uv run --no-project python -m unittest discover -s benchmarks/tests
```

No empirical run, real OpenCode SDK parity, independently audited
egress exclusivity, provider cognition, randomized causal benefit,
or independent oracle judgement is asserted by these tests.

## E273–E276: provenance of a **provider-issued** Hashmarks tool call

Previous strict native Chat request checks validated the assistant/tool
message adjacency within the *client's continuation*. That was not
proof the HTTPS provider had ever issued that call. A forged assistant
tool-call entry could have been attached to a matching Hashmarks result
by a hostile or malfunctioning client.

The privileged native gateway now records the upstream **pre-evidence
response** directly from its HTTPS transport (the response passed to
the agent) and extracts exactly one completed native Hashmarks function
call. Both ordinary Chat Completion response JSON and bounded, complete
SSE `delta.tool_calls` streams are supported. Each observed call binds
its exact ID, bounded allowlisted Hashmarks function name, byte-exact
JSON arguments SHA-256 and source provider response digest. Unknown,
malformed, parallel, incomplete or unclosed tool issues fail before
a privileged treatment-bearing provider submission. If the response
contains no issued call, the agent cannot introduce one afterward.

When the later native request carries `role=tool`, the gateway requires
that the immediately preceding client-repeated assistant tool-call has
**the same ID, function name and original argument bytes** as the
host-observed provider response. Only then does it apply the existing
strict treatment-surface check and forward the identical serialized
model request. The gateway does not synthesize or rewrite an assistant
call or tool result. A second tool-free request after an issued tool
call is rejected, and a failed/incomplete pre-evidence response makes
the gateway terminal (no try-another-request fallback).

The host's already signed, create-only `trial-execution.json` embeds
`provider_issued_tool_observed`,
`provider_issued_response_sha256`, and
`provider_issued_tool_continuation_verified`. The privileged
isolation signer and separate paired-pilot readback require all three,
including a valid source response SHA-256, for every planned arm.
Older isolation receipts without provider-issued provenance no longer
qualify. Model-free regression attacks cover forged/unissued calls,
changed arguments/function/ID, parallel tools, malformed chunks,
truncated SSE, and unsigned replacement of the host-observed
response identity.

**Limits:** This closes one *host-observed transport* lineage gap.
It does **not** establish that Hashmarks MCP executed, that an external
auditor observed the client OS process, that the model reasoned with
the evidence, or that the experiment has a measurable causal effect.
It also does not solve the historic structured-object vs native
plain-text incompatibility or permit diagnostic-only trials to be
graded as paired interventions.

```sh
uv run --no-project python -m unittest benchmarks.tests.test_native_provider_tool_provenance -v
uv run --no-project python -m unittest benchmarks.tests.test_native_chat_pair_integrity benchmarks.tests.test_native_chat_transport_parity benchmarks.tests.test_native_netns_pilot_readiness -v
uv run --no-project python -m unittest discover -s benchmarks/tests
```

## E273–E276: real native Hashmarks stdio interception, fail-closed

The previous native Chat and provider-side tests prove that a *specific*
tool-message presentation can be observed and signed. They do **not**
show that a real Hashmarks MCP binary returned the frozen original packet
before an intervention. The verified native MCP stdio bridge adds that
missing local call/response boundary as an **explicit, opt-in** mode.

**E273 — freeze the source, not just the presented text.** The host
derives a create-only `native-mcp-contract.json` from the already
selected frozen trial, original Hashmarks packet, real exact hashmarks
binary SHA-256 and the intended model-visible text surface. The contract
pins trial, manifest, generation, semantic and surface identities.
The original packet and selected UTF-8 text must decode to the same
canonical JSON; no foreign or stale source can enter the bridge.

**E274 — observe the actual local JSON-RPC response.** When configured,
OpenCode starts the stdio bridge rather than invoking `hashmarks mcp`
directly. The bridge itself launches the exact frozen Hashmarks binary
with `--workspace <repo> mcp`. JSON-RPC initialization and non-subject
requests are forwarded. For exactly one `tools/call` of a supported
Hashmarks operation, the original Hashmarks output must contain exactly
one structured JSON text block equal to the frozen source. Optional
`structuredContent` must match the same source. Only then may the
bridge replace the MCP text block with the selected frozen presentation.
Original errors, unexpected tool names, extra subject calls, ambiguous
multi-block outputs and changed/unknown source packets terminate with
an error. No successful Hashmarks tool response is generated without a
real matching subprocess response.

**E275 — exact runtime and mount authority.** The bridge and its small
source verifier are mounted read-only into the Linux network namespace
alongside the pinned contract. The strict mount probe checks those
files exist and that OpenCode's *single* Hashmarks MCP command points
to the Python bridge with exactly the source path, binary identity and
workspace chosen by the host. Host source hash checks detect replacement
during the trial. Host-side provider credentials are never passed to
the subprocess. The old direct MCP path remains unchanged; activate
the new path deliberately:

```sh
make benchmark-opencode-native-netns VERIFIED_NATIVE_MCP_BRIDGE=1 \
  MANIFEST=/frozen/manifest.json TRIAL_ID=<assigned-trial-id> \
  OPENCODE_BIN=/trusted/opencode OPENCODE_SHA256=<64-hex> \
  OPENCODE_VERSION=<pinned-version> \
  HASHMARKS_BIN=/trusted/hashmarks HASHMARKS_SHA256=<64-hex> \
  RUN_ROOT=/private/results/<trial-id> WORKSPACE=/disposable/workspace \
  PROMPT_FILE=/frozen/prompt.txt CURRENT_SOURCE=/frozen/source.json \
  HOST_KEY_FILE=/privileged/host.key HOST_IDENTITY=<host-id> \
  UPSTREAM=https://api.openai.com/v1/chat/completions \
  APPROVED_ORIGIN=https://api.openai.com \
  CATALOG_SHA256=<64-hex> ORACLE_SHA256=<64-hex> \
  WORKSPACE_SHA256=<64-hex>
```

The existing study-wide pair admission still applies, including the
explicit diagnostic-only handling for legacy incompatible presentations.

**E276 — executable, model-free stdio proof.** The attack ring launches
the actual bridge process owner against a deterministic executable
MCP fixture, using real stdin/stdout pipes. It verifies a benign
`tools/list` pass-through, one exact packet replacement, byte drift,
original tool errors, mismatched `structuredContent`, unknown calls,
retries, stale frozen source/binary and unsafe config/mount behavior:

```sh
make benchmark-native-mcp-bridge-tests
uv run --no-project python -m unittest discover -s benchmarks/tests
```

**Qualification limits:** CI does **not** launch a real Hashmarks
release, OpenCode executable or model provider, and cannot establish
which MCP output shape a particular installed Hashmarks version uses.
An unsupported Hashmarks response fails closed and needs an explicitly
versioned adapter and a focused regression, never automatic normalization.
**Real local Hashmarks protocol smoke (no model/provider):** After
pinning a particular installed standalone Hashmarks executable and
capturing a matching *original* source packet, the host can run the
same bridge executable directly against that binary. This sends actual
MCP `initialize`, `tools/list`, and one explicit `tools/call`, then
rejects missing tools, incompatible output shapes, missing responses,
original packet drift and duplicate-source observation:

```sh
make benchmark-native-mcp-live-smoke \
  MANIFEST=/frozen/manifest.json TRIAL_ID=<assigned-trial-id> \
  HASHMARKS_BIN=/trusted/hashmarks HASHMARKS_SHA256=<64-hex> \
  WORKSPACE=/disposable/repository \
  CURRENT_SOURCE=/frozen/source.json \
  TOOL_NAME=task_evidence TOOL_ARGS_FILE=/frozen/tool-arguments.json
```

The operator selects the exact installed Hashmarks operation and its
bounded JSON arguments. This invokes the real installed executable in
a scrubbed local HOME/XDG environment, but is not a network namespace
or OpenCode/provider execution and has no independently held source
signature. MCP version/tool schema differences are **explicit failures**,
not grounds to invent compatible tool results. CI exercises this entry
with a deterministic executable stand-in and cannot certify a user
installation.

The bridge's in-namespace process observation is not an independent
host attestation of the original Hashmarks response. Provider gateway
and native-session evidence still independently check whether the
selected text reached an outbound model request. No model attention,
exclusive process origin, completed paired empirical pilot or causal
effect is claimed.


### Campaign-wide publication gate for host-attested timing trials

A complete matched treatment campaign is an **all-assigned-cells**
contract, not an average computed from whichever trials survived. The
existing `benchmarks.harness.intervention_audit` now keeps
`coverage_state`, `qualified_matched_pairs`, `expected_matched_pairs`,
`partial_observed_matched_pairs`, and structured issue counts for
diagnostics. But `matched_pairs` and `per_harness_descriptive_effect`
are published **only** when there are no coverage, duplicate,
foreign-cell, treatment-drift, or corrupt-bundle issues and all assigned
matched pairs qualify. In every other case,
`comparison_publication_state=WITHHELD_INCOMPLETE_CAMPAIGN` and both
comparison payloads are empty. This avoids accidental positive results
from survivorship-biased subsets. The existing scoring oracle is unchanged.

The independent campaign qualifier now also **requires** the audit's
`PUBLISHED_COMPLETE_DESCRIPTIVE` state and complete manifest alignment
before it can admit a descriptive comparison. For every `study: timing`,
provider submission read-back is mandatory even if the operator omitted
`--require-provider-submission`. Every assigned trial must have
MAC-verified complete model-input evidence and provider submission
covering each returned packet. Missing/changed host receipts or
partial provider submission invalidate publication. The report identifies
`provider_submission_policy=MANDATORY_TIMING`, expected versus
verified submission-trial counts, and
`descriptive_comparison_publication_admitted`.

An authenticated reviewer seal and complete trial coverage support only
an **evidence-qualified descriptive comparison**, never a causal timing
uplift claim: the deferred arm still incurs an additional model request,
and external reviewer claims do not prove trustworthy time ordering or
model attention. A standalone intervention audit is diagnostic; the
independent manifest/seal and provider-readback qualifier is the final
publication boundary.

Model-free regressions use the actual two-stage fake HTTPS dispatch,
create-only signed host receipts, immutable ATIF packet identities and
real host MAC verification. Only Harbor's immutable bundle loader is
replaced with synthetic scored receipts.

```sh
uv run --no-project python -m unittest \
  benchmarks.tests.test_timing_campaign_qualification \
  benchmarks.tests.test_trusted_http_provider \
  benchmarks.tests.test_host_attested_interventions -v
```

## E277–E280: one authority for native MCP request/response correlation

The verified stdio bridge added by E273–E276 binds the *selected* Hashmarks
reply to a frozen source. It did not bind **other JSON-RPC responses** to
requests actually sent by OpenCode. An unsolicited success, duplicate
`tools/list` reply, or type-confused request ID could therefore pass through
as model-visible context without an observed owning request.

The native bridge now keeps a bounded, type-exact request ledger. It records
each forwarded request ID exactly once, admits only one correlated response
(even when unrelated replies arrive out of order), denies unsolicited,
duplicate and ambiguous response envelopes, and distinguishes integer and
string IDs from booleans. Notifications with no IDs remain pass-through only
when they use the JSON-RPC `notifications/` method family with no synthetic
result. The already pinned **single Hashmarks subject result** retains its
one-use, original-packet equality and selected text transformation.

Admission is deliberately finite: at most 128 simultaneous outstanding IDs
and 2,048 IDs per stdio session. Exceeding either bound fails the local trial,
not an automatic retry or a reclassification as evidence absence.

The operator's live stdio smoke also requires exactly one well-formed reply
to each of `native-init`, `native-list`, and `native-subject`; extra,
duplicate, malformed or missing replies cannot be silently dropped by an ID
map before reporting success. A deterministic **real local subprocess** attack
verifies that a fake successful response for an unissued ID terminates the
bridge without emitting the frozen Hashmarks treatment.

Run the bundled model-free local bridge regression:

```sh
make benchmark-native-mcp-bridge-tests
```

These checks prove **local protocol correlation**, not authenticity of the
installed Hashmarks release, native model use, an independent original MCP
response signature, an upstream provider call, or causal uplift. Host-side
provider-issued tool-call provenance and externally attested final outcome
qualification remain separate authorities.


### Two-stage timing: cross-trial transport and empirical qualification

The signed-host transport auditor now understands the same frozen request
sequence as the trusted provider boundary: `control` must deliver evidence
on model request **1**, while `variant` must deliver on request **2**
after a previously submitted tool-free request. Both prior digests must
be zero for the direct control, and both must be present and nonzero for
the deferred arm. Each actual delivery must pass MAC-bound ATIF packet
read-back, 2xx provider-submission verification with exact returned packet
coverage, pinned host identity/build and approved endpoint. Cross-arm
prompt, catalog, oracle, and workspace identities must remain equivalent.

This replaces the former *single request-1-for-all-arms* assumption in
`host_transport_provenance.audit_transport_provenance`, which prevented
otherwise qualified deferred trials from passing the independent empirical
read-back. The observation reports a precise protocol name
(`DIRECT_REQUEST_1_VS_DEFERRED_REQUEST_2`) and explicitly retains
`timing_extra_model_request_confound=true`. Prior transport records
remain signed assertions from the host key-holder; they are not external
proof of upstream processing or model attention.

A model-free regression now exercises the complete path using the trusted
two-request fake HTTPS host, its create-only signed receipt, real ATIF
packet verification, complete Harbor campaign qualification and
`assess_empirical_campaign`. Only the immutable graded bundle loader is
stubbed; **no provider/model run is implied**. It establishes a qualified
two-arm *descriptive transport*, while the one-task pilot still fails
the independent eight-task-cluster statistical evidence gate rather than
inventing significance or causal improvement. Resigned tampering of
sequence, prior transport, host, endpoint and prompt, as well as missing
and replayed trial receipts, must fail closed.

```sh
uv run --no-project python -m unittest \
  benchmarks.tests.test_timing_transport_provenance_e2e \
  benchmarks.tests.test_native_provenance_and_empirical_decision \
  benchmarks.tests.test_timing_campaign_qualification -v
```


## E277–E280: verify actual Hashmarks MCP server, catalog and original wire before a pilot

This checkpoint strengthens the E273–E276 stdio bridge *without* creating
a new evidence format inside Hashmarks or redefining its semantics.
The existing standalone Hashmarks native MCP server publishes:
`serverInfo.name=Hashmarks`, a typed `tools/list` catalog with
`inputSchema`, the real native operations `task_evidence` and `find`,
and successful tool results with `structuredContent` on the MCP SDK.
These behaviors are grounded in Hashmarks' upstream
`hashmarks/mcp_server.py`, `hashmarks/mcp_contract.py` and
`tests/test_mcp_native_interop.py`. A different installed version must
still be observed and verified.

**E277 — prove the initialized native server identity.**
The bridge now tracks the observed JSON-RPC `initialize` response,
requires an initialized `Hashmarks` server with a bounded version
string, and refuses extra initialization. A JSON-RPC server that
happens to echo a frozen evidence packet cannot impersonate Hashmarks
merely by replaying that packet.

**E278 — bind response to the actual listed input contract.**
The same bridge observes `tools/list` and requires an unambiguous
catalog of typed object input schemas. The subject result is not
released until its native tool is present in that catalog and its
arguments satisfy declared required fields and basic JSON types;
unknown extra arguments are refused when `additionalProperties=false`.
The genuine Hashmarks tool/server still owns full JSON-Schema validation.
No source is invented, no foreign result substituted, and
`structuredContent` must agree with the original parsed text packet
before the frozen textual presentation can replace it.
This does **not** establish that OpenCode subsequently used the
selected content; its provider gateway and exported native session
remain separate verification owners.

**E279 — inspect a real installed MCP version without guessing its shape.**
The new direct wire probe launches a SHA-pinned Hashmarks executable
in a scrubbed environment and performs bounded sequential native
`initialize → tools/list → tools/call` over actual subprocess pipes,
with a strict timeout and an exact selected tool name. It records
only native server version, catalog/tool-schema digests, original
result digest, explicit content-shape observations and compatibility
flags, **not the returned repository code**. The optional frozen
original packet is used to compare semantic identity; it is never
replaced or silently normalized.

```sh
# Diagnostic even if the installed result uses a different format:
make benchmark-native-mcp-wire-probe \
  HASHMARKS_BIN=/trusted/hashmarks HASHMARKS_SHA256=<64-hex> \
  WORKSPACE=/disposable/repo TOOL_NAME=task_evidence \
  TOOL_ARGS_FILE=/frozen/task-args.json \
  ORIGINAL_PACKET=/frozen/original-packet.json

# Same probe, but nonzero exit unless it can safely feed the current
# E273 bridge with the exact frozen original packet:
make benchmark-native-mcp-wire-probe REQUIRE_BRIDGE_COMPATIBLE=1 \
  HASHMARKS_BIN=/trusted/hashmarks HASHMARKS_SHA256=<64-hex> \
  WORKSPACE=/disposable/repo TOOL_NAME=task_evidence \
  TOOL_ARGS_FILE=/frozen/task-args.json \
  ORIGINAL_PACKET=/frozen/original-packet.json
```

For a real `task_evidence` call, the input is
`{"task":"locate the source-owner behavior"}`, **not** a synthetic
`{"path": ...}` argument; for `find`, use a `query` string.
An installed version whose catalog or original content differs
must fail the strict gate. The result is a compatibility finding, not
a claim about a model's code-edit success.

**E280 — end-to-end protocol attack ring with accurate nonclaims.**
The local executable tests now simulate the real MCP initialize
identity, schema-bearing catalog, subject call and dual-format
structured/text response. Negative cases include spoofed server,
missing tool, duplicate/malformed input schemas, absent required
argument, wrong argument type, foreign argument, original packet
drift and partial/replayed responses.

```sh
uv run --no-project python -m unittest benchmarks.tests.test_native_mcp_bridge -v
uv run --no-project python -m unittest discover -s benchmarks/tests
```

CI executes a deterministic local MCP executable, not a released
Hashmarks/OpenCode/provider stack. The direct wire probe has **no
independent host-signature custody**, OS network isolation or model
calls; a local process can still emit deceptive data. In particular,
this does not prove the original Hashmarks response was independently
host-attested, that native OpenCode received the presentation, or that
paired agent performance improved. Those are requirements for later
real paired trials, not flags that this protocol test may set.


### Native provider-issued call ↔ OpenCode export semantic-argument binding

The host gateway already captures a complete provider-issued Hashmarks
function call before accepting a client continuation. The native trial now
also requires the **saved, completed OpenCode session tool part** to carry the
same call ID, one of the two explicitly approved Hashmarks operations, and
the **entire semantic argument object** from that observed provider response.
The provider's original argument-byte digest is retained for strict
provider-continuation verification; a separate canonical argument digest
supports SDK exports that retain the same JSON object with different
whitespace or key order. Nested fields and non-navigation arguments are
not discarded for the identity check, even though the human-readable ATIF
navigation projection deliberately remains limited.

The new strict join is performed by
`opencode_native_session.inspect_native_export` before any native export,
ATIF or host MAC receipt is written. The confined native launcher supplies
the actual `NativeOpenCodeGateway.provider_issued_tool` and requires its
provider-response SHA-256 to agree with the gateway's observed response.
The completed trial reports
`provider_issued_native_export_arguments_bound`. A synthetic legacy
gateway/ATIF fixture may still be inspected for older tests, but returns
`provider_issued_native_arguments_bound=false` and cannot satisfy the
confined-run strict gate. Only known `task_evidence` and `find` operation
aliases normalize; changing operation, call ID, nested arguments or omitting
provider-issued evidence fails before receipt creation.

This joins **host-observed provider issuance → native-session observation →
verified Hashmarks packet in host-model-input receipt**. It does not prove
that a specific isolated MCP subprocess generated the returned bytes:
the native MCP bridge's own original-packet authority and the guest/host
execution-boundary proof remain distinct. Nor does it establish provider
attention, external native origin, an independently graded outcome, or
causal Hashmarks uplift.

Model-free regression:

```sh
uv run --no-project python -m unittest \
  benchmarks.tests.test_native_provider_session_chain \
  benchmarks.tests.test_opencode_native_session \
  benchmarks.tests.test_native_provider_tool_provenance -v
```

### Opt-in real Linux process execution observation for native OpenCode

The pinned native OpenCode launcher now supports a **host-owned** `strace -f`
observer on the actual `bwrap` native process invocation. This is disabled by
default. Pre-work admission requires an explicitly pinned, absolute tracer
executable and its SHA-256, plus `--require-netns-isolation`. Unconfined
fallback or an incomplete tracer selection is rejected before model work.

An observing host launches the exact isolated command, follows forked process
`execve` events, parses only unambiguous successful syscall observations,
and creates a private, MAC-authenticated diagnostic receipt containing counts
and source digests. Raw syscall logs can contain prompt text and arguments:
they are held outside the sandbox, never added to the report or model input,
and deleted after processing. Receipt bytes are create-only and mode 0600.

To explicitly request it when launching the native pilot:

```sh
make benchmark-opencode-native-netns \
  MANIFEST=/authority/manifest.json TRIAL_ID=... \
  OPENCODE_BIN=/pinned/opencode OPENCODE_SHA256=... OPENCODE_VERSION=... \
  HASHMARKS_BIN=/pinned/hashmarks HASHMARKS_SHA256=... \
  RUN_ROOT=/private/run WORKSPACE=/workspace PROMPT_FILE=/prompt.txt \
  CURRENT_SOURCE=/source.json HOST_KEY_FILE=/trusted/host.key \
  HOST_IDENTITY=trusted-host UPSTREAM=... APPROVED_ORIGIN=... \
  CATALOG_SHA256=... ORACLE_SHA256=... WORKSPACE_SHA256=... \
  REQUIRE_NATIVE_EXECVE_OBSERVATION=1 \
  NATIVE_EXECVE_TRACER=/usr/bin/strace \
  NATIVE_EXECVE_TRACER_SHA256=<pinned-64-character-sha256>
```

Or use the CLI flags `--require-native-execve-observation`,
`--native-execve-tracer-bin`, and `--native-execve-tracer-sha256` with the
existing `benchmarks.harness.opencode_native_trial` command.

Run the local model-free and tamper regression:

    make benchmark-native-host-execve-check

One optional test runs actual Linux `strace` plus a real process when supported
by the host; other tests execute a real subprocess through a simulated
transport adapter **without** claiming synthetic logs are kernel evidence.
Kernel tracing can interfere with a sandbox in some environments. If tracing
fails or is incomplete, the explicitly requested trial cannot qualify;
the launcher never disables the observer to let a trial pass.

**Evidence limit:** recognized `rg`/`grep`-family executable launches are
only a lower bound on some search-related processes. Shell builtins, in-process
library searches, `git grep`, other executables, and operations without an
`execve` are not counted as zero. This is **not** per-tool-call ancestry or
proof that all nested calls were intercepted. It does not populate the
host-authenticated per-parent receipt from PR #248, upgrade the canonical
Harbor search-savings denominator, or establish model attention or causation.
Host-signed syscall observation remains a key-holder assertion, not external
independent kernel attestation.

### Opt-in host-authenticated nested search comparison

Opaque ATIF shell and tool-router calls never qualify as zero hidden searches. The
separate `benchmarks.harness.host_nested_search` verifier accepts *only*
externally MAC-authenticated host observations against exactly matched immutable
bare/Hashmarks bundles. The privileged host must instrument all nested execution
outside the agent workspace and seal actual tool calls using its separately
custodied host key. An agent-origin `nested_calls` field is rejected.

Receipts bind campaign/trial, exact ATIF bytes, expected host identity/build,
all opaque parent IDs, child IDs, tool names and inputs. Every router and shell
must have an explicitly complete observed child list, including nested routers.
Missing parents, duplicate direct/nested IDs, wrong source, forged MAC, unsafe
files, unqualified tool order and unmatched trial identities fail closed.

Model-free local regression including a full immutable two-arm bundle:

    make benchmark-host-nested-search-check

Opt-in verification of existing externally captured data:

    make benchmark-host-nested-search-pair \
      BARE_BUNDLE=/bundles/bare HASHMARKS_BUNDLE=/bundles/treated \
      BARE_NESTED_RECEIPT=/host/bare.json HASHMARKS_NESTED_RECEIPT=/host/treated.json \
      HOST_KEY_FILE=/host/host.key HOST_IDENTITY=trusted-host \
      HOST_BUILD_SHA256=<64-character-pinned-host-build-digest>

The report records explicit, authenticated-nested and combined observed
native-search counts and a matched two-arm delta. It does not overwrite the
canonical Harbor report or retroactively qualify unsigned ATIF evidence.
`host_asserted_complete` is a key-holder assertion, not independent proof
that every operation was intercepted. `native_search_savings_proven`,
`model_attention_proven` and `causal_influence_proven` remain false.

## E281–E284: independently re-read host execve and provider-session custody

The existing `benchmark-netns-pilot-readiness` verifier now re-opens the
exact create-only host-execve attestation for each opt-in native trial,
verifies its HMAC with the separately custodied host key and frozen campaign,
trial, host and build identities, and compares the **exact receipt bytes**
against `native_host_execve_evidence.receipt_sha256`. Successful native exit
status, the host's observed search-process count and conservative no-completeness
claims must also agree. An unrequested or stray execve receipt is rejected.

An independently signed trial-execution report must also bind the native
session's complete provider-issued argument identity to the **same actual
provider-response SHA-256** as the captured gateway. Confined trials without
the binding do not qualify for independent grading readiness.

Opt-in and opt-out trials cannot be mixed within the same frozen paired cohort:
comparing positive observations against absent measurements would be unsound.
Existing fully opt-out trial cohorts remain supported.

The verifier does NOT synthesize host process traces, re-sign evidence, infer
zero hidden searches, turn process launches into tool-call ancestry, establish
kernel tracing completeness or certify randomized causal uplift. A verified
MAC attests its host key holder's statement, not independent OS provenance.
The existing `benchmark-netns-pilot-readiness` CLI and `--require-ready`
flag remain the authoritative command; no second readiness verdict is made.

```sh
make benchmark-native-execve-readback-tests
# Use the existing real frozen manifest, sources, workspace, host key and
# binary pins when invoking benchmark-netns-pilot-readiness.
```

Model-free regressions cover valid host-key receipts, missing/tampered/replayed
receipts, signed but inconsistent counts, forbidden completeness promotion,
mixed observation cohorts and mismatched provider-response identities.


## E285–E288: admit native MCP tool work before dispatch

E285 requires the actual successful and verified native MCP \`initialize\`
and \`tools/list\` responses **before** forwarding a subject \`tools/call\`
to the SHA-pinned Hashmarks executable. An outstanding catalog request is not
authorization. A client that speculatively submits the subject is rejected,
not retried silently or granted authority from a guessed schema.

E286 moves the native tool catalog membership and observable input-schema
checks (required fields, JSON primitive types, additional properties) to the
one canonical \`BridgeState.receive_request\` admission boundary. Previously,
some of these checks ran only when a response was returned, after Hashmarks
could already have performed work. Full JSON Schema validation remains owned
by the Hashmarks server. The bridge still validates the original returned
packet against the frozen source before any selected presentation is exposed.

E287 tests that denied requests cannot enter the JSON-RPC in-flight ledger or
reserve a completed subject identity. E288 exercises the actual local stdio
bridge with a deterministic executable fixture, waiting for real initialization
and catalog replies before issuing a subject call. A test-only native child
writes an observable marker only if it receives \`tools/call\`: malformed
arguments are denied, no marker is created and no selected evidence is emitted.

Existing interface, binary pinning, sandbox execution and bridge contract
format are preserved. There is no second MCP proxy, new agent harness, new
benchmark denominator, or per-call receipt writer.

\`\`\`sh
make benchmark-native-mcp-bridge-tests
uv run --no-project python -m unittest discover -s benchmarks/tests
\`\`\`

These regressions use a deterministic local child; they do not establish
independent native-process origin, full kernel/process tracing completeness,
installed Hashmarks compatibility or model-visible provider causality.
