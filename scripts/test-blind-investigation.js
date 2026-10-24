#!/usr/bin/env node
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { qualifyEvidence } = require('./skill-evidence-contract');
const root = path.resolve(__dirname, '..');
const c = JSON.parse(fs.readFileSync(path.join(root, 'evals/sharp-skill-cases.json'), 'utf8'));
const x = c.cases.find(v => v.id === 'blind-investigation-alpha');
assert.ok(x && x.blind && x.expected === 'FINDING');
assert.equal(x.skill, 'single-source-of-truth-review');
const dir = path.join(root, x.fixture);
for (const a of x.evidence_contract.anchors) {
  assert.ok(fs.statSync(path.join(dir, a.path)).isFile());
}
const proof = qualifyEvidence({
  verdict: 'FINDING', expected: 'FINDING', evidence: 'two accepted paths',
  fixture: dir, anchors: x.evidence_contract.anchors,
  location: x.evidence_contract.anchors.map(a => a.path + ':' + a.line).join(' ||| '),
  quote: x.evidence_contract.anchors.map(a => a.quote).join(' ||| '),
});
assert.equal(proof.state, 'LOCATED_NOT_ADJUDICATED');
const fixtureContents = fs.readdirSync(dir).map(n => fs.readFileSync(path.join(dir, n), 'utf8')).join('\n');
assert.ok(!fixtureContents.includes('EVAL_VERDICT:'));
assert.ok(!fixtureContents.includes('pinned-fixture-anchor'));
process.stdout.write('blind skill investigation fixtures PASS\n');
