# Configuration-preservation implementation scope

The operator selects native runtime configuration. EvalShot must not silently
retune inference. Role permissions, chosen benchmark subjects, fixture/workspace
selection and experiment-specific execution bounds remain separate responsibilities.

## Corrected launch paths

`run-skill-benchmarks.js` no longer puts a temperature or model default in generated
OpenCode agent Markdown. With no explicit model selection it omits `--model` too.
An explicit `--model` or `SKILL_EVAL_MODEL` selection is forwarded through the
runtime CLI, matching the existing Pi path. Read-only permissions and the evaluator
prompt/output contract remain intact.

The native OpenCode adapter no longer sets `OPENCODE_DISABLE_PRUNE=1` by default.
The host chooses pruning. Explicit native environment selections still travel
through the existing `BENCHMARK_PASSTHROUGH_ENV_KEYS` boundary; this change does
not enable wholesale inheritance of ambient environment or credentials.
Automatic-update, sharing, selected-subject and scratch controls are unchanged.

## Behavioral regressions

Run the existing benchmark test discovery or target the new tests:

```sh
uv run --no-project python -m unittest discover -s benchmarks/tests -p test_native_configuration_ownership.py -v
node --test scripts/test-evaluator-config-ownership.js
```

The JavaScript matrix launches the actual skill evaluator with a fixture runtime
and inspects argv, generated role definition, native config bytes and selected
environment values. It covers absent settings, HOME/XDG configuration, custom file
and directory selectors, inline false/zero settings, explicit model selection,
Pi settings, and cleanup after runtime failure. It does not simulate or certify
the native runtime's configuration precedence.

The Python regression calls the existing native environment builder and bounded
process path with an instrumented child. Missing pruning remains missing; explicit
`0` and `1` remain unchanged. No real model or physical containment is claimed.
These tests are automatically included in the existing benchmark CI test discovery.

## Current measurement corrections

Native OpenCode inspection preserves the complete model ID after the provider
separator, including nested IDs. Native Codex/OpenCode token measurements preserve
missing or invalid counts as null; explicit zero remains zero. A partial OpenCode
token sum is not a complete total. Reports expose unknown-observation counts and
an observed subtotal; their total remains null when any measurement is unknown.
Paired deltas also remain null when either measurement is unknown. Historical
receipts are not rewritten.

## Remaining work from the ownership review

This bundle does not claim complete configuration neutrality across every launch
path. In particular:

- The legacy flow evaluator and skill evaluator still handle isolated HOME/config
  sources differently. Unify transport only after validating global, project,
  custom-directory, relative-path, credentials and session-state behavior. Do not
  introduce an evaluator-owned configuration precedence algorithm.
- Native Codex preparation still uses single-file model observation. Replace that
  assumption using a supported runtime-resolved observation, preserving explicit
  experiment identity requirements and distinguishing unavailable observation from
  invalid configuration.

The implementation changes which runtime behavior is evaluated. Start a new
campaign generation for these source revisions; preserve historical suites,
receipts and source pins instead of rewriting old evidence.
