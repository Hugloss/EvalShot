#!/usr/bin/env node
'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { qualifyEvidence } = require('./skill-evidence-contract');

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'cookbook-proof-'));
try {
  const source = path.join(root, 'owner.js');
  fs.writeFileSync(source, 'function owner() { return false; }\n');
  const claim = {
    verdict: 'FINDING',
    evidence: 'owner returns false without the guard',
    location: 'owner.js:1',
    quote: 'return false;',
    fixture: root,
    expected: 'FINDING',
    anchors: [{ path: 'owner.js', line: 1, quote: 'return false;' }],
  };
  assert.strictEqual(qualifyEvidence(claim).state, 'LOCATED_NOT_ADJUDICATED');
  assert.strictEqual(qualifyEvidence({ ...claim, quote: 'return true;' }).reason, 'quote-not-on-cited-line');
  assert.strictEqual(qualifyEvidence({ ...claim, location: 'owner.js:3' }).reason, 'line-out-of-range');
  assert.strictEqual(qualifyEvidence({ ...claim, location: '../outside.js:1' }).reason, 'path-escapes-fixture');
  assert.strictEqual(qualifyEvidence({ ...claim, location: 'owner.js:0' }).reason, 'invalid-line-number');
  assert.strictEqual(qualifyEvidence({ ...claim, location: '' }).reason, 'missing-file-line-quote');
  assert.strictEqual(qualifyEvidence({ ...claim, anchors: null }).reason, 'positive-fixture-oracle-anchor-missing');
  assert.strictEqual(qualifyEvidence({ ...claim, anchors: [
    { path: 'owner.js', line: 1, quote: 'function owner()' }
  ] }).reason, 'citation-does-not-match-pinned-fixture-anchors');
  assert.strictEqual(qualifyEvidence({ ...claim, anchors: [
    { path: 'owner.js', line: 1, quote: 'return false;' },
    { path: 'owner.js', line: 1, quote: 'function owner()' }
  ] }).reason, 'missing-required-evidence-anchors');
  assert.strictEqual(qualifyEvidence({ ...claim, fixture: null }).state, 'SCENARIO_ONLY');
  assert.strictEqual(qualifyEvidence({ ...claim, verdict: 'CLEAN', expected: 'CLEAN' }).state, 'NOT_APPLICABLE');
  fs.symlinkSync('/etc/hosts', path.join(root, 'foreign.js'));
  assert.strictEqual(qualifyEvidence({ ...claim, location: 'foreign.js:1', quote: 'localhost' }).reason, 'symlink-escapes-fixture');
  process.stdout.write('skill evidence qualification regressions PASS\n');
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}
