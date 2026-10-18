# Native host trial projection (checkpoint after PR #241)

This checkpoint removes hand-authored **paired campaign** and **host capture
manifest** inputs. It consumes a frozen export from an independently trusted
host-side collector, projects canonical task/arm pairs and capture entries, then
invokes the existing byte-level host capture audit and paired outcome gate.

It **does not extract live native OpenCode/Codex trace formats**, create evidence,
grade source patches, or attest host delivery. A provider-specific adapter
must first extract the authoritative observations with independent host
provenance checks. A self-reported subject result is inadmissible.

## Required input

`agentscookbook.native-host-trials.v1`: `{"schema": "...", "trials": [...]}`.

Every trial must supply:
- `task_id`, `arm` (`baseline` or `treatment`)
- `native_agent` (`opencode-native` or `codex-native`)
- `matched_authority_sha256`: the same externally frozen experiment
  authority identity across both arms, without encoding the intentionally
  varied treatment
- `first_choice_owner_correct`, `patch_test_correct`: host/oracle-graded
  booleans (not the agent's self-assessment)
- `exploratory_native_source_reads`: nonnegative observed count
- `host_attested: true`, `host_trial_id`, `delivery_id`,
  `payload_sha256`, and `payload_path` relative to the capture root

Exactly one baseline and one treatment per task are required. All trial
and delivery IDs must be unique. A mixed-agent or mixed-authority pair is
rejected, as are unmatched tasks. Capture paths and actual bytes are verified
downstream. No data is imputed or classified from unrecognized native events.

Run from the repository root:

```sh
uv run --no-project python -m unittest discover -s tests -p 'test_native_host_trial_projection.py' -v
uv run --no-project python benchmarks/native_host_trial_projection.py \
  trusted-native-host-export.json \
  --capture-root frozen-host-captures \
  --output-dir paired-audit-result
```

The output directory must be new. The command writes:
`paired-campaign.json`, `host-capture-manifest.json`, and
`audited-decision.json`. Exit 0 means all point targets pass; exit 1 means
the admitted campaign misses a target; exit 2 means input/receipt admission
failed. A PASS does **not** prove statistical equivalence or causal benefit.
The trust level still states `EXTERNAL_VERIFICATION_REQUIRED`: callers must
verify the host collector and frozen experimental authority upstream.

## Next integration

Implement explicit native-host adapters against actual observed OpenCode and
Codex trace formats, preserving captured byte offsets, canonical host receipt
identities, independent oracle grade and native read classifications. Only
after that boundary is supported should the projection become an automatic
benchmark CLI command or a required empirical campaign gate.
