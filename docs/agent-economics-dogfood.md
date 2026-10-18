# Agent Economics real-agent campaign gate

This empirical closeout gate is intentionally separate from deterministic repository qualification.

## Purpose

Measure whether an external coding agent benefits from the Agent Economics capability bridge while preserving the consumer repository's own authority model.

The external agent owns source edits. Agent Economics owns bounded evidence and execution of explicitly selected repository commands. The consumer repository owns what counts as final qualification.

## Repository-neutral campaign contract

A campaign compares the same starting repository bytes in `baseline` and `bridge` modes.

EvalShot does **not** prescribe repository names, task classes, scenario names, verification commands, CI providers, or certification targets.

The campaign manifest declares:

- arbitrary `scenario_requirements`, each with a `scenario_id` and exact `required_pair_count`;
- `minimum_distinct_repositories`;
- the exact task pairs admitted to the campaign;
- one shared measurement-contract identity;
- per-mode session, isolation, freeze, and post-freeze oracle-access receipt identities;
- cleanup evidence for every participating consumer repository;
- the exact Agent Economics implementation identity;
- producer qualification evidence for that implementation.

Each outcome separately binds:

- the consumer repository identity;
- the exact starting source/defect identity;
- the repository-owned independent qualification authority ID;
- the repository-owned independent qualification evidence ID.

This means one repository may use a hosted CI workflow while another uses a native certification command. EvalShot compares evidence; it does not replace either authority.

## Examples, not normative policy

A Python library campaign might declare scenarios such as:

- `assertion-repair`;
- `import-failure`;
- `affected-dependent-selection`;
- `focused-pass-broader-gate-fail`.

A native runner/release repository might instead declare scenarios such as:

- `local-runtime-acceptance`;
- `native-certification`;
- `package-replay`;
- `release-artifact-identity`.

For example, a repository may bind an independent qualification authority such as `make local-access-runtime-acceptance` or `make certify`, plus exact candidate/archive/patch identities. Those names and semantics belong to that repository; they are not embedded in EvalShot.

The built-in `dogfood-corpus` is a reference/adversarial fixture set for developing Agent Economics. It is not the scenario authority for consumer campaigns.

## Pre-admission qualification

A final empirical pair must be admitted only after its non-treatment authorities are frozen and usable. Do not spend a baseline or bridge session discovering that required campaign authority was unavailable.

Before either mode starts:

- materialize the exact declared starting bytes and verify them independently in that session;
- define `initial_source_id` from the shared immutable starting authority, not from a session-local extraction or traversal algorithm. For an archive-backed task, use the verified archive digest (for example `sha256:<archive-digest>`) in both modes; a derived extracted-tree digest may be retained as supplemental evidence but must not replace the shared initial-source identity;
- freeze the task fixture/corpus, agent profile, measurement contract, and repository-owned independent qualification authority;
- prove the independent qualification authority is obtainable in the intended execution environment, or mark the task setup incomplete before empirical admission;
- freeze one `execution_environment_id` whose evidence covers repository/tool supply, relevant dependency availability, host permissions, network policy, and controller/runtime budgets that can affect the run. Baseline and bridge must bind the same identity;
- for bridge mode, materialize the exact Agent Economics implementation before admission, freeze the command manifest before task evidence is gathered, and prove the declared implementation can execute the bounded bridge entry points. If the exact implementation or manifest cannot be materialized, the bridge setup is incomplete; do not simulate Agent Economics;
- keep setup-incomplete attempts as setup evidence. They are not empirical outcomes and must never be discarded because of a later unfavorable result.

Pre-admission does not require the final local qualification result: that receipt is produced by the admitted bridge run and remains mandatory for a strict pair. It only proves that the declared authorities and treatment are actually available before measurement starts.

## Per-pair strict records

Every pair admitted to a final campaign must first pass:

```bash
python -m agent_economics benchmark-outcomes \
  --input outcomes.jsonl \
  --strict-dogfood
```

