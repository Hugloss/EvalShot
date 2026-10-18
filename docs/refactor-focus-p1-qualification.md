# Refactor Focus — P1 Qualification

Status: **PASS**

P1 establishes an explicit evidence-authority boundary and an adversarial qualification corpus.

Current corpus result in this environment:

- TRUE_RELEVANT: 4
- FALSE_RELEVANT: 0
- MISSED_RELEVANT: 0
- UNKNOWN: 6
- TRUE_NEGATIVE: 1
- confirmable-relation precision: 1.0
- confirmable-relation recall: 1.0
- oversized candidates: 11
- bounded top-3 candidate reduction: 72.7%
- bounded evidence-file reduction in the synthetic corpus: 76.0%

Important closures found by P1:

1. Mirrored paths, same-name tests, and bounded transitive-owner matches are supporting evidence, not confirmed test authority.
2. Exact imports and explicit helper-loader paths remain confirmed evidence.
3. Stronger evidence overrides weaker evidence when the same test is discovered multiple ways.
4. Path-feature matching no longer counts the `.py` suffix as a feature token.
5. Custom source and test import package names are exercised by the corpus.
6. Non-confirmed evidence cannot set corresponding-test ownership, confirmed test sizes, or confirmed-test actions.

Runtime and byte figures are intentionally not frozen in this document because they vary by environment. The qualification JSON emitted by `refactor_focus_qualification` records them per run. P1 reports lower-bound read/parse economics; exact invocation accounting was subsequently closed in Phase 2.
