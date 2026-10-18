# Held-out run 000014: internal review

This run is local diagnostic evidence for improving the benchmark and Hashmarks. The source of truth is `.benchmark-runs/heldout-v1/runs/000014`; its receipts and derived reports remain immutable. These findings are not a qualified product comparison.

## What the run establishes

- All 108 expected trials have durable receipts: 102 PASS, 3 semantic FAIL, and 3 INCOMPLETE. A durable receipt does not mean the agent produced a gradeable outcome. The campaign is **NOT_QUALIFIED** because of the incomplete outcomes and failed Enola exposure qualification. [Status](../.benchmark-runs/heldout-v1/runs/000014/reports/status.json) · [Decision evidence](../.benchmark-runs/heldout-v1/runs/000014/reports/decision-evidence.json)
- The three semantic FAIL outcomes are `locate-prefix-path-enumerator`: Enola replicates 6201 and 6202 and bare replicate 6201. They are wrong locations, not runtime failures. Across the 98 valid localization outcomes, strict bare-JSON format compliance is 0/98. Semantic location scoring and instruction-format scoring must stay separate. [Report](../.benchmark-runs/heldout-v1/runs/000014/reports/report.json)
- Enola was configured and invocation was observable in 36/36 trials, but no Enola tool was invoked. This run cannot establish an Enola treatment effect. Hashmarks was invoked in 25/36 trials; its contracted `task_evidence` operation returned a successful nonempty result in 23. The 22 valid contracted-treatment pairs show one gain, 21 preserved outcomes, and no regression. These are within-agent paired observations from an unqualified campaign, not an overall ranking. [Report](../.benchmark-runs/heldout-v1/runs/000014/reports/report.json)

## Operational failures

The [trace diagnostics](../.benchmark-runs/heldout-v1/runs/000014/reports/trace-diagnostics.json) classify one context overflow and two permission denials. All three remain INCOMPLETE and must be rerun under fresh authority rather than regraded into agent failures.

| Trial | Observed cause | Receipt |
| --- | --- | --- |
| `locate-stale-index-removal` / Enola / 6203 | Gemma rejected a request with at least 66,305 input tokens plus 32,000 requested output tokens against a 98,304-token window. The benchmark had disabled auto-compaction. | [result](../.benchmark-runs/heldout-v1/runs/000014/results/d5a28bdb3f569f6442464ad92be18b5ff8bfeb4f909930ae7cff5190a7bcf400/result.json) |
| `repair-partial-receipt-regression` / Hashmarks / 6201 | After edits and verification, OpenCode rejected cleanup of `/tmp/opencode/...`; no final answer followed. | [result](../.benchmark-runs/heldout-v1/runs/000014/results/ca6af3fd43e70c21a4a5844dbca4be1ffe840a02325adb79c187ab61558132ee/result.json) |
| `repair-partial-receipt-regression` / Enola / 6202 | After edits and verification, OpenCode rejected cleanup spanning trial scratch and `/tmp/receipt_bug_repro`; no final answer followed. | [result](../.benchmark-runs/heldout-v1/runs/000014/results/e32a70173c0c3fd32c0e0ef7fb50142539b3da6e12dd17cc76f84b777e7a9ba2/result.json) |

The native OpenCode `liteLLM/gemma4` declaration available during review reported `limit.context=81920` and `limit.output=81920`, inconsistent with the provider rejection above. Correct that native declaration and fail admission when model limits leave no prompt budget. A fresh benchmark execution policy enables compaction and runs OpenCode in a private mount namespace where `/tmp` is bound to the isolated trial `$TMPDIR`; run 000014 stays under its original policy.

## Hashmarks investigation leads

- In 23 observed `task_evidence` results, 22 reported ambiguous ownership and 22 reported canonical results omitted from the returned presentation. These are packet characteristics, not proof that the underlying search was truncated or that ownership is wrong.
- The frozen target for `locate-refresh-outcome` was absent from returned candidates in one of 20 evaluable calls. Inspect that call's retrieval and ranking path before changing the product.
- Retrieval truncation was not reported in all 23 packets. Make the completeness signal explicit before treating absence from returned candidates as absence from the searched repository.
- Native navigation was lower by a mean of 3.92 calls across 25 paired trials with observed Hashmarks invocation; this is descriptive behavior, not a correctness score or causal efficiency claim. [Trace diagnostics](../.benchmark-runs/heldout-v1/runs/000014/reports/trace-diagnostics.json)

## Fresh evidence under the repaired runtime

The [focused v6 run](../.benchmark-runs/heldout-v1-focus-v6-implementation/runs/000001/reports/status.json) covered the two original blocker tasks across bare, Hashmarks, and Enola: 18/18 PASS, zero INCOMPLETE. Enola still had 0/6 observed invocations, so that run remained unqualified for Enola exposure. A stopped v6 full run then found a different runtime edge case: OpenCode compacted after a context overflow and exported a valid final answer, but its CLI kept the earlier nonzero exit. The v7 runtime accepts that result only when the raw event stream proves the overflow, compaction continuation, and later stopped assistant turn with the same final text. Its original CLI status remains in the receipt. That recovery check is validated against the saved v6 receipt; it was not exercised by the fresh v7 full run, whose 72 OpenCode CLI exits were all zero.

The [v7 full status](../.benchmark-runs/heldout-v1-hashmarks-opencode-v7-implementation/runs/000001/reports/status.json) is **QUALIFIED** for the bare-plus-Hashmarks selection: 72/72 receipts, 71 PASS, one semantic FAIL, zero INCOMPLETE, zero authority transitions. The sole FAIL was a bare `locate-prefix-path-enumerator` location mismatch. Across 36 paired trials, Hashmarks had one gain, 35 preserved outcomes, and no regression. Its exact `task_evidence` operation returned a successful nonempty result in 28/36 assisted trials; eight assisted trials did not invoke the subject. The one gain belongs to the observed contracted-treatment group. These are within-agent paired observations; the report does not permit an overall winner claim. [Report](../.benchmark-runs/heldout-v1-hashmarks-opencode-v7-implementation/runs/000001/reports/report.json) · [Score](../.benchmark-runs/heldout-v1-hashmarks-opencode-v7-implementation/runs/000001/reports/score.json)

Keep semantic location correctness separate from output formatting. The 33 location trials per arm were semantically correct in 33/33 Hashmarks and 32/33 bare cases, while strict bare-JSON format compliance was 0/33 in both arms. In the 28 observed Hashmarks calls, all packets reported ambiguous ownership; 26 reported canonical results omitted from presentation, and none reported retrieval completeness. The frozen target was absent from returned candidates in three of 25 evaluable calls, with three other calls unknown. These are concrete retrieval and evidence-shape investigation leads, not proof that the underlying repository search missed the target. [Trace diagnostics](../.benchmark-runs/heldout-v1-hashmarks-opencode-v7-implementation/runs/000001/reports/trace-diagnostics.json)

Enola routing remains separate work. The zero-incomplete v6 focus establishes that the original operational blockers were addressed for those tasks; its 0/6 natural Enola invocations still cannot support an Enola treatment claim. A native Enola integration experiment would need a declared treatment and fresh authority. Do not combine either follow-up run with run 000014.
