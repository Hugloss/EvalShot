# Empirical benchmark framework

This directory owns reusable, product-neutral experiments for agents, tools, evidence systems, and software-engineering outcomes.

## Authority boundary

EvalShot owns [measurement of runtime-reported model context usage](../docs/model-context-observation.md) and tool-routing measurement (`benchmarks/tool_routing.py`). Model/provider configuration owns context limits and compaction behavior; cookbook prompts retain model-neutral context and reviewer-selection guidance. A campaign can record the runtime context configuration selected for that experiment, but the runtime/provider—not EvalShot—sets the window; task-specific budgets do not establish a second model-context limit.

The harness is the experiment authority. A product under test never grades itself.

Repository-location scoring keeps semantic correctness and answer-format compliance separate. One unambiguous JSON object, whether bare, in one JSON fence, or in one JSON fence surrounded by prose, may be semantically gradeable; only the bare exact object is format-compliant. Multiple fences, duplicate keys, malformed JSON, or prose with only inline JSON remain ungradeable. Any change to this interpretation advances the frozen experiment/scoring authority before a new campaign.

A benchmark suite freezes repository commit and tree identity, task, mutation digest, oracle identity, conditions, budgets, and scoring before campaign execution. Observed outcomes are preserved as `PASS`, `FAIL`, `INCOMPLETE`, `INVALID`, `CONTAMINATED`, or `NO_QUALIFYING_DEFECT`; infrastructure failures are never silently converted into product failures.

The optional [multidomain v2 suite](suites/repository-intelligence/multidomain-v2/README.md)
adds frozen external fixtures and a separate model-free evidence diagnostic. It
runs only when explicitly requested. No benchmark target is a dependency of the
Hashmarks release workflow.

The [behavioral v4 suite](suites/repository-intelligence/behavioral-v4/README.md)
adds eight repository-intelligence dimensions and reports ordered lexigram grades for
authority, resolution, evidence preservation, and paired assistance. It is an explicit
research suite and is not a release gate.

The [context-invariance v1 suite](suites/repository-intelligence/context-invariance-v1/README.md)
holds repository/task/oracle/runtime authority fixed while varying only declared prompt
context. It has its own example config and `make benchmark-context-invariance*`
operator flow so running it never requires rewriting heldout-v1 configuration. Its
neutral↔variant evidence is descriptive-only and never performs cross-agent ranking.

## Model

`suite -> experiment -> condition -> trial definition -> observed execution`

Subjects and agents are replaceable participants. Hashmarks, Enola, Codex, local models, test selectors, and future tools are adapters, not schema concepts.

Definition identity binds the frozen experiment, task, condition, trial, and paired replicate ID. Execution identity additionally binds observed subject, agent, oracle, harness, environment, and mutation authority. Re-running the same frozen task against a different product/model version therefore creates a different execution identity instead of overwriting or reusing an older result.

New suites use explicit `replicate_ids` for paired stochastic observations. The legacy `seed` field remains readable for historical receipts; it was never transported as a provider sampling seed. A real `provider_seed`, if supported later, must be independently observed and bound to execution authority. Reports treat disagreement across replicates as evidence, with no scored retries.

Each trial uses isolated HOME, TMP, and XDG roots where the agent contract requires them, bounded process execution, sealed raw event evidence, an independently healthy oracle, and a create-once verified result receipt. A trial is resumably complete only when its result, checksum, completion record, and bound artifacts agree.

## Shared execution and admission authority

Benchmark command execution reuses the repository's existing `scripts.agent_economics.bounded_process` authority instead of maintaining a second weaker process runner.

Trial admission is single-owned by `benchmarks.harness.admission`:

`materialize exact source -> apply frozen mutation -> prepare subject -> prepare agent -> healthcheck oracle -> bind observed authority`

Both `preflight` and `run` use that same path. Preflight is diagnostic and never invokes the coding agent or publishes a trial result.

`campaign-audit` uses the same campaign authority observer as `run` across every selected task/condition. It checks global runtime and subject identity, task-scoped native configuration, and paired input equality without invoking a model or publishing campaign authority. It reports pending oracle reviews separately and exits 2 until they are complete. Run it before an expensive campaign; an audit result is diagnostic and never authorizes inference.

Runtime readiness is deliberately separate from trial admission. `benchmark-check`/the `check` command never selects or materializes a task, applies a mutation, runs an oracle, derives a trial/execution identity, inspects receipts, or invokes a model. It creates one disposable smoke workspace, verifies shared subject prerequisites once, and checks each distinct agent/subject pair, including bare conditions, once. The held-out suite therefore produces six pair outcomes rather than expanding its 216 task/condition/replicate definitions. The disposable workspace is deleted when the command exits; rerunning the check is always an explicit user action.

