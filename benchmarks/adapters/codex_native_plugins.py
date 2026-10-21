"""Bounded opaque native plugin inputs and native-owned capability readback.

The documented cache layout is a transport format, never a version selector.
All immutable candidates for explicitly configured IDs are transported. Codex
chooses and reports what is installed/loaded. Raw MCP configuration, hook
payloads and errors never enter the public observation.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tomllib
from typing import Any

from benchmarks.adapters.codex_native_namespace import build_native_argv
from benchmarks.adapters.codex_native import validate_private_state
from benchmarks.harness.model import McpExposure, TrialContext
from scripts.agent_economics.bounded_process import ProcessLimits, StopStdinExchange, run_bounded

_MAX_FILES = 50_000
_MAX_BYTES = 536_870_912


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _component(value: Any) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."} or any(c in value for c in ("/", "\\", "\0")):
        raise ValueError("native-plugin-invalid-address")
    return value


def _records(root: Path) -> dict[str, Any]:
    """Hash the admitted artifact opaquely; do not interpret vendor programs."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("native-plugin-artifact-root-unavailable")
    records = {".": {"directory_mode": stat.S_IMODE(root.stat().st_mode)}}
    total = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if len(records) >= _MAX_FILES:
                raise ValueError("native-plugin-artifact-observation-bound")
            if path.is_symlink():
                if not path.resolve().is_relative_to(root.resolve()) or not path.exists():
                    raise ValueError("native-plugin-artifact-link-escapes")
                records[relative] = {"link": os.readlink(path)}
            elif path.is_file():
                before = path.stat()
                total += before.st_size
                if total > _MAX_BYTES or len(records) >= _MAX_FILES:
                    raise ValueError("native-plugin-artifact-observation-bound")
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1_048_576), b""):
                        digest.update(block)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError("native-plugin-artifact-changed")
                records[relative] = {"sha256": digest.hexdigest(), "mode": stat.S_IMODE(after.st_mode)}
            elif path.is_dir():
                records[relative] = {"directory_mode": stat.S_IMODE(path.stat().st_mode)}
            else:
                raise ValueError("native-plugin-artifact-special-file")
    return records


def _registry(private: Path) -> tuple[dict[str, Any], dict[str, Path]]:
    plugins: dict[str, Any] = {}
    marketplaces: dict[str, Path] = {}
    inputs = [private / "config.toml", private / "managed_config.toml", private / "requirements.toml", *private.glob("*.config.toml")]
    def layers(value: dict[str, Any]):
        yield value
        for item in value.get("profiles", {}).values():
            if isinstance(item, dict):
                yield from layers(item)
    for path in inputs:
        if not path.is_file():
            continue
        value = tomllib.loads(path.read_text())
        for layer in layers(value):
            rows = layer.get("plugins", {})
            if not isinstance(rows, dict):
                raise ValueError("native-plugin-registry-shape")
            # This union transports candidate inputs; it does not resolve
            # effective enabled state between native config layers.
            plugins.update(rows)
            declarations = layer.get("marketplaces", {})
            if not isinstance(declarations, dict):
                raise ValueError("native-plugin-marketplace-registry-shape")
            for name, declaration in declarations.items():
                if not isinstance(declaration, dict) or declaration.get("source_type") != "local" or not isinstance(declaration.get("source"), str):
                    raise ValueError("native-plugin-marketplace-transport-unsupported")
                selected = Path(declaration["source"])
                if not selected.is_absolute():
                    raise ValueError("native-plugin-marketplace-path-not-qualified")
                if name in marketplaces and marketplaces[name] != selected:
                    raise ValueError("native-plugin-marketplace-layer-ambiguity")
                marketplaces[_component(name)] = selected
    if len(plugins) > 64:
        raise ValueError("native-plugin-registry-observation-bound")
    return plugins, marketplaces


