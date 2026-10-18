#!/usr/bin/env node
'use strict';

const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const RUNTIME_SCHEMA = 'agents-cookbook-opencode-runtime/v3';
const BENCHMARK_COMPACTION_RESERVE = 10000;
const SECRET_KEYS = new Set([
  'api_key',
  'apikey',
  'authorization',
  'cookie',
  'credential',
  'credentials',
  'password',
  'secret',
  'token',
]);

function runCommand(command, args, options = {}) {
  const executable = Array.isArray(command) ? command[0] : command;
  const argv = Array.isArray(command) ? [...command.slice(1), ...args] : args;
  const result = spawnSync(executable, argv, {
    cwd: options.cwd || process.cwd(),
    env: { ...process.env, ...(options.env || {}) },
    encoding: 'utf8',
    maxBuffer: options.maxBuffer || 50 * 1024 * 1024,
  });

  return {
    status: typeof result.status === 'number' ? result.status : 1,
    stdout: result.stdout || '',
    stderr: result.stderr || '',
    error: result.error ? String(result.error.message || result.error) : null,
    signal: result.signal || null,
  };
}

function runCommandToFile(command, args, options = {}) {
  const executable = Array.isArray(command) ? command[0] : command;
  const argv = Array.isArray(command) ? [...command.slice(1), ...args] : args;
  const directory = fs.mkdtempSync(
    path.join(os.tmpdir(), 'agents-cookbook-opencode-export-'),
  );
  const outputPath = path.join(directory, 'stdout.json');
  const output = fs.openSync(outputPath, 'w');
  try {
    const result = spawnSync(executable, argv, {
      cwd: options.cwd || process.cwd(),
      env: { ...process.env, ...(options.env || {}) },
      encoding: 'utf8',
      stdio: ['ignore', output, 'pipe'],
      maxBuffer: options.maxBuffer || 50 * 1024 * 1024,
    });
    const stdout = fs.readFileSync(outputPath, 'utf8');
    return {
      status: typeof result.status === 'number' ? result.status : 1,
      stdout,
      stderr: result.stderr || '',
      error: result.error ? String(result.error.message || result.error) : null,
      signal: result.signal || null,
    };
  } finally {
    fs.closeSync(output);
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function readJsonText(raw, label) {
  const parsed = JSON.parse(raw.trim());
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(`${label}: expected one JSON object`);
  }
  return parsed;
}

function extractFinalAnswer(exportOutput) {
  const data = readJsonText(exportOutput, 'opencode export');
  if (!Array.isArray(data.messages)) {
    throw new Error('opencode export: messages must be an array');
  }
  const messages = data.messages;
  const assistantMessages = messages.filter(
    (message) =>
      message &&
      message.info &&
      message.info.role === 'assistant',
  );
  if (assistantMessages.length === 0) {
    throw new Error('opencode export: no assistant messages found');
  }

  const finalMessage = assistantMessages[assistantMessages.length - 1];
  const parts = Array.isArray(finalMessage.parts) ? finalMessage.parts : [];
  const lastTool = parts.findLastIndex((part) => part && part.type === 'tool');
  const text = parts.slice(lastTool + 1)
    .filter((part) => part && part.type === 'text' && typeof part.text === 'string')
    .map((part) => part.text)
    .join('\n')
    .trim();
  if (!text) {
    throw new Error('opencode export: terminal assistant message has no final text');
  }

  return { data, text };
}

function normalizeKey(key) {
  return String(key).toLowerCase().replaceAll('-', '_');
}

function isSecretKey(key) {
  const normalized = normalizeKey(key);
  return (
    SECRET_KEYS.has(normalized) ||
    normalized.endsWith('_api_key') ||
    normalized.endsWith('_token') ||
    normalized.endsWith('_secret') ||
    normalized.endsWith('_password')
  );
}

function sanitize(value, key = '') {
  if (isSecretKey(key)) {
    return '<redacted>';
  }
  if (Array.isArray(value)) {
    return value.map((item) => sanitize(item));
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([childKey, child]) => [
          childKey,
          sanitize(child, childKey),
        ]),
    );
  }
  return value;
}

function sortValue(value) {
  if (Array.isArray(value)) {
    return value.map(sortValue);
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, child]) => [key, sortValue(child)]),
    );
  }
  return value;
}

function canonicalJson(value) {
  return `${JSON.stringify(sortValue(value))}\n`;
}

function sha256Text(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

function configuredModel(config, agentName = null) {
  const agent =
    agentName &&
    config &&
    config.agent &&
    typeof config.agent === 'object' &&
    config.agent[agentName] &&
    typeof config.agent[agentName] === 'object'
      ? config.agent[agentName]
      : null;
  if (agent && typeof agent.model === 'string' && agent.model) {
    return agent.model;
  }
  return typeof config.model === 'string' && config.model
    ? config.model
    : null;
}

function providerFromModel(model) {
  if (typeof model !== 'string' || !model.includes('/')) {
    return null;
  }
  return model.split('/', 1)[0];
}

function inspectMcp(config) {
  const mcp =
    config && config.mcp && typeof config.mcp === 'object'
      ? config.mcp
      : {};
  const nested =
    mcp.servers && typeof mcp.servers === 'object'
      ? mcp.servers
      : null;
  const servers = nested || mcp;
  const entries = Object.entries(servers)
    .filter(([, value]) => value && typeof value === 'object')
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([name, value]) => ({
      name,
      enabled:
        value.enabled !== false &&
        value.disabled !== true,
    }));
  return {
    shape: nested ? 'nested-servers' : 'flat',
    servers: entries,
  };
}

function inspectConfig(config, agentName = null) {
  const model = configuredModel(config, agentName);
  const mcp = inspectMcp(config);
  const [provider, modelId] = typeof model === 'string'
    ? model.split('/', 2) : [];
  const modelLimits = config.provider?.[provider]?.models?.[modelId]?.limit;
  return {
    model,
    provider: providerFromModel(model),
    model_limits: modelLimits && typeof modelLimits === 'object'
      ? { context: modelLimits.context, output: modelLimits.output }
      : null,
    config_sha256: sha256Text(canonicalJson(sanitize(config))),
    mcp_shape: mcp.shape,
    mcp_servers: mcp.servers,
  };
}

