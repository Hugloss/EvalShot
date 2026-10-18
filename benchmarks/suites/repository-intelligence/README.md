# Repository Intelligence

This suite measures repository-intelligence correctness and its effect on coding-agent
work without making any evaluated product its own grading authority.

Hashmarks and Enola keep their native CLI/MCP semantics. The benchmark does not ETL
their facts into a shared repository graph. Only the experiment envelope is normalized:
participant identity, tool exposure/adoption, execution evidence, budgets,
contamination, independent grading, receipts, and reporting.

## pilot-v1

`pilot-v1/` is the first executable harness qualification. It freezes three tasks on
one exact agentsCookbook commit/tree and compares bare, Hashmarks, and Enola using the
Codex host-default model.

It remains immutable historical evidence.

## agent-matrix-v2

`agent-matrix-v2/` freezes a new experiment version on the merged pilot
implementation bytes. It keeps the same three task families and expands to two agent
runtime authorities:

- Codex with explicit `gpt-5.6-sol` / high reasoning;
- native OpenCode using the host's already configured provider/model/auth.

Each runtime runs bare, with Hashmarks, and with Enola, producing 18 frozen definitions.

**Gemma is never routed through Codex.** When native OpenCode is configured to Gemma,
the benchmark uses that native setup and observes/binds what actually ran. No
OpenCode model/provider configuration is stored in this repository.

The benchmark composes a transient OpenCode runtime overlay while preserving the
host's native model, provider, authentication, permissions, and existing inline
configuration. Bare trials disable repository-intelligence MCP servers. Assisted
trials take the selected subject's MCP exposure from its benchmark adapter: the exact
admitted Hashmarks or Enola executable, arguments, working directory, and isolated
state/config needed for that trial. Any same-named project/global OpenCode MCP entry is
observed but shadowed for the trial; it is not benchmark subject authority.

Native OpenCode assistance is admitted only when that benchmark-owned subject exposure
can be proven to target the isolated trial workspace and the effective OpenCode MCP
command resolves to the admitted executable identity. Hashmarks is proven from its
explicit `--workspace`; Enola is proven from its explicit trial repository/config
binding. Missing executables, changed exposure identity, or unprovable/outside-workspace
bindings produce `INCOMPLETE` before agent work.

Primary interpretation is assistance gain within the same runtime/model authority.
Cross-runtime rows are descriptive only.

## Measurement boundary

The suites measure correctness, subject availability, MCP configuration/adoption,
command/tool calls, MCP evidence bytes when observable, token usage when exposed,
duration, contamination, and oracle health.

The native agent event surfaces do not authoritatively expose repository file-read
bytes, so the suites explicitly mark that archaeology metric unavailable rather than
estimating it.
OpenCode Code Mode child calls are counted from export metadata when present. Per-child
result bytes and calls without usable metadata remain unobserved.

Do not publish an overall winner score.

## Current native suites

`multidomain-v2/` is an explicit draft benchmark with 60 frozen evidence cases
and 36 agent tasks covering logs, Splunk CSV, dependencies, semantics, identities,
and code ownership across four pinned repositories. Its new questions and oracles
await independent review; the reports disclose that state. It is never invoked
by Hashmarks release or ordinary test workflows. See its README for exact commands.

`native-matrix-v3/` is the current native Codex/OpenCode comparison, with one paired smoke task by default from Hashmarks. It pins a source revision without checked-in answers. `enola-cycle-reproduction-v1/` adapts the published TypeScript cycle example and reports functional success and cycle introduction separately. Both are development-only experiments in EvalShot; neither enters the installed Hashmarks product.

## context-invariance-v1

`context-invariance-v1/` is a research-only matched-context suite. It holds
repository bytes, task, oracle, subject, agent, budgets, and replicate identity
constant while varying only declared prompt context across neutral, placebo,
authority-claim, and misleading-hint arms. Reports expose paired semantic
transitions plus answer, semantic, route, and subject-authority-use flip rates.
The suite is descriptive-only, performs no cross-agent ranking, and has no release
authority.

See `context-invariance-v1/README.md` for the frozen population and analysis
contract.

## headroom-v1

`headroom-v1/` is a diagnostic-only repeated paired suite created after heldout-v1
campaign 000006 exposed a bare-control ceiling. It reuses four harder behavioral-v4
case/oracle authorities (change impact, freshness, declarations, and verification)
with three replicates per bare/Hashmarks/Enola condition. It explicitly reports whether
bare-control headroom was actually observed; it does not assume assistance benefit,
replace heldout-v1, or carry release authority.

