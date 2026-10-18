# Observing model context in evaluations

**Canonical boundary:** the configured model, its provider, and its agent host
(e.g. OpenCode or Pi) determine the actual context window, output limit,
compaction policy and effective runtime constraints.

EvalShot **does not configure a model's context window** and does not require
any project-wide fixed token ceiling, working-set target or reserve. The reusable
agentsCookbook prompts and skills likewise do not prescribe one. Each agent
works with the context made available by its host; an operator who wants a
fixed setting selects it in the model/provider configuration.

See [OpenCode's provider configuration](https://docs.opencode.ai/docs/providers/)
for model `limit.context` and `limit.output`. For recognized standard
providers, OpenCode can load model limits automatically. Declared limits
must match what the backend actually supports; increasing a client-side
declaration does not enlarge the model server's real capacity.
Compaction behavior also belongs to the agent host, not the evaluation suite.

## Native OpenCode benchmark execution

The evaluator's OpenCode trial overlay controls the selected MCP subject and
trial workspace permissions; it does **not** write `compaction`, reserve a
fixed number of tokens, or reject a model because its context metadata is
missing or smaller than an evaluator-chosen threshold. A freshly observed
base configuration and the effective trial configuration must retain the same
compaction policy. If the host's token limits are absent, their value is
unknown to EvalShot rather than an evaluator admission failure.

Host/provider failures during actual model execution remain real observed
results; this boundary removes only the evaluator's competing preflight rule.
Independent experiment timeouts, output evidence bounds and subject/tool
selection are unaffected.

## EvalShot's responsibility

EvalShot observes and grades outcomes, not the agent's context-management
algorithm. For tests that claim context behavior, record as available:

- Exact evaluated model, provider/host, source revision and configuration
  identity; any supplied effective context/output limit.
- Actual model-visible token usage, if exposed, as well as the basis of the
  measurement and any known limitations.
- Observed compaction, truncation, rejected oversized requests, and whether
  agent tasks continued successfully.
- Bounded evidence selection and task outcomes, without treating a lower
  token count as more correct by itself.

**Missing token data is UNKNOWN**, not a PASS, FAIL or guessed capacity.
Character/byte measurements alone do not establish token-window compliance.

## Experimental comparison of runtime settings

A benchmark may compare **operator-selected runtime configurations** with
different model context windows or compaction behavior. The experiment can
freeze and report the selected configuration identity, but EvalShot does not
impose an additional context-window threshold on an agent, resize a model,
or override the provider's limits. Configure each condition in the host
(e.g. OpenCode) and record the observed result.

Existing frozen suite budgets for time, evidence, output or particular
experiments retain their historical authority; they must not be
misinterpreted as a global model context setting. Frozen definitions,
identities and historical results remain intact.

Evaluation must not silently discard task requirements or declare failure
solely because an otherwise supported model has a different context
window.

## Cookbook boundary

The [agentsCookbook context protocol](https://github.com/Hugloss/agentsCookbook/blob/main/protocols/context-discipline.md)
describes selective evidence, compact receipts and acknowledgement of unknowns.
The host decides whether, when and how to compact the conversation.
[EvalShot evaluation](../evals/README.md) measures behavior against the
**selected model actually used**, rather than requiring its context size to
match a hard-coded cookbook or EvalShot profile.
