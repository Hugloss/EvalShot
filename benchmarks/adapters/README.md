# Benchmark adapters

Adapters isolate product/model-specific mechanics from experiment definitions.

Roles are independent: **subject** (capability under evaluation), **agent** (execution participant), and **oracle** (independent grading authority). A subject must never be its own oracle. The `none` subject is a first-class control.

Adapters expose concrete identity/version/provenance and preserve raw observations. Startup failure, unavailable dependency, timeout, or failed oracle healthcheck remains infrastructure evidence and is never rewritten as a product FAIL.

The `oh-goon` agent adapter delegates coding to Oh-Goon's public CLI and original
Run/Step owners. It accepts explicitly selected native uv runtimes, public
providers catalogs, and supply receipts; native authentication remains opaque.
Its integrated mandatory Hashmarks evidence is distinct from an additional
EvalShot MCP subject. The versioned
[release-pair diagnostic](../suites/repository-intelligence/oh-goon-hashmarks-release-pair-v1/README.md)
compares two released dependency versions without a bare arm.