function mergeObjects(base, overlay) {
  const result = { ...base };
  for (const [key, value] of Object.entries(overlay)) {
    if (
      value && typeof value === 'object' && !Array.isArray(value) &&
      result[key] && typeof result[key] === 'object' && !Array.isArray(result[key])
    ) {
      result[key] = mergeObjects(result[key], value);
    } else {
      result[key] = value;
    }
  }
  return result;
}

function inlineConfig(env) {
  const raw = env.OPENCODE_CONFIG_CONTENT;
  if (!raw) return {};
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error('OPENCODE_CONFIG_CONTENT is not valid JSON');
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('OPENCODE_CONFIG_CONTENT must be a JSON object');
  }
  return parsed;
}

function readBenchmarkExposure(filePath, selectedSubject, repoDir) {
  if (!selectedSubject) {
    if (filePath) {
      throw new Error('bare benchmark must not provide a subject exposure');
    }
    return null;
  }
  if (!filePath) {
    throw new Error(`benchmark subject ${selectedSubject} has no exposure file`);
  }

  let parsed;
  try {
    parsed = JSON.parse(fs.readFileSync(path.resolve(filePath), 'utf8'));
  } catch (error) {
    throw new Error(
      `benchmark subject exposure cannot be read: ${error.message || error}`,
    );
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('benchmark subject exposure must be a JSON object');
  }
  if (parsed.name !== selectedSubject) {
    throw new Error(
      `benchmark subject exposure names ${parsed.name || 'nothing'}, expected ${selectedSubject}`,
    );
  }
  if (
    !Array.isArray(parsed.command) ||
    parsed.command.length === 0 ||
    parsed.command.some((value) => typeof value !== 'string' || !value)
  ) {
    throw new Error('benchmark subject exposure command must be non-empty strings');
  }
  if (typeof parsed.cwd !== 'string' || !parsed.cwd) {
    throw new Error('benchmark subject exposure cwd is required');
  }
  const cwd = canonicalPath(parsed.cwd);
  const workspace = canonicalPath(repoDir);
  if (cwd !== workspace) {
    throw new Error(
      `benchmark subject exposure cwd resolves outside trial workspace: ${cwd}`,
    );
  }
  const environment =
    parsed.environment && typeof parsed.environment === 'object' &&
    !Array.isArray(parsed.environment)
      ? parsed.environment
      : {};
  if (
    Object.entries(environment).some(
      ([key, value]) =>
        typeof key !== 'string' || !key ||
        typeof value !== 'string',
    )
  ) {
    throw new Error('benchmark subject exposure environment must contain strings');
  }
  const semanticIdentity =
    parsed.semantic_identity && typeof parsed.semantic_identity === 'object' &&
    !Array.isArray(parsed.semantic_identity)
      ? parsed.semantic_identity
      : {};

  return {
    name: selectedSubject,
    command: [...parsed.command],
    cwd,
    environment: { ...environment },
    semantic_identity: semanticIdentity,
  };
}

function mcpEntries(config, shape) {
  const mcp = config.mcp || {};
  return shape === 'nested-servers' ? (mcp.servers || {}) : mcp;
}

function benchmarkScratch(repoDir, env, controlRoot) {
  if (!controlRoot || !env.TMPDIR) {
    throw new Error('benchmark OpenCode trial scratch directory is unavailable');
  }
  const control = fs.realpathSync.native(controlRoot);
  const scratch = fs.realpathSync.native(env.TMPDIR);
  const relativeToWorkspace = path.relative(
    fs.realpathSync.native(repoDir), scratch,
  );
  if (control !== path.resolve(controlRoot) ||
      scratch !== path.resolve(env.TMPDIR) ||
      path.relative(control, scratch) !== path.join('_environment', 'tmp') ||
      !(relativeToWorkspace === '..' ||
        relativeToWorkspace.startsWith(`..${path.sep}`))) {
    throw new Error('benchmark OpenCode trial scratch directory is outside control root');
  }
  if (env.BENCHMARK_PRIVATE_TMP === '1') {
    const privateTmp = fs.statSync('/tmp');
    const trialScratch = fs.statSync(scratch);
    if (privateTmp.dev !== trialScratch.dev ||
        privateTmp.ino !== trialScratch.ino) {
      throw new Error('benchmark private /tmp is not bound to trial scratch');
    }
  }
  return scratch;
}

function benchmarkOverlay(config, subjectExposure, agentName, scratch, privateTmp) {
  const inspected = inspectMcp(config);
  const nested = inspected.shape === 'nested-servers';
  const source = mcpEntries(config, inspected.shape);
  const selectedSubject = subjectExposure?.name || null;
  const servers = {};
  const tools = {};

  for (const [name, value] of Object.entries(source)) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) continue;
    if (name === selectedSubject) continue;
    servers[name] = nested
      ? { ...value, disabled: true }
      : { ...value, enabled: false };
    tools[`${name}_*`] = false;
  }

  let selectedDefinition = null;
  if (subjectExposure) {
    const environment = subjectExposure.environment || {};
    const definition = {
      type: 'local',
      command: [...subjectExposure.command],
      cwd: subjectExposure.cwd,
      ...(Object.keys(environment).length > 0
        ? { environment: { ...environment } }
        : {}),
    };
    selectedDefinition = nested
      ? { ...definition, disabled: false }
      : { ...definition, enabled: true };
    servers[selectedSubject] = selectedDefinition;
    tools[`${selectedSubject}_*`] = true;
  }

  const nativeDefinition =
    selectedSubject !== null && Object.hasOwn(source, selectedSubject)
      ? source[selectedSubject]
      : null;
  const nativeServerShadowed = nativeDefinition !== null;
  const nativeServerConflict =
    nativeServerShadowed &&
    nativeDefinition &&
    nativeDefinition.enabled !== false &&
    nativeDefinition.disabled !== true;

  return {
    selected: selectedSubject,
    shape: inspected.shape,
    selected_definition: selectedDefinition,
    native_server_shadowed: nativeServerShadowed,
    native_server_conflict: Boolean(nativeServerConflict),
    subject_exposure_sha256: subjectExposure
      ? sha256Text(canonicalJson(subjectExposure))
      : null,
    config: {
      compaction: { auto: true, reserved: BENCHMARK_COMPACTION_RESERVE },
      permission: {
        external_directory: {
          [scratch]: 'allow',
          [`${scratch}/**`]: 'allow',
          ...(privateTmp ? { '/tmp': 'allow', '/tmp/**': 'allow' } : {}),
        },
      },
      mcp: nested ? { servers } : servers,
      ...(nested ? {} : {
        tools,
        agent: { [agentName]: { tools } },
      }),
    },
  };
}

