#!/usr/bin/env node
'use strict';

// Model-free, derived coverage observation. No skill registry or evaluator fork.
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { resolveCookbookSource } = require('./cookbook-source');

const root = path.resolve(__dirname, '..');
const specialistSections = new Set([
  'Skill authoring', 'Change review', 'Concurrency', 'Execution integrity', 'Data handling',
  'Evidence integrity', 'Semantic authority', 'Qualification integrity',
  'Structural simplicity', 'Test-derived architecture', 'Discovery',
]);

function specialistsFromCatalog(source) {
  const names = [];
  let active = false;
  for (const line of fs.readFileSync(source.file('skills/README.md'), 'utf8').split(/\r?\n/)) {
    const heading = line.match(/^## (.+)$/);
    if (heading) { active = specialistSections.has(heading[1]); continue; }
    const bullet = active && line.match(/^- `([^`]+)` — /);
    if (bullet) names.push(bullet[1]);
  }
  if (!names.length || new Set(names).size !== names.length) throw new Error('Invalid or empty canonical specialist catalog');
  for (const name of names) source.file(path.join('skills', name, 'SKILL.md'));
  return names;
}

function sourceIdentity(directory) {
  try {
    const git = (...args) => execFileSync('git', ['-C', directory, ...args], { encoding: 'utf8' }).trim();
    const top = fs.realpathSync(git('rev-parse', '--show-toplevel'));
    if (top !== fs.realpathSync(directory)) throw new Error('checkout root is not Git top-level');
    const revision = git('rev-parse', 'HEAD');
    const dirty = git('status', '--porcelain', '--untracked-files=normal').length !== 0;
    return { revision, dirty, reproducible: !dirty };
  } catch (error) {
    return { revision: null, dirty: null, reproducible: false, observation_error: String(error.message).slice(0, 200) };
  }
}

function makeReport(source, corpus) {
  if (corpus.schema_version !== 1 || !Array.isArray(corpus.cases)) throw new Error('Unsupported corpus schema');
  const skills = specialistsFromCatalog(source);
  const rows = new Map(skills.map(name => [name, {
    skill: name, total: 0, positive: 0, control: 0, confusion: 0,
    repository_fixture: 0, scenario_only: 0, repository_positive: 0,
    repository_control: 0, repository_confusion: 0,
    blind_discovery: 'not_measured', semantic_oracle: 'not_measured',
    repair: 'not_measured', model_backed_qualification: 'not_measured',
  }]));
  const ids = new Set();
  for (const item of corpus.cases) {
    if (!item || typeof item.id !== 'string' || ids.has(item.id)) throw new Error('Duplicate or missing case ID');
    ids.add(item.id);
    const row = rows.get(item.skill);
    if (!row || !['positive', 'control', 'confusion'].includes(item.kind)) throw new Error('Unknown skill or case type: ' + item.id);
    row.total++;
    row[item.kind]++;
    if (item.fixture) {
      row.repository_fixture++;
      row['repository_' + item.kind]++;
    } else row.scenario_only++;
  }
  const coverage = [...rows.values()];
  const sum = key => coverage.reduce((total, row) => total + row[key], 0);
  return {
    schema: 'evalshot.skill-coverage.v1',
    semantics: 'model_free_corpus_inventory_only',
    cookbook: { root: source.root, ...sourceIdentity(source.root) },
    corpus: 'evals/sharp-skill-cases.json',
    counts: {
      specialists: coverage.length, cases: corpus.cases.length,
      positive: sum('positive'), control: sum('control'), confusion: sum('confusion'),
      repository_fixture: sum('repository_fixture'), scenario_only: sum('scenario_only'),
      specialists_without_confusion: coverage.filter(x => !x.confusion).length,
      specialists_without_repository_fixture: coverage.filter(x => !x.repository_fixture).length,
    },
    specialists: coverage,
  };
}

function cli(argv) {
  let cookbookRoot = '', output = '';
  for (let i = 0; i < argv.length; ++i) {
    if (argv[i] === '--cookbook-root' && argv[i + 1]) cookbookRoot = argv[++i];
    else if (argv[i] === '--output' && argv[i + 1]) output = argv[++i];
    else throw new Error('Usage: node scripts/skill-coverage.js --cookbook-root DIR [--output FILE]');
  }
  const source = resolveCookbookSource(cookbookRoot);
  const corpus = JSON.parse(fs.readFileSync(path.join(root, 'evals/sharp-skill-cases.json'), 'utf8'));
  const report = makeReport(source, corpus);
  const payload = JSON.stringify(report, null, 2) + '\n';
  if (output) { fs.mkdirSync(path.dirname(path.resolve(output)), { recursive: true }); fs.writeFileSync(output, payload); }
  else process.stdout.write(payload);
}

if (require.main === module) {
  try { cli(process.argv.slice(2)); }
  catch (error) { process.stderr.write('COVERAGE_ERROR ' + error.message + '\n'); process.exitCode = 1; }
}
module.exports = { makeReport, specialistsFromCatalog };
