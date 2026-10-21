"""Passthrough credentials must not replace independent trial state roots."""

from __future__ import annotations

import unittest

from benchmarks.harness.runtime_authority import (
    PRIVATE_STATE_ENV_KEYS,
    transport_native_codex_environment,
    transport_runtime_authority,
)


RESERVED_ROOTS = frozenset({
    "HOME", "TMPDIR", "TMP", "TEMP", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
    "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "CODEX_HOME", "CODEX_SQLITE_HOME",
})
ERROR = "benchmark passthrough cannot replace trial state roots"


class RuntimeAuthorityIsolationTests(unittest.TestCase):
    def test_native_client_inputs_are_explicit_without_transporting_session_or_state(self):
        source = {
            "CODEX_INTERNAL_ORIGINATOR_OVERRIDE": "operator-selected-client",
            "CODEX_MCP_NODE_PATH": "/admitted/runtime/node",
            "CODEX_CLI_PATH": "",
            "CODEX_THREAD_ID": "source-conversation",
            "CODEX_SESSION_ID": "source-session",
            "HOME": "/shared/home",
            "UNRELATED_CREDENTIAL": "must-not-transport",
        }
        destination = {"HOME": "/private/home"}
        transport_native_codex_environment(source, destination)
        self.assertEqual(destination, {
            "HOME": "/private/home",
            "CODEX_INTERNAL_ORIGINATOR_OVERRIDE": "operator-selected-client",
            "CODEX_MCP_NODE_PATH": "/admitted/runtime/node", "CODEX_CLI_PATH": "",
        })

    def private_environment(self):
        return {**{name: f"/trial/private/{name.lower()}" for name in RESERVED_ROOTS},
                "EXISTING_VALUE": "keep-original"}

    def test_all_native_mutable_roots_are_reserved(self):
        self.assertEqual(set(PRIVATE_STATE_ENV_KEYS), RESERVED_ROOTS)

    def test_reserved_names_are_rejected_even_when_source_value_is_missing_or_empty(self):
        for name in sorted(RESERVED_ROOTS):
            for present in (False, True):
                with self.subTest(name=name, value="empty" if present else "missing"):
                    source = {"BENCHMARK_PASSTHROUGH_ENV_KEYS": name}
                    if present:
                        source[name] = ""
                    destination = self.private_environment()
                    original = dict(destination)
                    with self.assertRaisesRegex(ValueError, ERROR):
                        transport_runtime_authority(source, destination)
                    self.assertEqual(destination, original)

    def test_rejection_is_atomic_including_other_runtime_authority_and_credentials(self):
        for name in sorted(RESERVED_ROOTS):
            with self.subTest(name=name):
                secret = "provider-credential-must-not-appear"
                shared = "/host/private-must-not-appear/shared-state"
                source = {
                    "HASHMARKS_BENCH_SOURCE": "/host/subject-source",
                    "BENCHMARK_OPENCODE_AGENT": "operator-selected-agent",
                    "BENCHMARK_PASSTHROUGH_ENV_KEYS": f" CUSTOM_PROVIDER_API_KEY , {name} ",
                    "CUSTOM_PROVIDER_API_KEY": secret,
                    name: shared,
                }
                original_source = dict(source)
                destination = self.private_environment()
                original_destination = dict(destination)
                with self.assertRaisesRegex(ValueError, ERROR) as rejected:
                    transport_runtime_authority(source, destination)
                self.assertEqual(destination, original_destination)
                self.assertEqual(source, original_source)
                self.assertNotIn(secret, str(rejected.exception))
                self.assertNotIn(shared, str(rejected.exception))
                self.assertNotIn(source["HASHMARKS_BENCH_SOURCE"], str(rejected.exception))

    def test_provider_credentials_and_CA_inputs_still_transport_without_changing_roots(self):
        allowed = {
            "CUSTOM_PROVIDER_API_KEY": "opaque-provider-credential",
            "CODEX_ACCESS_TOKEN": "opaque-native-auth-input",
            "CODEX_CA_CERTIFICATE": "/operator/certificates/company.pem",
            "SSL_CERT_FILE": "/operator/certificates/fallback.pem",
        }
        source = {
            **allowed,
            "BENCHMARK_PASSTHROUGH_ENV_KEYS": " , ".join(allowed),
            "HASHMARKS_BENCH_SOURCE": "/operator/subject-source",
            "BENCHMARK_OPENCODE_AGENT": "build",
            "UNDECLARED_INPUT": "must-not-be-copied",
        }
        destination = self.private_environment()
        roots_before = {name: destination[name] for name in RESERVED_ROOTS}
        transport_runtime_authority(source, destination)
        self.assertEqual({name: destination[name] for name in RESERVED_ROOTS}, roots_before)
        self.assertEqual({name: destination[name] for name in allowed}, allowed)
        self.assertEqual(destination["HASHMARKS_BENCH_SOURCE"], source["HASHMARKS_BENCH_SOURCE"])
        self.assertEqual(destination["BENCHMARK_OPENCODE_AGENT"], "build")
        self.assertEqual(destination["EXISTING_VALUE"], "keep-original")
        self.assertNotIn("UNDECLARED_INPUT", destination)


if __name__ == "__main__":
    unittest.main()
