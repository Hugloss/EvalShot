# Byte-verified paired host capture checkpoint

The first paired outcome gate consumes SHA-256 evidence identity labels.
This additional, **opt-in strict entrypoint** checks SHA-256 against actual
captured payload bytes and rejects wrong task/arm binding, missing, duplicate,
extra, tampered, symlinked and traversal payloads *before* scoring.

It does not assert the collector is trustworthy, that the bytes were truly
delivered to the model, or that the experiment is causally valid. Obtain
capture files and manifest from an independently trusted, host-observed
collection mechanism and validate its authority upstream. Neither this tool
nor the subject under test may mint host-attested receipts.

Input: existing `agentscookbook.paired-outcome.v1` campaign JSON plus
`agentscookbook.host-capture.v1` manifest:

```json
{
  "schema": "agentscookbook.host-capture.v1",
  "entries": [
    {
      "task_id": "heldout-task-001",
      "arm": "baseline",
      "host_trial_id": "trial-control-001",
      "delivery_id": "delivery-control-001",
      "payload_sha256": "<64 lowercase hex characters>",
      "payload_path": "capture-control-001.bin"
    }
  ]
}
```

Every matched task must have exactly one baseline and one treatment
manifest entry, with distinct raw payload file paths. The manifest must
contain no extra entries. Payload paths are resolved below the explicit
capture root; absolute paths, traversal, duplicates and final-file
symlinks are disallowed. Manifest task, arm, trial ID, delivery ID and
payload digest must match the paired campaign's delivery entry exactly.

Run with the repository-owned uv front door, never with a system Python:

```sh
uv run --no-project python -m unittest discover -s tests -p 'test_paired_host_capture_audit.py' -v
uv run --no-project python benchmarks/paired_host_capture_audit.py \
  frozen-paired-campaign.json \
  --capture-manifest host-capture-manifest.json \
  --capture-root frozen-host-captures \
  --output audited-decision.json
```

Exit codes: 0 = metrics qualify and byte audit passes; 1 = valid
campaign fails target(s); 2 = invalid/incomplete capture or campaign.
The decision reports `capture_root_trust: EXTERNAL_VERIFICATION_REQUIRED`
to explicitly forbid claiming authentication from byte matching alone.
Use the existing host-delivery, treatment-freeze and empirical campaign
qualification authorities to authenticate source provenance and justify
causal interpretation. This extension remains a consumer, not a new
competing host authority.
