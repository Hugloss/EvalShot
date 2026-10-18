# Contributing to EvalShot

EvalShot owns benchmark execution, comparison contracts, evaluation corpora,
oracles, scoring, reporting, and the Agent Economics support toolkit.
[agentsCookbook](https://github.com/Hugloss/agentsCookbook) owns canonical skills,
agent prompts, flows, and their installation adapters.

## Preserve experiment meaning

Keep source revisions, task inputs, oracle expectations, and receipt identities
explicit. A repository move or documentation change must not silently alter a
frozen experiment. Existing `agentscookbook.*` schema identifiers are versioned
compatibility contracts; change them only as part of an intentional format
change. New runtime work is attributed to EvalShot.

Fix reproducible defects with focused regressions. Prefer existing owners for
process execution, runtime identity, configuration, result persistence, and
scoring. The shared bounded process implementation remains in
`scripts.agent_economics.bounded_process`.

## Model context ownership — do not drift

The **model/provider and agent host** (e.g. OpenCode/Pi), not EvalShot,
configure the actual context window, output limits, and compaction policy.
Do not add a project-wide token window, required reserve, model override,
automatic resize or context-based admission gate in the harness. Do not
move a runtime configuration decision into evaluator settings just because
that makes a benchmark easier to run.

EvalShot observes usage and behavior on the operator-selected configuration.
When token consumption, truncation or compaction cannot be observed,
report that evidence as unknown rather than estimating a qualifying window.
Comparisons may deliberately freeze different **host-provided**
configurations; the suite records the inputs and scores outcomes but
does not own the model-context mechanism.

Keep **experiment-specific time, output, artifact and other limits** distinct
from model context capacity. Those existing frozen experiment contracts
remain valid but cannot turn into a cookbook/runtime-wide context policy.
For the normative boundary, read
[Model Context Observation](docs/model-context-observation.md)
and [agentsCookbook Context Discipline](https://github.com/Hugloss/agentsCookbook/blob/main/protocols/context-discipline.md).

Before merging evaluator/configuration changes, check that no new default
or acceptance requirement incorrectly assumes a particular model window.

## Python and validation

Use uv with the pinned `.python-version`. The stdlib-only harness keeps the
`./benchmark` front door and `uv run --no-project python -m ...` entrypoints.
Test dependencies are declared in `pyproject.toml` and locked in `uv.lock`.

```sh
uv run --no-project python -m unittest discover -s benchmarks/tests -v
uv run --locked --group test python -m pytest tests -q
node scripts/test-cookbook-migration.js --cookbook-root ../agentsCookbook
node scripts/test-skill-evidence-contract.js
node scripts/test-opencode-runtime.js
```

Run the affected Agent Economics qualification modules when changing that
toolkit. The complete module list and additional frozen-suite checks live in
[the validation workflow](.github/workflows/validate.yml).

## Readiness and campaign execution

`BENCHMARK_AGENT` explicitly selects the native agents for preflight, execution,
and scoring. Configuration comes from the explicit `--env-file`; exported shell
values cannot fill missing required benchmark choices.

`benchmark-check` checks each distinct agent/subject pair once. It does not
materialize tasks, apply mutations, run oracles, derive trial identities, inspect
receipts, or invoke a model. `benchmark-check-all` is the separate exhaustive
preflight. Campaign execution must not call either diagnostic implicitly.

Keep model-backed and model-free evidence distinct. No synthetic or mocked
result establishes an empirical effect or a real provider/runtime qualification.

## Cookbook evaluation changes

Keep the skill corpus and fixtures here. Load live definitions with the explicit
`--cookbook-root` input; do not copy live cookbook skills into this repository.
Use positive and control cases for every evaluated skill, and confusion cases
to distinguish neighboring responsibilities. Changes to a cookbook skill and
its corresponding evaluation should link their coordinated pull requests.

CI pins its cookbook input to a commit so unrelated source changes do not
silently alter an evaluation. Update that pin deliberately with the corpus and
record which source revision was validated.

## Pull requests

Explain the concrete problem, changed behavior, affected experiment contracts,
and the checks actually run. Keep local settings, credentials, campaign outputs,
and generated qualification evidence outside tracked source. Preserve the MIT
copyright notice when moving or reusing existing code.