def prepare_native_plugin_inputs(context: TrialContext, source_home: Path, *, discovered_ids: tuple[str, ...] = ()) -> dict[str, Any]:
    validate_private_state(context)
    private = Path(context.environment["CODEX_HOME"])
    selected_home = context.environment.get("BENCHMARK_NATIVE_HOME")
    if selected_home:
        personal = Path(selected_home) / ".agents/plugins/marketplace.json"
        if personal.exists() or personal.is_symlink():
            raise ValueError("native-plugin-personal-marketplace-transport-unsupported")
    plugins, markets = _registry(private)
    if not isinstance(discovered_ids, tuple) or any(not isinstance(value, str) for value in discovered_ids):
        raise ValueError("native-plugin-discovered-selection-shape")
    discovered = set(discovered_ids) - set(plugins)
    if len(set(plugins) | discovered) > 64:
        raise ValueError("native-plugin-registry-observation-bound")
    artifacts = []
    candidates: dict[str, list[str]] = {}
    total_entries = 0
    total_bytes = 0

    def admit(path: Path) -> None:
        nonlocal total_entries, total_bytes
        before = _records(path)
        total_entries += len(before)
        total_bytes += sum(path.joinpath(name).stat().st_size for name, record in before.items() if "sha256" in record)
        if total_entries > _MAX_FILES or total_bytes > _MAX_BYTES:
            raise ValueError("native-plugin-input-observation-bound")
        try:
            relative = path.relative_to(source_home)
        except ValueError:
            # Outside the aliased home, the namespace exposes original paths
            # read-only. They remain separately identity-bound observations.
            artifacts.append({"kind": "external", "path": str(path), "records": before})
            return
        destination = private / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if _records(destination) != before:
                raise ValueError("native-plugin-artifact-destination-drift")
        else:
            shutil.copytree(path, destination, symlinks=True)
        if _records(path) != before or _records(destination) != before:
            raise ValueError("native-plugin-artifact-copy-changed")
        artifacts.append({"kind": "private", "path": relative.as_posix(), "records": before})

    selected_by_market: dict[str, set[str]] = {}
    for plugin_id in sorted(set(plugins) | discovered):
        if not isinstance(plugin_id, str) or "@" not in plugin_id:
            raise ValueError("native-plugin-id-format-unsupported")
        name, market = plugin_id.rsplit("@", 1)
        name, market = _component(name), _component(market)
        if market not in markets and plugin_id not in discovered:
            raise ValueError("native-plugin-selected-marketplace-unavailable")
        if market in markets:
            selected_by_market.setdefault(market, set()).add(name)
        cache = source_home / "plugins" / "cache" / market / name
        versions = []
        if cache.is_dir():
            for candidate in sorted(cache.iterdir()):
                if candidate.is_dir() and not candidate.is_symlink() and (candidate / ".codex-plugin/plugin.json").is_file():
                    version = _component(candidate.name)
                    if len(versions) >= 8:
                        raise ValueError("native-plugin-version-candidate-bound")
                    admit(candidate)
                    versions.append(version)
        candidates[plugin_id] = versions
    for market, names in selected_by_market.items():
        root = markets[market]
        catalog = root / ".agents/plugins/marketplace.json"
        try:
            if catalog.is_symlink() or catalog.stat().st_size > 16_000_000:
                raise ValueError("native-plugin-marketplace-catalog-unavailable")
            data = json.loads(catalog.read_text())
        except (OSError, ValueError):
            raise ValueError("native-plugin-marketplace-catalog-unavailable") from None
        admit(root / ".agents")
        entries = data.get("plugins") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise ValueError("native-plugin-marketplace-catalog-shape")
        matched = set()
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("name") not in names:
                continue
            declaration = entry.get("source")
            if not isinstance(declaration, dict) or declaration.get("source") != "local" or not isinstance(declaration.get("path"), str):
                raise ValueError("native-plugin-package-source-unsupported")
            path = Path(declaration["path"])
            path = path if path.is_absolute() else root / path
            if not (path / ".codex-plugin/plugin.json").is_file():
                raise ValueError("native-plugin-source-package-manifest-unavailable")
            admit(path.resolve())
            matched.add(entry["name"])
        if matched != names:
            raise ValueError("native-plugin-selected-catalog-entry-unavailable")
    identity_data = {"configured_ids": sorted(plugins), "discovered_ids": sorted(discovered), "candidates": candidates, "artifacts": artifacts}
    return {"schema": "evalshot.native-codex-plugin-inputs.v1", **identity_data, "identity": _digest(identity_data)}


