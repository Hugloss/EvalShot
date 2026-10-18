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

## Experiment-specific budgets

A frozen benchmark may deliberately impose a **scenario-specific**
context budget to compare defined conditions. Such a budget is part of
that suite's independently specified experiment, not a default for the
entire project and not a source of runtime model configuration.
Existing frozen suites, identities, and historical results remain intact.

Evaluation must not override a correctly configured runtime to manufacture
a profile match, silently discard task requirements, or declare failure
solely because one unrelated model uses a different context window.

## Cookbook boundary

The [agentsCookbook context protocol](https://github.com/Hugloss/agentsCookbook/blob/main/protocols/context-budget.md)
describes selective evidence, compact receipts and acknowledgement of unknowns.
The host decides whether, when and how to compact the conversation.
[EvalShot evaluation](../evals/README.md) measures behavior against the
**selected model actually used**, rather than requiring its context size to
match a hard-coded cookbook or EvalShot profile.
