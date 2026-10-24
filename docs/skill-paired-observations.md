# Paired skill observations (v1)

This **derived report** reads existing skill-evaluator `result.json` receipts. It neither executes an agent nor establishes a new scoring authority.

```sh
node scripts/skill-paired-report.js --artifacts-dir .runs/blind-pilot --output .runs/skill-comparison.json
```

Each row is paired by case ID, runtime and reported model. The report requires baseline and skill arms to agree on case kind, expected verdict, and blinded status. It reports incomplete and incompatible pairs separately, preserving the underlying evidence and classification status. No missing receipt is silently classified as a failure of the agent.

**Important boundaries:** The current skill evaluator does not bind all experimental conditions (including exact model/provider configuration, repository digest, skill commit, actual context and sampling seed) into these small receipts. Therefore even matching pairs are `DESCRIPTIVE_ONLY`: they are *not* demonstrated causal skill improvements or statistical effect sizes. The reporter cannot grade semantic correctness, infer missing evidence, or authorize repair effectiveness.

Before asserting skill effectiveness, extend frozen experiment authority and independently qualified comparability over the full set of inputs, observed environment and replicates. Existing repository-intelligence benchmark admission, score and report infrastructure owns those stronger claims. Do not merge that authority into this script.

## Observed authority comparison

When future execution receipts provide `experimentalAuthority`, the derived report compares the following *recorded* fields for both arms: `repository_sha256`, `task_sha256`, `skill_revision`, `runtime_config_sha256`, `oracle_revision`, and `replicate_id`. Any unequal supplied binding causes `NOT_COMPARABLE`. A missing field stays `UNKNOWN`; it is **never** inferred from a path, current checkout, or report time.

Matching strings alone are not independent proof that a source, oracle, and runtime were correctly observed and frozen. The comparison remains `DESCRIPTIVE_ONLY` even if every binding agrees. The execution/admission owner must establish and persist trustworthy identities before effectiveness claims can advance; this derived report cannot mint them.
