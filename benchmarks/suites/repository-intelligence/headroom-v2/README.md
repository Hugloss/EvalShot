# Repository intelligence headroom diagnostic v2

This is a diagnostic paired benchmark. It has no release authority and does not
replace heldout-v1. It keeps the four behavioral-v4 cases, source revision,
mutations, expected claims, task prompts, and three replicate IDs from headroom-v1.
The oracle identity and scoring version changed because headroom-v1's command
oracle treated a complete JSON object inside a single `json` fence as an empty
answer. That turned answer formatting into false semantic failures. Run v1 receipts
remain historical evidence and are not rescored as v2 results.

The v2 oracle accepts one bare JSON object or one complete `json` fence for
semantic grading. It records `format_compliant` separately: only a bare object
meets the task prompt. Prose, multiple fences, malformed JSON, duplicate keys,
and non-object JSON are ungradeable. The v2 score counts those answers separately
and excludes them from bare semantic headroom. File edits are checked from the
workspace as before.

Four tasks × three conditions (bare, Hashmarks, Enola) × three replicates give
36 trials per native agent. Selection of one agent and bare plus Hashmarks gives
24 trials. Subject use is natural and is never required by the task prompt.

Copy `.env.example` to a private env file, set the exact clean Hashmarks checkout,
then run:

```bash
uv run --no-project python -m benchmarks check --env-file .env.headroom-v2
uv run --no-project python -m benchmarks prepare --new --env-file .env.headroom-v2 --agent opencode-native --subject none --subject hashmarks
uv run --no-project python -m benchmarks run --resume --run-id 000001 --env-file .env.headroom-v2 --agent opencode-native --subject none --subject hashmarks
```

The score separates semantic success, format compliance, subject adoption,
paired assistance, and cost. Inspect qualification before drawing product
conclusions. A configured subject that is never invoked supplies no treatment
evidence. Compare before and after campaigns only with the same suite, task and
replicate population, agent configuration, and measured host environment.