Strict mode fails closed unless baseline and bridge bind the same non-null repository, task-fixture/corpus, initial-source, agent-profile, execution-environment, and independent-qualification-authority identities.

The two modes must have distinct run identities. Both outcomes bind final-source and repository-owned independent qualification evidence and prove the oracle was opened only after the outcome was frozen.

The bridge outcome additionally binds the exact Agent Economics implementation, command manifest, local qualification receipt, and a known local-vs-independent-qualification agreement result.

A task specification identity does **not** substitute for the initial-source identity. The latter binds the actual starting repository/defect bytes shared by both modes.

## Final campaign gate

Per-pair validity is necessary but not sufficient. The complete campaign must also pass:

```bash
python -m agent_economics dogfood-campaign \
  --outcomes outcomes.jsonl \
  --campaign campaign.json \
  --artifact campaign-result.json
```

The repository-neutral campaign manifest is `agent-economics-dogfood-campaign` v2.

The validator fails closed unless:

- every task uses a declared scenario;
- observed scenario cardinality exactly matches the manifest;
- repository diversity meets `minimum_distinct_repositories`;
- campaign membership exactly matches the outcome pairs, so unfavorable rows cannot be silently omitted;
- baseline/bridge run identities are globally unique;
- isolation, freeze, and oracle-access receipt identities are globally unique;
- every task binds the campaign measurement contract;
- cleanup evidence covers exactly every participating consumer repository;
- every pair binds one repository-owned independent qualification authority;
- producer qualification evidence binds the exact Agent Economics implementation used by the campaign.

The validator never converts empirical evidence into automatic release, merge, edit, execution, or certification authority.

## Measurement fairness

Use one bound measurement contract in both modes for files opened, evidence bytes, context estimate, tool calls, local commands, repair iterations, verification attempts, failed edits, and time to first correct edit.

Use fresh isolated worktrees/session records so artifacts, prior edits, generated evidence, or mutable repository state cannot leak between modes. Baseline and bridge MAY be executed by the same agent/runtime when the campaign explicitly declares that execution model. In that case, model-memory independence is not claimed: comparability comes from exact starting bytes, distinct workspaces/run identities, frozen per-mode inputs and outputs, no cross-mode artifact reuse, and oracle access only after both outcomes are frozen. Keep host permissions and repository/tool supply equivalent; the intended treatment difference is Agent Economics use.

Freeze each outcome before opening the oracle. Retain failed and unfavorable pairs rather than rerunning and discarding them.

## Hard rules

- Repository bytes must be locally materialized to the coding agent.
- Agent Economics must not edit consumer source or install dependencies.
- A focused PASS is not automatically repository qualification.
- Run the repository-owned independent qualification only according to the repository's declared authority.
- Unknown local-vs-independent-qualification agreement cannot close a strict pair.
- Missing strict receipts make the pair incomplete.
- Temporary Agent Economics materialization/artifacts must remain outside consumer release authority unless the consumer explicitly adopts them.
- Do not add repository-specific parsing, paths, scenario names, commands, or acceptance semantics to EvalShot merely to make one dogfood campaign pass.
- Isolation receipts attest the isolation properties actually enforced by the campaign. They MUST NOT claim stronger isolation than was provided. Separate-agent/model-memory isolation is optional rather than a universal closeout requirement; when the same agent/runtime executes both modes, the receipt must say so and the campaign must still use distinct clean workspaces, run identities, frozen inputs/outputs, and post-freeze oracle access.

## Producer qualification

EvalShot's own deterministic workflow compiles and qualifies Agent Economics, including adversarial campaign-gate regressions. That validates the bridge/validator implementation but does **not** substitute for consumer repository evidence or real-agent campaign evidence.

A campaign executed in one agent/runtime must not self-attest model-memory isolation. It may still qualify when its receipts accurately declare same-agent execution and prove the repository/workspace, artifact, run-identity, freeze, and oracle-access boundaries required above.
