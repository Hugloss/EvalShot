"""E250: opt-in, version-pinned OpenCode run through captured provider.

The launcher keeps provider credentials outside the evaluated child, pins
the native executable, scopes one frozen assignment to one local port, and
rejects incomplete request/response enforcement. Native process origin is
NOT proved merely because a bearer token and environment overlay were used.

No host receipt is synthesized from the process exit code. A real completed
ATIF trace must be separately supplied to TrustedModelRequestCapture.finalize
and verified; native benchmark certification remains unavailable by default.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import time
import stat
import subprocess
from pathlib import Path
from typing import Any, Mapping

from .opencode_native_gateway import (
    NativeOpenCodeGateway, admit_opencode_binary, gateway_config,
    sha256_file, write_gateway_config,
)
from .trusted_treatments import (
    load_manifest, verify_manifest, select_treatment,
)
from .native_chat_transport_parity import inspect_selected_content
from .native_chat_pair_admission import admit_native_chat_pair
from .native_mcp_bridge_contract import freeze_bridge_contract, write_bridge_contract
from .opencode_native_export import acquire_native_export, native_export_preflight
from .native_netns_probe import probe_netns_kernel
from .native_mount_probe import probe_native_mounts, verify_worker_source_unchanged
from .native_bwrap_execution import (
    admit_netns_substrate, build_isolated_argv, verify_substrate_unchanged,
    admit_host_only_paths,
)
from .opencode_native_session import finalize_native_export
from .host_input_attestation import load_host_key
from .native_host_execve import admit_execve_tracer, run_host_execve_observed
from .native_netns_host_attestation import (
    sign_isolation_report, write_isolation_attestation,
)

SCHEMA = "agentscookbook.pinned-opencode-gateway-trial.v3"


def _make_private_dir(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError("trial-run-directory-already-exists")
    path.mkdir(mode=0o700, parents=False)
    if path.stat().st_mode & 0o077:
        raise ValueError("trial-directory-permissions-not-private")


def launch_native_trial(
    manifest: Mapping[str, Any], *, trial_id: str,
    opencode_executable: Path, expected_binary_sha256: str,
    expected_version: str, run_root: Path, workspace: Path,
    prompt: str, host_key_file: Path, host_identity: str,
    current: Mapping[str, Any], replaced: Mapping[str, Any] | None,
    upstream: str, approved_origin: str,
    upstream_api_key: str, catalog_sha256: str,
    oracle_sha256: str, workspace_sha256: str,
    timeout_seconds: int = 600,
    allow_unconfined_execution: bool = False,
    use_netns_isolation: bool = False,
    hashmarks_executable: Path | None = None,
    expected_hashmarks_sha256: str | None = None,
    allow_unpaired_native_diagnostic: bool = False,
    verified_native_mcp_bridge: bool = False,
    require_native_execve_observation: bool = False,
    execve_tracer_executable: Path | None = None,
    expected_execve_tracer_sha256: str | None = None,
) -> dict[str, Any]:
    frozen = verify_manifest(dict(manifest))
    cells = [x for x in frozen["assignments"] if x["trial_id"] == trial_id]
    if len(cells) != 1 or cells[0]["harness"] not in ("opencode", "opencode-native"):
        raise ValueError("unknown-or-non-opencode-native-trial")
    if (not isinstance(prompt, str) or not 0 < len(prompt) <= 16_384
            or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600):
        raise ValueError("invalid-native-trial-input")
    if (workspace.is_symlink() or not workspace.is_dir()
            or not (workspace / ".git").exists()):
        raise ValueError("untrusted-or-unbound-native-workspace")
    # This experimental standalone launch is NOT the existing bwrap-backed
    # benchmark runner. OS confinement and exclusive egress are not proven.
    # Require deliberate opt-in rather than silently executing an agent with
    # access to the user's host filesystem/network.
    if verified_native_mcp_bridge and not use_netns_isolation:
        raise ValueError("verified-native-mcp-bridge-requires-netns-isolation")
    if allow_unconfined_execution and use_netns_isolation:
        raise ValueError("conflicting-native-execution-backends")
    if not allow_unconfined_execution and not use_netns_isolation:
        raise ValueError("unconfined-native-launch-not-explicitly-authorized-or-netns-backend-missing")
    if require_native_execve_observation:
        if (
            not use_netns_isolation
            or execve_tracer_executable is None
            or expected_execve_tracer_sha256 is None
        ):
            raise ValueError("native-execve-observation-requires-isolated-pinned-tracer")
        admit_execve_tracer(
            execve_tracer_executable, sha256=expected_execve_tracer_sha256
        )
    elif execve_tracer_executable is not None or expected_execve_tracer_sha256 is not None:
        raise ValueError("unassigned-native-execve-tracer-configuration")
    substrate = admit_netns_substrate() if use_netns_isolation else None
    if use_netns_isolation:
        if (hashmarks_executable is None or expected_hashmarks_sha256 is None
                or not hashmarks_executable.is_absolute()
                or hashmarks_executable.is_symlink()
                or not hashmarks_executable.is_file()
                or len(expected_hashmarks_sha256) != 64
                or any(c not in "0123456789abcdef" for c in expected_hashmarks_sha256)
                or sha256_file(hashmarks_executable) != expected_hashmarks_sha256):
            raise ValueError("missing-or-stale-frozen-hashmarks-mcp-executable")
    if use_netns_isolation:
        admit_host_only_paths(
            workspace=workspace, run_root=run_root,
            host_key_file=host_key_file,
            executable=opencode_executable,
            hashmarks_executable=hashmarks_executable,
        )
    workspace_root = workspace.resolve()
    if (run_root.resolve().is_relative_to(workspace_root)
            or host_key_file.resolve().is_relative_to(workspace_root)):
        raise ValueError("native-run-authority-inside-evaluated-workspace")
    if any((workspace_root / name).exists()
           or (workspace_root / name).is_symlink()
           for name in ("opencode.json", "opencode.jsonc", ".opencode")):
        raise ValueError("uncontrolled-project-opencode-config")
    # No filesystem or model work before exact executable and trial admission.
    pin = admit_opencode_binary(
        opencode_executable,
        expected_sha256=expected_binary_sha256,
        expected_version=expected_version,
    )
    # A bounded native-session export is mandatory whenever model work can
    # succeed. Reject missing Linux capture authority before creating the
    # workspace/run root or starting the native model.
    native_export_preflight()
    # Check the frozen source and both treatment arms before the
    # effective Linux kernel probe and before any model work.
    chat_contract = None
    paired_contract = None
    bridge_contract = None
    if use_netns_isolation:
        cell = cells[0]
        selected = select_treatment(
            frozen, trial_id=trial_id, harness=cell["harness"],
            task=cell["task"], replicate=cell["replicate"],
            arm=cell["arm"], current=current, replaced=replaced,
        )
        chat_contract = inspect_selected_content(selected)
        if not chat_contract["chat_tool_content_wire_compatible"]:
            raise ValueError(
                "native-chat-tool-content-shape-not-supported-before-model-work"
            )
        if frozen["design"]["study"] == "text_presentation":
            opposite = [
                row for row in frozen["assignments"]
                if row["pair_id"] == cell["pair_id"] and row["arm"] != cell["arm"]
            ]
            if len(opposite) != 1:
                raise ValueError("incomplete-native-text-presentation-pair")
            partner = select_treatment(
                frozen, trial_id=opposite[0]["trial_id"],
                harness=opposite[0]["harness"],
                task=opposite[0]["task"],
                replicate=opposite[0]["replicate"],
                arm=opposite[0]["arm"],
                current=current, replaced=replaced,
            )
            partner_contract = inspect_selected_content(partner)
            if (not partner_contract["chat_tool_content_wire_compatible"]
                    or selected["semantic_sha256"] != partner["semantic_sha256"]
                    or selected["generation_sha256"] != partner["generation_sha256"]
                    or selected["surface_sha256"] == partner["surface_sha256"]):
                raise ValueError("unqualified-native-text-presentation-pair")
        paired_contract = admit_native_chat_pair(
            frozen, trial_id=trial_id, current=current, replaced=replaced,
            allow_unpaired_diagnostic=allow_unpaired_native_diagnostic,
        )
        if verified_native_mcp_bridge:
            bridge_contract = freeze_bridge_contract(
                selected, original_packet=current["content"],
                hashmarks_sha256=expected_hashmarks_sha256,
            )
    # Only after complete frozen pair admission may the host probe the kernel.
    kernel_probe = probe_netns_kernel(substrate) if substrate is not None else None
    gateway = NativeOpenCodeGateway(
        frozen, trial_id=trial_id, host_key_file=host_key_file,
        host_identity=host_identity, current=current, replaced=replaced,
        gateway_token=secrets.token_urlsafe(32),
        upstream=upstream, approved_origin=approved_origin,
        upstream_api_key=upstream_api_key, catalog_sha256=catalog_sha256,
        oracle_sha256=oracle_sha256, workspace_sha256=workspace_sha256,
        enforce_chat_tool_wire_contract=use_netns_isolation,
    )
    _make_private_dir(run_root)
    for name in ("home", "config", "cache", "data", "state", "gateway"):
        _make_private_dir(run_root / name)
    started = False
    bridge_contract_sha256 = None
    bridge_module_sha256 = None
    if verified_native_mcp_bridge:
        bridge_contract_sha256 = write_bridge_contract(
            run_root / "native-mcp-contract.json", bridge_contract,
        )
        bridge_files = [
            Path(__file__).with_name("native_mcp_stdio_bridge.py"),
            Path(__file__).with_name("native_mcp_bridge_contract.py"),
        ]
        if any(p.is_symlink() or not p.is_file() for p in bridge_files):
            raise ValueError("native-mcp-bridge-source-not-admitted")
        bridge_module_sha256 = [
            sha256_file(p) for p in bridge_files
        ]
    try:
        port = secrets.randbelow(16_000) + 40_000 if use_netns_isolation else None
        if use_netns_isolation:
            gateway.start_unix(
                run_root / "gateway" / "upstream.sock",
                require_isolated_peer=True,
            )
        else:
            port = gateway.start()
        started = True
        cfg = gateway_config(
            port=port, model_id=frozen["design"]["model"],
            hashmarks_executable=hashmarks_executable if use_netns_isolation else None,
            workspace=workspace if use_netns_isolation else None,
            bridge_python=substrate["python"] if verified_native_mcp_bridge else None,
            bridge_hashmarks_sha256=(
                expected_hashmarks_sha256 if verified_native_mcp_bridge else None
            ),
        )
        config = run_root / "opencode.json"
        config_sha256 = write_gateway_config(config, cfg)
        model = cfg["model"]
        child_env = {
            "HOME": str(run_root / "home"),
            "XDG_CONFIG_HOME": str(run_root / "config"),
            "XDG_CACHE_HOME": str(run_root / "cache"),
            "XDG_DATA_HOME": str(run_root / "data"),
            "XDG_STATE_HOME": str(run_root / "state"),
            "OPENCODE_CONFIG": str(config),
            "OPENCODE_CONFIG_DIR": str(run_root / "config"),
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "OPENCODE_AUTO_SHARE": "false",
            "BENCHMARK_NATIVE_GATEWAY_TOKEN": gateway.token,
            "PATH": os.defpath,
        }
        # E265: probe the REAL full bwrap worker + mount layout, including
        # readonly config and host-key absence, before the first model call.
        # The wrapper under test is also the one used for actual native work.
        mount_probe = (
            probe_native_mounts(
                substrate, executable=opencode_executable,
                hashmarks=hashmarks_executable, workspace=workspace,
                run_root=run_root, host_key_file=host_key_file,
                environment=child_env, port=port,
                config_sha256=config_sha256,
                use_native_mcp_bridge=verified_native_mcp_bridge,
            ) if substrate is not None else None
        )
        # Distinguish this new session from concurrent or earlier sessions.
        # The launcher never resolves "latest session" as evidence.
        native_title = "agentscookbook:" + trial_id + ":" + secrets.token_hex(12)
        started_at_ms = time.time_ns() // 1_000_000
        # Do not pass upstream API keys or ambient LLM credentials to the
        # agent; its only configured provider is the loopback gateway.
        native_command = [
            str(opencode_executable), "run", "--model", model,
            "--title", native_title, "--format", "json", prompt,
        ]
        native_argv = (
            build_isolated_argv(
                substrate, executable=opencode_executable, workspace=workspace,
                run_root=run_root, gateway_dir=run_root / "gateway",
                environment=child_env, command=native_command,
                worker_port=port,
                hashmarks_executable=hashmarks_executable,
                use_native_mcp_bridge=verified_native_mcp_bridge,
            ) if substrate is not None else native_command
        )
        if mount_probe is not None:
            verify_worker_source_unchanged(mount_probe)
        native_execve_evidence = None
        if require_native_execve_observation:
            exit_code, native_execve_evidence = run_host_execve_observed(
                native_argv,
                tracer=execve_tracer_executable,
                tracer_sha256=expected_execve_tracer_sha256,
                workspace=workspace, run_root=run_root,
                environment=child_env, timeout_seconds=timeout_seconds,
                campaign_id=frozen["design"]["campaign_id"],
                trial_id=trial_id, host_identity=host_identity,
                host_build_sha256=frozen["host_build_sha256"],
                key=load_host_key(host_key_file),
            )
        else:
            try:
                result = subprocess.run(
                    native_argv,
                    cwd=workspace, env=child_env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=timeout_seconds, check=False,
                )
                exit_code = result.returncode
            except (OSError, subprocess.SubprocessError):
                exit_code = None
        if verified_native_mcp_bridge:
            files = [
                Path(__file__).with_name("native_mcp_stdio_bridge.py"),
                Path(__file__).with_name("native_mcp_bridge_contract.py"),
            ]
            if ([sha256_file(p) for p in files] != bridge_module_sha256
                    or sha256_file(run_root / "native-mcp-contract.json")
                    != bridge_contract_sha256):
                raise ValueError("native-mcp-bridge-source-changed-during-trial")
        if sha256_file(opencode_executable) != expected_binary_sha256:
            raise ValueError("native-opencode-binary-changed-during-trial")
        if mount_probe is not None:
            verify_worker_source_unchanged(mount_probe)
        if substrate is not None:
            verify_substrate_unchanged(substrate)
            if sha256_file(hashmarks_executable) != expected_hashmarks_sha256:
                raise ValueError("native-hashmarks-executable-changed-during-trial")
        observation = gateway.result()
        routed = (
            exit_code == 0
            and observation["post_evidence_submissions"] == 1
            and (
                substrate is None
                or observation["distinct_netns_peer_connections"] >= 1
            )
        )
        session_result: dict[str, Any] | None = None
        session_error: str | None = None
        if routed:
            try:
                # Export commands run in fresh deny-egress namespaces too:
                # no unconfined inspection subprocess can silently reenable
                # native external provider access after the trial.
                isolated_export = (
                    (lambda command: build_isolated_argv(
                        substrate, executable=opencode_executable,
                        workspace=workspace, run_root=run_root,
                        gateway_dir=run_root / "gateway",
                        environment=child_env, command=command,
                        worker_port=None,
                        hashmarks_executable=hashmarks_executable,
                        use_native_mcp_bridge=verified_native_mcp_bridge,
                    )) if substrate is not None else None
                )
                session_id, exported = acquire_native_export(
                    opencode_executable, workspace=workspace,
                    environment=child_env, exact_title=native_title,
                    started_at_ms=started_at_ms,
                    isolate_command=isolated_export,
                )
                if sha256_file(opencode_executable) != expected_binary_sha256:
                    raise ValueError("native-opencode-executable-drift-after-export")
                # Exact observed provider/model IDs must match the local
                # configured OpenCode provider, not the upstream API model.
                session_result = finalize_native_export(
                    exported, capture=gateway.capture, session_id=session_id,
                    title=native_title, workspace=workspace, run_root=run_root,
                    expected_provider="agentscookbook-captured",
                    expected_model=frozen["design"]["model"],
                    original_packet=current["content"],
                    provider_issued=gateway.provider_issued_tool,
                    require_provider_issued=use_netns_isolation,
                )
                if use_netns_isolation and (
                    not session_result["provider_issued_native_arguments_bound"]
                    or session_result["provider_issued_response_sha256"]
                    != observation["provider_issued_response_sha256"]
                ):
                    raise ValueError("native-provider-issued-session-identity-not-joined")
            except (OSError, ValueError, TypeError, UnicodeError):
                session_error = "native-session-not-bound-to-observed-provider-submission"
        qualified = (
            routed and session_result is not None
            and (
                not require_native_execve_observation
                or native_execve_evidence is not None
                and native_execve_evidence["state"] == "HOST_EXECVE_OBSERVED"
            )
        )
        report = {
            "schema": SCHEMA,
            "trial_id": trial_id,
            "manifest_sha256": frozen["manifest_sha256"],
            "binary_sha256": pin["executable_sha256"],
            "opencode_version": pin["version"],
            "config_sha256": config_sha256,
            "verified_native_mcp_bridge_configured": verified_native_mcp_bridge,
            "frozen_native_mcp_bridge_sha256": bridge_contract_sha256,
            "native_mcp_original_hashmarks_response_independently_attested": False,
            "provider_issued_native_export_arguments_bound": (
                session_result is not None
                and session_result["provider_issued_native_arguments_bound"]
            ),
            "native_exit_code": exit_code,
            "native_host_execve_observation_required": require_native_execve_observation,
            "native_host_execve_evidence": native_execve_evidence,
            "native_nested_tool_coverage_proven": False,
            "gateway": observation,
            "observed_provider_response_relays": observation["post_evidence_submissions"],
            "native_gateway_route_completed": routed,
            "native_session_binding": session_result,
            "native_session_binding_error": session_error,
            "native_trial_evidence_qualified": qualified,
            "native_chat_transport_preflight": chat_contract,
            "native_chat_pair_preflight": paired_contract,
            "host_receipt_finalized": qualified,
            "native_process_origin_proven": False,
            "linux_network_namespace_requested": substrate is not None,
            "host_network_egress_intentionally_disabled": substrate is not None,
            "native_sandbox_binary_sha256": (
                substrate["bwrap_sha256"] if substrate else None
            ),
            "frozen_hashmarks_executable_sha256": (
                expected_hashmarks_sha256 if substrate else None
            ),
            "network_egress_exclusivity_independently_attested": False,
            "kernel_distinct_netns_relay_observed": (
                substrate is not None
                and observation["distinct_netns_peer_connections"] >= 1
            ),
            "native_kernel_preflight": kernel_probe,
            "native_mount_preflight": mount_probe,
            "native_model_input_delivered_proven": False,
            "causal_improvement_proven": False,
        }
        if substrate is not None:
            # Host-owned create-only report. The independent E260 audit
            # rechecks this against raw session, signed transport and source;
            # self-reported isolation alone cannot establish causal uplift.
            data = json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
            output = run_root / "trial-execution.json"
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(output, flags, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if qualified:
                # The signed receipt binds this exact create-only execution
                # report to the already signed host/provider packet receipt.
                attested_provider = (run_root / "host-attestation.json").read_bytes()
                signed = sign_isolation_report(
                    report, execution_bytes=data,
                    provider_attestation_bytes=attested_provider,
                    host_identity=host_identity,
                    host_key=load_host_key(host_key_file),
                )
                write_isolation_attestation(
                    run_root / "trial-execution.attestation.json", signed,
                )
        return report
    finally:
        if started:
            gateway.close()


def _json_file(path: Path, *, max_bytes: int = 262_144) -> object:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError("untrusted-native-trial-json-source")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--binary-sha256", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--host-key-file", type=Path, required=True)
    parser.add_argument("--host-identity", required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--replaced", type=Path)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--approved-origin", required=True)
    parser.add_argument("--catalog-sha256", required=True)
    parser.add_argument("--oracle-sha256", required=True)
    parser.add_argument("--workspace-sha256", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--allow-unconfined-native-test", action="store_true")
    parser.add_argument("--require-netns-isolation", action="store_true")
    parser.add_argument("--hashmarks-binary", type=Path)
    parser.add_argument("--hashmarks-sha256")
    parser.add_argument("--verified-native-mcp-bridge", action="store_true")
    parser.add_argument("--allow-unpaired-native-diagnostic", action="store_true")
    parser.add_argument("--require-native-execve-observation", action="store_true")
    parser.add_argument("--native-execve-tracer-bin", type=Path)
    parser.add_argument("--native-execve-tracer-sha256")
    args = parser.parse_args()
    try:
        api_key = os.environ.get("BENCHMARK_UPSTREAM_API_KEY")
        if not api_key:
            raise ValueError("missing-privileged-upstream-api-key")
        if args.prompt_file.is_symlink() or args.prompt_file.stat().st_size > 16_384:
            raise ValueError("untrusted-prompt-file")
        result = launch_native_trial(
            load_manifest(args.manifest), trial_id=args.trial_id,
            opencode_executable=args.binary,
            expected_binary_sha256=args.binary_sha256,
            expected_version=args.version, run_root=args.run_root,
            workspace=args.workspace,
            prompt=args.prompt_file.read_text(encoding="utf-8"),
            host_key_file=args.host_key_file, host_identity=args.host_identity,
            current=_json_file(args.current),
            replaced=_json_file(args.replaced) if args.replaced else None,
            upstream=args.upstream, approved_origin=args.approved_origin,
            upstream_api_key=api_key,
            catalog_sha256=args.catalog_sha256,
            oracle_sha256=args.oracle_sha256,
            workspace_sha256=args.workspace_sha256,
            timeout_seconds=args.timeout_seconds,
            allow_unconfined_execution=args.allow_unconfined_native_test,
            use_netns_isolation=args.require_netns_isolation,
            hashmarks_executable=args.hashmarks_binary,
            expected_hashmarks_sha256=args.hashmarks_sha256,
            verified_native_mcp_bridge=args.verified_native_mcp_bridge,
            allow_unpaired_native_diagnostic=args.allow_unpaired_native_diagnostic,
            require_native_execve_observation=args.require_native_execve_observation,
            execve_tracer_executable=args.native_execve_tracer_bin,
            expected_execve_tracer_sha256=args.native_execve_tracer_sha256,
        )
    except (ValueError, OSError, TypeError, UnicodeError) as exc:
        print(json.dumps({
            "schema": SCHEMA, "native_gateway_route_completed": False,
            "native_process_origin_proven": False,
            "causal_improvement_proven": False,
            "reason": str(exc),
        }, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["native_trial_evidence_qualified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
