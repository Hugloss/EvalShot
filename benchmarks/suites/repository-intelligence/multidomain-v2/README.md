# Multidomain repository-intelligence benchmark v2

This is an **explicit research benchmark**, not a Hashmarks release gate. No test,
CI, or release target invokes it. The older heldout-v1 suite remains unchanged.

The frozen draft has 60 evidence cases, ten each for logs, Splunk CSV exports,
dependencies, cross-artifact semantics, identities, and code ownership. Six cases
per family are also agent tasks. Four pinned repositories contribute 15 cases
each: Hashmarks, doctor-scheduler-core, doctor-scheduler-frontend, and
uv-fleet-updater. Fixtures are synthetic and content-hashed. The agent suite has
36 tasks × two native agents × three conditions × one attempt = **216 trials**.

The cases are currently marked `review.state=pending`. Their source anchors came
from inspected pinned source and existing repository-quality labels, but the new
questions and oracles have **not** received two independent reviews. The corpus
validator refuses an `approved` label without two distinct reviewer identifiers.
Reports show the reviewed count; do not present pending cases as validated
benchmark conclusions. Case review must confirm that the question is realistic,
the expected answer is correct, the fixture does not reveal it, and plausible
alternative answers are handled fairly. Editing a case or fixture changes its
definition identity and requires renewed review. Each approval contains a
reviewer ID and the exact case digest (excluding the review field); stale
approvals are rejected. Read the digest without changing the corpus:

```sh
uv run --no-project python -m benchmarks.evidence review-digests \
  --corpus benchmarks/suites/repository-intelligence/multidomain-v2/evidence.json \
  --case logs-00
```

## Set up and run explicitly

Set the following in this checkout's `.env` before agent work:

```dotenv
BENCHMARK_SUITE_PATH=benchmarks/suites/repository-intelligence/multidomain-v2
BENCHMARK_CAMPAIGN_ROOT=/tmp/agentscookbook-multidomain-v2
BENCHMARK_HARNESS_REPO_ROOT=.
BENCHMARK_AGENT=codex-native,opencode-native
HASHMARKS_BENCH_SOURCE=/absolute/path/to/clean/Hashmarks
BENCHMARK_SCORE_SCRIPT_PATH=benchmarks/suites/repository-intelligence/multidomain-v2/score.py
BENCHMARK_SCORE_OUTPUT_PATH=score.json
```

All required local settings must be explicit; a missing setting stops the target
with an error. Native Codex, OpenCode, and Enola installation and provider setup
are observed from the host as described in the benchmark root README.

```sh
make benchmark-evidence-validate  # no model call
make benchmark-check              # six distinct native readiness pairs, no model call
make benchmark-smoke              # six tasks, bare + Hashmarks, selected agent(s)
make benchmark                    # all selected agent/subject tasks
make benchmark-report             # generic complete receipt report
make benchmark-score              # six-family score for selected agent(s)
```

`benchmark-check-all` remains an optional, explicit preflight over all selected
definitions. It is never run as a prerequisite to `benchmark` or a release.
Use a fresh campaign root after changing agent, model, subject, fixture, or harness
authority. The score requires 108 valid bundles per selected agent; selecting
both requires all 216.

The direct evidence lane is also explicit. It preserves raw native output,
records whether an expected path is visible, and grades Hashmarks' structured
owner/ambiguity claim for the code-ownership cases. It records whether required
provenance fields are present; it does not independently certify their content.
**Path visibility is only a retrieval diagnostic.** Other direct semantic answers
remain `NOT_ASSESSED` until a case-specific native grader exists. Cases without
a reviewed equivalent native Enola probe are `NOT_COMPARABLE` in this protocol,
not product failures. The agent tasks provide the shared outcome comparison
across all three subject arms.

```sh
suite=benchmarks/suites/repository-intelligence/multidomain-v2
root=/tmp/agentscookbook-multidomain-v2
uv run --no-project python -m benchmarks.evidence validate --corpus "$suite/evidence.json"
uv run --no-project python -m benchmarks.evidence run --corpus "$suite/evidence.json" \
  --env-file .env --subject hashmarks --cache "$root/cache" \
  --work "$root/evidence-work" --results "$root/evidence-hashmarks" \
  --local-source hashmarks=../Hashmarks \
  --local-source doctor-scheduler-core=../doctor-scheduler-core \
  --local-source doctor-scheduler-frontend=../doctor-scheduler-frontend \
  --local-source uv-fleet-updater=../uv-fleet-updater
uv run --no-project python -m benchmarks.evidence report --corpus "$suite/evidence.json" \
  --subject hashmarks --results "$root/evidence-hashmarks"
```

Run Enola with `--subject enola` and a separate results directory. `--case ID`
selects an explicit subset for a probe. Existing direct-evidence results are
immutable; choose a fresh results root for a new execution. The native probes
do not call an LLM. No direct output is normalized into a fabricated common
graph or used as the independent oracle.

The agent score reports task success, failed and unresolved statuses, paired
bare/assisted outcomes, tool adoption, tokens, and duration by family and
agent. It does not calculate an overall winner or affect release decisions.