See `headroom-v1/README.md` for its isolated config and commands.

## headroom-v2

`headroom-v2/` preserves the headroom-v1 cases and prompts with a new oracle
identity. Its oracle grades a complete fenced JSON object semantically while
recording plain-JSON format compliance separately. This corrects false semantic
failures found in the interrupted v1 baseline. V1 receipts are not mixed into
v2 campaigns. See `headroom-v2/README.md` for the run contract.

## Required-tool diagnostics after heldout-v1 run 000007

Run 000007 completed 108 receipts but did not qualify: Enola was never called, and
Hashmarks was called in 7 of its 36 assisted trials. Its gradeable location answers
also used JSON fences even though the prompts requested one plain JSON object. The
ordinary paired suite remains the natural-use measure. The separate tool probes below
ask what happens after a specific subject tool is required; they are diagnostic only.

Prepare one 9-trial suite per non-control subject from the reviewed heldout
task and oracle bytes:

```bash
make benchmark-tool-probe-prepare PROBE_SUBJECT=hashmarks
make benchmark-tool-probe-prepare PROBE_SUBJECT=enola
# Future subject:
# make benchmark-tool-probe-prepare PROBE_SUBJECT=<subject-id>
```

The required tool comes from the frozen subject definition's
`exposure_probe.required_tool`; the probe framework contains no product-name mapping.
A future MCP subject therefore adds its own contract rather than changing probe code.
The generator copies task and independent review records exactly. It puts the
required-tool instruction in the diagnostic OpenCode agent definition, which changes
the trial definition identity while preserving reviewed oracle authority. The generated
`.env` uses runtime choices from the root `.env` and points to the diagnostic suite,
scorer, and separate ignored run root. `--reuse` checks that an existing suite still
matches source tasks, reviews, subject, agent definition, and runtime choices; it never
overwrites that suite.

First check the suite and native runtime without a model call:

```bash
make benchmark-tool-probe-check PROBE_SUBJECT=hashmarks
make benchmark-tool-probe-check PROBE_SUBJECT=enola
```

When model calls to the pinned public Hashmarks source are authorized, run one
three-trial smoke per subject. Inspect its score and use the read-only gate before
launching the nine-trial diagnostic:

```bash
make benchmark-tool-probe-smoke PROBE_SUBJECT=hashmarks
make benchmark-tool-probe-smoke-gate PROBE_SUBJECT=hashmarks
make benchmark-tool-probe PROBE_SUBJECT=hashmarks
```

Repeat for each selected non-control subject. `benchmark-tool-probe` checks the saved
smoke gate before the full run. Heldout campaign admission separately requires every
selected non-control subject to carry this frozen exposure-probe contract, and completed
campaign qualification still requires actual observed subject use. Use `benchmark-tool-probe-resume RUN_ID=...`, `-status`, and `-score` for a
saved full run. The v2 score records required-call attempt and nonempty completion,
whether that call preceded native file search, and unknown observations independently
of semantic correctness. A complete nested Code Mode call without visible output
bytes remains unknown; the smoke gate will not treat it as success.

Every completed report now writes `trace-diagnostics.json`, a derived view of sealed
traces with per-call order, status, failure category, input hash, file path attempts,
and Hashmarks returned-candidate target rank. It reports absent or unsupported traces
as unknown. To inspect an older campaign without rewriting its historical reports:

```bash
python3 -m benchmarks trace-diagnostics \
  --results .benchmark-runs/heldout-v1/runs/000007/results \
  --output .benchmark-runs/heldout-v1/runs/000007/reports/trace-diagnostics-retro.json
```

For an observed Hashmarks query, `hashmarks-retrieval-probe` replays its exact text
against a specified checkout and executable at limits 20 and 50, then checks whether
exact symbol search can find the frozen oracle target. This is candidate evidence;
its output does not claim a product defect or comparability across repository
revisions.

Run 000007's pinned repository replay is saved as
`reports/hashmarks-retrieval-probe-retro.json`. It used the currently installed
Hashmarks executable against the task's pinned repository checkout, so the repository
revision matches while the executable's implementation remains a separate recorded
identity.

## behavioral-v3

`behavioral-v3/` adds independent agent-outcome challenges for post-edit refresh,
change impact, focused verification, and dependency-delta semantics. Its edit tasks
start with the subject prepared against the frozen synthetic mini-project and then let
the agent change only the declared owner path, so post-change behavior is exercised
without copying Hashmarks unit tests into the benchmark. It is research-only and has
no release authority.
