# Repository-intelligence headroom diagnostic v1

This is a **diagnostic-only** paired benchmark created from the harder behavioral-v4
case authority. It exists to answer one question that heldout-v1 cannot currently
answer well:

> Does the bare native agent have reproducible semantic headroom on repository
> intelligence tasks where Hashmarks or Enola could plausibly help?

It does **not** replace heldout-v1, carry release authority, or force either subject
to be used.

## Scope

Four reused behavioral cases:

- `change_impact-00`
- `freshness-00`
- `declarations-00`
- `verification-00`

Each condition uses three frozen replicate IDs. For one selected native agent that is
36 trials:

```text
4 tasks x 3 conditions x 3 replicates = 36
```

For both native agents it is 72 trials.

The task prompts never mention Hashmarks, Enola, MCP, or subject tool names. Bare,
Hashmarks, and Enola remain ordinary paired conditions.

## Authority

The case expectations, command oracle, pinned repository revision, and mutation
fixtures are copied from behavioral-v4. The native agent definitions are taken from
the current heldout-v1 runtime authority.

This suite is descriptive research evidence only:

- release authority: false
- heldout replacement: false
- overall winner: none

## Run

Copy the example config and set the exact local Hashmarks source under test:

```bash
cp benchmarks/suites/repository-intelligence/headroom-v1/.env.example .env.headroom
$EDITOR .env.headroom
```

Then run a cheap provider/host/subject smoke before spending on the full diagnostic:

```bash
uv run --no-project python -m benchmarks check --env-file .env.headroom

uv run --no-project python -m benchmarks run \
  --new \
  --env-file .env.headroom \
  --task freshness-00 \
  --condition hashmarks-opencode-native
```

That smoke is exactly three frozen definitions. Its automatic scorer is bound to those
three definition IDs; it does not demand the rest of the 36-trial OpenCode population.

Only after the smoke completes and writes its report bundle, run the full paired
diagnostic:

```bash
uv run --no-project python -m benchmarks run \
  --new \
  --env-file .env.headroom

uv run --no-project python -m benchmarks report \
  --env-file .env.headroom

uv run --no-project python -m benchmarks score \
  --env-file .env.headroom
```

The run command persists the complete shareable report bundle automatically when
execution completes, even when the campaign is not qualified:

- `status.json`
- `report.json`
- `decision-evidence.json`
- `score.json`

`decision-evidence.json` is a derived first-read view; it performs no ranking or
recommendation.

## Interpretation

The score reports `bare_control_headroom.state`:

- `observed`: at least one valid bare trial is a semantic FAIL
- `not-observed`: every valid bare trial passed
- `unavailable`: no valid bare outcome exists

Headroom is observed, never assumed. If the bare control still passes every valid
trial, do not tune Hashmarks to force a gain; the diagnostic itself needs more
difficulty.

Also inspect:

- subject adoption
- paired assistance transitions
- invocation-separated paired economics
- task-level assistance evidence
- native tool-strategy summaries (tool identities/counts and subject first-call position)
- bare stability across replicates
- unresolved execution diagnostics
- explicit observability gaps

Full per-trial tool order remains sealed in receipts for drill-down. Shareable reports
do not copy command arguments, source contents, or guessed source-read archaeology.

Do not infer product benefit from an assisted condition where the subject was
configured but never invoked.