def verify_native_plugin_inputs(context: TrialContext, manifest: dict[str, Any]) -> bool:
    try:
        data = {key: manifest[key] for key in ("configured_ids", "discovered_ids", "candidates", "artifacts")}
        if manifest.get("schema") != "evalshot.native-codex-plugin-inputs.v1" or _digest(data) != manifest.get("identity"):
            return False
        private = Path(context.environment["CODEX_HOME"])
        for artifact in manifest["artifacts"]:
            root = private / artifact["path"] if artifact["kind"] == "private" else Path(artifact["path"])
            if _records(root) != artifact["records"]:
                return False
        for plugin_id, versions in manifest["candidates"].items():
            name, market = plugin_id.rsplit("@", 1)
            root = private / "plugins/cache" / market / name
            current = sorted(p.name for p in root.iterdir() if p.is_dir() and (p / ".codex-plugin/plugin.json").is_file()) if root.is_dir() else []
            if current != sorted(versions):
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _rpc(context: TrialContext, command: str, source_home: Path, requests: list[tuple[int, str, dict]], overrides: tuple[str, ...]) -> dict[int, Any]:
    validate_private_state(context)
    def encode(value: Any) -> bytes:
        return (json.dumps(value, separators=(",", ":")) + "\n").encode()

    def responses(raw: bytes, final: bool = False) -> dict[int, Any]:
        result = {}
        if final and raw and not raw.endswith(b"\n"):
            raise ValueError("native-plugin-rpc-truncated-line")
        complete = raw if final else raw[:raw.rfind(b"\n") + 1]
        for line in complete.splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError:
                raise ValueError("native-plugin-rpc-malformed-line") from None
            if not isinstance(value, dict):
                raise ValueError("native-plugin-rpc-malformed-message")
            if isinstance(value, dict) and type(value.get("id")) is int:
                if "error" in value or value["id"] in result:
                    raise ValueError("native-plugin-rpc-response-invalid")
                result[value["id"]] = value.get("result")
        return result

    def ready(raw: bytes, ids: set[int]) -> bool:
        try:
            return ids.issubset(responses(raw))
        except ValueError:
            raise StopStdinExchange() from None

    initialize = encode({"id": 0, "method": "initialize", "params": {"clientInfo": {"name": "evalshot-native-inputs", "version": "1"}, "capabilities": {"experimentalApi": True}}})
    expected = {i for i, _, _ in requests}
    if len(expected) != len(requests) or expected.intersection({0, 2_147_483_646, 2_147_483_647}):
        raise ValueError("native-plugin-rpc-request-identity")
    runtime = [(i, method, params) for i, method, params in requests if method == "mcpServerStatus/list" and "threadId" not in params]
    initial = [request for request in requests if request not in runtime]
    payload = encode({"method": "initialized"}) + b"".join(encode({"id": i, "method": method, "params": params}) for i, method, params in initial)
    stages = [(initialize, lambda raw: ready(raw, {0}))]
    if runtime:
        # A catalog can be cached while its server is disconnected. Start only
        # an ephemeral native thread, never a turn, and ask that same runtime.
        thread_request = 2_147_483_646
        close_request = 2_147_483_647
        payload += encode({"id": thread_request, "method": "thread/start", "params": {"cwd": str(context.workspace), "ephemeral": True}})
        def thread_id(raw: bytes) -> str:
            value = responses(raw).get(thread_request)
            thread = value.get("thread") if isinstance(value, dict) else None
            selected = thread.get("id") if isinstance(thread, dict) else None
            if not isinstance(selected, str) or not selected or len(selected) > 512:
                raise ValueError("native-mcp-ephemeral-runtime-unavailable")
            return selected
        def runtime_payload(raw: bytes) -> bytes:
            selected = thread_id(raw)
            return b"".join(encode({"id": i, "method": method, "params": {**params, "threadId": selected}}) for i, method, params in runtime)
        def close_payload(raw: bytes) -> bytes:
            return encode({"id": close_request, "method": "thread/unsubscribe", "params": {"threadId": thread_id(raw)}})
        stages.extend(((payload, lambda raw: ready(raw, {thread_request, *(i for i, _, _ in initial)})), (runtime_payload, lambda raw: ready(raw, expected)), (close_payload, lambda raw: ready(raw, {close_request}))))
    else:
        stages.append((payload, lambda raw: ready(raw, expected)))
    process = run_bounded(
        repository_root=context.workspace,
        argv=build_native_argv(context, (command, "--no-daemon", *overrides, "app-server", "--listen", "stdio://"), source_home),
        environment=context.environment, inherit_environment=False,
        limits=ProcessLimits(timeout_seconds=30, max_stdout_bytes=16_000_000, max_stderr_bytes=1_000_000),
        stdin_stages=tuple(stages),
    )
    if process.executable_missing or process.timed_out or process.stdout_truncated or process.stderr_truncated or process.return_code not in (0, None):
        raise ValueError("native-plugin-private-api-unavailable")
    values = responses(process.stdout, final=True)
    if not expected.issubset(values):
        raise ValueError("native-plugin-private-api-incomplete")
    return values



