# Agent/runtime handoff: natural use of `task_evidence`

## Task for the agent/runtime team

Build a frozen natural-use diagnostic that combines unknown-path behavior
questions with measured bare-control headroom. Preserve the distinction
between MCP availability, the agent's tool choice, a successful nonempty
`task_evidence` result, and correctness of the final answer. Keep Hashmarks as
a repository observer; any change to agent tool choice belongs to the
agent/runtime owner.

## Frozen evidence to start from

- Heldout-v1 run `000015` completed 108/108 receipts. Hashmarks was invoked in
  23/36 assisted trials; Enola was never invoked, so the whole run did not
  qualify. On `locate-mcp-task-evidence`, one of three Hashmarks trials used
  `task_evidence`, while two used `find`. All three bare and assisted outcomes
  passed. See `.benchmark-runs/heldout-v1/runs/000015/reports/`.
- Headroom-v2 runs `000001` and `000002` used the same 24 definitions and
  observed 6 PASS / 18 gradeable semantic FAIL each. Bare controls failed in
  9/12 trials. Hashmarks use was 0/12 before repair and 1/12 after repair;
  the post-repair call was `change_impact`. Neither run exercised
  `task_evidence`. The first run was not qualified for subject exposure; the
  second qualified through that one call. All 48 answers were JSON fenced,
  so use `score.json.answer_contract` for the separate format measure. See
  `.benchmark-runs/headroom-v2-implementation/runs/{000001,000002}/reports/`.
- A fresh paired heldout follow-up on `locate-mcp-task-evidence` completed
  6/6 PASS. Its Hashmarks trials used `find` twice and no Hashmarks tool once.
  The exact `task_evidence` exposure contract was not met. See
  `.benchmark-runs/heldout-v1-task-evidence-post-v5/runs/000001/reports/`.
- A fresh paired heldout follow-up on the unknown-path
  `repair-partial-receipt-regression` task completed 6/6 PASS and qualified.
  All three assisted trials called `task_evidence` once with a successful
  nonempty result. In run `000015`, v4 packets emitted `next_read` paths into
  unrelated scripts in all three replicates; the agent read the actual receipt
  owner instead. In the v5 follow-up, those unsupported `next_read` values are
  absent, retrieval truncation is reported, and the agent again read the
  receipt owner. Both bare and assisted arms passed 3/3, so this task measures
  packet use and preservation, not a correctness gain. Mean agent duration was
  57.5 seconds bare and 101.3 seconds assisted in this small follow-up. See
  `.benchmark-runs/heldout-v1-unknown-path-post-v5/runs/000001/reports/`.
- Hashmarks commit `c9da51e` repaired the packet itself. On the pinned heldout
  source, the old v4 packet resolved the enclosing class and omitted the
  requested method; v5 preserves ambiguity, returns the method at rank 19,
  and reports incomplete, truncated canonical retrieval. The class name in
  `locate-mcp-task-evidence` is explicit, so `find` is a defensible choice
  under the current MCP routing contract.

The two follow-ups show a task-dependent route: the named-class question
selected `find`, while the unknown-path repair selected `task_evidence` 3/3.
They do not establish a general MCP exposure failure or an agent routing
defect. The remaining evidence gap is a task population with both natural
`task_evidence` use and bare semantic headroom.

## Investigation and experiment contract

1. Capture the MCP tool catalog and server instructions as delivered to the
   native agent. Verify that `task_evidence` is available with its declared
   description in each assisted trial; record any runtime shadowing or tool
   gating separately from the agent's choice.
2. Select a behavior question whose implementation path and exact symbol are
   unknown to the agent. Prefer a frozen heldout task with an independent
   oracle. If a new task is needed, create a new diagnostic experiment version
   and freeze its prompt, source revision, expected answer, replicate IDs,
   subject definitions, and scoring before collecting results. The task
   prompt should describe repository work without naming Hashmarks or a tool.
3. Run paired bare and Hashmarks conditions with the same native agent and
   replicate IDs. Preflight the exact selection and preserve immutable
   receipts. Report tool availability, exact `task_evidence` calls and usable
   results, subsequent native reads, semantic outcomes, answer format, and
   execution cost as separate observations.
4. Classify every non-use case from its trace. If the tool was available and
   the agent chose native search or `find`, report an agent routing decision.
   If the catalog or instructions were missing or changed, repair the owning
   runtime or product catalog and rerun the unchanged frozen definitions.

A complete, clean campaign with zero `task_evidence` calls is still a useful
negative routing observation. Do not count configured exposure or a call to
another Hashmarks operation as treatment by this packet. Report a correctness
or cost effect only for comparable trials with observed relevant treatment.
The Enola exposure failure is a separate campaign track.
