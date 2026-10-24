#!/usr/bin/env node
'use strict';
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { makeReport, specialistsFromCatalog } = require('./skill-coverage');

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'evalshot-coverage-'));
try {
  fs.mkdirSync(path.join(temporary, 'skills', 'test-specialist'), { recursive: true });
  fs.writeFileSync(path.join(temporary, 'skills', 'README.md'),
    '# Skills\n\n## Semantic authority\n- `test-specialist` — description\n\n## Overlap boundaries\n- `not-a-skill` — note\n');
  fs.writeFileSync(path.join(temporary, 'skills', 'test-specialist', 'SKILL.md'), '# Skill\n');
  const source = {
    root: temporary,
    file: relative => {
      const file = path.join(temporary, relative);
      if (!fs.statSync(file).isFile()) throw new Error('missing file: ' + relative);
      return file;
    },
  };
  assert.deepStrictEqual(specialistsFromCatalog(source), ['test-specialist']);
  const corpus = { schema_version: 1, cases: [
    { id: 'positive', skill: 'test-specialist', kind: 'positive', fixture: 'evals/fixtures/one' },
    { id: 'control', skill: 'test-specialist', kind: 'control' },
    { id: 'confusion', skill: 'test-specialist', kind: 'confusion' },
  ] };
  const report = makeReport(source, corpus);
  assert.strictEqual(report.counts.specialists, 1);
  assert.strictEqual(report.counts.cases, 3);
  assert.strictEqual(report.counts.repository_fixture, 1);
  assert.strictEqual(report.counts.scenario_only, 2);
  assert.strictEqual(report.counts.specialists_without_confusion, 0);
  assert.strictEqual(report.specialists[0].repository_positive, 1);
  assert.strictEqual(report.specialists[0].semantic_oracle, 'not_measured');
  assert.strictEqual(report.cookbook.reproducible, false);
  assert.throws(() => makeReport(source, { ...corpus, cases: [...corpus.cases, corpus.cases[0]] }), /Duplicate/);
  assert.throws(() => makeReport(source, { ...corpus, cases: [{ id: 'unknown', skill: 'other', kind: 'positive' }] }), /Unknown skill/);
  assert.throws(() => makeReport(source, { ...corpus, schema_version: 2 }), /Unsupported/);
  fs.writeFileSync(path.join(temporary, 'skills', 'README.md'),
    '# Skills\n\n## Semantic authority\n- `test-specialist` — one\n- `test-specialist` — two\n');
  assert.throws(() => specialistsFromCatalog(source), /Invalid or empty/);
  process.stdout.write('skill coverage regressions PASS\n');
} finally {
  fs.rmSync(temporary, { recursive: true, force: true });
}
