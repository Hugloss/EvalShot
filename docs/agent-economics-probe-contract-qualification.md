# P4 — Common Agent Economics Probe Contract Qualification

Phase 4 establishes the reusable JSON envelope for every Agent Economics Probe.

## Contract authority

The current contract is:

- schema: `agent-economics-probe`
- schema version: `1.0`
- reference producer: `refactor-focus` `0.4.0`

The common top-level sections are `schema`, `tool`, `generated_at`, `repository`, `configuration`, `evidence`, `derived`, `interpretation`, `uncertainty`, `warnings`, `candidates`, `required_next_evidence`, `deferred_evidence`, `verification_suggestions`, and `economics`.

Candidate records separately expose measured `facts`, `evidence`, `derived` state, `interpretation`, `recommendations`, `uncertainty`, `required_next_evidence`, and `verification_suggestions`.

## Qualification

Run:

```bash
python -m scripts.agent_economics.probe_contract_qualification
```

The P4 gate proves that:

- the envelope validates against the common v1 contract;
- repository identity is independent of checkout location;
- repository identity changes when analyzed repository bytes change;
- configuration identity is independent of artifact destination;
- configuration identity changes when semantic configuration changes;
- optional ownership-hint bytes participate in configuration identity;
- portable paths do not leak the temporary/absolute repository root;
- recommendations and interpretation cannot leak into candidate facts;
- bounded-out candidates are represented as deferred evidence;
- uncertain candidates identify required next evidence;
- confirmed test ownership produces focused verification suggestions;
- P1 evidence authority, P2 parse-once economics, and P3 pytest ownership remain green after the contract migration.

A schema that serializes successfully but violates any of those boundaries is not qualified.