`run --new` freezes the selected population and immediately executes the full campaign. `run --resume` requires the same frozen selection and authority. `prepare --new` remains an advanced model-free preparation primitive, not the normal `make benchmark-new` workflow. Every later trial compares observed authority and exact task inputs against the campaign receipt before invoking the model. A durable launch claim makes interrupted trials visible. If a process dies before publishing a complete receipt, the next invocation retires that active claim into immutable numbered interruption evidence and starts a new explicit attempt; already completed receipts are reused and never retried. Changed runtime, model, configuration, subject source, workspace input, or selection requires a new saved run. Canonical status/report/score refreshes share the same run-store owner: an inspection performed while execution owns the store stays inspection-only, and canonical derived data is recomputed after acquiring the lock so an older observation cannot overwrite newer run state.

Native tool discovery and benchmark-subject selection are separate authorities.

Codex, OpenCode, and Enola are normal host-installed tools. The benchmark does not duplicate their standard Linux installation paths or config roots in `.env`. It resolves their executables from the native `PATH`, derives Codex/OpenCode config roots from the host's normal `HOME`, `XDG_CONFIG_HOME`, and `CODEX_HOME` conventions, then records the resolved executable hash, version, native config identity, and effective subject exposure. Native defaults are therefore **observed authority**, not hidden benchmark configuration.

Hashmarks is intentionally different: it is the locally developed product under test, so `HASHMARKS_BENCH_SOURCE` explicitly selects the clean committed checkout and its exact `.venv/bin/hashmarks`. This prevents an unrelated installed Hashmarks from silently replacing the candidate under test.

For native OpenCode and Codex trials, ambient/global MCP registrations are not subject authority. The selected Hashmarks or Enola subject is injected ephemerally from the adapter-owned exact executable/cwd/args. OpenCode replaces a same-name native registration in the trial's in-memory config, including when the task repository enables it. Admission verifies the resolved MCP definition and executable before model work; the native config file is never rewritten. OpenCode native configuration is freshly observed for every admission; task-scoped base-config snapshots are not reused as authority. The selected subject runtime identity binds executable bytes and, for Enola, normalized config semantics. Hashmarks additionally binds its clean committed source checkout because an unchanged launcher can still import changed editable source.

Participant processes do not inherit arbitrary host environment variables. The harness carries one fixed process-substrate allowlist—`PATH`, locale variables, terminal identity, and equivalent Windows launch variables—so native tools can start. Provider variables must be named explicitly through `BENCHMARK_PASSTHROUGH_ENV_KEYS`. The resulting observed environment is bound into execution identity.

Benchmark settings are parsed once per command by `benchmarks/config.py`. There is no automatic `.env` discovery: readiness, preflight, execution, and native evidence require `--env-file`. Required entries must be present and nonempty in that file even when a CLI flag supplies an override. An exported shell value cannot fill a missing entry. Explicit CLI flags override declared file values for one-off selections; file values override shell values for benchmark authority. Native host discovery and explicitly admitted provider secrets still use the host environment.

Agent Economics remains a benchmark consumer/suite; shared process semantics remain single-owned until that module is promoted to a more generic repository location.

## Python runtime authority

EvalShot's benchmark and qualification Python is stdlib-only. The repository pins Python 3.11 in `.python-version` and executes it through uv. Do not call `python` or `python3` directly from repository-owned entrypoints, CI, or benchmark documentation.

There is intentionally no synthetic Python package/dependency layer just to launch these modules. The repository-owned `./benchmark` front door relocates execution to the EvalShot root and delegates interpreter ownership to `uv run --no-project`, so callers never select a `.venv/bin/python`, `PYTHONPATH`, or neighboring repository runtime.

The separate test-only dependency group in `pyproject.toml` and `uv.lock` pins
pytest for the function-style regression tests. It does not add a dependency to
benchmark execution. Run those tests with
`uv run --locked --group test python -m pytest tests -q`.

## Recommended campaign workflow

### Authority vocabulary

Keep deliberate benchmark choices separate from native host discovery:

| Setting | Meaning | Authority/lifetime |
| --- | --- | --- |
| `HASHMARKS_BENCH_SOURCE` | Clean committed Hashmarks checkout being measured | Explicit product source authority |
| `BENCHMARK_AGENT` | One native agent or a comma-separated list for preflight/run/report/score | Mandatory selected-agent authority; no default |
| `BENCHMARK_OPENCODE_AGENT` | Exact OpenCode agent persona to execute | Explicit benchmark semantic choice |
| `BENCHMARK_PASSTHROUGH_ENV_KEYS` | Comma-separated provider variables explicitly admitted into participant processes | Optional provider environment authority |
| `BENCHMARK_SUITE_PATH` | Committed suite definition inside EvalShot | Benchmark source/configuration |
| `BENCHMARK_CAMPAIGN_ROOT` | Ignored store of numbered campaigns under `runs/`; the newest run is selected by default | Generated campaign evidence |
| `BENCHMARK_HARNESS_REPO_ROOT` | EvalShot checkout containing the runner | Harness source authority |
| `BENCHMARK_SCORE_SCRIPT_PATH` | Optional suite-specific scorer | Specialized reporting authority |
| `BENCHMARK_SCORE_OUTPUT_PATH` | Score filename inside each saved run's `reports/` directory | Derived report |

