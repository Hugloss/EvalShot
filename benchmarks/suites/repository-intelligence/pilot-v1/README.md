# Repository Intelligence Pilot v1

This is the first executable campaign for the generic EvalShot benchmark
framework. It qualifies replay, isolation, subject exposure, independent grading,
contamination handling, evidence publication, and reporting before the corpus grows.

## Frozen authority

The three tasks all start from the same immutable agentsCookbook source:

- commit: `0841a8822f417b8fd03af61c03779df8f1cdc941`
- tree: `65a32888329e308615647dda194f0e36c2afe2ac`

The matrix is three tasks by three conditions:

- Codex with no repository-intelligence MCP subject;
- Codex with Hashmarks;
- Codex with Enola.

The conditions use the same pairing seed. The seed is a benchmark trial identity and
ordering input; it is **not** a claim that the remote model sampler is seedable.

Two tasks are read-only localization tasks with frozen exact JSON answer oracles. The
third injects a checksum-bound defect into `benchmarks/harness/receipt.py` and grades
the repair with a pre-existing focused regression.

## Important model limitation

`agents/codex.json` deliberately uses `"model": null`, meaning the Codex host
default. Codex JSONL does not expose the resolved model in its thread-start event, so
pilot-v1 must not be presented as a frozen model-to-model benchmark.

Before publishing comparative agent results, create a **new experiment version** with
an explicit Codex model in the frozen agent definition. Do not rewrite pilot-v1 after
seeing its results.

## Prerequisites

The execution host needs:

- Python 3.11 or newer;
- Git;
- `codex`;
- `hashmarks` with its MCP dependency available;
- `enola`.

The adapters record executable versions and executable SHA-256 values where available.
A missing subject, agent, unhealthy oracle, timeout, or evidence-bound violation is not
converted into a product failure.

Codex is run with an isolated `CODEX_HOME`. Authentication can be supplied either
through an already-exported Codex/OpenAI credential environment variable or by copying
only an existing Codex `auth.json` into the isolated home with `--codex-auth`.
The credential contents are never written to benchmark receipts.

## Validate and inspect the plan

```bash
uv run --no-project python -m benchmarks validate-suite \
  --suite benchmarks/suites/repository-intelligence/pilot-v1

uv run --no-project python -m benchmarks plan \
  --suite benchmarks/suites/repository-intelligence/pilot-v1
```

The frozen plan contains exactly nine unique definition identities.

## Run

Keep generated campaign state outside the repository. From an EvalShot checkout, set `HASHMARKS_BENCH_SOURCE` in `.env`
to the clean Hashmarks checkout used by the assisted condition. The harness
fetches the historical source repository and commit declared by each task:

```bash
root="${TMPDIR:-/tmp}/agents-cookbook-ri-pilot-v1"

uv run --no-project python -m benchmarks run \
  --suite benchmarks/suites/repository-intelligence/pilot-v1 \
  --env-file .env \
  --harness-root . \
  --agent codex \
  --cache "$root/cache" \
  --work "$root/work" \
  --results "$root/results" \
  --codex-auth "$HOME/.codex/auth.json"
```

If Codex is authenticated through an exported credential instead, omit
`--codex-auth`.

A narrower diagnostic can select one task and/or condition:

```bash
uv run --no-project python -m benchmarks run \
  --suite benchmarks/suites/repository-intelligence/pilot-v1 \
  --env-file .env \
  --harness-root . \
  --agent codex \
  --cache "$root/cache" \
  --work "$root/work" \
  --results "$root/results" \
  --task locate-receipt-completion-owner \
  --condition hashmarks-codex
```

Complete valid result bundles are reusable. `PASS` and `FAIL` are experiment
outcomes. `INCOMPLETE`, `INVALID`, and `CONTAMINATED` remain distinct evidence
states and make the campaign command exit non-zero.

## Report

```bash
uv run --no-project python -m benchmarks report \
  --suite benchmarks/suites/repository-intelligence/pilot-v1 \
  --agent codex \
  --results "$root/results"
```

The report shows status counts, per-condition success rates, MCP adoption, tool calls,
MCP result bytes, tokens, duration, and paired deltas against the same-agent bare
condition. It does **not** calculate an overall winner.

Reporting fails closed when:

- a frozen definition is missing, unless `--allow-incomplete` is explicit;
- a result bundle is corrupt or its sealed evidence changed;
- results contain an execution outside the frozen suite;
- more than one execution exists for the same frozen definition.

The last rule prevents a results directory from silently mixing, for example, two
different Hashmarks versions.

## Archaeology measurement boundary

Codex JSONL exposes commands, MCP calls/results, file-change events, and token usage,
but it does not authoritatively report repository file-read bytes. Pilot-v1 therefore
does not claim a source-byte budget or source-read byte metric. Add a separate observed
filesystem/OS evidence mechanism in a future experiment version before using that
metric.