def observe_native_plugin_selection(context: TrialContext, command: str, source_home: Path, reference: bool = False) -> dict[str, Any]:
    """Observe CLI-owned installed selection, with an optional read-only source view."""
    validate_private_state(context)
    # Reuse namespace validation for canonical/disjoint roots and runtime inputs.
    private_argv = build_native_argv(context, (command, "--no-daemon", "plugin", "list", "--json"), source_home)
    environment = dict(context.environment)
    if reference:
        selected_home = environment.get("BENCHMARK_NATIVE_HOME")
        if not selected_home:
            raise ValueError("native-plugin-source-home-scope-unavailable")
        home = Path(selected_home)
        if not home.is_absolute() or home != home.resolve() or not home.is_dir():
            raise ValueError("native-plugin-source-home-scope-unavailable")
        scratch = context.control_root / "native-reference-sqlite"
        if scratch.is_symlink():
            raise ValueError("native-plugin-reference-state-unsupported")
        scratch.mkdir(exist_ok=True)
        environment.update(HOME=str(home), CODEX_HOME=str(source_home), CODEX_SQLITE_HOME=str(scratch))
        # No original input/state mount is writable. This reference never starts
        # a thread or service; native startup write attempts fail at the kernel.
        argv = (private_argv[0], "--die-with-parent", "--new-session", "--unshare-pid", "--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev", "--bind", str(context.control_root.resolve()), str(context.control_root.resolve()), "--bind", str(context.workspace.resolve()), str(context.workspace.resolve()), "--chdir", str(context.workspace.resolve()), "--", command, "--no-daemon", "plugin", "list", "--json")
    else:
        argv = private_argv
    process = run_bounded(repository_root=context.workspace, argv=argv, environment=environment, inherit_environment=False, limits=ProcessLimits(timeout_seconds=35, max_stdout_bytes=3_000_000, max_stderr_bytes=100_000))
    if process.executable_missing or process.timed_out or process.stdout_truncated or process.stderr_truncated or process.return_code != 0:
        raise ValueError("native-plugin-cli-selection-unavailable")
    try:
        value = json.loads(process.stdout)
    except (ValueError, UnicodeError):
        raise ValueError("native-plugin-cli-selection-shape") from None
    if not isinstance(value, dict) or not isinstance(value.get("installed"), list) or not isinstance(value.get("available"), list):
        raise ValueError("native-plugin-cli-selection-shape")
    installed = []
    for row in value["installed"]:
        if not isinstance(row, dict) or any(not isinstance(row.get(key), str) or not row[key] for key in ("name", "marketplaceName", "pluginId")) or type(row.get("enabled")) is not bool or row.get("installed") is not True or row.get("version") is not None and not isinstance(row["version"], str):
            raise ValueError("native-plugin-cli-selection-shape")
        if any(row.get(key) is not None and not isinstance(row[key], str) for key in ("authPolicy", "installPolicy")):
            raise ValueError("native-plugin-cli-selection-shape")
        installed.append({"id": row["pluginId"], "name": row["name"], "marketplace": row["marketplaceName"], "enabled": row["enabled"], "installed": True, "version": row.get("version"), "auth_policy": row.get("authPolicy"), "install_policy": row.get("installPolicy")})
    if len(installed) > 64 or len({(row["name"], row["marketplace"]) for row in installed}) != len(installed):
        raise ValueError("native-plugin-cli-selection-shape")
    installed.sort(key=lambda row: (row["marketplace"], row["name"]))
    # Compare the exact selected semantic environment while keeping mutable
    # roots, transport pointers and all raw values out of public observations.
    from benchmarks.harness.runtime_authority import PRIVATE_STATE_ENV_KEYS
    semantic_environment = {key: hashlib.sha256(value.encode()).hexdigest() for key, value in context.environment.items() if key not in PRIVATE_STATE_ENV_KEYS and not key.startswith("BENCHMARK_")}
    return {"schema": "evalshot.native-codex-plugin-selection.v1", "state": "observed", "scope": "source-readonly" if reference else "trial", "source_filesystem_readonly": reference, "installed": installed, "selection_digest": _digest(installed), "semantic_environment_digest": _digest(semantic_environment), "available_count": len(value["available"])}