Not configured in `.env`:

- `enola`, `codex`, and `opencode` executables are resolved from native `PATH`;
- Codex uses native `CODEX_HOME` when set, otherwise `$HOME/.codex`;
- OpenCode uses native `HOME` and `XDG_CONFIG_HOME` (or `$HOME/.config`);
- all resolved executable/config identities are observed and recorded.

A **suite path is not an output directory**. A **campaign root is not source configuration**. A native agent's config root is not subject executable authority. These distinctions are enforced so one configured authority cannot silently stand in for another.

For local human-driven campaigns:

```sh
cp -n .env.example .env
# edit .env and set the authorities used by the selected suite
```

The example stores every run under ignored `.benchmark-runs/heldout-v1/runs/<run_id>/`. Each completed trial publishes its own receipt immediately; aggregate JSON and scores are derived later. The per-trial receipts are the durability boundary.

Human-facing derived outputs are kept separately from raw evidence:

```text
.benchmark-runs/heldout-v1/runs/<run_id>/
├── results/          # authoritative receipts, events, and agent traces
├── cache/
├── work/
└── reports/          # small shareable output
    ├── status.json
    ├── report.json
    ├── decision-evidence.json
    └── score.json
```

`decision-evidence.json` is a compact derived first-read view over `report.json`: runtime/host instability, semantic misses, strict-format behavior, subject adoption/assistance evidence, and explicit observability gaps. It performs no ranking or recommendation.

`make benchmark-status`, `make benchmark-report`, and `make benchmark-score` refresh their normal outputs; `benchmark-report` also refreshes `decision-evidence.json`. `make benchmark-reports` refreshes the whole shareable set through the single completed-report owner: scoring is staged and must succeed before the canonical first-read artifacts are refreshed. Canonical files under `reports/` always describe the run's exact frozen campaign selection **and the suite authority frozen at admission**. Selector subsets, `report --allow-incomplete`, or inspection under changed suite authority never replace canonical first-read artifacts. Intentional scoring reinterpretation belongs to the explicit `regrade-score` path rather than canonical report refresh. Share or archive only `reports/` for ordinary benchmark review; keep `results/` when raw execution evidence is needed for audit or debugging.

In the selected Hashmarks source checkout, install its locked MCP extra with `uv sync --frozen --extra mcp --group test` before running readiness. A correct `HASHMARKS_BENCH_SOURCE` path alone does not install the MCP server dependency.

Set one or both execution agents explicitly in `.env` before selected-agent work; omission is a hard failure for those targets:

```dotenv
BENCHMARK_AGENT=opencode-native
# Or: BENCHMARK_AGENT=codex-native,opencode-native
```

For normal use, the benchmark is start-and-leave:

```sh
make benchmark
```

If there is no unfinished run with the same frozen selection, this creates a new numbered run and executes the full population selected by `BENCHMARK_AGENT`. If the latest matching run is unfinished, it refuses to guess and tells you to choose explicitly:

```sh
make benchmark-runs    # find the frozen run ID
make benchmark-resume RUN_ID=000014
make benchmark-new     # intentionally start a separate fresh run
```

`benchmark-new` also executes immediately; it does not stop after preparation. `benchmark-resume` requires `RUN_ID` and the current `BENCHMARK_AGENT` population and selected definitions to match the run's frozen campaign before expensive admission or any model work. Before preflight, preparation, or execution can begin, the harness also verifies that the selected suite scorer exists, belongs to that suite, accepts the exact frozen-definition interface (`--results`, `--output`, `--agent`, repeated `--definition-id`), and has a canonical score filename suitable for the run's `reports/` directory. Scoring enforces the same frozen agent set.

`MATRIX` selects the execution matrix through the same Make targets and numbered
campaign store. The default is `heldout`; `harbor-smoke` projects one task across
three harnesses and two subjects (6 trials), and `harbor-full` projects three
tasks with three attempts (54 trials). For example, run
`make benchmark-check MATRIX=harbor-full`, then
`make benchmark-new MATRIX=harbor-full`. See the
[Harbor matrix guide](harbor/README.md) for model and credential setup.

The remaining targets are optional diagnostics or advanced controls:

```sh
make benchmark-check       # fast runtime readiness
make benchmark-doctor      # same model-free runtime authority diagnostics
make benchmark-oracle-review-check # oracle qualification check
make benchmark-check-all   # exhaustive model-free preflight
make benchmark-runs        # list saved run IDs
make benchmark-report      # print report + save report/decision-evidence
make benchmark-score       # save reports/<score filename>
make benchmark-reports     # refresh the complete shareable reports set
```

The Makefile passes only `.env` and fixed smoke task selectors to the CLI. The configuration loader owns benchmark choices and validates them before runtime work. Codex, OpenCode, and Enola use their installed host conventions; the benchmark observes what resolves. Missing selected-agent choices fail before preflight, execution, reporting, or scoring. Remote repository mirrors are also single-owned per repository identity: clone/fetch/materialization holds one non-blocking cache lock, and a competing command fails before Git work instead of sharing a mutable mirror generation.

OpenCode benchmark trials use one task prompt and permit native automatic context compaction with a 10,000-token reserve. Admission rejects model limit declarations that leave no usable prompt budget and verifies the effective compaction policy. OpenCode runs inside a Bubblewrap mount namespace with `/tmp` bound to the isolated trial `$TMPDIR`; both overlay paths therefore reach the same trial scratch directory. The runtime verifies that mount before granting `/tmp` access. Bubblewrap must be installed for native OpenCode benchmark trials. The shared trial prompt directs temporary work to `$TMPDIR`. These execution changes are versioned separately from earlier single-turn receipts. Model/provider and repository-local native config remain observed and frozen. Every model invocation gets a unique operation ID in its session title, and session discovery rejects sessions older than that operation's start time. Native OpenCode config, executable bytes, selected-subject runtime identity, and source-backed subject identity are revalidated around execution; a generation change makes the result incomplete. After a successful OpenCode process exits, the shared runtime may boundedly reread the same session export while terminal text is being persisted. This does not invoke the model again and leaves a genuinely textless session incomplete after the observation bound. A nonzero OpenCode CLI exit can be treated as complete only when the raw event stream shows a context overflow, a compaction continuation, and a later stopped assistant turn whose text matches the final exported assistant message; the original exit status remains in the receipt.

`benchmark-check` answers only **“can each suite agent/subject combination be wired on this machine right now?”** For held-out v1 it checks Codex and OpenCode with bare tools, Hashmarks, and Enola: six pair outcomes, independent of `BENCHMARK_AGENT`. OpenCode's assisted probes supply a live stdio connection check. Only when that connection fails does readiness launch the selected MCP executable and args in the selected cwd and environment with stdin closed and a five-second bound, solely to capture a direct startup failure. A clean exit after stdin closes is inconclusive and adds no diagnostic. Codex readiness proves its native config plus the exact ephemeral subject exposure without invoking a model; it is reported as ready rather than falsely labelled connected. No readiness command retries automatically.

`benchmark-check-all` is an optional intentionally expensive diagnostic: it preflights every frozen definition for the explicitly selected agents. It is not a prerequisite for `make benchmark`, `make benchmark-new`, or `make benchmark-resume`. Held-out v1 has 108 definitions for one agent or 216 when both are listed.

### Host-neutral tool-routing diagnostics

Tool-selection diagnostics score semantic routing rather than host-specific tool names. Trace projection classifies calls as `subject-repository-intelligence`, `native-search`, `native-read`, `shell`, `tool-router`, or `other`. The required subject call must complete before the first native repository-discovery class; wrapper/router calls do not count by themselves.

Completed campaign reports also classify *when* the configured repository-intelligence subject entered the observed tool order:

- `FIRST_CHOICE`: the subject was selected before native repository exploration;
- `LATE_RESCUE`: native search/read/shell discovery happened first and the subject was invoked later;
- `NEVER_INVOKED`: invocation absence is authoritatively observed;
- `UNKNOWN`: ordering/invocation evidence is insufficient, including partial opaque routing;
- `NOT_CONFIGURED`: no repository-intelligence subject was configured.

This timing surface is diagnostic only. It does not change task scoring, campaign qualification, or subject-exposure admission. Reports retain explicit timing observation denominators, and older receipts are reclassified only when their stored tool sequence, subject ordinal, invocation state, and order observability are sufficient.

This lets OpenCode names such as `grep`/`read` and ChatGPT-style names such as `mcp__GitHub__search`/`mcp__GitHub__fetch_file` use the same scoring contract. Subject namespaces are normalized too, so `hashmarks_task_evidence`, `tools.hashmarks.task_evidence`, and `mcp__hashmarks__task_evidence` represent the same operation.

A host catalog is admissible only when both the required subject tool and at least one native discovery class are visible. Missing Hashmarks is `ENVIRONMENT_BLOCKED`, not a routing failure; only a READY catalog can answer whether the host selected Hashmarks before native repository discovery.