function verifyBenchmarkConfig(
  base,
  effective,
  overlay,
  subjectExposure,
  agentName,
) {
  const original = inspectConfig(base, agentName);
  const resolved = inspectConfig(effective, agentName);
  if (
    !effective.compaction ||
    effective.compaction.auto !== true ||
    effective.compaction.reserved !== BENCHMARK_COMPACTION_RESERVE
  ) {
    throw new Error(
      'benchmark OpenCode auto-compaction reserve did not resolve',
    );
  }
  for (const [pattern, action] of Object.entries(
    overlay.config.permission.external_directory,
  )) {
    if (effective.permission?.external_directory?.[pattern] !== action) {
      throw new Error('benchmark OpenCode trial scratch permission did not resolve');
    }
  }
  if (original.model !== resolved.model || original.provider !== resolved.provider) {
    throw new Error('benchmark overlay changed native OpenCode model/provider');
  }

  const baseServers = mcpEntries(base, overlay.shape);
  const servers = mcpEntries(effective, overlay.shape);
  const expectedNames = new Set(Object.keys(baseServers));
  if (overlay.selected) expectedNames.add(overlay.selected);
  if (
    canonicalJson(Object.keys(servers).sort()) !==
    canonicalJson([...expectedNames].sort())
  ) {
    throw new Error('benchmark overlay resolved an unexpected MCP server set');
  }

  for (const [name, nativeDefinition] of Object.entries(baseServers)) {
    if (name === overlay.selected) continue;
    const expected = overlay.shape === 'nested-servers'
      ? { ...nativeDefinition, disabled: true }
      : { ...nativeDefinition, enabled: false };
    if (canonicalJson(servers[name]) !== canonicalJson(expected)) {
      throw new Error(`benchmark overlay changed native MCP server ${name}`);
    }
  }

  if (overlay.selected) {
    if (!subjectExposure || subjectExposure.name !== overlay.selected) {
      throw new Error('benchmark selected subject exposure is missing');
    }
    const observedSelected = { ...servers[overlay.selected] };
    const expectedSelected = { ...overlay.selected_definition };
    if (overlay.shape === 'nested-servers') {
      delete observedSelected.enabled;
      delete expectedSelected.enabled;
    } else {
      delete observedSelected.disabled;
      delete expectedSelected.disabled;
    }
    if (canonicalJson(observedSelected) !== canonicalJson(expectedSelected)) {
      throw new Error(
        `effective benchmark MCP definition changed for ${overlay.selected}`,
      );
    }
  }

  if (overlay.shape === 'flat') {
    const tools = effective.tools || {};
    const agentTools = ((effective.agent || {})[agentName] || {}).tools || {};
    for (const [name, expected] of Object.entries(overlay.config.tools)) {
      if (tools[name] !== expected || agentTools[name] !== expected) {
        throw new Error(`benchmark MCP tool gate did not resolve for ${name}`);
      }
    }
  }
  return resolved;
}

function sha256File(filePath) {
  return crypto.createHash('sha256')
    .update(fs.readFileSync(filePath))
    .digest('hex');
}

function normalizedSubjectConfigurationSha256(
  server,
  selectedSubject,
  effectiveCwd,
  repoDir,
) {
  if (
    selectedSubject !== 'enola' ||
    !Array.isArray(server.command) ||
    server.command.length !== 2 ||
    typeof server.command[1] !== 'string'
  ) {
    return null;
  }
  try {
    const configPath = path.resolve(effectiveCwd, server.command[1]);
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    if (!config || typeof config !== 'object' || Array.isArray(config)) {
      return null;
    }
    const workspace = canonicalPath(repoDir);
    const repo = canonicalPath(config.repo);
    const normalized = {
      ...config,
      repo: repo === workspace ? '<trial-workspace>' : repo,
    };
    return sha256Text(canonicalJson(normalized));
  } catch {
    return null;
  }
}

function resolveExecutable(command, cwd, env) {
  if (typeof command !== 'string' || !command) return null;
  const hasSeparator = command.includes('/') || command.includes('\\');
  const candidates = [];
  if (path.isAbsolute(command)) {
    candidates.push(command);
  } else if (hasSeparator) {
    candidates.push(path.resolve(cwd, command));
  } else {
    const searchPath = (env && env.PATH) || process.env.PATH || '';
    for (const directory of searchPath.split(path.delimiter)) {
      if (directory) candidates.push(path.join(directory, command));
    }
  }
  for (const candidate of candidates) {
    try {
      const resolved = fs.realpathSync.native(candidate);
      if (fs.statSync(resolved).isFile()) return resolved;
    } catch {
      // Keep searching PATH candidates.
    }
  }
  return null;
}

