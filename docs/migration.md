# Migration from agentsCookbook

EvalShot owns benchmark and evaluation execution. agentsCookbook owns the live
skills, agents, flows, protocols, installation adapters, and their production
checks.

## Source

This extraction starts from agentsCookbook commit
[`40be826e6883d819ee6cf5c10946c793f0ef0f5f`](https://github.com/Hugloss/agentsCookbook/commit/40be826e6883d819ee6cf5c10946c793f0ef0f5f).
The original history and attribution remain available there. The original
MIT license is included in EvalShot.

The move includes 658 original files:

| Group | Files |
| --- | ---: |
| `benchmarks/` | 538 |
| `scripts/` including the complete Agent Economics support toolkit | 84 |
| Measurement and qualification documentation in `docs/` | 17 |
| `evals/` corpus and fixtures | 10 |
| Additional regression tests in `tests/` | 5 |
| `benchmark`, `Makefile`, `.env.example`, `.python-version` | 4 |

EvalShot also has its own README, CI workflow, ignore rules, test dependency
lock, and migration regressions. Agent Economics moves as a complete support
toolkit because the harness uses its bounded process runner and the cookbook's
retained production scripts have no runtime dependency on that package.

## Canonical ownership after extraction

| Responsibility | Owner | Consumer boundary |
| --- | --- | --- |
| Canonical skills/agents/flows and reviewer-selection guidance | agentsCookbook | EvalShot reads only the explicitly selected checkout |
| Prompt active-context discipline and OpenCode/Pi installation/session adapters | agentsCookbook | No evaluator-specific numerical ceiling or score is an adapter preflight requirement |
| Model context size, output limits and compaction configuration | Selected model provider/agent host (e.g., OpenCode/Pi) | No repository-wide cookbook or EvalShot token-window requirement |
| [Observed context consumption and evaluation outcomes](model-context-observation.md) | EvalShot | Record what the selected runtime actually reports, including unknown measurements; no duplicated model configuration |
| Tool-call classification, native-vs-subject routing observations and measured first-choice/late-rescue outcomes | EvalShot (`benchmarks/tool_routing.py`) | Distinct from cookbook's reviewer-selection methodology |
| Executable evaluation/Agent Economics helpers and [their security policy](../SECURITY.md) | EvalShot | No mirror command runner or policy in cookbook |
| Repository intelligence observations | Hashmarks (when used as a subject) | Evaluator independently freezes task/oracle/scoring authority |
| Agentic workflow execution and repair-loop control | External runtime such as OH-Goon | Neither prompt library nor evaluator owns the consumer's workflow engine |

Historical receipt/schema identifiers continue to name their original protocol
versions; their spelling does not imply an active second owner.

## Commands and source inputs

Run the existing `./benchmark` and `make benchmark-*` commands from EvalShot.
The Python import paths (`benchmarks` and `scripts.agent_economics`) and
in-repository asset paths remain the same.

The skill and flow evaluators now require `--cookbook-root /path/to/agentsCookbook`
when consuming live cookbook definitions or helpers. This source input is
separate from the evaluator's corpus/fixtures and from `--repo`, which selects
the target working repository. No sibling checkout is discovered implicitly.
Skill corpus validation continues to check actual source definitions at this
explicit boundary.

The Python readiness and review-queue projections inspect the frozen corpus
inside EvalShot. They do not require a live cookbook checkout. Their corpus
coverage tests are retained; live-source validation is exercised by the JS
boundary tests and the CI checkout pinned to the source commit above.

## Existing configuration and campaigns

Start from EvalShot's `.env.example` or copy your existing local configuration
deliberately. Check every path, particularly:

- `BENCHMARK_HARNESS_REPO_ROOT` must identify the EvalShot checkout; `.` works
  when running from its root.
- Relative suite and scorer paths still name the same files inside EvalShot.
- `HASHMARKS_BENCH_SOURCE` still selects the separate product checkout under test.
- `BENCHMARK_CAMPAIGN_ROOT` identifies the chosen local campaign store.

The migration carries tracked source only. Local `.env` files, credentials,
model traces, and ignored campaign directories are not Git source and must be
managed separately by their operator. The cookbook continues to ignore old
local benchmark runs so this cleanup cannot accidentally expose them.

Existing receipts bind their original harness commit, tree, and working-copy
identity. Keep those receipts and use the matching historical harness when
reproducing or resuming that generation. Start a new campaign for the migrated
harness. Do not rewrite old receipts, completed-run markers, or authority fields
to make an old run appear to have been produced by EvalShot.

## Preserved experiment contracts

Some frozen tasks use historical agentsCookbook commits as repositories under
test. Their repository URL, commit, tree, oracle expectations, and definition
identities remain unchanged. They are independent of the location of the
current evaluator. The three pilot mutation regressions use the self-contained
[`cookbook-pilot.bundle`](../benchmarks/tests/fixtures/README.md) fixture to
materialize their two historical source commits. A fresh EvalShot clone can run
those tests offline without importing cookbook history into its own Git branch.

Versioned `agentscookbook.*` and `agents-cookbook-*` schema identifiers and
runtime transport strings are also preserved for compatibility. The live
harness identity and the default Agent Economics bootstrap repository name
EvalShot. Historical analysis documents and PR references describe their
original source-repository context.

## Integration order

Integrate the EvalShot import before the agentsCookbook removal. The latter
updates cookbook documentation and CI to direct evaluation work here while
retaining canonical source and adapter checks. No forwarding benchmark runtime
remains in agentsCookbook.
