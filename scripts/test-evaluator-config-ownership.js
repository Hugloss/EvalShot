#!/usr/bin/env node
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');

const root = path.resolve(__dirname, '..');
const noInference = /^(?:model|temperature|thinking|reasoningEffort|top_p|compaction):/m;
function write(file, text) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text);
}
function fixture(t) {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'evalshot-native-options-'));
  t.after(() => fs.rmSync(temp, { recursive: true, force: true }));
  const cookbook = path.join(temp, 'selected cookbook');
  const home = path.join(temp, 'operator home');
  const work = path.join(temp, 'working repository');
  const log = path.join(temp, 'observed.json');
  const binary = path.join(temp, 'fake-runtime');
  const corpus = path.join(temp, 'corpus.json');
  fs.mkdirSync(home); fs.mkdirSync(work);
  write(path.join(cookbook, 'skills/README.md'), '# Skills\n\n## Concurrency\n\n- `stale-work-race-review` — Fixture.\n');
  write(path.join(cookbook, 'skills/stale-work-race-review/SKILL.md'), '# Selected skill\nInspect current evidence.\n');
  write(corpus, JSON.stringify({ schema_version: 1, cases: [
    { id: 'positive', skill: 'stale-work-race-review', kind: 'positive', expected: 'FINDING', scenario: 'A bounded positive fixture.' },
    { id: 'control', skill: 'stale-work-race-review', kind: 'control', expected: 'CLEAN', scenario: 'A safe control fixture.' },
  ] }));
  write(binary, `#!/usr/bin/env node
const fs = require('fs'), path = require('path');
const argv = process.argv.slice(2), pi = argv.includes('--system-prompt');
const read = (file) => fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : null;
const configDir = path.join(process.env.XDG_CONFIG_HOME || path.join(process.env.HOME, '.config'), 'opencode');
fs.writeFileSync(process.env.OPTIONS_LOG, JSON.stringify({
  argv, cwd: process.cwd(), home: process.env.HOME, configDir,
  agent: pi ? null : read(path.join(configDir, 'agents/skill-benchmark.md')),
  config: read(path.join(configDir, 'opencode.jsonc')),
  plainConfig: read(path.join(configDir, 'opencode.json')),
  customFile: process.env.OPENCODE_CONFIG || null,
  customDir: process.env.OPENCODE_CONFIG_DIR || null,
  inline: process.env.OPENCODE_CONFIG_CONTENT || null,
  pruning: process.env.OPENCODE_DISABLE_PRUNE ?? null,
  piSettings: read(path.join(process.env.HOME, '.pi/agent/settings.json')),
}));
process.stdout.write('EVAL_VERDICT: CLEAN\\nEVAL_SCOPE: NONE\\nEVAL_EVIDENCE: Configuration transport regression fixture.\\nEVAL_EVIDENCE_LOCATION: NONE\\nEVAL_EVIDENCE_QUOTE: NONE\\n');
process.exit(Number(process.env.OPTIONS_EXIT || '0'));
`);
  fs.chmodSync(binary, 0o755);
  return { temp, cookbook, home, work, log, binary, corpus };
}
function run(context, extraEnv = {}, args = [], runtime = 'opencode') {
  const env = { ...process.env };
  for (const name of ['XDG_CONFIG_HOME', 'OPENCODE_CONFIG', 'OPENCODE_CONFIG_DIR', 'OPENCODE_CONFIG_CONTENT', 'OPENCODE_DISABLE_PRUNE', 'SKILL_EVAL_MODEL', 'PI_CODING_AGENT_DIR']) delete env[name];
  Object.assign(env, { HOME: context.home, OPENCODE_BIN: context.binary, PI_BIN: context.binary, OPTIONS_LOG: context.log }, extraEnv);
  const result = spawnSync(process.execPath, [path.join(root, 'scripts/run-skill-benchmarks.js'),
    '--cookbook-root', context.cookbook, '--corpus', context.corpus,
    '--repo', context.work, '--runtime', runtime, '--case', 'control', ...args], {
    env, cwd: context.work, encoding: 'utf8', timeout: 15000,
  });
  assert.ifError(result.error);
  assert.equal(result.status, extraEnv.OPTIONS_EXIT ? 1 : 0, result.stdout + result.stderr);
  const observed = JSON.parse(fs.readFileSync(context.log, 'utf8'));
  assert.equal(observed.cwd, context.work);
  if (runtime === 'opencode') {
    const header = observed.agent.split('---')[1];
    assert.doesNotMatch(header, noInference);
    assert.match(header, /permission:\n  "\*": deny/);
    assert.match(header, /  read: allow/);
    assert.equal(fs.existsSync(observed.home), false, 'temporary home must be cleaned after success/failure');
  }
  return observed;
}

