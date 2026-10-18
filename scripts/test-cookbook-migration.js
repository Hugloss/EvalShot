#!/usr/bin/env node
'use strict';

// Optional integration check: node scripts/test-cookbook-migration.js --cookbook-root DIR
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const args = process.argv.slice(2);
if (args.length && (args.length !== 2 || args[0] !== '--cookbook-root')) {
  throw new Error('Usage: node scripts/test-cookbook-migration.js [--cookbook-root DIR]');
}
const realCookbookRoot = args.length ? path.resolve(args[1]) : null;
const realCorpus = JSON.parse(fs.readFileSync(path.join(repoRoot, 'evals/sharp-skill-cases.json'), 'utf8'));
const fixtureCase = realCorpus.cases.find((item) => item.id === 'stale-work-race-positive');
assert.ok(fixtureCase, 'the retained async-ownership fixture case must exist');

function write(file, content, executable = false) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, content);
  if (executable) fs.chmodSync(file, 0o755);
}

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'evalshot-cookbook-migration-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const cookbook = path.join(root, 'selected cookbook');
  const cwd = path.join(root, 'unrelated working directory');
  const home = path.join(root, 'home');
  const artifacts = path.join(root, 'artifacts');
  const log = path.join(root, 'processes.jsonl');
  const binary = path.join(root, 'fake-model');
  const corpus = path.join(root, 'cases.json');
  for (const directory of [cookbook, cwd, home]) fs.mkdirSync(directory);
  write(path.join(cookbook, 'skills/README.md'), `# Skills\n\n## Concurrency\n\n- \`${fixtureCase.skill}\` — Migration fixture.\n`);
  write(path.join(cookbook, 'skills', fixtureCase.skill, 'SKILL.md'), '# SELECTED_COOKBOOK_SKILL\nUse the externally selected source.\n');
  write(corpus, JSON.stringify({ schema_version: 1, cases: [
    fixtureCase,
    { id: 'migration-control', skill: fixtureCase.skill, kind: 'control', expected: 'CLEAN', scenario: 'A safe control case.' },
  ] }));
  // Any unexpected model execution leaves a marker and fails; no real model binary is used.
  write(binary, '#!/usr/bin/env node\nrequire("fs").appendFileSync(process.env.MIGRATION_LOG, "unexpected model invocation\\n"); process.exit(97);\n', true);
  return { root, cookbook, cwd, home, artifacts, log, binary, corpus };
}

function run(script, argv, context, options = {}) {
  const result = spawnSync(process.execPath, [path.join(__dirname, script), ...argv], {
    cwd: options.cwd || context.cwd,
    env: {
      ...process.env,
      HOME: context.home,
      OPENCODE_BIN: context.binary,
      PI_BIN: context.binary,
      MIGRATION_LOG: context.log,
      MIGRATION_STATE: path.join(context.root, 'session.json'),
      ...(options.env || {}),
    },
    encoding: 'utf8', timeout: 10000, maxBuffer: 2 * 1024 * 1024,
  });
  assert.ifError(result.error);
  return result;
}

function pass(result) {
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
}

function untouched(context) {
  assert.equal(fs.existsSync(context.log), false, 'source/model processes must not run');
  assert.equal(fs.existsSync(context.artifacts), false, 'preflight must precede artifact creation');
}

test('help and flow metadata listing work without a cookbook checkout', (t) => {
  const context = fixture(t);
  for (const script of ['run-skill-benchmarks.js', 'run-opencode-benchmarks.js']) {
    const result = run(script, ['--help'], context);
    pass(result);
    assert.match(result.stdout, /--cookbook-root/);
  }
  const listed = run('run-opencode-benchmarks.js', ['--list'], context);
  pass(listed);
  assert.match(listed.stdout, /TOTAL benchmarks=11/);
  untouched(context);
});

test('source-consuming operations require an explicit root even from a valid cookbook cwd', (t) => {
  const context = fixture(t);
  const commands = [
    ['run-skill-benchmarks.js', '--validate-corpus'],
    ['run-skill-benchmarks.js', '--list'],
    ['run-opencode-benchmarks.js', '--benchmark', 'explicit-fact-auditor'],
  ];
  for (const [script, ...argv] of commands) {
    const result = run(script, [...argv, '--repo', context.cookbook, '--artifacts-dir', context.artifacts], context, {
      cwd: context.cookbook,
      env: { COOKBOOK_ROOT: context.cookbook },
    });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /--cookbook-root is required/);
  }
  untouched(context);
});

test('invalid cookbook directories fail before process execution or artifact creation', (t) => {
  const context = fixture(t);
  const file = path.join(context.root, 'regular-file');
  write(file, 'not a checkout');
  for (const source of [path.join(context.root, 'missing'), file]) {
    for (const script of ['run-skill-benchmarks.js', 'run-opencode-benchmarks.js']) {
      const result = run(script, ['--cookbook-root', source, '--artifacts-dir', context.artifacts], context);
      assert.notEqual(result.status, 0);
      assert.match(result.stderr, /--cookbook-root must name an existing directory/);
    }
  }
  untouched(context);
});

