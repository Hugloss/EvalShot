# Behavioral repository-intelligence benchmark v3

This is an **explicit research benchmark**, not a Hashmarks release gate. It extends
the repository-intelligence experiments with abilities that Hashmarks already proves
internally but that were not independently challenged as agent outcomes by
`multidomain-v2`.

The suite freezes eight synthetic tasks, two per family:

- **post_change** — edit an already indexed repository, then identify invalidated/reusable
  work and an owner transition;
- **change_impact** — edit an owner and identify downstream implementation plus focused
  verification surfaces in Python and TypeScript;
- **verification** — select focused native pytest and Go verification surfaces without
  broad-suite guessing;
- **dependency_delta** — distinguish a uv version-selection transition from a Maven
  relationship/scope-only change.

Each task runs bare, with Hashmarks, and with Enola under each selected native agent.
That is **24 trials per selected agent**, or 48 with both Codex and OpenCode.

## Independence

Hashmarks does not grade itself. Every task is anchored to agentsCookbook commit
`844dfa06d50b2f4402306b16a217b29946a3dedf`, which predates this suite. A frozen mutation adds a synthetic
mini-project before subject preparation. Edit tasks are therefore indexed before the
agent changes them. The independent `oracle.py` checks the agent's final JSON and,
for edit tasks, the edited bytes. Workspace contamination rules independently restrict
which files may change.

The benchmark does not require or reward calling a particular tool. It measures whether
assistance changes agent outcomes.

## Run explicitly

```dotenv
BENCHMARK_SUITE_PATH=benchmarks/suites/repository-intelligence/behavioral-v3
BENCHMARK_CAMPAIGN_ROOT=/tmp/agentscookbook-behavioral-v3
BENCHMARK_HARNESS_REPO_ROOT=.
BENCHMARK_AGENT=opencode-native
HASHMARKS_BENCH_SOURCE=/absolute/path/to/clean/Hashmarks
BENCHMARK_SCORE_SCRIPT_PATH=benchmarks/suites/repository-intelligence/behavioral-v3/score.py
BENCHMARK_SCORE_OUTPUT_PATH=score.json
```

Then run:

```sh
make benchmark-check
make benchmark
make benchmark-report
make benchmark-score
```

`benchmark-check-all` remains an explicit exhaustive preflight and is never required
before execution. Use a fresh campaign root after changing agent, model, subject,
Hashmarks candidate, or harness authority.

This suite deliberately does **not** turn Hashmarks unit tests into benchmark oracles.
The frozen expected outcomes come from the synthetic repository bytes and task contract.
