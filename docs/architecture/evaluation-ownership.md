# Evaluation ownership and coverage (v1)

## Canonical authorities

| Fact or behavior | Canonical owner | Consumer |
| --- | --- | --- |
| Review skill methodology, routing guidance, agents and flows | agentsCookbook | EvalShot explicitly selects an immutable checkout |
| Model limits, tool decisions, reasoning, compaction and code edits | Selected agent runtime/provider | EvalShot observes the actual execution |
| Frozen task, mutation, condition and oracle definition | EvalShot suite authority | Trial admission and scorer |
| Process lifetime, bounded execution and cleanup in EvalShot | `scripts.agent_economics.bounded_process` | All benchmark consumers |
| Repository-intelligence evidence | Selected subject (such as Hashmarks) | Agent and experiment |
| Trial admission and execution identity | `benchmarks.harness.admission` | Receipts and reporting |
| Skill source selection | `scripts/cookbook-source.js` | Skill and flow evaluators |
| Skill evidence localization | `scripts/skill-evidence-contract.js` | Skill evaluation |
| Semantic oracle and verdict | Frozen EvalShot task oracle | Reports and qualification |
| Experiment scoring | Existing suite-specific scorer | Cross-experiment analysis |
| Agentic workflow execution outside evaluation | External runtime, optionally OH-Goon | EvalShot evaluates outcomes |

**Data flow:** explicitly selected canonical cookbook revision → frozen EvalShot task → selected agent host → raw execution evidence → independent oracle → qualified result → derived report.

## Invariants

1. One owner resolves a fact; consumers transport, verify or report without making a competing decision.
2. A skill is **an experiment input**, never the authority that grades its own outcome. No skill descriptions or reviewer procedures are copied into EvalShot.
3. The selected cookbook checkout is explicit (`--cookbook-root`); no implicit sibling discovery or floating runtime source. Reports record its actual Git HEAD and dirty state; a dirty source is not reproducibly qualified.
4. EvalShot never modifies the selected model's context window, compaction or provider limits.
5. Existing frozen suites, receipts, scoring semantics and migration provenance are immutable. New interpretations require new versioned experiments and oracles.
6. `SCENARIO_ONLY`, `LOCATED_NOT_ADJUDICATED` and semantic-oracle qualification are different evidence claims. A matching citation is not independently adjudicated proof.
7. `UNKNOWN`, `INCOMPLETE`, `INVALID` and `CONTAMINATED` are never silently converted into success, failure, zero, or absence.
8. A derived coverage report may inventory cases but is **not** a second skill catalog, scorer, admission path, or qualification gate.
9. Every reproducible implementation defect fixed gets a focused regression.

## Coverage inventory

`node scripts/skill-coverage.js --cookbook-root PATH --output reports/skill-coverage.json`

This is a model-free **derived snapshot**. It loads canonical specialist names from the selected cookbook catalog with the same parser as the existing evaluator, and checks the existing EvalShot corpus. It makes no model calls and does not assign behavioral quality grades. Its counters distinguish scenario cases from repository fixtures, positive/control/confusion cases and their intersections. Unrepresented dimensions are recorded as `not_measured`, not zero capability.

The generated output belongs in ignored local reports/artifacts, not a second committed catalog. Evaluation effectiveness and repair outcomes require independently qualified, model-backed experiments. Source revision is observed from the checkout, not from a duplicated pin in the report generator.

## Change-control check

Before introducing a new module, demonstrate why the existing skill runner, source resolver, evidence qualifier, admission authority, bounded process runner, suite scorer, or reporting owner cannot provide the needed function. Prefer extending that owner. Keep source-selection, evidence-qualification and actual execution responsibilities separate.
