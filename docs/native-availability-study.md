# Native Hashmarks availability measurement

The explicit study report is separate from historical subject-use qualification.
The latter requires at least one invocation and usable result in each subject
condition; it remains unchanged. An availability study must be able to measure
zero adoption after proving the admitted server catalog, native configuration,
complete supported MCP telemetry, and independent task outcome.

Run this read-only command against an existing frozen suite and native campaign:

```sh
uv run --no-project python -m benchmarks.harness.availability_study \
  --suite /path/to/frozen-suite \
  --results-root /path/to/run/results \
  --baseline-condition codex-bare \
  --available-condition codex-hashmarks
```

The schema is `evalshot.native-availability-study.v1`. The command reuses bundle,
campaign, scoring and runtime-comparability checks. It requires matching tasks,
replicate identities, native agent and declared context in the two selected
conditions. Codex must use `native_host=true`; forced OpenCode tool diagnostics
cannot enter this optional-availability comparison. No suite or campaign is
created, edited, resumed or executed by the command.

The primary comparison retains **every frozen assignment**, including
non-invokers, missing trials and unknown outcomes. Unknown task outcomes remain
unknown, never failures or successes. Incomplete arms have a null success rate
and explicit best/worst outcome bounds over the assigned denominator. Paired
mean differences require complete study measurements. Authority epoch taint
suppresses paired differences. This report is descriptive and performs no
significance test, model ranking or causal attribution.

The report reads Codex JSONL and OpenCode session exports through the existing
adapter parsers. A complete supported trace can establish zero observed MCP
invocations. An absent, malformed, unfinished or unsupported trace, or opaque
OpenCode `execute` metadata, cannot establish zero. A positive direct invocation
survives partial telemetry. The final native answer is joined to the answer in
the independently graded receipt; a mismatch blocks study qualification.
These scopes do not establish exhaustive shell or nested-agent activity.

The admitted MCP **server catalog** is not the catalog submitted to a model.
Likewise, a successful nonempty native tool return is not submitted-request
inclusion, attention or comprehension. The successful-return subset is a
post-assignment diagnostic and never replaces the primary denominator. Current
native exports do not establish submitted-model-request inclusion, so the
model-delivered subset and host catalog inclusion remain **UNKNOWN**. No host
attestation is synthesized from exports or checksums.

Missing catalog proof, missing assignments, incomplete telemetry, unjoined
answers, unknown oracle outcomes and baseline Hashmarks MCP contamination are
reported per trial. A well-measured zero-invocation availability arm can qualify
even when the historical subject-use report correctly remains `FAIL`.

The assigned-trial invocation counts remain visible even when the experiment
is unqualified, but `voluntary_adoption.rate` is then `null` and
`rate_qualification` is `unqualified-operational-counts-only`. Only a
qualified complete study publishes a rate, including a legitimate measured
zero. This prevents an invalid or contaminated campaign from presenting
operational call counts as a qualified adoption estimate.

This prerequisite supplies measurement, not an effectiveness result. Freeze a
fresh study protocol, task/oracle provenance, cost budget and sampling analysis
before a live campaign; do not rewrite historical frozen suites to reuse their
identity for a different experiment. Host-request capture, inference/adoption
effects and power for held-out confirmation remain separate follow-up work.