A raw host catalog can be checked without invoking a model:

```bash
./benchmark tool-routing-catalog \
  --catalog host-tools-raw.json \
  --subject hashmarks
```

The raw catalog may be a JSON list of tool names or an object with a `tools` list containing names or `{"name": "..."}` entries. Exit 0 means `READY`; exit 2 means `ENVIRONMENT_BLOCKED`.

For an ordered trace, materialize a canonical catalog capture first so catalog and trace belong to one explicit observation session:

```bash
./benchmark tool-routing-catalog \
  --catalog host-tools-raw.json \
  --subject hashmarks \
  --host chatgpt \
  --capture-id chatgpt-routing-001 \
  --output-capture host-tools.json
```

The command prints the canonical `catalog_sha256` and writes `agents-cookbook-tool-routing-catalog-capture.v1`. `--host`, `--capture-id`, and `--output-capture` are all-or-nothing so the scorer never receives a partially bound catalog artifact.

The ordered tool trace is a separate authority. Normalize the matching external host run into this small capture shape rather than pretending it is an OpenCode campaign:

```json
{
  "schema": "agents-cookbook-tool-routing-trace.v1",
  "host": "chatgpt",
  "capture_id": "chatgpt-routing-001",
  "catalog_sha256": "sha256:<digest printed by tool-routing-catalog>",
  "calls": [
    {
      "tool": "mcp__hashmarks__task_evidence",
      "status": "completed",
      "result_bytes": 1200
    },
    {
      "tool": "mcp__GitHub__search",
      "status": "completed",
      "input": {"query": "checkout discount owner"}
    }
  ]
}
```

The capture owns only observed tool name/order/status/input/result evidence. It must carry the same `host` and `capture_id` as the canonical catalog capture plus that capture's exact `catalog_sha256`. Host/session/catalog mismatches are rejected before scoring. It must not supply derived fields such as `tool_class`, `ordinal`, or router observability. A wrapper call may include observable `nested_calls`; without them an orchestration router is treated as opaque and cannot produce a false Hashmarks-first PASS.

Score a READY catalog and its matching trace without executing a model:

```bash
./benchmark tool-routing-trace \
  --catalog host-tools.json \
  --trace host-trace.json \
  --subject hashmarks \
  --output routing-score.json
```

Outcomes are `PASS` when the required subject call completed with an observable nonempty result before native discovery, `FAIL` when observable native discovery won first, `UNKNOWN` when ordering/result evidence is insufficient, and `ENVIRONMENT_BLOCKED` when the host catalog was not admissible. Exit codes are respectively 0, 1, 3, and 2. The score records canonical hashes of both input artifacts and recomputes all semantic classifications itself.

### OpenAI Responses routing dogfood

After a Hashmarks Secure MCP Tunnel handoff has been qualified, EvalShot can run one external OpenAI Responses diagnostic that puts Hashmarks `task_evidence` and `find` plus bounded native `grep`/`read` in the same model-visible tool set.

Prerequisites:

- a clean Git workspace to analyze;
- a current Hashmarks `hashmarks.chatgpt-secure-mcp-tunnel-handoff.v1` receipt bound to that exact workspace;
- a current `tunnel-client` binary;
- an existing OpenAI tunnel ID with Tunnels Read + Use;
- `OPENAI_API_KEY` for the Responses API;
- `CONTROL_PLANE_API_KEY` for `tunnel-client`.

Keep both keys in the process environment. The probe never writes either key to an artifact, and the tunnel runtime receives its control-plane key through a temporary mode-0600 file reference so the Hashmarks stdio child does not inherit OpenAI credentials.

Use an explicit model and prompt file:

```bash
export OPENAI_API_KEY='...'
export CONTROL_PLANE_API_KEY='...'

./benchmark openai-routing-probe \
  --workspace /absolute/path/to/repository \
  --handoff /absolute/path/to/hashmarks/dist/chatgpt-secure-mcp-tunnel-handoff.json \
  --tunnel-client /absolute/path/to/tunnel-client \
  --tunnel-id tunnel_0123456789abcdef0123456789abcdef \
  --model '<tool-capable-responses-model>' \
  --prompt-file routing-prompt.txt \
  --output-dir dist/openai-routing-probe
```

The probe:

1. verifies the handoff workspace, Hashmarks executable SHA/version, and any source-bound Hashmarks repository identity;
2. requires a clean Git workspace and snapshots all tracked bytes used by native `grep`/`read`;
3. takes a nonblocking local lock keyed by tunnel ID;
4. launches one foreground `tunnel-client` with the exact qualified Hashmarks stdio command;
5. calls the Responses API with `store:false`, `parallel_tool_calls:false`, and neutral repository instructions;
6. exposes only Hashmarks `task_evidence` and `find` from MCP plus native `grep` and `read`;
7. revalidates workspace and Hashmarks implementation authority around execution;
8. emits the same canonical catalog/trace score contract used by the host-neutral scorer.

