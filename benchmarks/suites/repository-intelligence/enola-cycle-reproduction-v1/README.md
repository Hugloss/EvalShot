# Adapted Enola cycle task

This is an adapted TypeScript recent-orders task inspired by [Enola's published benchmark example](https://github.com/enola-labs/enola/blob/main/docs/BENCHMARKS.md). It measures whether a coding agent finishes the requested behavior and whether its edit introduces a module dependency cycle. It is not a reproduction of Enola's published Claude/hook result: this suite uses native Codex and OpenCode, MCP-only assistance, a different pinned repository, and an independent local oracle.

The source repository is pinned to commit `0841a8822f417b8fd03af61c03779df8f1cdc941`, before benchmark answers were checked in. A frozen mutation adds `domain.ts`, `store.ts`, and `api.ts` under `benchmarks/fixtures/recent-orders`. The store imports the domain's `Order` type, so a direct domain-to-store import closes a module cycle, matching the published task's trap. The agent implements `recentOrders(customerId)` and `getRecentOrders`. The oracle independently checks Alice/Bob/unknown-customer ordering and walks TypeScript imports, including type-only imports. Exit codes 0, 10, 11, and 12 encode the two independent outcomes; other exits invalidate grading.

Run `make benchmark-check BENCHMARK=cycle` before `make benchmark BENCHMARK=cycle` from Hashmarks for three trials in each of six native agent/subject conditions. `make benchmark-report BENCHMARK=cycle` gives generic receipt statistics and separate functional/cycle counts for the latest run. The direct score command for a printed run ID is:

```sh
cd ../EvalShot
PYTHONPATH=. uv run --no-project python benchmarks/suites/repository-intelligence/enola-cycle-reproduction-v1/score.py ../Hashmarks/.hashmarks/benchmarks/native/enola-cycle-reproduction-v1/cycle/runs/RUN_ID/results
```

The score script requires one comparable receipt for every selected frozen definition, unless `--allow-incomplete` is explicit. It rejects duplicates, verifies every receipt bundle, and reads the sealed oracle event. `--agent codex-native` or `--agent opencode-native` scores one native CLI. Incomplete and invalid trials remain outside the functional/cycle denominator. Compare subject arms only within the same native agent identity and configuration.