def discover_native_plugin_ids(context: TrialContext, command: str, source_home: Path) -> tuple[str, ...]:
    """Return native-selected installed IDs; never discover versions ourselves."""
    reply = _rpc(context, command, source_home, [(1, "plugin/installed", {"cwds": [str(context.workspace)]})], ())[1]
    if not isinstance(reply, dict) or not isinstance(reply.get("marketplaces"), list) or reply.get("marketplaceLoadErrors") != []:
        raise ValueError("native-plugin-marketplace-readback-incomplete")
    selected = []
    for market in reply["marketplaces"]:
        if not isinstance(market, dict) or not isinstance(market.get("plugins"), list):
            raise ValueError("native-plugin-marketplace-readback-shape")
        for row in market["plugins"]:
            if not isinstance(row, dict) or type(row.get("installed")) is not bool:
                raise ValueError("native-plugin-selection-readback-shape")
            if row["installed"]:
                plugin_id = row.get("id")
                if not isinstance(plugin_id, str) or "@" not in plugin_id:
                    raise ValueError("native-plugin-id-format-unsupported")
                name, address = plugin_id.rsplit("@", 1)
                _component(name)
                _component(address)
                selected.append(plugin_id)
    if len(selected) != len(set(selected)) or len(selected) > 64:
        raise ValueError("native-plugin-selection-readback-shape")
    return tuple(sorted(selected))


