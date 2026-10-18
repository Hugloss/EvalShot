# Refactor Focus P3 Qualification

Phase 3 closes bounded Python/pytest ownership recovery while preserving the probe rule that discovery hints are not ownership facts.

Run:

```bash
python -m scripts.agent_economics.refactor_focus_p3_qualification
```

## Qualification surface

The temporary corpus covers:

- explicit and autouse ancestor `conftest.py` fixtures;
- fixture-to-fixture dependency chains;
- literal `request.getfixturevalue("...")`;
- an unused fixture negative;
- direct and nested repository-local `pytest_plugins`;
- plugin traversal beyond the configured depth bound;
- a used plugin fixture and an unused plugin fixture negative;
- helper-to-helper imports and helper depth overflow;
- literal `importlib.import_module(...)` and `__import__(...)`;
- a runtime-computed dynamic module name negative;
- explicit versioned repository ownership hints;
- stale, absolute, and repository-escaping ownership hint paths.

The P3 runner also executes P2 so parse-once and prior evidence-authority behavior remain regression gates.

## Current closure criteria

- expected confirmed ownership relationships: **11**;
- confirmed ownership relationships observed: **11**;
- false authority: **0**;
- invalid hint cases fail closed: **3/3**;
- P2 qualification: **PASS**.

Every confirmed P3 match carries a portable provenance string using repository-relative paths. Imports inside unused plugin fixtures are not treated as plugin-wide ownership. Runtime-built dynamic module names are not guessed.

Probe economics keep Python analysis reads/parses separate from auxiliary ownership-hint input and publish total file/byte reads as well.
