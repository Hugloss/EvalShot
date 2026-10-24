#!/usr/bin/env node
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { qualifyEvidence } = require('./skill-evidence-contract');
const corpus = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'evals/sharp-skill-cases.json'), 'utf8'));
const selected = corpus.cases.filter(x => x.id.startsWith('blind-investigation-'));
assert.ok(selected.length >= 5);
assert.ok(selected.some(x => x.expected === 'CLEAN' && x.kind === 'control'));
assert.ok(selected.some(x => x.kind === 'confusion'));
assert.ok(new Set(selected.map(x => x.skill)).size >= 2);
for (const item of selected) {
  assert.equal(item.blind, true);
  assert.ok(item.fixture && fs.statSync(path.join(__dirname, '..', item.fixture)).isDirectory());
  if (item.expected !== 'FINDING') continue;
  const anchors = item.evidence_contract.anchors;
  const outcome = qualifyEvidence({
    verdict: 'FINDING', expected: 'FINDING', evidence: 'fixed oracle citation',
    fixture: path.join(__dirname, '..', item.fixture), anchors,
    location: anchors.map(a => a.path + ':' + a.line).join(' ||| '),
    quote: anchors.map(a => a.quote).join(' ||| '),
  });
  assert.equal(outcome.state, 'LOCATED_NOT_ADJUDICATED', item.id + ': ' + outcome.reason);
}
process.stdout.write('blind control matrix regressions PASS\n');