Artifacts:

```text
dist/openai-routing-probe/
├── catalog.json
├── trace.json
├── score.json
└── receipt.json
```

The shell exit code is the routing outcome: 0 PASS, 1 FAIL, 2 ENVIRONMENT_BLOCKED, 3 UNKNOWN.

This diagnostic deliberately remains `heldout_comparable=false`. It proves model-visible routing under one exact local run, but it cannot prove that no second `tunnel-client` with the same tunnel ID is active on another host. OpenAI documents multiple active stdio clients sharing one tunnel ID as unsupported. Use a dedicated tunnel ID and operator discipline for dogfood runs; promotion to held-out authority requires a separately provable exclusive tunnel lease.

### Multi-task OpenAI routing dogfood campaign

For a real dogfood pass, use the three-way manifest at `benchmarks/dogfood/openai-routing-v3.json`. It contains five unknown-path behavior-localization tasks where `task_evidence` should win, two exact-symbol/unknown-path controls where Hashmarks `find` should win, and two exact-known-path controls where native `read` should win immediately. The prompts do not tell the model which tool to choose. Expected owner paths and routing expectations are used only after the model returns; they are never inserted into the model-visible instructions.

First create a fresh Hashmarks handoff bound to the EvalShot checkout from the sibling Hashmarks repository:

```bash
cd ../Hashmarks
make mcp-chatgpt-handoff \
  CHATGPT_MCP_WORKSPACE=/absolute/path/to/EvalShot
```

Then configure the two non-secret routing choices **once** in EvalShot's `.env`:

```dotenv
OPENAI_ROUTING_TUNNEL_ID=tunnel_0123456789abcdef0123456789abcdef
OPENAI_ROUTING_MODEL=<tool-capable-responses-model>
```

The checked-in `.env.example` contains the optional path/repeat/run-root overrides. Empty optional values keep the repository defaults. API credentials are deliberately not read from `.env`; export them only in the shell that will spend model calls:

```bash
cd ../EvalShot
export OPENAI_API_KEY='...'
export CONTROL_PLANE_API_KEY='...'
```

Run the model-free admission whenever you want to verify the environment without starting a model:

```bash
make benchmark-openai-routing-check
```

The check verifies the neutral manifest, clean workspace, Hashmarks handoff/executable/source identity, native repository snapshot bounds, tunnel-client identity, local tunnel-ID lock, model selection, and presence of both runtime keys. It does not start the tunnel or call a model.

The normal start-and-leave path is intentionally one command:

```bash
make benchmark-openai-routing
```

Every invocation starts a **new numbered immutable run**. The explicit spelling is equivalent:

```bash
make benchmark-openai-routing-new
```

Inspect without model calls:

```bash
make benchmark-openai-routing-runs
make benchmark-openai-routing-status
```

To change repeat count persistently, set `OPENAI_ROUTING_REPEATS=3` in `.env`. The old `make benchmark-openai-routing-dogfood` target remains an alias for starting a new run, but the shorter `make benchmark-openai-routing` is the canonical operator path.

Each trial starts from fresh Responses state and runs sequentially. There is no automatic retry. An infrastructure error writes one `error.json`, marks the campaign incomplete, and aborts all remaining trials so a broken external dependency cannot repeatedly spend model calls.

Each run is allocated atomically under `.benchmark-runs/openai-routing/runs/`:

```text
runs/
└── 000001/
    ├── preflight.json
    ├── manifest.json
    ├── summary.json
    └── trials/
    └── <task>-rNN/
        ├── catalog.json
        ├── trace.json
        ├── score.json
        └── receipt.json
```

`summary.json` preserves the raw host-neutral `task_evidence`-first outcomes and separately reports task-semantic routing fit. The three primary rates are `semantic_task_evidence_first_rate` for unknown-path behavior localization, `exact_symbol_hashmarks_find_rate` for exact-symbol/unknown-path controls, and `known_path_native_read_rate` for exact-path controls, plus overall `routing_fit_rate`, expected-path mention rate, first-tool counts, token totals, and elapsed time. A strong Hashmarks routing change should improve the semantic/tool-specific rates **without stealing direct-read cases**.

For routing changes, use numbered runs as an A/B sequence: first run the three-way campaign against the current Hashmarks main and keep that run as the baseline; then change/merge the Hashmarks candidate, regenerate the handoff, and start another numbered run. Compare all three routing rates together. `summary.json` records the admitted Hashmarks handoff SHA plus runtime version/executable identity, so the two results remain attributable without inspecting individual trial receipts.