def _stable(value: Any, context: TrialContext) -> Any:
    """Normalize only trial filesystem identities for comparison, never payloads."""
    if isinstance(value, str):
        return value.replace(str(context.control_root), "<trial-control>").replace(str(context.workspace), "<trial-workspace>")
    if isinstance(value, list):
        return [_stable(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _stable(item, context) for key, item in value.items()}
    return value


def _resource(path: str, context: TrialContext, source_home: Path) -> Path:
    selected = Path(path)
    if selected.is_relative_to(source_home):
        selected = Path(context.environment["CODEX_HOME"]) / selected.relative_to(source_home)
    return selected


def _subject_conformance(effective: dict[str, Any], controlled: str | None, exposure: McpExposure | None) -> None:
    if not controlled:
        return
    rows = effective.get("mcp_servers")
    if not isinstance(rows, dict):
        raise ValueError("native-mcp-controlled-config-unavailable")
    row = rows.get(controlled)
    if exposure is None:
        if row is not None and (not isinstance(row, dict) or row.get("enabled") is not False):
            raise ValueError("native-mcp-controlled-baseline-not-disabled")
        return
    if exposure.name != controlled or not isinstance(row, dict):
        raise ValueError("native-mcp-controlled-subject-mismatch")
    expected = {"command": exposure.command, "args": list(exposure.args), "cwd": str(exposure.cwd.resolve()), "enabled": True, "env": dict(exposure.environment)}
    # Current native config/read adds its local execution-environment marker.
    # It does not alter the admitted transport, and arbitrary environment IDs
    # remain unsupported. This is not a general environment resolver.
    native_local = {"environment_id"} if row.get("environment_id") == "local" else set()
    if any(row.get(key, {} if key == "env" else None) != value for key, value in expected.items()) or any(key not in expected and key not in native_local and value is not None for key, value in row.items()):
        raise ValueError("native-mcp-controlled-transport-not-exact")


def observe_native_plugins(context: TrialContext, command: str, source_home: Path, overrides: tuple[str, ...] = (), controlled_subject: str | None = None, exposure: McpExposure | None = None, selection_observation: dict[str, Any] | None = None, source_selection_observation: dict[str, Any] | None = None) -> dict[str, Any]:
    values = _rpc(context, command, source_home, [(1, "plugin/installed", {"cwds": [str(context.workspace)]}), (2, "config/read", {"cwd": str(context.workspace), "includeLayers": False}), (3, "mcpServerStatus/list", {"detail": "full"})], overrides)
    installed = values[1]
    if not isinstance(installed, dict) or not isinstance(installed.get("marketplaces"), list) or installed.get("marketplaceLoadErrors") != []:
        raise ValueError("native-plugin-marketplace-readback-incomplete")
    configuration = values[2]
    if not isinstance(configuration, dict) or not isinstance(configuration.get("config"), dict):
        raise ValueError("native-plugin-effective-config-unavailable")
    effective = configuration["config"]
    _subject_conformance(effective, controlled_subject, exposure)
    declared_plugins = effective.get("plugins")
    if not isinstance(declared_plugins, dict):
        raise ValueError("native-plugin-effective-selection-unavailable")
    selected = []
    reads = []
    remote_reads = {}
    for market in installed.get("marketplaces", []):
        if not isinstance(market, dict) or not isinstance(market.get("plugins"), list):
            raise ValueError("native-plugin-marketplace-readback-shape")
        for plugin in market["plugins"]:
            if not isinstance(plugin, dict):
                raise ValueError("native-plugin-selection-readback-shape")
            if plugin.get("installed") is True:
                if type(plugin.get("enabled")) is not bool or not isinstance(plugin.get("id"), str):
                    raise ValueError("native-plugin-selection-readback-shape")
                if not isinstance(plugin.get("name"), str) or not isinstance(market.get("name"), str):
                    raise ValueError("native-plugin-selection-readback-shape")
                selected.append({**plugin, "_marketplace": market["name"]})
                request_id = 100 + len(reads)
                if market.get("path") is None:
                    remote_id = plugin.get("remotePluginId")
                    if not isinstance(remote_id, str) or not remote_id:
                        raise ValueError("native-plugin-remote-identity-unavailable")
                    params = {"pluginName": remote_id, "remoteMarketplaceName": market["name"]}
                    remote_reads[request_id] = (market["name"], remote_id)
                else:
                    params = {"pluginName": plugin["name"], "marketplacePath": market["path"]}
                reads.append((request_id, "plugin/read", params))
    requested = {key for key, value in declared_plugins.items() if isinstance(value, dict) and value.get("enabled") is True}
    not_selected = sorted(requested - {row["id"] for row in selected if row["enabled"]})
    cli_rows = None
    if selection_observation is not None or source_selection_observation is not None:
        observations = (selection_observation, source_selection_observation)
        if any(not isinstance(row, dict) or row.get("schema") != "evalshot.native-codex-plugin-selection.v1" or row.get("state") != "observed" or not isinstance(row.get("installed"), list) or row.get("selection_digest") != _digest(row["installed"]) for row in observations):
            raise ValueError("native-plugin-reference-selection-unqualified")
        if selection_observation.get("scope") != "trial" or source_selection_observation.get("scope") != "source-readonly" or source_selection_observation.get("source_filesystem_readonly") is not True or selection_observation["selection_digest"] != source_selection_observation["selection_digest"] or selection_observation.get("semantic_environment_digest") != source_selection_observation.get("semantic_environment_digest"):
            raise ValueError("native-plugin-source-selection-mismatch")
        cli_rows = {(row["marketplace"], row["name"]): row for row in selection_observation["installed"]}
        if set(cli_rows) != {(row["_marketplace"], row["name"]) for row in selected} or any(cli_rows[(row["_marketplace"], row["name"])]["enabled"] != row["enabled"] for row in selected):
            raise ValueError("native-plugin-cli-api-selection-mismatch")
    elif not_selected:
        raise ValueError("native-plugin-enabled-selection-unavailable")
    details = _rpc(context, command, source_home, reads, overrides) if reads else {}
    public = []
    capability_bytes = []
    plugin_servers = set()
    for plugin, request in zip(selected, reads, strict=True):
        response = details[request[0]]
        detail = response.get("plugin") if isinstance(response, dict) else None
        if not isinstance(detail, dict) or any(not isinstance(detail.get(key), list) for key in ("skills", "mcpServers", "hooks", "apps", "appTemplates")):
            raise ValueError("native-plugin-detail-readback-shape")
        summary = detail.get("summary", {})
        if not isinstance(summary, dict) or any(summary.get(key) != plugin.get(key) for key in ("id", "name", "enabled", "installed")):
            raise ValueError("native-plugin-detail-selection-mismatch")
        if plugin.get("localVersion") is not None and summary.get("localVersion") != plugin["localVersion"]:
            raise ValueError("native-plugin-detail-version-mismatch")
        local_version = summary.get("localVersion")
        remote = remote_reads.get(request[0])
        if remote and summary.get("remotePluginId") != remote[1]:
            raise ValueError("native-plugin-remote-identity-mismatch")
        if cli_rows is not None and local_version is not None and cli_rows[(plugin["_marketplace"], plugin["name"])]["version"] not in (None, local_version):
            raise ValueError("native-plugin-cli-resource-version-mismatch")
        if not remote and plugin["enabled"] and (not isinstance(local_version, str) or not local_version):
            raise ValueError("native-plugin-loaded-version-unknown")
        for key in ("version", "localVersion"):
            if summary.get(key) is not None and not isinstance(summary[key], str):
                raise ValueError("native-plugin-version-readback-shape")
        if any(not isinstance(name, str) for name in detail["mcpServers"]):
            raise ValueError("native-plugin-mcp-readback-shape")
        if plugin["enabled"]:
            plugin_servers.update(detail["mcpServers"])
        if any(not isinstance(row, dict) or not isinstance(row.get("eventName"), str) for row in detail["hooks"]) or any(not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in detail["apps"]):
            raise ValueError("native-plugin-capability-readback-shape")
        if isinstance(detail.get("scheduledTasks"), list) and any(not isinstance(row, dict) or not isinstance(row.get("key"), str) for row in detail["scheduledTasks"]):
            raise ValueError("native-plugin-capability-readback-shape")
        skills = []
        resource_requests = []
        resource_rows = []
        for skill in detail.get("skills", []):
            if not isinstance(skill, dict):
                raise ValueError("native-plugin-skill-readback-shape")
            if type(skill.get("enabled")) is not bool or not isinstance(skill.get("name"), str):
                raise ValueError("native-plugin-skill-readback-shape")
            path = skill.get("path")
            observed_skill = {"name": skill["name"], "enabled": skill["enabled"]}
            if plugin["enabled"] and skill["enabled"]:
                if remote:
                    resource_requests.append((1000 + len(resource_requests), "plugin/skill/read", {"remoteMarketplaceName": remote[0], "remotePluginId": remote[1], "skillName": skill["name"]}))
                    resource_rows.append(observed_skill)
                else:
                    if not isinstance(path, str) or not _resource(path, context, source_home).is_file():
                        raise ValueError("native-plugin-enabled-skill-unavailable")
                    observed_skill["resource_authority"] = "native-local-path"
                    observed_skill["resource_sha256"] = hashlib.sha256(_resource(path, context, source_home).read_bytes()).hexdigest()
            skills.append(observed_skill)
        if resource_requests:
            bodies = _rpc(context, command, source_home, resource_requests, overrides)
            for row, resource_request in zip(resource_rows, resource_requests, strict=True):
                reply = bodies[resource_request[0]]
                content = reply.get("contents") if isinstance(reply, dict) else None
                if not isinstance(content, str) or len(content.encode()) > 2_000_000:
                    raise ValueError("native-plugin-remote-skill-unavailable")
                row["resource_authority"] = "native-remote-skill-read"
                row["resource_sha256"] = hashlib.sha256(content.encode()).hexdigest()
        public.append({"id": plugin["id"], "enabled": plugin["enabled"], "installed": True, "version": summary.get("version"), "local_version": local_version, "skills": sorted(skills, key=lambda row: row["name"]), "mcp_servers": sorted(detail["mcpServers"]), "hook_events": sorted(row.get("eventName") for row in detail["hooks"]), "apps": sorted(row.get("id") for row in detail["apps"]), "scheduled_tasks": sorted(row.get("key") for row in detail["scheduledTasks"]) if isinstance(detail.get("scheduledTasks"), list) else None})
        capability_bytes.append({"detail": _stable(detail, context), "skill_resources": skills})
    inventory = values[3]
    if not isinstance(inventory, dict) or inventory.get("nextCursor") is not None or not isinstance(inventory.get("data"), list):
        raise ValueError("native-mcp-inventory-incomplete")
    servers = []
    expected_servers = effective.get("mcp_servers")
    if not isinstance(expected_servers, dict):
        raise ValueError("native-mcp-effective-universe-unavailable")
    required = {name for name, entry in expected_servers.items() if isinstance(entry, dict) and entry.get("enabled") is True}
    if any(not isinstance(entry, dict) or type(entry.get("enabled")) is not bool for entry in expected_servers.values()):
        raise ValueError("native-mcp-effective-enabled-state-unknown")
    observed_names = [row.get("name") for row in inventory["data"] if isinstance(row, dict)]
    if len(observed_names) != len(set(observed_names)):
        raise ValueError("native-mcp-inventory-duplicate")
    if not (required | plugin_servers).issubset(set(observed_names)):
        raise ValueError("native-mcp-enabled-inventory-incomplete")
    for server in inventory["data"]:
        if not isinstance(server, dict):
            raise ValueError("native-mcp-enabled-capability-unavailable")
        name, tools = server.get("name"), server.get("tools")
        if not isinstance(name, str) or not isinstance(tools, dict) or server.get("toolsError") is not None:
            raise ValueError("native-mcp-enabled-capability-unavailable")
        status = server.get("runtimeStatus")
        if status not in ("connected", "disabled") or name in required and status != "connected":
            raise ValueError("native-mcp-enabled-runtime-unavailable")
        if name == controlled_subject:
            if exposure is None and status != "disabled":
                raise ValueError("native-mcp-controlled-disabled-route-active")
            if exposure is not None and (server.get("pluginId") is not None or server.get("httpOrigin") is not None):
                raise ValueError("native-mcp-controlled-runtime-transport-mismatch")
            continue
        info = server.get("serverInfo")
        tool_names = [*tools, *(row.get("name") for row in tools.values() if isinstance(row, dict) and isinstance(row.get("name"), str))]
        if controlled_subject and status == "connected" and ((isinstance(info, dict) and controlled_subject.lower() in str(info.get("name", "")).lower()) or any(str(tool).lower().rsplit("__", 1)[-1].startswith(controlled_subject.lower() + "_") for tool in tool_names)):
            raise ValueError("native-mcp-uncontrolled-subject-route")
        servers.append({"name": name, "plugin_id": server.get("pluginId"), "runtime_status": status, "tools": sorted(tools), "capability_digest": _digest(_stable(server, context))})
    return {"schema": "evalshot.native-codex-plugins.v1", "state": "observed", "plugins": sorted(public, key=lambda row: row["id"]), "non_subject_servers": sorted(servers, key=lambda row: row["name"]), "native_capability_digest": _digest(capability_bytes), "non_subject_capability_digest": _digest(servers), "source_equivalence": "unknown", "selection": selection_observation, "source_selection": source_selection_observation, "requested_but_not_native_selected": not_selected}
