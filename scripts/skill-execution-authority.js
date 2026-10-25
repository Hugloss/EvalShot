'use strict';

// One execution-time authority owner for skill evaluations. Unknown governing
// inputs are deliberately absent: a guessed digest is worse than no digest.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const digestFile = name => digest(fs.readFileSync(name));

function directoryDigest(root) {
  const visited = [];
  function scan(dir, prefix) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      if (entry.name === '.git') continue;
      const relative = prefix ? prefix + '/' + entry.name : entry.name;
      const full = path.join(dir, entry.name);
      const stat = fs.lstatSync(full);
      if (stat.isSymbolicLink()) {
        visited.push(['symlink', relative, fs.readlinkSync(full)]);
      } else if (stat.isDirectory()) {
        scan(full, relative);
      } else if (stat.isFile()) {
        visited.push(['file', relative, digestFile(full)]);
      } else {
        throw new Error('Unsupported repository entry: ' + relative);
      }
    }
  }
  scan(fs.realpathSync(root), '');
  return digest(JSON.stringify(visited));
}

function resolveExecutable(command, env) {
  if (command.includes(path.sep)) {
    const file = fs.realpathSync(path.resolve(command));
    fs.accessSync(file, fs.constants.X_OK);
    return file;
  }
  for (const dir of (env.PATH || '').split(path.delimiter)) {
    if (!dir) continue;
    const candidate = path.join(dir, command);
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      return fs.realpathSync(candidate);
    } catch { /* continue */ }
  }
  throw new Error('Cannot resolve runtime executable: ' + command);
}

function resolveAuthority({ item, options, cookbook, cwd, prompt, skillText }) {
  const binary = resolveExecutable(options.runtime === 'pi' ? (process.env.PI_BIN || 'pi')
    : (process.env.OPENCODE_BIN || 'opencode'), process.env);
  const revision = digest(skillText);
  const task = { id: item.id, fixture: item.fixture || null, kind: item.kind,
    expected: item.expected, blind: !!item.blind, scenario: item.scenario,
    evidence_contract: item.evidence_contract || null, prompt };
  const authority = {
    repository_sha256: directoryDigest(cwd),
    task_sha256: digest(JSON.stringify(task)),
    skill_revision: revision,
    // The executable bytes are observed, not an assertion about provider/model
    // selection or about configuration loaded inside the process.
    runtime_executable_sha256: digestFile(binary),
    runtime_executable_path: binary,
    oracle_revision: digestFile(path.join(__dirname, 'skill-evidence-contract.js')) +
      ':' + digestFile(path.join(__dirname, 'skill-outcome-qualification.js')),
    replicate_id: options.replicateId || null,
    runtime_config_sha256: null,
    // CLI selection is a request, not proof of the model actually used.
    requested_model: options.model || null,
    model_identity: null,
    intervention: options.withoutSkill ? 'WITHOUT_CANONICAL_SKILL' : 'WITH_CANONICAL_SKILL',
    execution_id: crypto.randomUUID(),
  };
  return authority;
}

function assertUnchanged(authority, cwd, skillFile) {
  if (directoryDigest(cwd) !== authority.repository_sha256)
    throw new Error('Repository inputs changed after authority resolution');
  if (skillFile && digestFile(skillFile) !== authority.skill_revision)
    throw new Error('Canonical skill changed after authority resolution');
  const oracle = digestFile(path.join(__dirname, 'skill-evidence-contract.js')) +
    ':' + digestFile(path.join(__dirname, 'skill-outcome-qualification.js'));
  if (oracle !== authority.oracle_revision)
    throw new Error('Evaluation oracle changed after authority resolution');
  if (digestFile(authority.runtime_executable_path) !== authority.runtime_executable_sha256)
    throw new Error('Runtime executable changed after authority resolution');
}

module.exports = { digest, directoryDigest, resolveExecutable, resolveAuthority, assertUnchanged };
