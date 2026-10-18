# Benchmark adapters

Adapters isolate product/model-specific mechanics from experiment definitions.

Roles are independent: **subject** (capability under evaluation), **agent** (execution participant), and **oracle** (independent grading authority). A subject must never be its own oracle. The `none` subject is a first-class control.

Adapters expose concrete identity/version/provenance and preserve raw observations. Startup failure, unavailable dependency, timeout, or failed oracle healthcheck remains infrastructure evidence and is never rewritten as a product FAIL.
