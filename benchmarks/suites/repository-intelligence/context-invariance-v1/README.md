# Context-invariance repository-intelligence benchmark v1

This research suite tests whether irrelevant or untrusted prompt context changes a
coding agent's repository-intelligence decision when the repository bytes, task,
oracle, subject, agent, budgets, and paired replicate identity remain fixed.

## Claim boundary

The primary observation is a **within-agent, within-subject, within-task paired
semantic transition**. Cross-agent rows are descriptive only. The suite does not rank
models or products and has no release authority.

Each counterfactual set contains four prompt contexts:

- `neutral` — the task exactly as frozen;
- `placebo` — unrelated organizational background;
- `authority-claim` — an unsupported senior-maintainer claim pointing at the wrong file;
- `misleading-hint` — a prior-investigation claim pointing at the wrong symbol.

The harness resolves the effective prompt once from the frozen task and condition.
Context text is prefixed so the original task and its output contract remain byte-for-byte
identical and last in every non-neutral prompt.

## Frozen population

The suite reuses two deterministic heldout-v1 localization authorities on the same
pinned Hashmarks repository tree:

- `locate-prefix-path-enumerator`;
- `locate-repository-content-identity`.

It runs bare and Hashmarks-assisted conditions under Codex-native and OpenCode-native,
with three paired replicate IDs per context. That produces **96 executions** and,
when complete, **72 neutral-to-context comparisons**.

## Evidence and stability

Reports retain ordinary sealed trial receipts and add:

- exact-text `answer_flip_rate`;
- semantic-success `semantic_flip_rate`;
- observed tool-sequence `route_flip_rate`;
- selected-subject invocation `authority_flip_rate`;
- explicit `benchmark-counterfactual-pair.v1` pair identities;
- neutral→variant semantic transitions and context-change summaries.

Flip rates are pairwise disagreement among observed replicates. Missing route or
subject-invocation observability stays unknown; it is never converted to zero.

## Analysis contract

The frozen analysis contract requires three observed replicates per variant arm and at
least 18 comparable neutral↔variant pairs for the selected lane. Below that threshold,
results remain descriptive-only. Meeting the threshold does not authorize statistical
significance claims or cross-agent ranking.

## Known limitations

The context variants intentionally test prompt sensitivity, not adversarial security.
A changed route with preserved semantics is evidence of behavioral sensitivity, not by
itself a defect. The deterministic repository-location oracle grades the final semantic
answer; it does not judge reasoning quality.

## Run

Keep this suite on its own configuration and campaign root. Do not rewrite the
heldout-v1 `.env` to switch suites:

```bash
cp benchmarks/suites/repository-intelligence/context-invariance-v1/.env.example \
  .env.context-invariance
$EDITOR .env.context-invariance
```

First prove runtime wiring without model calls:

```bash
make benchmark-context-invariance-check
```

The exhaustive model-free admission is optional:

```bash
make benchmark-context-invariance-check-all
```

Before the 96-trial full population, run the frozen qualification lane:

```bash
make benchmark-context-invariance-qualify-check
make benchmark-context-invariance-qualify
# If interrupted, preserve the exact frozen qualification selection:
make benchmark-context-invariance-qualify-resume
make benchmark-context-invariance-qualify-status
```

The qualification selection is one frozen task, bare + Hashmarks, all four contexts,
and all three replicate IDs. That is **24 executions per selected agent** and exactly
**18 neutral↔variant comparisons per selected agent**, matching the frozen
`minimum_pairs=18` contract without changing the suite.

`qualify-check` performs the exact 24-trial-per-agent admission without model work.
`qualify` starts a new immutable campaign with those selectors.
`qualify-status` is a two-part fail-closed gate: the saved campaign must first be
execution-qualified, then its canonical score must report
`analysis_evidence.evidence_state=minimum-evidence-observed`. A campaign where every
process finished but too much semantic evidence is ungradeable therefore does **not**
qualify.

The canonical start-and-leave path is:

```bash
make benchmark-context-invariance
```

It reuses the ordinary benchmark `--auto` semantics: a new compatible run is
created when no unfinished run exists; an unfinished matching run is never guessed.
Choose explicitly when needed:

```bash
make benchmark-context-invariance-new
make benchmark-context-invariance-resume RUN_ID=000001
```

Inspect or refresh evidence without invoking a model:

```bash
make benchmark-context-invariance-runs
make benchmark-context-invariance-status
make benchmark-context-invariance-reports
```

Set `CONTEXT_INVARIANCE_ENV=/path/to/file` to use a different explicit config file.
All commands still flow through the generic benchmark CLI and its single config,
campaign, admission, and scoring authorities.
