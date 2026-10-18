# Working evidence

Agent Economics uses plain files for task-local continuity. It does not own a database, hidden evidence service, repository graph, or persistent repository-intelligence cache.

## Ownership rule

Ask one question before putting information in working evidence: Could another agent working on the same repository reuse this fact without knowing the current task? If yes, it belongs in Hashmarks or another repository-intelligence provider when one is available.

Working evidence is for the current task: the goal, task/environment-local facts, agent decisions, and remaining work.

## Minimal shape

The canonical shape has schema agentscookbook-working-evidence/v1 and exactly four conceptual sections: task, known, decisions, and remaining. A host may store it as .agent/evidence.yaml. Agent Economics does not require that path, does not create a daemon around it, and does not make the file historical.

## Do not copy repository intelligence

Do not place repository graphs, ownership graphs, test graphs, repository snapshots, or verification caches in working evidence. Prefer a provider-neutral reference containing only `provider`, `evidence_identity`, and optional `repository_identity` / `generation`, then query the repository-intelligence owner for current facts. Provider references deliberately reject extra embedded repository-intelligence fields.

Hashmarks may own repository identity/generation, structural impact, imports/references/ownership, test relationships, provenance, completeness, freshness, observation deltas, diagnostic identities, and generation-bound external observations. Agent Economics owns the policy decision made from those facts.

## Lifecycle

Keep only the current useful working set. Delete task-local facts when they are no longer useful. Environment-local facts need explicit invalidation conditions when reuse could be unsafe. Supported lifetimes are file-edit, worktree-change, repository-generation, dependency-change, environment-change, process-restart, session-end, and never-within-session. Repository-generation invalidation requires a provider reference: working evidence must not create its own repository generation counter.

## Graceful degradation

The contract works without Hashmarks. A host may discover evidence locally when no repository-intelligence provider exists. That fallback must not become a second persistent repository model.

## Current implementation slice

scripts.agent_economics.working_evidence provides a stdlib-only constructor and validator for this minimal contract. The validator rejects obvious top-level repository-intelligence copies that would create parallel ownership.


## Reuse boundary

A provider reference is not proof that evidence is still fresh. The provider owns repository freshness and generation semantics. Agent Economics may decide to reuse evidence only after the provider says the referenced evidence remains applicable, or when the evidence is explicitly task/environment-local and its own invalidation condition has not occurred.

This keeps the workflow simple: working evidence remembers *that* the agent already obtained useful evidence and *why* it made a decision; Hashmarks or another provider remains responsible for what the repository currently says.


## Anti-repeat rule

Agent Economics treats an evidence-acquisition action as no progress when its canonical action, inputs, and provider evidence references are unchanged. Input order does not create novelty. A changed provider evidence identity permits reconsideration because the underlying evidence set may have changed.

This is a policy primitive, not a runner: it does not execute, retry, or suppress commands by itself. The consuming skill or host uses the fact when deciding whether another tool call is worth its cost.


## Dirty-gate differential

Agent Economics consumes provider-produced diagnostic deltas rather than parsing raw lint/type output into a second repository model. Equal global counts are not treated as equal evidence: the policy surface retains added, removed, unchanged, and newly added diagnostics intersecting the changed scope.

The helper exposes facts only. A skill may use them to decide whether another broad dirty gate is worth running, but the working-evidence layer does not declare a repository clean or execute a gate.


## Scoped evidence reuse

Working evidence does not compare repository paths to decide freshness. It consumes the provider's freshness result. A provider may preserve focused evidence across a repository change when it has proved the edit is outside the observation/dependency scope; a relevant intersection makes that evidence stale.

Unknown or unsupported freshness fails closed. This preserves useful evidence without moving dependency analysis into Agent Economics.


## Evidence sufficiency and stopping

Evidence acquisition stops when every declared risk boundary has at least one fresh, direct proof and no new evidence has expanded the affected scope. A merely related test is not automatically direct behavioral proof, and stale proof does not satisfy a boundary.

Scope expansion reopens acquisition even if the previously declared boundaries were satisfied. The next-evidence path checks this sufficiency state before translating another evidence requirement into a command, so an already-proven task does not manufacture another verification step.

Risk-boundary declaration and sufficiency are Agent Economics policy. Repository relationships, evidence identity, and freshness remain provider-owned facts.


## Scope expansion provenance

Scope expansion is not a free boolean owned by the agent. Reopening evidence acquisition requires a concrete provider evidence identity and the newly exposed risk boundary. Malformed expansion evidence fails closed. This keeps repository impact authority with Hashmarks or another repository-intelligence provider while Agent Economics retains the policy decision to acquire more proof.


## Provider-bound proof

A risk boundary cannot be satisfied by agent-authored `fresh=true` or `direct=true` booleans. A proof must reference provider evidence, carry provider-owned freshness, and cite a relationship evidence identity whose classification is direct. Missing or malformed proof provenance fails closed.

This keeps the stop decision in Agent Economics while preventing Agent Economics from manufacturing the repository facts that justify the decision.
