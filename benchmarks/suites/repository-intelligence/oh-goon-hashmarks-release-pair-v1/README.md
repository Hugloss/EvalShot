# Oh-Goon released-Hashmarks paired coding diagnostic v1

This frozen diagnostic compares released Hashmarks 0.32.0 and 0.32.1 through
Oh-Goon's original native Codex service path. Both arms receive mandatory
qualified Hashmarks evidence. The `none` EvalShot subject means **no additional
MCP subject**; neither arm is a bare coding-agent control.

The population is three distinct Python coding tasks (inclusive range merging,
deterministic deployment dependencies, and CIDR access-policy evaluation), two
released dependency arms, and one shared paired replicate ID, 9101: six original
Runs. Arm order alternates by task. Replicate identity is not a provider RNG seed.
This small sample is descriptive only: no causal quality, productivity, cost,
latency improvement, or overall winner claim is admitted. The tasks already
appeared in earlier Oh-Goon dogfood; this is not a held-out generalization set.

## Frozen authority and independent grading

Fixture and verifier literals come from the exact Oh-Goon source recorded in
`fixture-provenance.json`. A pinned unrelated historical agentsCookbook snapshot
provides the existing materialization owner; each actor receives only its new
`candidate/` directory. Source fixtures have explicit byte digests. Only declared
implementation files may change. The suite-owned oracle files and expected
answers stay outside the actor's admitted repository and Hashmarks evidence.
The optional caller-selected maintenance Skill is separately captured by Oh-Goon.
Native Git initializes the candidate directory as its own repository before the
admitted snapshot, with a deterministic branch and an empty owned template.
The historical parent checkout remains unchanged. Candidate Git metadata stays
in EvalShot's enclosing snapshot and contamination accounting; no extra ignored
source or generated-output allowance is introduced. Actual trial preparation
also invokes Oh-Goon's public CLI-selection owner for this exact root, without
starting a native service or model. Readiness remains a metadata-only probe.

EvalShot's existing campaign authority freezes suite/task/fixture/oracle/harness,
runtime supply, and paired inputs before inference. Its existing CommandOracle
checks grader syntax and rejection of each known broken fixture before admission.
The native coding result is frozen before the independent verifier executes.
Original Oh-Goon Run, source-admission, Step, native observation, and cleanup
projections remain separate from benchmark correctness. A successful coding Run
can therefore produce an independent benchmark FAIL. Infrastructure failures
remain INCOMPLETE; failed or unfavorable trials are not retried for score.

The adapter does not inspect, hash, copy, export, or force-refresh any native
authentication or configuration store. The native home is an opaque path supplied
only to the selected Oh-Goon service owner. Public runtime and benchmark paths
must be outside it. Coding and authentication always enter through Oh-Goon.
A model-free native `--version` probe runs with the isolated trial HOME and binds
the selected public executable bytes. No model server or Ollama is used.

Compatibility disposition: **retain** Oh-Goon's Codex service protocol,
reserved-directory policy, frozen instruction delivery, and original settlement
owners. This adapter adds no native session adoption, configuration fallback, or
credential adaptation. Each selected implementation/mode/environment still needs
its own campaign evidence; executable readiness does not inherit installed
execution qualification. The deterministic adapter tests prove translation and
refusal boundaries, not live model quality or credential lifecycle.

## Matched runtime supply

Prepare two native uv environments from the **same Oh-Goon source**, changing only
`backend/pyproject.toml` and its native `backend/uv.lock` to select the two released
Hashmarks versions. Never inject an editable Hashmarks source or private wheel.
Each public runtime-supply receipt uses schema
`evalshot.oh-goon-runtime-supply/v1` with:

- `oh_goon_source_root`, `oh_goon_source_commit`, `oh_goon_source_tree`;
- `pyproject_sha256`, `uv_lock_sha256`, and exact `dependency_overrides` paths;
- `hashmarks_version` and the loaded `hashmarks_implementation_identity`.

Native Git source identity, interpreter bytes, dependency-file digests, public
providers digest, loaded Oh-Goon module origin, loaded producer identity, and native
Codex executable bytes/version are observed before and after model execution.
Native observations must match that executable implementation and version.
Companion runtime-root bytes are not independently observed by this adapter;
matching full installation supply remains an explicit caller qualification. Changes to those observed source, executable, producer, or public-catalog
authorities cannot qualify. The caller must independently establish
matched non-treatment tool/dependency/network supply before campaign admission.

Each public providers catalog selects profile `benchmark-service`, an explicitly
selected complete public Codex installation, `codex_service`, model `gpt-6-luna`,
reasoning effort `low`, and the exact campaign workspace root. Native Codex alone
owns sign-in. The adapter invokes the selected virtual-environment Python
launcher without resolving away its uv environment.

Copy `.env.example` outside source, replace the seven explicit runtime paths and
campaign paths, then use the existing front door:

```sh
./benchmark check --env-file /absolute/private/paired.env
./benchmark prepare --new --env-file /absolute/private/paired.env
./benchmark campaign-audit --env-file /absolute/private/paired.env
./benchmark run --resume --env-file /absolute/private/paired.env
./benchmark report --env-file /absolute/private/paired.env
```

Use both declared agents; do not drop an unfavorable arm or task. Existing
create-once receipts, campaign locking, interrupted-launch history, and resume
semantics remain authoritative. A changed harness or experiment needs a new run.

The adapter measures elapsed wall time of `oh-goon ai`, including its startup
and shutdown. Runtime observation probes and independent grading are outside
that counter. Native tool counts, context consumption, tokens, final-edit timing,
and provider sampling seed are
unavailable and omitted from numeric measurements. Missing metrics must remain
unknown. Generic report assistance rows do not measure this version treatment;
inspect the three task-matched baseline/candidate outcomes and their exact
original Run IDs. No bare-versus-Hashmarks treatment is claimed.

The optional installed CLI-selection regression is model-free. Select the public
Oh-Goon Python environment explicitly; its synthetic service catalog and native
home stay in test scratch, with no native daemon or inference:

```sh
BENCHMARK_OH_GOON_CANDIDATE_PYTHON=/absolute/Oh-Goon/backend/.venv/bin/python \
uv run --no-project python -m unittest benchmarks.tests.test_oh_goon_adapter -v
```
