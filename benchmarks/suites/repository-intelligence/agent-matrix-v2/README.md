# Repository Intelligence Agent Matrix v2

This experiment extends pilot-v1 without modifying it.

It freezes the same three task families against exact agentsCookbook source:

- commit: `6ce8b0d9230dd9bc5369ddf495ad9404766fbbaf`
- tree: `1e253d251f8e0a874aeca0b05358b36253a714cc`

The matrix is three repository-intelligence conditions by two agent runtimes:

- bare, Hashmarks, Enola;
- Codex with frozen `gpt-5.6-sol` + high reasoning effort;
- **native OpenCode**, using the model/provider/auth already configured on the host.

That produces exactly 18 frozen definitions.

## Hard boundary: Gemma never runs through Codex

The benchmark has no Codex local-provider/Gemma condition. Codex is used for Codex.
Local models such as Gemma are exercised through the user's already-working OpenCode
installation.

The OpenCode agent definition intentionally contains no model, provider, base URL, or
credential configuration. Those remain native OpenCode authority.

If native OpenCode currently resolves to Gemma, the run records that observed
provider/model. If native OpenCode later resolves to another model/config, that changes
execution identity instead of silently reusing the earlier result.

## Native OpenCode behavior

OpenCode inherits the user's real HOME/XDG configuration and credentials. The
benchmark does **not** copy or recreate its provider/model configuration.

Before admission the adapter runs native `opencode --pure debug config` and records:

- OpenCode executable/version identity;
- the resolved build-agent/default model;
- provider inferred from that model;
- a SHA-256 of the effective configuration after recursively redacting credential-like
  values;
- the names of configured native MCP servers.

For comparison control, the benchmark composes an in-memory MCP overlay with the
host's existing `OPENCODE_CONFIG_CONTENT` layer. Host inline settings, including
provider, model, authentication, and permissions, remain in that runtime layer. The
benchmark does not store the composed content or its secrets in a receipt.

The bare condition disables repository-intelligence MCP servers. An assisted condition
disables non-selected repository-intelligence servers and supplies the selected
subject from its benchmark adapter. That exposure contains the exact admitted
Hashmarks or Enola executable and its trial-scoped command, working directory, and
state/config. OpenCode still owns provider, model, authentication, permissions, and
all unrelated native configuration.

A pre-existing project/global OpenCode server with the same subject name is not reused
as command authority. The benchmark temporarily shadows it, proves the effective
OpenCode definition matches the adapter exposure, and records whether shadowing was
required. The selected subject executable must exist and connect successfully; its
workspace binding and executable SHA-256 must be positively verified before admission.

Workspace verification is intentionally conservative:

- Hashmarks: the benchmark exposure directly invokes the admitted Hashmarks executable,
  ends in `mcp`, and contains exactly one explicit `--workspace` resolving to the
  isolated trial workspace. Its explicit isolated state directory is part of the
  exposure identity.
- Enola: the benchmark exposure uses the admitted Enola executable plus an explicit
  trial-owned config whose repository resolves to the isolated workspace.
- Any missing executable, changed exposure identity, unsupported command, or otherwise
  unprovable binding produces `INCOMPLETE`, never product `FAIL`.

Full absolute path diagnostics stay in preparation evidence, while only stable proof
semantics are bound into execution identity so temporary workspace paths do not break
resumability. Both flat and nested OpenCode MCP configuration shapes are supported. The
report separates campaigns if one agent's observed native model or configuration changes
across selected receipts.

`--pure` disables external OpenCode plugins during the measurement while retaining
native provider/model/auth configuration.

## Interpretation

Primary comparisons remain assistance gain inside one runtime/model authority:

- Codex bare -> Codex + Hashmarks / Enola;
- native OpenCode bare -> native OpenCode + Hashmarks / Enola.

Cross-runtime rows are descriptive only. They do not produce a winner ranking.

The shared seed is experiment identity/pairing evidence; it is not a claim that either
model sampler is seedable.

## Validate

```bash
uv run --no-project python -m benchmarks validate-suite \
  --suite benchmarks/suites/repository-intelligence/agent-matrix-v2

uv run --no-project python -m benchmarks plan \
  --suite benchmarks/suites/repository-intelligence/agent-matrix-v2

uv run --no-project python -m benchmarks plan \
  --suite benchmarks/suites/repository-intelligence/agent-matrix-v2 \
  --agent opencode-native --subject hashmarks
```

The full plan contains exactly 18 unique definition identities. The selected plan
contains three Hashmarks trials; add `--subject none` to include the three matching
bare controls. `--task`, `--agent`, and `--subject` can be repeated on plan, run,
and report. `--agent` also accepts comma-separated agent IDs. `--condition`
selects one exact condition and may be combined with its matching single `--agent`,
but not with `--subject`.

## Run

Keep campaign output outside the repository:

```bash
root="${TMPDIR:-/tmp}/agents-cookbook-ri-agent-matrix-v2"

uv run --no-project python -m benchmarks run \
  --suite benchmarks/suites/repository-intelligence/agent-matrix-v2 \
  --env-file .env \
  --harness-root . \
  --agent codex-sol,opencode-native \
  --cache "$root/cache" \
  --work "$root/work" \
  --results "$root/results" \
  --codex-auth "$HOME/.codex/auth.json"
```

The Codex auth seed is used only by Codex conditions. OpenCode conditions use the
already-working native OpenCode configuration/authentication.

A native OpenCode Hashmarks comparison can be run without changing the frozen
experiment:

```bash
uv run --no-project python -m benchmarks run \
  --suite benchmarks/suites/repository-intelligence/agent-matrix-v2 \
  --env-file .env \
  --harness-root . \
  --cache "$root/cache" \
  --work "$root/work" \
  --results "$root/results" \
  --agent opencode-native \
  --subject hashmarks
```

For an isolated diagnostic of only one condition, use
`--condition hashmarks-opencode-native` with `--agent opencode-native` instead
of the subject filter.

## Report

```bash
uv run --no-project python -m benchmarks report \
  --suite benchmarks/suites/repository-intelligence/agent-matrix-v2 \
  --results "$root/results" \
  --agent opencode-native --subject hashmarks
```

Use the same selectors for run and report. The report requires all selected frozen
definitions by default and ignores other valid definitions in the suite. Its selection
metadata records the requested filters; bare controls are included only when selected.
For a diagnostic of a partial campaign, add `--allow-incomplete`. The report contains per-condition
profiles, per-agent profiles, paired assistance rows, and descriptive cross-agent
observations. It never calculates an overall winner.

Report schema v3 counts native Code Mode child MCP calls from OpenCode export metadata.
When that metadata is unavailable, MCP calls and adoption are unobserved and excluded
from the adoption denominator. Code Mode does not expose each child's result bytes, so
those bytes are omitted rather than recorded as zero.

## Measurement boundary

Codex JSONL and OpenCode session export expose different native event shapes. Both are
projected only into the generic experiment metrics needed for comparison: task outcome,
tool/MCP adoption, token counts when available, output evidence, duration, and
contamination.

Neither surface authoritatively exposes repository file-read bytes, so v2 does not
manufacture an archaeology-byte metric.
