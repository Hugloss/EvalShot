# Signed host submission admission for native trial evaluation

This checkpoint adds a strict admission path reusing the existing
`benchmarks.harness.host_input_attestation.verify_host_attestations` authority.
It verifies an independently MAC-signed, immutable ATIF trajectory binding
for **each** native host trial before the already-merged byte-level capture
audit and paired-outcome target gate runs.

Inputs:
- `agentscookbook.native-host-trials.v1` export (PR #245)
- `agentscookbook.native-host-signed-receipts.v1` mapping (example below)
- host-key file outside the evaluated workspace, mode owner-only (existing verifier)
- immutable trajectory/attestation files and captured response bytes

```json
{
  "schema": "agentscookbook.native-host-signed-receipts.v1",
  "campaign_id": "campaign-identity-from-frozen-host-authority",
  "records": [
    {
      "host_trial_id": "trial-001-control",
      "trajectory_path": "trial-001-control/trajectory.json",
      "attestation_path": "trial-001-control/attestation.json"
    }
  ]
}
```

Both arms of every pair must be covered without extras or duplicates.
The existing host verifier must report `VERIFIED`, `PROVEN`,
`SUBMITTED`, and full provider-submission packet coverage for each
trial. Paths must remain inside the explicit receipt root and must not
use symlink components. Host keys and receipts are **not** produced by
this tool, and subject-side text cannot substitute for signed host proof.

```sh
uv run --no-project python -m unittest discover -s tests -p 'test_signed_host_trial_admission.py' -v
uv run --no-project python benchmarks/signed_host_trial_admission.py \
  trusted-native-host-export.json \
  --receipt-map signed-receipt-map.json \
  --receipt-root frozen-receipts \
  --capture-root frozen-host-captures \
  --host-key-file /secure/host-receipt-key \
  --output signed-audited-decision.json
```

Exit codes: 0=admitted & all targets pass, 1=admitted but target missed,
2=fail-before-scoring. No signed host receipts or native source-trace
capture are manufactured here. This is a *limited join*: signed ATIF
packet bindings and separately captured payload bytes are both verified,
but **the exact equality between the signed model input packet and the
separate capture is not yet proven by this bridge**. The decision records
`payload_capture_equivalence_proven=false`, along with explicit false
provider-consumption/model-attention/causal-influence claims. Until a
host-signed response-byte binding is available, do not interpret this
result as fully verified model-visible delivery.

Next: use an existing authoritative host response-byte binding and
native trace source to prove capture equality, rather than joining by
self-declared IDs alone.