test('skill validation rejects missing catalog and referenced skill assets before running', (t) => {
  const context = fixture(t);
  const argv = ['--cookbook-root', context.cookbook, '--corpus', context.corpus, '--runtime', 'pi', '--all', '--artifacts-dir', context.artifacts];
  const catalog = path.join(context.cookbook, 'skills/README.md');
  const text = fs.readFileSync(catalog, 'utf8');
  fs.rmSync(catalog);
  const missingCatalog = run('run-skill-benchmarks.js', argv, context);
  assert.notEqual(missingCatalog.status, 0);
  assert.match(missingCatalog.stderr, /missing a readable file: skills\/README.md/);
  write(catalog, text);
  fs.rmSync(path.join(context.cookbook, 'skills', fixtureCase.skill, 'SKILL.md'));
  const missingSkill = run('run-skill-benchmarks.js', argv, context);
  assert.notEqual(missingSkill.status, 0);
  assert.match(missingSkill.stderr, /CORPUS_ERROR .*missing a readable file: skills\/stale-work-race-review\/SKILL.md/);
  untouched(context);
});

test('flow validates both selected helpers before setup or artifact creation', (t) => {
  const context = fixture(t);
  write(path.join(context.cookbook, 'scripts/link-opencode-local.sh'), '#!/bin/sh\nprintf "unexpected setup\\n" >> "$MIGRATION_LOG"\n', true);
  const result = run('run-opencode-benchmarks.js', ['--cookbook-root', context.cookbook, '--artifacts-dir', context.artifacts], context);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /missing a readable file: scripts\/check-opencode-session.sh/);
  untouched(context);
});

test('skill corpus fixtures remain rooted in EvalShot while live skills come from the selected checkout', (t) => {
  const context = fixture(t);
  // A same-named but invalid fixture in the source checkout and cwd must not replace EvalShot's fixture.
  for (const decoy of [context.cookbook, context.cwd]) {
    write(path.join(decoy, fixtureCase.fixture, 'service.js'), 'WRONG_FIXTURE\n');
  }
  for (const operation of ['--validate-corpus', '--list']) {
    const result = run('run-skill-benchmarks.js', ['--cookbook-root', context.cookbook, '--corpus', context.corpus, operation], context);
    pass(result);
    assert.match(result.stdout, operation === '--list' ? /CASE id=stale-work-race-positive/ : /SUMMARY status=pass cases=2 specialists=1/);
  }
  untouched(context);
});

function skillStub(context, selectedCase) {
  const finding = selectedCase.expected === 'FINDING';
  const anchors = selectedCase.evidence_contract?.anchors || [];
  const output = [
    `EVAL_VERDICT: ${selectedCase.expected}`,
    `EVAL_SCOPE: ${finding ? selectedCase.skill : 'NONE'}`,
    'EVAL_EVIDENCE: Evaluator migration process fixture.',
    `EVAL_EVIDENCE_LOCATION: ${finding ? anchors.map((item) => `${item.path}:${item.line}`).join(' ||| ') : 'NONE'}`,
    `EVAL_EVIDENCE_QUOTE: ${finding ? anchors.map((item) => item.quote).join(' ||| ') : 'NONE'}`,
  ].join('\n') + '\n';
  write(context.binary, `#!/usr/bin/env node
const fs = require('fs');
fs.appendFileSync(process.env.MIGRATION_LOG, JSON.stringify({ cwd: process.cwd(), argv: process.argv.slice(2) }) + '\\n');
process.stdout.write(${JSON.stringify(output)});
`, true);
}

test('skill execution reads selected skill bytes and defaults prompt-only work to that checkout', (t) => {
  const context = fixture(t);
  skillStub(context, { expected: 'CLEAN' });
  const result = run('run-skill-benchmarks.js', ['--cookbook-root', context.cookbook, '--corpus', context.corpus, '--runtime', 'pi', '--case', 'migration-control'], context);
  pass(result);
  const invocation = JSON.parse(fs.readFileSync(context.log, 'utf8').trim());
  assert.equal(invocation.cwd, fs.realpathSync(context.cookbook));
  const systemPrompt = invocation.argv[invocation.argv.indexOf('--system-prompt') + 1];
  assert.match(systemPrompt, /SELECTED_COOKBOOK_SKILL/);
});