The checked-in EvalShot repository is intentionally small enough for the current native comparison snapshot bound; preflight still rechecks the actual local checkout before any model work.

### Advanced direct CLI

The repository-owned `./benchmark` launcher is the canonical CLI for automation and explicit one-off selections. It resolves the harness root before delegating to uv, so invocation is independent of the caller's current Python environment. Core benchmark commands do not discover `.env`; they use the explicitly selected file. The convenience `openai-routing-check/new/runs/status` commands are the narrow exception and default their `--env-file` to `.env` so the one-command dogfood workflow stays simple. The snippets below show optional CLI overrides. Direct `--agent` accepts repeated values or a comma-separated list.

```bash
suite=benchmarks/suites/repository-intelligence/heldout-v1
root=.benchmark-runs/heldout-v1
agents=opencode-native  # or codex-native,opencode-native
```

Fast runtime readiness:

```bash
./benchmark check \
  --env-file .env \
  --suite "$suite"
```

This command uses no campaign root, harness root, or agent selection and creates no benchmark trial. Add `--agent "$agents"` to diagnose only the selected agents.

For advanced workflows that deliberately separate preparation from execution, create a saved run explicitly before preflight:

```bash
./benchmark prepare --new --env-file .env
```

```bash
./benchmark preflight \
  --env-file .env \
  --suite "$suite" \
  --root "$root" \
  --harness-root . \
  --agent "$agents" \
  --subject hashmarks \
  --subject none
```

Selecting `--subject hashmarks` selects only Hashmarks conditions. If a paired bare control is wanted, request `--subject none` explicitly; the selection layer never adds controls implicitly.

Then run the exact same explicit selection:

```bash
./benchmark run \
  --resume \
  --env-file .env \
  --suite "$suite" \
  --root "$root" \
  --harness-root . \
  --agent "$agents" \
  --subject hashmarks \
  --subject none
```

Status and report do not execute participants, so they need only the suite, campaign results, and the same explicit selection:

```bash
./benchmark status \
  --suite "$suite" \
  --root "$root" \
  --agent "$agents" \
  --subject hashmarks \
  --subject none

./benchmark report \
  --suite "$suite" \
  --root "$root" \
  --agent "$agents" \
  --subject hashmarks \
  --subject none
```

Preflight reports every selected frozen definition as one of:

- `READY` — source, mutation, subject, agent, workspace binding, and oracle admission succeeded;
- `COMPLETE` — the same observed execution identity already has a valid experimental outcome receipt;
- `INCOMPLETE` / `INVALID` / `CONTAMINATED` — the same classifications used by execution admission;
- `RECORDED_INCOMPLETE`, `RECORDED_INVALID`, or `RECORDED_CONTAMINATED` — an immutable non-outcome receipt already exists for that execution identity;
- `INVALID_RESULT` / `ERROR` — corrupt persisted evidence or a preflight infrastructure error.

Existing callers may pass `--cache` and `--work` without `--results` for a diagnostic preflight. In that mode preflight does not inspect existing receipts.

Then run the exact same selection:

```bash
./benchmark run \
  --resume \
  --suite "$suite" \
  --root "$root" \
  --agent "$agents" \
  --subject hashmarks \
  --subject enola
```

The runner reuses valid existing receipts. If the process died during one definition, its prior launch and available event bytes are preserved as numbered `INTERRUPTED` evidence; only that unfinished definition starts a new attempt. Run `./benchmark runs --env-file .env` to list saved IDs and pass `--run-id` to inspect or resume an older one. Status exposes separate integrity, completeness, and qualification checks. Live stderr shows processed definitions separately from verified receipts.

Inspect resumability without invoking any agent:

```bash
./benchmark status \
  --suite "$suite" \
  --root "$root" \
  --agent "$agents" \
  --subject hashmarks \
  --subject enola
```

`status.complete` means every selected definition has a verified immutable receipt. `status.qualified` additionally requires every receipt to be a valid experimental outcome (`PASS`, `FAIL`, or `NO_QUALIFYING_DEFECT`). A campaign can therefore be structurally complete but not qualified.

Each complete status row includes the receipt's diagnostic stage and reason code when available. Report diagnostics include the same fields, plus a bounded human-readable receipt `reason`, and mark older receipts without structured diagnostics as `legacy-inferred`. The detailed diagnostic remains in `result.json`; compact status and report output do not copy tracebacks or diagnostic detail. `decision-evidence.json` is always derived from the same `report.json` plus the same `trace-diagnostics.json` projection, both when a run first completes and when canonical reports are regenerated later. Regeneration therefore cannot silently add or remove trace-derived decision evidence for unchanged immutable receipts.