function nativeSubjectExecutableIdentity(
  config,
  shape,
  selectedSubject,
  repoDir,
  env,
) {
  if (!selectedSubject) return null;
  const server = mcpEntries(config, shape)[selectedSubject];
  if (!server || !Array.isArray(server.command) || !server.command[0]) {
    return {
      verified: false,
      subject: selectedSubject,
      command: null,
      executable_path: null,
      executable_sha256: null,
      configuration_sha256: null,
      reason_code: 'benchmark-subject-executable-unresolved',
    };
  }
  const effectiveCwd = canonicalPath(
    server.cwd ? path.resolve(repoDir, server.cwd) : repoDir,
  );
  const resolved = resolveExecutable(
    server.command[0],
    effectiveCwd,
    env,
  );
  if (!resolved) {
    return {
      verified: false,
      subject: selectedSubject,
      command: path.basename(String(server.command[0])),
      executable_path: null,
      executable_sha256: null,
      configuration_sha256: null,
      reason_code: 'benchmark-subject-executable-unresolved',
    };
  }
  return {
    verified: true,
    subject: selectedSubject,
    command: path.basename(resolved),
    executable_path: resolved,
    executable_sha256: sha256File(resolved),
    configuration_sha256: normalizedSubjectConfigurationSha256(
      server,
      selectedSubject,
      effectiveCwd,
      repoDir,
    ),
    reason_code: null,
  };
}

function canonicalPath(value) {
  const resolved = path.resolve(value);
  try {
    return fs.realpathSync.native(resolved);
  } catch {
    return resolved;
  }
}

function commandWorkspace(command, flag, cwd) {
  if (!Array.isArray(command)) return null;
  const matches = [];
  for (let index = 0; index < command.length; index += 1) {
    const value = command[index];
    if (value === flag) {
      const next = command[index + 1];
      if (typeof next !== 'string' || !next || next.startsWith('-')) return null;
      matches.push(next);
    }
    if (typeof value === 'string' && value.startsWith(`${flag}=`)) {
      const next = value.slice(flag.length + 1);
      if (!next) return null;
      matches.push(next);
    }
  }
  return matches.length === 1
    ? canonicalPath(path.resolve(cwd, matches[0]))
    : null;
}

function verifyWorkspaceBinding(config, shape, selectedSubject, repoDir) {
  const workspace = canonicalPath(repoDir);
  if (!selectedSubject) {
    return {
      verified: true,
      subject: null,
      method: 'bare-no-subject',
      workspace,
      effective_cwd: workspace,
      reason: null,
      reason_code: null,
    };
  }

  const server = mcpEntries(config, shape)[selectedSubject];
  if (!server || typeof server !== 'object' || Array.isArray(server)) {
    return {
      verified: false,
      subject: selectedSubject,
      method: null,
      workspace,
      effective_cwd: null,
      reason: `native MCP server ${selectedSubject} is not configured`,
      reason_code: 'native-server-missing',
    };
  }
  if (server.type !== 'local' || !Array.isArray(server.command)) {
    return {
      verified: false,
      subject: selectedSubject,
      method: null,
      workspace,
      effective_cwd: null,
      reason: `native MCP server ${selectedSubject} is not a local command`,
      reason_code: 'native-server-not-local',
    };
  }

  const effectiveCwd = canonicalPath(
    server.cwd ? path.resolve(repoDir, server.cwd) : repoDir,
  );
  const executable = typeof server.command[0] === 'string'
    ? path.basename(server.command[0]).toLowerCase()
    : '';

  if (selectedSubject === 'hashmarks') {
    if (!['hashmarks', 'hashmarks.exe'].includes(executable) ||
        server.command.at(-1) !== 'mcp' ||
        server.command.slice(1, -1).includes('--')) {
      return {
        verified: false,
        subject: selectedSubject,
        method: null,
        workspace,
        effective_cwd: effectiveCwd,
        reason: 'native Hashmarks MCP command form is unverified',
        reason_code: 'hashmarks-command-unverifiable',
      };
    }
    const bound = commandWorkspace(
      server.command,
      '--workspace',
      effectiveCwd,
    );
    if (!bound) {
      return {
        verified: false,
        subject: selectedSubject,
        method: null,
        workspace,
        effective_cwd: effectiveCwd,
        reason: 'native Hashmarks MCP has no unique explicit --workspace binding',
        reason_code: 'hashmarks-workspace-unverifiable',
      };
    }
    return {
      verified: bound === workspace,
      subject: selectedSubject,
      method: 'hashmarks-explicit-workspace',
      command_executable: server.command[0],
      workspace,
      effective_cwd: effectiveCwd,
      resolved_workspace: bound,
      reason: bound === workspace
        ? null
        : `native Hashmarks workspace resolves outside trial workspace: ${bound}`,
      reason_code: bound === workspace
        ? null
        : 'hashmarks-workspace-outside-trial',
    };
  }

  if (selectedSubject === 'enola') {
    if (!['enola', 'enola.exe'].includes(executable)) {
      return {
        verified: false,
        subject: selectedSubject,
        method: null,
        command_executable: server.command[0],
        workspace,
        effective_cwd: effectiveCwd,
        reason: 'benchmark Enola MCP executable is unverified',
        reason_code: 'enola-command-unverifiable',
      };
    }
    if (effectiveCwd !== workspace) {
      return {
        verified: false,
        subject: selectedSubject,
        method: null,
        command_executable: server.command[0],
        workspace,
        effective_cwd: effectiveCwd,
        reason: `benchmark Enola MCP cwd resolves outside trial workspace: ${effectiveCwd}`,
        reason_code: 'enola-workspace-outside-trial',
      };
    }
    if (server.command.length === 1) {
      return {
        verified: true,
        subject: selectedSubject,
        method: 'enola-default-repository-from-mcp-cwd',
        command_executable: server.command[0],
        workspace,
        effective_cwd: effectiveCwd,
        reason: null,
        reason_code: null,
      };
    }
    if (server.command.length !== 2 || typeof server.command[1] !== 'string') {
      return {
        verified: false,
        subject: selectedSubject,
        method: null,
        command_executable: server.command[0],
        workspace,
        effective_cwd: effectiveCwd,
        reason: 'benchmark Enola MCP command form is unverified',
        reason_code: 'enola-command-unverifiable',
      };
    }
    try {
      const configPath = path.resolve(effectiveCwd, server.command[1]);
      const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
      const bound = canonicalPath(config.repo);
      return {
        verified: bound === workspace,
        subject: selectedSubject,
        method: 'enola-explicit-config-repository',
        command_executable: server.command[0],
        workspace,
        effective_cwd: effectiveCwd,
        resolved_workspace: bound,
        reason: bound === workspace
          ? null
          : `benchmark Enola config resolves outside trial workspace: ${bound}`,
        reason_code: bound === workspace
          ? null
          : 'enola-workspace-outside-trial',
      };
    } catch {
      return {
        verified: false,
        subject: selectedSubject,
        method: null,
        command_executable: server.command[0],
        workspace,
        effective_cwd: effectiveCwd,
        reason: 'benchmark Enola MCP config cannot prove repository binding',
        reason_code: 'enola-workspace-unverifiable',
      };
    }
  }

  return {
    verified: false,
    subject: selectedSubject,
    method: null,
    workspace,
    effective_cwd: effectiveCwd,
    reason: `no workspace-binding verifier for native MCP subject ${selectedSubject}`,
    reason_code: 'workspace-verifier-unavailable',
  };
}

