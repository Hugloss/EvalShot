"""E245: explicit native-harness model-input capture capability register.

Never promote provider-managed MCP routing, ATIF, or an HTTP client sitting
beside a native agent into proof of the *native agent's* model input. This
is an admission register, not a configurable self-declared capability flag.
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Mapping

from .intervention_audit import validate_design

SCHEMA = "agentscookbook.native-provider-boundary-readiness.v1"

# These are bounded static guarantees of the shipped instrumentation, not
# vendor capability speculation or ratings. A future adapter must add focused
# native-host integration tests before this register can change.
PROFILES: dict[str, tuple[str, str]] = {
    "codex": ("OPAQUE_NATIVE_PROVIDER_BOUNDARY", "native-codex-request-not-intercepted"),
    "codex-native": ("OPAQUE_NATIVE_PROVIDER_BOUNDARY", "native-codex-request-not-intercepted"),
    "opencode": ("OPAQUE_NATIVE_PROVIDER_BOUNDARY", "native-opencode-request-not-intercepted"),
    "opencode-native": ("OPAQUE_NATIVE_PROVIDER_BOUNDARY", "native-opencode-request-not-intercepted"),
    "claude-code": ("OPAQUE_NATIVE_PROVIDER_BOUNDARY", "native-claude-code-request-not-intercepted"),
    "openai-responses": ("PROVIDER_MANAGED_MCP", "responses-mcp-output-is-not-model-input-packet"),
    "trusted-http-chat": ("FIRST_PARTY_TRANSPORT_ONLY", "not-a-native-agent-harness"),
}


def describe_boundary(harness: str) -> dict[str, Any]:
    if not isinstance(harness, str) or not 0 < len(harness) <= 256:
        raise ValueError("invalid-host-harness")
    state, reason = PROFILES.get(
        harness, ("UNSUPPORTED_HOST", "no-reviewed-native-host-integration"),
    )
    return {
        "harness": harness,
        "integration_state": state,
        "blocker": reason,
        "native_model_input_attested": False,
        "native_treatment_enforcement_attested": False,
        "native_provider_execution_qualified": False,
        "may_claim_native_hashmarks_uplift": False,
    }


def audit_native_readiness(design: Mapping[str, Any]) -> dict[str, Any]:
    frozen = validate_design(dict(design))
    rows = [describe_boundary(name) for name in frozen["harnesses"]]
    return {
        "schema": SCHEMA,
        "campaign_id": frozen["campaign_id"],
        "harnesses": rows,
        "native_model_input_population_qualified": False,
        "provider_managed_mcp_is_model_input_proof": False,
        "trusted_http_host_is_native_harness": False,
        "qualification_policy": (
            "An independently keyed host assertion about an arbitrary request "
            "does not establish that an opaque native harness emitted it. "
            "Require a version-bound, directly integrated host capture API "
            "and native execution receipts before asserting native uplift."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", required=True)
    parser.add_argument("--require-native-capture", action="store_true")
    args = parser.parse_args()
    try:
        from pathlib import Path

        path = Path(args.design)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 65_536:
            raise ValueError("unsafe-native-capability-design")
        design = json.loads(path.read_text(encoding="utf-8"))
        result = audit_native_readiness(design)
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"schema": SCHEMA, "native_model_input_population_qualified": False,
                          "reason": str(exc)}, sort_keys=True, indent=2))
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 2 if args.require_native_capture and not result["native_model_input_population_qualified"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
