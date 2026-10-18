# Frozen pilot Git fixture

`cookbook-pilot.bundle` supplies the historical repository objects used by the
three pilot mutation and oracle regression tests in `test_pilot_execution.py`.
The tests locate the bundle relative to their own file, clone it locally, and
verify the frozen commit and tree through the existing repository materializer.
They need neither an agentsCookbook checkout nor network access.

Source: <https://github.com/Hugloss/agentsCookbook.git>.
The bundle contains the complete ancestry reachable from exactly these two
refs; it contains no later source work.

| Bundle ref | Commit | Tree |
| --- | --- | --- |
| `refs/heads/pilot-v1` | `0841a8822f417b8fd03af61c03779df8f1cdc941` | `65a32888329e308615647dda194f0e36c2afe2ac` |
| `refs/heads/agent-matrix-v2` | `6ce8b0d9230dd9bc5369ddf495ad9404766fbbaf` | `1e253d251f8e0a874aeca0b05358b36253a714cc` |

Checked-in size: 861,664 bytes.

Checked-in SHA-256:

```text
0d4c08fa54d3e77bb42fcb9720f2b806e8e53b586aeefbfd74d123c6ab2b57c3
```

## Regenerate

Run from the EvalShot repository root. Set `fixture_source` to a local
agentsCookbook clone containing both exact commits. Only this maintenance
operation needs that source clone; ordinary test runs use the committed bundle.

```bash
set -euo pipefail
fixture_source="/path/to/agentsCookbook"
fixture_output="$PWD/benchmarks/tests/fixtures/cookbook-pilot.bundle"
fixture_work="$(mktemp -d)"
trap 'rm -rf -- "$fixture_work"' EXIT

git init --bare --quiet "$fixture_work/repository.git"
git -C "$fixture_work/repository.git" fetch --no-tags "$fixture_source" \
  0841a8822f417b8fd03af61c03779df8f1cdc941:refs/heads/pilot-v1 \
  6ce8b0d9230dd9bc5369ddf495ad9404766fbbaf:refs/heads/agent-matrix-v2
git -C "$fixture_work/repository.git" bundle create "$fixture_output" \
  refs/heads/pilot-v1 refs/heads/agent-matrix-v2
git -C "$fixture_work/repository.git" bundle verify "$fixture_output"
git bundle list-heads "$fixture_output"
sha256sum "$fixture_output"
```

Keep the two commit/tree identities and frozen suite files unchanged.
Git versions or pack settings can produce different bundle bytes for the same
objects; update the recorded size and checksum if a verified regeneration does.
