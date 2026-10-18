# EvalShot

Benchmarks and evaluation tools for coding agents, repository intelligence,
skill discrimination, and agent workflow outcomes.

EvalShot owns the runners, frozen task suites, adapters, oracles, scoring,
reports, and evaluation fixtures. The canonical skills, agents, flows, and
installation adapters live in
[agentsCookbook](https://github.com/Hugloss/agentsCookbook).

EvalShot also owns [observing and evaluating runtime-reported model context usage](docs/model-context-observation.md), [tool-routing measurement](benchmarks/tool_routing.py), and [Agent Economics execution security](SECURITY.md). Model context limits and compaction settings belong to the selected model provider/agent runtime, not EvalShot or the cookbook. The cookbook retains portable prompt guidance and reviewer-selection flows; neither provides duplicate scoring or process-execution implementations.

## Get started

The Python harness uses the standard library and the Python version pinned in
`.python-version`. Install Git, [uv](https://docs.astral.sh/uv/), and Make.
The JavaScript evaluators require Node.js 22 or newer.

```sh
git clone https://github.com/Hugloss/EvalShot.git
cd EvalShot
uv python install
./benchmark --help
```

Validate a frozen suite without running an agent:

```sh
./benchmark validate-suite \
  --suite benchmarks/suites/repository-intelligence/pilot-v1
./benchmark plan \
  --suite benchmarks/suites/repository-intelligence/pilot-v1
```

For a real repository-intelligence campaign, create the explicit configuration:

```sh
cp -n .env.example .env
```

Edit `.env` to select the clean Hashmarks checkout under test, the native agent
or agents, and any required provider environment variable names. The native
agents and subjects must be installed for the selected suite. Then run:

```sh
make benchmark-check
make benchmark
```

`benchmark-check` checks runtime wiring without inference. `benchmark` executes
the configured campaign and may use your model provider. Campaign data is kept
under the ignored `.benchmark-runs/` directory. Read the
[benchmark guide](benchmarks/README.md) for resume, reporting, the Harbor matrix,
context-invariance studies, routing diagnostics, and native execution modes.

## Evaluate cookbook skills and flows

These evaluators need an explicitly selected agentsCookbook checkout. Other
EvalShot benchmark suites run independently of the live cookbook.

From the EvalShot root, clone the cookbook alongside it if you do not already
have a checkout:

```sh
git clone https://github.com/Hugloss/agentsCookbook.git ../agentsCookbook
```

Validate the corpus against that source without a model:

```sh
node scripts/run-skill-benchmarks.js \
  --cookbook-root ../agentsCookbook --validate-corpus
node scripts/run-skill-benchmarks.js \
  --cookbook-root ../agentsCookbook --list
node scripts/run-opencode-benchmarks.js --list
```

Run one skill using your configured OpenCode installation:

```sh
node scripts/run-skill-benchmarks.js \
  --cookbook-root ../agentsCookbook \
  --runtime opencode \
  --skill stale-work-race-review \
  --artifacts-dir .runs/skill-evals
```

Run the cookbook's flow benchmarks:

```sh
node scripts/run-opencode-benchmarks.js \
  --cookbook-root ../agentsCookbook \
  --suite all \
  --artifacts-dir .runs/flow-evals
```

`--cookbook-root` selects skill definitions and cookbook-owned helpers.
`--repo` independently selects the repository where model work takes place;
it defaults to the selected cookbook for these cookbook-specific evaluators.
The corpus and its fixtures stay in EvalShot. See the
[evaluation guide](evals/README.md) and
[discrimination contract](evals/sharp-skill-discrimination.md).

## Agent Economics

The complete portable Agent Economics toolkit is included at
`scripts/agent_economics/`. It provides evidence and comparison tools and owns
the bounded process runner used by the benchmark harness. Keeping this toolkit
together preserves one implementation of those shared process semantics.

```sh
uv run --no-project python -m scripts.agent_economics --help
uv run --no-project python -m scripts.agent_economics doctor \
  --repository-root . --json
```

Its [usage guide](scripts/agent_economics/README.md) covers standalone imports,
bootstrap by exact EvalShot commit, local verification, and outcome comparisons.

## Model-free checks

```sh
uv run --no-project python -m unittest discover -s benchmarks/tests -v
uv run --locked --group test python -m pytest tests -q
node scripts/test-cookbook-migration.js
node scripts/test-skill-evidence-contract.js
node scripts/test-opencode-runtime.js
```

The test-only pytest dependency is locked in `uv.lock`; benchmark execution
remains stdlib-only. The JS migration tests can additionally verify the real
corpus against a supplied checkout:

```sh
node scripts/test-cookbook-migration.js --cookbook-root ../agentsCookbook
```

[GitHub Actions](.github/workflows/validate.yml) also validates frozen suite
definitions and runs the Agent Economics qualification modules. Its cookbook
input is pinned to an explicit commit. Passing model-free checks establishes
implementation and corpus integrity; empirical results require actual campaign
execution and the evidence required by the selected experiment.

## Repository layout

| Path | Owns |
| --- | --- |
| `benchmark`, `Makefile`, `.env.example` | Operator entrypoints and configuration example |
| `benchmarks/` | Harness, runtime adapters, suites, schemas, oracles, reports, and regression tests |
| `evals/` | Skill discrimination corpus, fixtures, and evaluation contracts |
| `scripts/` | JavaScript evaluators, their runtime helpers, and Agent Economics |
| `tests/` | Additional host-evidence and Agent Economics regressions |
| `docs/` | Measurement methods, qualification guidance, and recorded analyses |

## Migration and compatibility

The implementation was extracted from agentsCookbook at commit
[`40be826e6883d819ee6cf5c10946c793f0ef0f5f`](https://github.com/Hugloss/agentsCookbook/commit/40be826e6883d819ee6cf5c10946c793f0ef0f5f).
Historical source pins and versioned receipt/schema identifiers are retained.
Current harness and bootstrap ownership is EvalShot. See the
[migration notes](docs/migration.md) for configuration, existing runs, and the
boundary between the two repositories.

## License

[MIT](LICENSE), retaining the original copyright notice.