function cleanDiagnosticText(value, maxBytes = 1200) {
  const cleaned = String(value || '')
    .replace(/\x1b\[[0-9;]*m/g, '')
    .trim();
  if (Buffer.byteLength(cleaned, 'utf8') <= maxBytes) return cleaned;
  let result = '';
  for (const character of cleaned) {
    if (Buffer.byteLength(result + character, 'utf8') > maxBytes) break;
    result += character;
  }
  return `${result}…`;
}

function selectedMcpLines(output, selectedSubject) {
  const lines = String(output || '').split(/\r?\n/);
  const normalized = lines.map((line) =>
    line.replace(/\x1b\[[0-9;]*m/g, '')
  );
  const index = normalized.findIndex((line) => {
    const tokens = line.trim()
      .split(/\s+/)
      .map((token) => token.replace(/:$/, ''));
    return tokens.includes(selectedSubject);
  });
  if (index < 0) return '';

  const selectedLine = normalized[index].trim();
  const treeMode = /^[●○◉◆◇■□]/u.test(selectedLine);
  if (!treeMode) {
    return cleanDiagnosticText(selectedLine, 4096);
  }

  const block = [normalized[index].trimEnd()];
  for (let cursor = index + 1; cursor < normalized.length; cursor += 1) {
    const raw = normalized[cursor];
    const trimmed = raw.trim();

    if (/^[●○◉◆◇■□]/u.test(trimmed)) break;
    if (!trimmed) {
      block.push('');
      continue;
    }

    block.push(raw.trimEnd());
  }

  return cleanDiagnosticText(block.join('\n'), 4096);
}

function connectedMcp(output, selectedSubject) {
  return output.split(/\r?\n/).some((line) => {
    const tokens = line.replace(/\x1b\[[0-9;]*m/g, '').trim()
      .split(/\s+/).map((token) => token.replace(/:$/, ''));
    const index = tokens.indexOf(selectedSubject);
    return index >= 0 && tokens[index + 1] === 'connected';
  });
}

function mcpConnectionFailure(connection, selectedSubject) {
  const details = [`exit=${connection.status}`];
  const selected = selectedMcpLines(connection.stdout, selectedSubject);
  if (selected) {
    details.push(`mcp-list=${JSON.stringify(selected)}`);
  } else {
    details.push('mcp-list=selected server missing');
  }
  const stderr = cleanDiagnosticText(connection.stderr);
  if (stderr) details.push(`stderr=${JSON.stringify(stderr)}`);
  const error = cleanDiagnosticText(connection.error);
  if (error) details.push(`error=${JSON.stringify(error)}`);
  if (connection.signal) details.push(`signal=${connection.signal}`);
  return (
    `benchmark OpenCode MCP connection ${selectedSubject} is not connected; ` +
    details.join('; ')
  );
}

function commandPrefix(pure) {
  return pure ? ['--pure'] : [];
}

function effectiveEnv({ homeDir, env }) {
  const result = { ...(env || {}) };
  if (homeDir) {
    result.HOME = homeDir;
  }
  return result;
}

async function findSessionId({
  opencodeBin = process.env.OPENCODE_BIN || 'opencode',
  repoDir,
  title,
  startedAt = 0,
  homeDir = '',
  env = {},
  attempts = 12,
  delayMs = 500,
  pure = true,
}) {
  const commandEnv = effectiveEnv({ homeDir, env });
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const sessionList = runCommand(
      opencodeBin,
      [
        ...commandPrefix(pure),
        'session',
        'list',
        '--format',
        'json',
        '--max-count',
        '20',
      ],
      { cwd: repoDir, env: commandEnv },
    );

    if (sessionList.status === 0) {
      try {
        const sessions = JSON.parse(sessionList.stdout);
        if (Array.isArray(sessions) && sessions.length > 0) {
          const exactMatch = sessions.find(
            (session) =>
              session &&
              session.title === title &&
              session.directory === repoDir &&
              typeof session.updated === 'number' &&
              Number.isFinite(session.updated) &&
              session.updated >= startedAt,
          );
          if (exactMatch && exactMatch.id) {
            return exactMatch.id;
          }

        }
      } catch {
        // Retry until OpenCode persists a readable session list.
      }
    }

    if (attempt < attempts - 1) {
      await sleep(delayMs);
    }
  }
  return '';
}

function exportSession({
  opencodeBin = process.env.OPENCODE_BIN || 'opencode',
  repoDir,
  sessionId,
  homeDir = '',
  env = {},
  pure = true,
}) {
  return runCommandToFile(
    opencodeBin,
    [...commandPrefix(pure), 'export', sessionId],
    {
      cwd: repoDir,
      env: effectiveEnv({ homeDir, env }),
    },
  );
}

function deleteSession({
  opencodeBin = process.env.OPENCODE_BIN || 'opencode',
  repoDir,
  sessionId,
  homeDir = '',
  env = {},
  pure = true,
}) {
  return runCommand(
    opencodeBin,
    [...commandPrefix(pure), 'session', 'delete', sessionId],
    {
      cwd: repoDir,
      env: effectiveEnv({ homeDir, env }),
      maxBuffer: 2 * 1024 * 1024,
    },
  );
}

function resolveNativeConfig({
  opencodeBin = process.env.OPENCODE_BIN || 'opencode',
  repoDir,
  env = {},
  pure = true,
  agentName = null,
}) {
  const resolved = readNativeConfig({ opencodeBin, repoDir, env, pure, agentName });
  const { config, ...safe } = resolved;
  return safe;
}

function readNativeConfig({
  opencodeBin = process.env.OPENCODE_BIN || 'opencode',
  repoDir,
  env = {},
  pure = true,
  agentName = null,
}) {
  const command = runCommand(
    opencodeBin,
    [...commandPrefix(pure), 'debug', 'config'],
    { cwd: repoDir, env },
  );
  const commandEvidence = {
    status: command.status,
    stderr: command.stderr,
    error: command.error,
    signal: command.signal,
  };
  if (command.status !== 0) {
    return {
      status: 'failed',
      command: commandEvidence,
      inspection: null,
      config: null,
    };
  }
  try {
    const config = readJsonText(
      command.stdout,
      'opencode debug config',
    );
    return {
      status: 'completed',
      command: commandEvidence,
      inspection: inspectConfig(config, agentName),
      config,
    };
  } catch (error) {
    return {
      status: 'failed',
      command: commandEvidence,
      inspection: null,
      config: null,
      parse_error: String(error.message || error),
    };
  }
}

function prepareBenchmarkConfig({
  opencodeBin = process.env.OPENCODE_BIN || 'opencode',
  repoDir,
  env = process.env,
  selectedSubject = null,
  subjectExposure = null,
  pure = true,
  probe = true,
  agentName,
  controlRoot,
}) {
  if (typeof agentName !== 'string' || !agentName) {
    throw new Error('benchmark OpenCode agent name is required');
  }
  const base = readNativeConfig({
    opencodeBin,
    repoDir,
    env,
    pure,
    agentName,
  });
  if (base.status !== 'completed') {
    return {
      status: 'failed',
      reason: 'native OpenCode config could not be resolved',
      native_config_probe: {
        status: base.command?.status ?? null,
        error: base.command?.error ?? null,
        signal: base.command?.signal ?? null,
        stderr_bytes: typeof base.command?.stderr === 'string'
          ? Buffer.byteLength(base.command.stderr)
          : null,
        stderr_sha256: typeof base.command?.stderr === 'string'
          ? crypto.createHash('sha256').update(base.command.stderr).digest('hex')
          : null,
        parse_error: base.parse_error ?? null,
      },
      inspection: base.inspection,
    };
  }
  try {
    if (selectedSubject && (!subjectExposure || subjectExposure.name !== selectedSubject)) {
      throw new Error(`benchmark subject ${selectedSubject} has no matching exposure`);
    }
    if (!selectedSubject && subjectExposure) {
      throw new Error('bare benchmark must not expose a subject');
    }
    const limits = base.inspection.model_limits;
    if (!limits || !Number.isSafeInteger(limits.context) ||
        !Number.isSafeInteger(limits.output) ||
        limits.output <= 0 ||
        limits.context <= limits.output + BENCHMARK_COMPACTION_RESERVE) {
      throw new Error(
        'native OpenCode model limits leave no usable prompt budget; '
        + 'correct provider model limit.context and limit.output before running',
      );
    }
    const scratch = benchmarkScratch(repoDir, env, controlRoot);
    const privateTmp = env.BENCHMARK_PRIVATE_TMP === '1';
    const overlay = benchmarkOverlay(
      base.config, subjectExposure, agentName, scratch, privateTmp,
    );
    const content = mergeObjects(inlineConfig(env), overlay.config);
    const commandEnv = {
      ...env,
      OPENCODE_CONFIG_CONTENT: JSON.stringify(content),
    };
    const effective = readNativeConfig({
      opencodeBin,
      repoDir,
      env: commandEnv,
      pure,
      agentName,
    });
    if (effective.status !== 'completed') {
      throw new Error('OpenCode rejected the composed benchmark configuration');
    }
    const effectiveInspection = verifyBenchmarkConfig(
      base.config,
      effective.config,
      overlay,
      subjectExposure,
      agentName,
    );
    const workspaceBinding = verifyWorkspaceBinding(
      effective.config,
      overlay.shape,
      selectedSubject,
      repoDir,
    );
    const subjectExecutable = nativeSubjectExecutableIdentity(
      effective.config,
      overlay.shape,
      selectedSubject,
      repoDir,
      commandEnv,
    );
    if (
      selectedSubject &&
      (!subjectExecutable || subjectExecutable.verified !== true)
    ) {
      throw new Error(
        `benchmark MCP executable for ${selectedSubject} cannot be identified`,
      );
    }
    if (probe && selectedSubject && workspaceBinding.verified) {
      const connection = runCommand(
        opencodeBin,
        [...commandPrefix(pure), 'mcp', 'list'],
        { cwd: repoDir, env: commandEnv },
      );
      const connected = connectedMcp(connection.stdout, selectedSubject);
      if (connection.status !== 0 || !connected) {
        return {
          status: 'failed',
          reason: mcpConnectionFailure(connection, selectedSubject),
          failure_stage: 'mcp-connection',
          inspection: base.inspection,
        };
      }
    }
    return {
      status: 'completed',
      inspection: base.inspection,
      effective_inspection: effectiveInspection,
      base_config_source: 'fresh',
      selected_server: overlay.selected,
      workspace_binding: workspaceBinding,
      native_subject_identity: subjectExecutable,
      overlay_identity: {
        shape: overlay.shape,
        compaction_auto: true,
        compaction_reserved: BENCHMARK_COMPACTION_RESERVE,
        scratch_path: scratch,
        private_tmp: privateTmp,
        selected_subject: selectedSubject,
        subject_definition_source:
          selectedSubject ? 'benchmark-subject-exposure' : null,
        native_server_shadowed: overlay.native_server_shadowed,
        native_server_conflict: overlay.native_server_conflict,
        subject_exposure_sha256: overlay.subject_exposure_sha256,
      },
      environment: commandEnv,
    };
  } catch (error) {
    return {
      status: 'failed',
      reason: String(error.message || error),
      inspection: base.inspection,
    };
  }
}

function runtimeIdentitySha256(identity) {
  return identity && typeof identity === 'object' && !Array.isArray(identity)
    ? sha256Text(canonicalJson(identity))
    : null;
}

function executableIdentitySha256(command, repoDir, env) {
  const executable = Array.isArray(command) ? command[0] : command;
  const resolved = resolveExecutable(executable, repoDir, env);
  if (!resolved) return null;
  try {
    return sha256File(resolved);
  } catch {
    return null;
  }
}

function benchmarkAuthorityFailure({
  prepared,
  options,
  selectedSubject,
  opencodeBin,
  repoDir,
  env,
}) {
  if (!prepared || prepared.status !== 'completed') {
    return prepared?.reason || 'benchmark OpenCode authority could not be resolved';
  }
  if (prepared.workspace_binding?.verified !== true) {
    return 'benchmark workspace binding changed';
  }
  if (
    prepared.inspection?.config_sha256 !== options['native-config-sha256']
  ) {
    return 'native OpenCode configuration changed';
  }
  if (
    executableIdentitySha256(opencodeBin, repoDir, env) !==
      options['opencode-executable-sha256']
  ) {
    return 'OpenCode executable authority changed';
  }
  if (!selectedSubject) return null;
  if (
    prepared.overlay_identity?.subject_exposure_sha256 !==
      options['subject-exposure-sha256']
  ) {
    return 'benchmark subject exposure changed';
  }
  if (
    runtimeIdentitySha256(prepared.native_subject_identity) !==
      options['subject-runtime-sha256']
  ) {
    return 'benchmark subject runtime authority changed';
  }
  return null;
}

function runSession({
  opencodeBin = process.env.OPENCODE_BIN || 'opencode',
  repoDir,
  agent = 'build',
  title,
  prompt,
  homeDir = '',
  env = {},
  pure = true,
}) {
  const startedAt = Date.now();
  const command = runCommand(
    opencodeBin,
    [
      ...commandPrefix(pure),
      'run',
      '--dir',
      repoDir,
      '--agent',
      agent,
      '--title',
      title,
      '--format',
      'json',
      prompt,
    ],
    {
      cwd: repoDir,
      env: effectiveEnv({ homeDir, env }),
    },
  );
  return { startedAt, command };
}

function recoveredContextOverflow(run, exported, finalText) {
  if (run.status !== 1 || run.error || run.signal || run.stderr ||
      exported?.status !== 0 || !finalText) return false;
  let events;
  let exportData;
  try {
    events = run.stdout.trim().split('\n').map((line) => JSON.parse(line));
    exportData = JSON.parse(exported.stdout);
  } catch {
    return false;
  }
  const messages = exportData.messages;
  const lastAssistant = Array.isArray(messages)
    ? messages.filter((message) => message?.info?.role === 'assistant').at(-1)
    : null;
  if (lastAssistant?.info?.finish !== 'stop') return false;
  const errorIndex = events.findLastIndex((event) => event.type === 'error');
  if (errorIndex < 0 || events[errorIndex].error?.name !== 'ContextOverflowError') {
    return false;
  }
  const continuationIndex = events.findIndex((event, index) =>
    index > errorIndex && event.type === 'text' &&
    event.part?.metadata?.compaction_continue === true);
  if (continuationIndex < 0) return false;
  const finalIndex = events.findLastIndex((event) =>
    event.type === 'text' && event.part?.text?.trim() === finalText);
  if (finalIndex <= continuationIndex) return false;
  const finish = events.find((event, index) =>
    index > finalIndex && event.type === 'step_finish' &&
    event.part?.reason === 'stop' &&
    event.part?.messageID === events[finalIndex].part?.messageID);
  return Boolean(finish);
}

async function runSessionAndExport({
  opencodeBin = process.env.OPENCODE_BIN || 'opencode',
  repoDir,
  agent = 'build',
  title,
  prompt,
  env = {},
  deleteAfterExport = true,
  pure = true,
  exportAttempts = 4,
  exportDelayMs = 250,
}) {
  const operationId = crypto.randomUUID();
  const operationTitle = `${title}:${operationId}`;
  const started = runSession({
    opencodeBin,
    repoDir,
    agent,
    title: operationTitle,
    prompt,
    env,
    pure,
  });
  const run = started.command;

  const sessionId = await findSessionId({
    opencodeBin,
    repoDir,
    title: operationTitle,
    startedAt: started.startedAt,
    env,
    pure,
  });
  let exported = null;
  let deleted = null;
  let finalText = null;
  let exportParseError = null;
  let exportDiagnostic = null;
  let exportAttemptsUsed = 0;
  if (sessionId) {
    const attempts = run.status === 0 ? Math.max(1, exportAttempts) : 1;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      exportAttemptsUsed = attempt + 1;
      exported = exportSession({
        opencodeBin,
        repoDir,
        sessionId,
        env,
        pure,
      });
      finalText = null;
      exportParseError = null;
      exportDiagnostic = null;
      if (exported.status === 0) {
        try {
          finalText = extractFinalAnswer(exported.stdout).text;
        } catch (error) {
          exportParseError = String(error.message || error);
          exportDiagnostic = String(
            error.stack || error.message || error,
          );
        }
      }
      if (finalText || attempt === attempts - 1) {
        break;
      }
      await sleep(exportDelayMs);
    }
    if (deleteAfterExport) {
      deleted = deleteSession({
        opencodeBin,
        repoDir,
        sessionId,
        env,
        pure,
      });
    }
  }

  return {
    schema: RUNTIME_SCHEMA,
    operation_id: operationId,
    run,
    session_id: sessionId || null,
    export: exported,
    export_attempts: exportAttemptsUsed,
    final_text: finalText,
    export_parse_error: exportParseError,
    export_diagnostic: exportDiagnostic,
    recovered_context_overflow: recoveredContextOverflow(run, exported, finalText),
    delete: deleted,
  };
}

function parseCli(argv) {
  if (argv.length === 0) {
    throw new Error(
      'usage: opencode-runtime.js inspect-config|run-export ...',
    );
  }
  const command = argv[0];
  const options = {};
  for (let index = 1; index < argv.length; index += 1) {
    const arg = argv[index];
    if (!arg.startsWith('--')) {
      throw new Error(`unexpected positional argument: ${arg}`);
    }
    const key = arg.slice(2);
    index += 1;
    if (index >= argv.length) {
      throw new Error(`${arg} requires a value`);
    }
    options[key] = argv[index];
  }
  return { command, options };
}

async function main(argv) {
  const { command, options } = parseCli(argv);
  const repoDir = path.resolve(options.repo || process.cwd());
  const benchmarkMode = Object.hasOwn(options, 'benchmark-subject');
  if (benchmarkMode && (!options.agent || typeof options.agent !== 'string')) {
    throw new Error('benchmark mode requires --agent');
  }
  const selectedSubject = benchmarkMode && options['benchmark-subject'] !== 'none'
    ? options['benchmark-subject']
    : null;
  const subjectExposure = selectedSubject
    ? readBenchmarkExposure(
      options['benchmark-exposure-file'],
      selectedSubject,
      repoDir,
    )
    : null;
  if (command === 'inspect-config') {
    const result = benchmarkMode
      ? prepareBenchmarkConfig({
        repoDir,
        env: process.env,
        selectedSubject,
        subjectExposure,
        agentName: options.agent,
        controlRoot: options['benchmark-control-root'],
      })
      : resolveNativeConfig({ repoDir, env: process.env });
    const { environment, ...safe } = result;
    process.stdout.write(`${JSON.stringify({
      schema: RUNTIME_SCHEMA,
      ...safe,
    })}\n`);
    return;
  }

  if (command === 'run-export') {
    if (!options.title || !options['prompt-file']) {
      throw new Error(
        'run-export requires --title and --prompt-file',
      );
    }
    const prompt = fs.readFileSync(
      path.resolve(options['prompt-file']),
      'utf8',
    );
    let env = process.env;
    const opencodeBin = process.env.OPENCODE_BIN || 'opencode';
    if (benchmarkMode) {
      const prepared = prepareBenchmarkConfig({
        opencodeBin,
        repoDir,
        env: process.env,
        selectedSubject,
        subjectExposure,
        agentName: options.agent,
        controlRoot: options['benchmark-control-root'],
      });
      const authorityFailure = benchmarkAuthorityFailure({
        prepared,
        options,
        selectedSubject,
        opencodeBin,
        repoDir,
        env: process.env,
      });
      if (authorityFailure) {
        process.stdout.write(`${JSON.stringify({
          schema: RUNTIME_SCHEMA,
          run: { status: 1 },
          error:
            prepared.reason ||
            `benchmark OpenCode authority changed after admission: ${authorityFailure}`,
        })}\n`);
        return;
      }
      env = prepared.environment;
    }
    let result = await runSessionAndExport({
      opencodeBin,
      repoDir,
      title: options.title,
      agent: benchmarkMode ? options.agent : (options.agent || 'build'),
      prompt,
      env,
      deleteAfterExport: options['keep-session'] !== 'true',
    });
    if (benchmarkMode) {
      const revalidated = prepareBenchmarkConfig({
        opencodeBin,
        repoDir,
        env: process.env,
        selectedSubject,
        subjectExposure,
        agentName: options.agent,
        controlRoot: options['benchmark-control-root'],
        probe: false,
      });
      const authorityFailure = benchmarkAuthorityFailure({
        prepared: revalidated,
        options,
        selectedSubject,
        opencodeBin,
        repoDir,
        env: process.env,
      });
      if (authorityFailure) {
        result = {
          ...result,
          final_text: null,
          error:
            `benchmark OpenCode authority changed during inference: ${authorityFailure}`,
          authority_revalidation: {
            status: 'failed',
            reason: authorityFailure,
          },
        };
      } else {
        result = {
          ...result,
          authority_revalidation: { status: 'verified' },
        };
      }
    }
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return;
  }

  throw new Error(`unknown command: ${command}`);
}

module.exports = {
  RUNTIME_SCHEMA,
  canonicalJson,
  configuredModel,
  deleteSession,
  exportSession,
  extractFinalAnswer,
  findSessionId,
  inspectConfig,
  inlineConfig,
  mergeObjects,
  benchmarkOverlay,
  benchmarkAuthorityFailure,
  prepareBenchmarkConfig,
  runtimeIdentitySha256,
  providerFromModel,
  readBenchmarkExposure,
  readJsonText,
  resolveNativeConfig,
  runCommand,
  runCommandToFile,
  runSession,
  runSessionAndExport,
  recoveredContextOverflow,
  sanitize,
  verifyWorkspaceBinding,
  nativeSubjectExecutableIdentity,
  mcpConnectionFailure,
  selectedMcpLines,
};

if (require.main === module) {
  main(process.argv.slice(2)).catch((error) => {
    process.stderr.write(
      `Error: ${error.stack || error.message || error}\n`,
    );
    process.exit(2);
  });
}