The canonical derived artifacts are published as one **generation-bound report set**. `reports/report-set.json` is checksum-bound to the exact persisted bytes of `report.json`, `trace-diagnostics.json`, `decision-evidence.json`, and the suite-owned score artifact (normally `score.json`). It is atomically published only after every bound replacement is durable. A crash midway through replacement therefore leaves either the previous verified generation or a manifest/file mismatch that status reports as `INVALID`; mixed generations are never authoritative.

The v2 report-set manifest names the exact score artifact and includes it in the same `generation_id`. Historical v1 manifests remain readable as `VERIFIED_REPORT_ONLY`, but they do **not** authorize a score; score-consuming gates require a v2 score-bound generation and instruct the operator to regenerate reports otherwise. Historical report files without any manifest are labeled `UNBOUND`, not silently treated as verified. `status.json` records the current verified report-set generation whenever canonical reports are published or refreshed.

There is one canonical writer for this generation. Run completion, `reports`, canonical `report` refresh, and `score` without an explicit external `--output` all converge on the same full persistence path. An explicit `score --output <path>` remains an export/inspection operation and does not mutate canonical generation authority. Consumers such as `tool-probe-smoke-gate` resolve their score only through the verified report-set manifest.

A final run summary on stderr reports verified receipts, outcome counts, and qualification. A completed run exits successfully after its canonical reports are persisted even when the campaign is not qualified; use `benchmark-status --require-qualified` when qualification itself must gate the shell command.

Finally:

```bash
./benchmark report \
  --suite "$suite" \
  --root "$root" \
  --agent "$agents" \
  --subject hashmarks \
  --subject enola
```

The same selectors should be used across preflight, run, status, and report. Bare controls are included only when explicitly selected or when the full frozen suite is run without subject filters.

### Immutable outcomes and interruption evidence

A published `INCOMPLETE`, `INVALID`, or `CONTAMINATED` receipt is evidence and is never deleted or silently overwritten. A process interruption before any complete receipt exists is different: it is preserved as immutable numbered attempt evidence and the unfinished definition may resume with a new explicit attempt.

Long unattended campaigns treat **participant authority drift as evidence, not as a campaign-wide crash**. If the native agent/runtime or selected subject authority changes after the campaign was admitted, the harness:

1. preserves the affected attempt/result exactly as observed;
2. records an immutable authority-epoch transition with old/new participant authority, changed fields, timestamp, and detection call stack;
3. derives subsequent execution identity from the new accepted epoch;
4. retires any stale pre-transition launch as numbered interruption evidence;
5. marks campaign evidence `TAINTED` and keeps collecting the remaining diagnostic evidence.

Epochs never silently restore comparability. Any participant-authority transition blocks qualification for that saved campaign even when execution reaches every planned definition; status and the final run summary expose the transition count. The same verified epoch projection is carried into `report.json`, `decision-evidence.json`, and the heldout score so derived artifacts cannot reclassify a tainted campaign as qualified.

A tainted campaign still produces diagnostic reports. Participant-mixed comparative surfaces are deliberately suppressed instead of computed across epochs: paired assistance, stability, context invariance, cross-agent observations, and the collapsed task/agent authority projection are unavailable with reason `participant-authority-epoch-taint`. Raw receipt diagnostics, per-condition operational summaries, subject-adoption evidence, and the compact epoch transition ledger remain inspectable. Start a fresh campaign under one stable authority when qualification or comparative conclusions are required.

This recovery boundary does **not** weaken benchmark authority. Frozen suite/oracle/task inputs, harness authority, environment authority, campaign receipt integrity, or evidence corruption remain hard failures because subsequent results could no longer be interpreted or trusted. Participant instability can be preserved and segmented; benchmark-definition or evidence-authority instability cannot.

For OpenCode executable drift, the durable failure diagnostic includes the admitted and observed path/digest/version/filesystem metadata where available, a field-level diff, and the detection call stack. This is deliberately diagnostic provenance rather than a second scoring authority.

If the underlying infrastructure problem is corrected but observed execution authority does not change, start a new saved run instead of mutating old evidence. If participant authority changes, the current saved run may continue collecting tainted evidence through the epoch mechanism while a new saved run remains the path to a clean comparable qualification campaign.

`--root` selects the run store; each numbered run owns its `cache/`, `work/`, and `results/`. A legacy single-directory campaign remains selectable as `--run-id legacy`. Frozen suite definitions and verified per-trial receipts remain authority.

## Method

Native runtime readiness (optional, explicit) -> frozen input -> preflight admission (optional, explicit) -> isolated execution -> observed execution identity -> independent oracle -> sealed events -> immutable verified receipt -> status/resume -> aggregate only valid evidence.

Do not change a frozen task, mutation, oracle, or scoring contract after seeing a result. Create a new suite/experiment version instead.