test('an explicit skill working repo does not replace the selected cookbook source', (t) => {
  const context = fixture(t);
  skillStub(context, { expected: 'CLEAN' });
  const result = run('run-skill-benchmarks.js', ['--cookbook-root', context.cookbook, '--corpus', context.corpus, '--repo', context.cwd, '--runtime', 'pi', '--case', 'migration-control'], context);
  pass(result);
  const invocation = JSON.parse(fs.readFileSync(context.log, 'utf8').trim());
  assert.equal(invocation.cwd, context.cwd);
  assert.match(invocation.argv[invocation.argv.indexOf('--system-prompt') + 1], /SELECTED_COOKBOOK_SKILL/);
});

test('fixture execution uses EvalShot fixture bytes independently of source and working repo', (t) => {
  const context = fixture(t);
  skillStub(context, fixtureCase);
  const result = run('run-skill-benchmarks.js', ['--cookbook-root', context.cookbook, '--corpus', context.corpus, '--repo', context.cwd, '--runtime', 'pi', '--case', fixtureCase.id], context);
  pass(result);
  const invocation = JSON.parse(fs.readFileSync(context.log, 'utf8').trim());
  assert.equal(invocation.cwd, path.join(repoRoot, fixtureCase.fixture));
  assert.match(invocation.argv[invocation.argv.indexOf('--system-prompt') + 1], /SELECTED_COOKBOOK_SKILL/);
});

function flowStubs(context) {
  for (const [name, action] of [['link-opencode-local.sh', 'link'], ['check-opencode-session.sh', 'check']]) {
    write(path.join(context.cookbook, 'scripts', name), `#!/usr/bin/env node
require('fs').appendFileSync(process.env.MIGRATION_LOG, JSON.stringify({ action: ${JSON.stringify(action)}, source: __filename, cwd: process.cwd(), argv: process.argv.slice(2) }) + '\\n');
`, true);
  }
  write(context.binary, `#!/usr/bin/env node
const fs = require('fs');
const args = process.argv.slice(2);
const record = (action) => fs.appendFileSync(process.env.MIGRATION_LOG, JSON.stringify({ action, cwd: process.cwd(), argv: args }) + '\\n');
if (args[0] === 'run') {
  record('model-run');
  fs.writeFileSync(process.env.MIGRATION_STATE, JSON.stringify({ id: 'fixture-session', title: args[args.indexOf('--title') + 1], directory: process.cwd(), updated: Date.now() }));
} else if (args[0] === 'session' && args[1] === 'list') {
  record('session-list');
  process.stdout.write('[' + fs.readFileSync(process.env.MIGRATION_STATE, 'utf8') + ']');
} else if (args[0] === 'export') {
  record('export');
  process.stdout.write(JSON.stringify({ messages: [{ info: { role: 'assistant' }, parts: [{ type: 'text', text: '# Subagent Router Result\\n## Selected Reviewer\\n## Task Status\\n## Reviewer Feedback\\n## Router Notes' }] }] }));
} else { throw new Error('Unexpected fake-model command: ' + args[0]); }
`, true);
}

for (const explicitRepo of [false, true]) {
  test(`flow calls the selected external install/session helpers with ${explicitRepo ? 'an explicit' : 'the default'} working repo`, (t) => {
    const context = fixture(t);
    flowStubs(context);
    const argv = ['--cookbook-root', context.cookbook, '--benchmark', 'explicit-fact-auditor', '--artifacts-dir', context.artifacts];
    if (explicitRepo) argv.push('--repo', context.cwd);
    const result = run('run-opencode-benchmarks.js', argv, context);
    pass(result);
    assert.match(result.stdout, /SUMMARY status=pass benchmarks=1 passed=1 failed=0/);
    const records = fs.readFileSync(context.log, 'utf8').trim().split('\n').map((line) => JSON.parse(line));
    assert.deepEqual(records.map((record) => record.action), ['link', 'model-run', 'session-list', 'check', 'export']);
    assert.equal(records[0].source, path.join(context.cookbook, 'scripts/link-opencode-local.sh'));
    assert.equal(records[0].cwd, context.cookbook);
    assert.equal(records[3].source, path.join(context.cookbook, 'scripts/check-opencode-session.sh'));
    for (const record of records.slice(1)) assert.equal(record.cwd, explicitRepo ? context.cwd : context.cookbook);
    assert.deepEqual(records[3].argv, ['--scope', 'session', '--expect-subagent', 'plan-fact-auditor', 'fixture-session']);
  });
}

test('the complete retained corpus validates against an explicitly supplied real cookbook checkout', {
  skip: !realCookbookRoot && 'Pass --cookbook-root DIR to add the real-source integration check.',
}, (t) => {
  const context = fixture(t);
  const result = run('run-skill-benchmarks.js', ['--cookbook-root', realCookbookRoot, '--validate-corpus'], context);
  pass(result);
  assert.match(result.stdout, new RegExp(`SUMMARY status=pass cases=${realCorpus.cases.length} `));
  untouched(context);
});
