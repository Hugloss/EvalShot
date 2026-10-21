# Native evidence discovery pilot v1

This is a **three-task, eighteen-assignment discovery pilot**, not held-out
confirmation. It measures whether the current native Codex configuration selects
shipped-default Hashmarks evidence and whether the measurement path works on
localization and one repair. Existing suites and receipts remain unchanged.

| Arm | Condition | Purpose |
| --- | --- | --- |
| A | `none-codex-native` | Native tools without admitted Hashmarks exposure |
| B | `hashmarks-codex-native` | Shipped-default Hashmarks MCP, optional invocation |
| C | `hashmarks-codex-required` | Explicitly request `task_evidence` before native search |

A and B share the same native agent definition. C uses the same installed Codex
and native model/provider settings with an additional frozen tool instruction.
It is an instructed-policy diagnostic: a C improvement cannot isolate the value
of repository information from the prompt's strategy/priming effect. No result
from C is folded into voluntary adoption or the A/B primary comparison.

## Frozen tasks and provenance

All task files, source commits/trees, prompts, oracle definitions, mutation and
source-review records are transferred **byte for byte** from `heldout-v1`.
`protocol.json` records the source digests and selection rationale. New experiment
and agent identities bind this smaller population and its interventions.

| Task | Role and limitation |
| --- | --- |
| `locate-prefix-path-enumerator` | Unknown-path Python localization; a prior OpenCode baseline error motivates inspection, without establishing current Codex headroom |
| `locate-refresh-outcome` | Unknown-path TypeScript localization; prior returned-candidate omissions motivate a retrieval diagnostic, without assuming an incorrect baseline answer |
| `repair-partial-receipt-regression` | Unknown-path edit-owner and behavioral-test smoke; previously solved baseline trials make this a possible ceiling control |

Each task has paired replicate IDs `9501` and `9502`. They are observation IDs,
not model RNG seeds. Six task/replicate blocks use each permutation of A/B/C once,
with every arm appearing in each ordinal twice. The experiment's execution order
is frozen in `experiment.json` before model work. The sequential runner preserves
that requested order. Current sealed trial events lack a cross-trial chronology;
the score therefore labels actual launch order `NOT_INDEPENDENTLY_AUDITED` and
does not claim random assignment. Retain the controller's run-order trace when
executing the campaign.
Fresh workspaces, sessions, scratch and subject state are required for each trial.
Changing source, effective native configuration, subject artifact or frozen
selection requires a fresh campaign. A controller interruption is an incomplete
execution; completed receipts are retained under the existing execution owner.

The earlier `freshness-00` candidate is omitted: its original prompt does not
specify the rewrite demanded by its oracle, source and observation metadata can
conflict, and its required next-evidence answer excludes defensible alternatives.
The original task remains historical. This pilot does not repair or regrade it.
The reviewed `declarations-00` case is also omitted: a container's selected
Python 3.12 runtime and a project's `>=3.12` requirement have different semantic
scopes, so equal normalized producer strings do not justify the oracle's claim
that the source declarations are equivalent. Its historical records remain intact.
Declarations, qualified negative evidence, actual cache invalidation, unsupported
languages and ambiguous-owner safety require separate qualification; no broad
coverage claim follows from these three tasks.

## Measurement and grading

Use the existing independent repository-location oracles and command test oracle.
Oracle expectations and reviews stay in the harness, outside the materialized
repository and subject index. Do not put oracle output or review rationale into
agent prompts. The workspace sandbox alone does not prove that host grader files
are secret; record that limitation and inspect explicit leakage if observed.

The primary A/B analysis retains all six frozen pairs, including non-invokers,
missing trials and unknown outcomes. Unknown outcomes have null rates and
explicit bounds; they are never coerced into failures. A completely measured
zero-adoption B arm is valid evidence. The older generic subject-use report may
still fail its invocation requirement; the availability report has its own
explicit schema and qualification.

Keep configured server catalog, invocation, successful return, lossless packet
decoding, submitted-model-request inclusion, and observable follow-through
separate. Current native exports do not prove request inclusion or attention;
these remain **UNKNOWN**. Returned-evidence subsets are post-assignment
diagnostics and cannot replace the primary denominator.

Grade patch behavior separately from localization and JSON formatting. Keep
semantic failures, ungradeable answers, invalid execution and incomplete work
distinct. Capture source/tool calls, serialized tool bytes, observed tokens,
wall time and index/startup costs where available. Missing metrics remain unknown;
fewer reads alone does not establish lower total cost. Native source-read
observability remains limited.

Agent `duration_ms` covers native execution only. Admission phase timings,
including preparation/startup and oracle health, are retained separately in each
receipt's `measurements.admission`. Its `total` already includes the listed
component phases. Publication and cleanup are outside those totals, so these
measurements do not establish complete trial wall time or total cost.

## Budget and decisions

The frozen budget is 18 trials, inherited 600-second trial limits, declared
qualification limits summing to 2,100 observed tool calls, and 50 MB captured
output per trial. Codex tool-call limits are checked after execution; they do
not establish a hard process stop at that number of calls.
The sum of trial timeout ceilings is three hours; it is not an expected runtime
or total wall-clock cap. There is no fabricated monetary or token ceiling, and
the evaluator does not change native inference settings to manufacture one.

Before inference, source/oracle/catalog/runtime admission and all independent
source reviews must pass. Afterward, keep every assignment and failure category.
Complete evidence can support a negative finding, including zero adoption or no
observed headroom. Reproducible delivery or evidence defects justify one focused
repair and regression. If C alone helps, investigate discovery/presentation and
a matched instruction control before attributing information value. False
certainty or an incorrect repository claim requires investigation before expanding
the affected evidence path.

Two repetitions do not establish statistical power, repeated-failure headroom or
correctness noninferiority. Do not apply the older three-replicate headroom
criterion to this pilot. Pilot variance and observed discordance can inform a
separately frozen held-out study; +10 percentage points localization and 20%
fewer reads remain hypotheses, not pilot acceptance gates.

## Running

From a clean, committed EvalShot checkout, copy `.env.example` to a private env
file and select the exact clean Hashmarks checkout. Run model-free readiness
first. Both `codex-native` and `codex-required` are required for the full pilot;
OpenCode and Pi are outside this host's initial population.

Preserve the complete selected native configuration, including installed plugins.
The native loader owns version and capability selection from the transported
plugin inputs. Unrelated native MCP capabilities must remain the same in A, B
and C. Both agent definitions declare `controlled_subject=hashmarks`; only that
admitted exposure changes between arms. Do not disable other plugins to pass
readiness. A missing plugin input, alternate Hashmarks route or unavailable
enabled service blocks admission until its support is complete.

The filesystem namespace maps native absolute Codex paths to private trial
state. It retains networking and host read access; it does not prove that an
external desktop service is independent or that oracle files are secret.

```sh
./benchmark check --env-file /path/to/private-pilot.env
./benchmark prepare --new --env-file /path/to/private-pilot.env
```

Preparation does not invoke a model. Start the admitted campaign only after its
runtime/source manifest and budget have been reviewed. The runner owns execution,
ordering, timeouts and receipts; Hashmarks supplies repository evidence only.
The suite score keeps A/B intention-to-treat measurement and C diagnostics
separate, with no overall-winner, release or causal authority.
