# Context-profile ownership and qualification

EvalShot owns **experimental context-profile policy**: numerical model
ceilings, working targets, reserved headroom, tokenizer measurement and
observed adherence. Agents Cookbook owns only portable context-use discipline
inside reusable prompts and its host adapters.

No cookbook agent, skill, flow, installer or preflight requires one particular
token-window size. A profile is selected by the evaluation operator and must
describe the actual model/provider/runtime being evaluated.

## Retained local-model profile: 98,304 tokens

The earlier cookbook local-model profile is retained here as an **optional,
named evaluation baseline**, not an unconditional harness default:

| Profile dimension | Target |
| --- | ---: |
| Advertised model maximum | 98,304 tokens |
| Normal working target | 65,536 tokens or less |
| Workflow hard target | 73,728 tokens or less |
| Reserved headroom | 24,576 tokens |
 
The working and reserve targets are planning guidelines, not
tokenizer-independent exact values. The 73,728 + 24,576 split is consistent
with the 98,304-token maximum; prompt, tools and output must fit into the
actual effective context window of the installed runtime.

An evaluator may select a different profile. A smaller effective context
window requires smaller targets. Never relabel the old 98,304-token profile as
validated for a different installed model.

## Required evidence for a qualification claim

1. Identify the exact model, runtime/provider versions, tokenizer (when
   known), configured maximum and any lower host/provider effective limit.
2. Freeze the selected profile and the exact cookbook commit under test
   before scoring. Keep `--cookbook-root` separate from the target `--repo`.
3. Record observed token usage if exposed by the runtime, including tools,
   instructions, reviewer artifacts and completion headroom.
4. Detect server rejection, silent truncation, missing outputs, or compaction
   that invalidates the tested prompt/case. These are not silent PASS results.
5. Run behavioral, positive/control/confusion and full-flow tests applicable
   to the claimed specialist/runtime profile, preserving model-free checks
   separately from model-backed evidence.
6. When the runtime does not expose reliable token measurement, report usage
   as **unknown**. Byte/character caps and model-free preflight alone do not
   establish model-specific token qualification.

The prompt may ask for selective evidence, compact reviewer receipts and
headroom; none of those instructions asserts numerical evaluation success.
Each campaign retains its own experimental authority and provenance.

## Adjacent boundaries

- [agentsCookbook context protocol](https://github.com/Hugloss/agentsCookbook/blob/main/protocols/context-budget.md) — portable selection/disclosure discipline, not numeric scoring authority
- [Cookbook runtime check](https://github.com/Hugloss/agentsCookbook/blob/main/docs/local-runtime-qualification.md) — local adapter and full-review session conformance, not an evaluator implementation
- [EvalShot benchmark guide](../benchmarks/README.md) — frozen experiment, oracle, condition and outcome authority
- [EvalShot skill corpus](../evals/README.md) — behavioral discrimination and evaluated live prompt sources
