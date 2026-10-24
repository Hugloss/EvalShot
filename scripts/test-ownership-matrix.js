#!/usr/bin/env node
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { qualifyEvidence } = require('./skill-evidence-contract');
const root = path.resolve(__dirname, '..');
const corpus = JSON.parse(fs.readFileSync(path.join(root, 'evals/sharp-skill-cases.json'), 'utf8'));
const skills = [
  'single-source-of-truth-review', 'semantic-redecision-review',
  'resolved-fact-regression-review', 'canonical-consumer-contract-review',
];
const families = ['split-authority', 'redecision', 'backtrack', 'refinement'];
for (const family of families) {
  const base = 'ownership-matrix-' + family;
  const positive = corpus.cases.find(c => c.id === base + '-positive');
  const neighbor = corpus.cases.find(c => c.id === base + '-neighbor-control');
  assert.ok(positive && neighbor, 'missing family pair: ' + family);
  assert.ok(skills.includes(positive.skill));
  assert.notEqual(positive.skill, neighbor.skill);
  assert.equal(positive.expected, 'FINDING');
  assert.equal(neighbor.expected, 'CLEAN');
  assert.equal(neighbor.kind, 'confusion');
  assert.equal(positive.fixture, neighbor.fixture);
  const evidence = positive.evidence_contract.anchors;
  assert.ok(evidence.length >= 2, 'multi-location proof required: ' + family);
  const result = qualifyEvidence({
    verdict: 'FINDING', expected: 'FINDING', evidence: 'fixed reviewed fixture',
    fixture: path.join(root, positive.fixture), anchors: evidence,
    location: evidence.map(a => a.path + ':' + a.line).join(' ||| '),
    quote: evidence.map(a => a.quote).join(' ||| '),
  });
  assert.equal(result.state, 'LOCATED_NOT_ADJUDICATED', family + ': ' + result.reason);
}
for (const skill of skills) {
  const control = corpus.cases.find(c => c.id === 'ownership-matrix-safe-' + skill);
  assert.ok(control && control.kind === 'control' && control.expected === 'CLEAN');
  assert.ok(fs.statSync(path.join(root, control.fixture, 'authority.py')).isFile());
}
process.stdout.write('ownership matrix fixture regressions PASS\n');
