# Sharp Skill Discrimination

The executable corpus lives in [`sharp-skill-cases.json`](sharp-skill-cases.json). Do not duplicate case definitions in Markdown.

The corpus tests the narrow repository-review skills on three case classes:

- **positive** — this skill's own invariant is violated and must produce a finding;
- **control** — nearby code is safe or legitimate and must not be reported;
- **confusion** — a real defect exists, but it belongs to a neighboring specialist and this skill must stay clean.

A useful skill must therefore do more than find plausible smells. It must identify its own failure class, cite evidence, and reject nearby false positives.

## Runner

Run these commands from EvalShot. `--cookbook-root` selects the external
agentsCookbook checkout containing the canonical skill definitions. Corpus
and fixture paths remain relative to EvalShot; `--repo` selects the target
working repository independently.

Validate the corpus without a model:

```bash
node scripts/run-skill-benchmarks.js --cookbook-root ../agentsCookbook --validate-corpus
node scripts/run-skill-benchmarks.js --cookbook-root ../agentsCookbook --list
```

Run one specialist on OpenCode:

```bash
node scripts/run-skill-benchmarks.js \
  --cookbook-root ../agentsCookbook \
  --runtime opencode \
  --skill stale-work-race-review \
  --artifacts-dir .runs/skill-evals
```

Run the same canonical cases on Pi:

```bash
node scripts/run-skill-benchmarks.js \
  --cookbook-root ../agentsCookbook \
  --runtime pi \
  --skill stale-work-race-review \
  --artifacts-dir .runs/skill-evals
```

Use `--all` for the full corpus, `--case ID` for one case, and `--model ID` or `SKILL_EVAL_MODEL` to override the runtime model.

The runner injects the exact canonical `SKILL.md` into a read-only one-shot evaluator. Install/discovery qualification remains separate: behavioral discrimination should not pass merely because a runtime found a skill file.

## Pass contract

Every case must end with:

```text
EVAL_VERDICT: FINDING|CLEAN
EVAL_SCOPE: <skill-name>|NONE
EVAL_EVIDENCE: <concise evidence>
```

A case passes only when the model:

- returns the expected finding/clean verdict;
- claims this skill as scope only for a real in-scope finding;
- supplies evidence rather than an unsupported label;
- exits successfully.

The runner also records elapsed time and output size. Runtime/token usage may be recorded separately when exposed by the local model stack.

## Qualification rule

Do not claim a specialist is behaviorally qualified from install smoke tests or corpus validation alone. Qualification requires model-backed positive and control results; confusion cases are the stronger proof that the skill keeps its intended edge.