test('absent model settings remain absent at the OpenCode launch boundary', (t) => {
  const context = fixture(t), observed = run(context);
  assert.equal(observed.config, null); assert.equal(observed.plainConfig, null);
  assert.equal(observed.argv.includes('--model'), false);
  assert.equal(observed.pruning, null);
});
for (const location of ['home', 'xdg']) {
  test(`skill launch retains ${location} global configuration bytes including false and zero`, (t) => {
    const context = fixture(t);
    const source = location === 'home' ? path.join(context.home, '.config') : path.join(context.temp, 'custom xdg');
    const file = path.join(source, 'opencode/opencode.jsonc');
    const bytes = '// native comments\n{"model":"local/org/custom","agent":{"skill-benchmark":{"temperature":0,"reasoningEffort":"low"}},"compaction":{"auto":false,"reserved":0}}\n';
    write(file, bytes);
    const observed = run(context, location === 'xdg' ? { XDG_CONFIG_HOME: source } : {});
    assert.equal(observed.config, bytes);
    assert.equal(fs.readFileSync(file, 'utf8'), bytes);
    assert.equal(observed.argv.includes('--model'), false);
  });
}

test('explicit native config selectors and inline options pass through unchanged', (t) => {
  const context = fixture(t);
  const file = path.join(context.temp, 'explicit.jsonc'), dir = path.join(context.temp, 'custom config');
  write(file, '// operator config\n{"model":"custom/source"}\n');
  fs.mkdirSync(dir);
  const inline = '{"agent":{"skill-benchmark":{"temperature":0}},"compaction":{"auto":false}}';
  const observed = run(context, { OPENCODE_CONFIG: file, OPENCODE_CONFIG_DIR: dir, OPENCODE_CONFIG_CONTENT: inline, OPENCODE_DISABLE_PRUNE: '0' });
  assert.equal(observed.customFile, file); assert.equal(observed.customDir, dir);
  assert.equal(observed.inline, inline); assert.equal(observed.pruning, '0');
  assert.equal(fs.readFileSync(file, 'utf8'), '// operator config\n{"model":"custom/source"}\n');
});
for (const via of ['cli', 'environment']) {
  test(`explicit ${via} model selection is forwarded as a native CLI argument only`, (t) => {
    const context = fixture(t), model = 'local/org/selected';
    const observed = run(context, via === 'environment' ? { SKILL_EVAL_MODEL: model } : {}, via === 'cli' ? ['--model', model] : []);
    assert.equal(observed.argv[observed.argv.indexOf('--model') + 1], model);
  });
}

test('failed skill runtime is not reported as success and temporary config is removed', (t) => {
  run(fixture(t), { OPTIONS_EXIT: '7' });
});

test('Pi launch keeps native settings and the reviewer tool contract', (t) => {
  const context = fixture(t), file = path.join(context.home, '.pi/agent/settings.json');
  const bytes = '{"defaultModel":"operator-model","defaultThinkingLevel":"off","compaction":{"enabled":false}}\n';
  write(file, bytes);
  const observed = run(context, {}, [], 'pi');
  assert.equal(observed.home, context.home); assert.equal(observed.piSettings, bytes);
  assert.equal(observed.argv.includes('--model'), false);
  assert.equal(observed.argv[observed.argv.indexOf('--tools') + 1], 'read,grep,find,ls');
  assert.equal(fs.readFileSync(file, 'utf8'), bytes);
});
