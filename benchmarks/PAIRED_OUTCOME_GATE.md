# Held-out paired outcome qualification (v1)

This is an **EvalShot-owned** downstream qualification gate, not a new
agent harness, repository index, evidence provider, or host-attestation generator.
Use independently collected and frozen paired trials from the same held-out task,
matching model, tool permissions, task wording, limits and oracle. One pair is
one task-level observation; repeated runs for a task must be preaggregated under
a documented frozen rule, not submitted as independent pairs.

## Targets

| Metric | Admission / calculation | Target |
| --- | --- | --- |
| First-choice owner accuracy | (treatment correct - baseline correct)/matched tasks, in percentage points | at least +10 pp |
| Exploratory native source reads | (baseline total - treatment total)/baseline total | at least 20% fewer, with correctness gate |
| Patch/test correctness | exact one-sided task-paired McNemar test on discordant correctness | no credible treatment regression at p < .05 |
| Model-visible delivery | two independent, unique host trial/delivery IDs and exact SHA-256 returned vs visible payload for each arm | 100% admitted |

A **PASS** means the point targets were met on the supplied admissible pairs and
the configured significance test did not detect regression. It does **not**
establish superiority, equivalent correctness, statistical power, or causality.
Treat small samples and zero-discordance cases as inconclusive with respect to
correctness equivalence. The empirical campaign decision remains the authority
for causal claims, contamination checks, pre-registration, and eligibility.

## Input shape

```json
{
  "schema": "agentscookbook.paired-outcome.v1",
  "pairs": [{
    "task_id": "heldout-task-001",
    "baseline": {
      "first_choice_owner_correct": false,
      "exploratory_native_source_reads": 5,
      "patch_test_correct": true,
      "delivery": {
        "host_attested": true,
        "host_trial_id": "host-trial-001-control",
        "delivery_id": "delivery-001-control",
        "payload_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "visible_payload_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      }
    },
    "treatment": {
      "first_choice_owner_correct": true,
      "exploratory_native_source_reads": 3,
      "patch_test_correct": true,
      "delivery": {
        "host_attested": true,
        "host_trial_id": "host-trial-001-treatment",
        "delivery_id": "delivery-001-treatment",
        "payload_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "visible_payload_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      }
    }
  }]
}
```

**Important:** SHA-256 fields are *host-attested trace fields* supplied by an
independent host collector. Matching user-supplied strings alone do not
cryptographically prove the host delivered the content. Before treating a
campaign as causal evidence, validate the host provenance, signature or trusted
capture path, response bytes and task/run binding outside this consumer. Never
manufacture a receipt to satisfy this input.

## Run locally

```sh
python3 -m unittest discover -s tests -p 'test_paired_outcome_gate.py' -v
python3 benchmarks/paired_outcome_gate.py frozen-paired-campaign.json --output decision.json
```

Exit codes: 0 = all targets pass; 1 = valid campaign misses a target;
2 = structurally inadmissible campaign. This code intentionally does not
silently drop, impute, or count invalid trials. Apply the project-wide
frozen-oracle, host-delivery and intervention audits before this gate.
