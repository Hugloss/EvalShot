# Hashmarks as an EvalShot dogfood corpus

This is a **model-free, read-only corpus maintenance check**, not a replacement
for EvalShot's experiment admission, host delivery, trial qualification,
independent grading, or effectiveness analysis. It interrogates existing frozen
Hashmarks-source tasks without editing historical task definitions or Hashmarks.

It answers: which frozen suites still use Hashmarks as their source repository,
which commits and task families are represented, whether the oracle transcribed
into each reviewed task still matches its independent review, whether a local
Hashmarks checkout contains the frozen commit/tree and cited file, and whether
the same `(commit, task ID)` appears with distinct definitions in multiple suites.

From the EvalShot checkout:

```sh
uv run --no-project python -m benchmarks.dogfood.hashmarks_corpus \
  --suite-root benchmarks/suites/repository-intelligence \
  --output /tmp/evalshot-hashmarks-corpus.json

# Strict reproducibility check, requires historical Git objects locally.
uv run --no-project python -m benchmarks.dogfood.hashmarks_corpus \
  --checkout /path/to/Hashmarks --require-source \
  --output /tmp/evalshot-hashmarks-source-audit.json

uv run --no-project python -m unittest benchmarks.tests.test_hashmarks_dogfood_corpus
```

The output is deterministic JSON with explicit `NOT_CHECKED` and
`SOURCE_UNAVAILABLE` states. A missing historical object can mean a shallow
checkout: it is not evidence the task is semantically invalid. The strict mode
rejects it rather than silently passing a reproducibility requirement. Review
files are never copied into prompts or materialized agent workspaces by this
command. A file's existence does **not** prove the referenced symbol or oracle
is correct; semantic ownership still requires an independent source review.

Do not edit already-frozen prompts, source hashes, oracle grades, or historical
results to make a report green. Record discovered gaps for a separately frozen
future benchmark. The active EvalShot native qualification and effectiveness
program remains in PR #12; product evidence changes remain in Hashmarks PR #443.
This helper owns neither program, and has no host, adapter, scorer, or subject
changes. It does not report an agent performance gain.
