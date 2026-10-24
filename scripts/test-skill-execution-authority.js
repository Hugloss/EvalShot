#!/usr/bin/env node
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { directoryDigest, resolveAuthority, assertUnchanged } = require('./skill-execution-authority');
const { persist, assertReceiptVacant } = require('./run-skill-benchmarks');

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'evalshot-authority-'));
try {
  const repo = path.join(root, 'fixture');
  fs.mkdirSync(repo);
  fs.writeFileSync(path.join(repo, 'owner.py'), 'value = 1\n');
  const first = directoryDigest(repo);
  const cookbook = { root: repo };
  const item = { id: 'blind-a', kind: 'positive', expected: 'FINDING', blind: true,
    scenario: 'not shown to agent', fixture: 'fixture' };
  const options = { runtime: 'pi', model: 'test-model', replicateId: 'paired-01', withoutSkill: false };
  const previousBin = process.env.PI_BIN;
  process.env.PI_BIN = process.execPath;
  try {
    const observed = resolveAuthority({ item, options, cookbook, cwd: repo,
      prompt: 'inspect repository', skillText: 'canonical text' });
    assert.equal(observed.repository_sha256, first);
    assert.equal(observed.replicate_id, 'paired-01');
    assert.equal(observed.runtime_executable_sha256.length, 64);
    assert.equal(observed.runtime_config_sha256, null);
    assert.equal(observed.requested_model, 'test-model');
    assert.equal(observed.model_identity, null);
    assert.equal(observed.intervention, 'WITH_CANONICAL_SKILL');
    assertUnchanged(observed, repo);
    const baseline = resolveAuthority({ item, options: { ...options, withoutSkill: true },
      cookbook, cwd: repo, prompt: 'inspect repository', skillText: 'canonical text' });
    assert.equal(baseline.task_sha256, observed.task_sha256);
    assert.equal(baseline.skill_revision, observed.skill_revision);
    assert.notEqual(baseline.execution_id, observed.execution_id);
    assert.equal(baseline.intervention, 'WITHOUT_CANONICAL_SKILL');
    fs.writeFileSync(path.join(repo, 'owner.py'), 'value = 2\n');
    assert.throws(() => assertUnchanged(observed, repo), /changed after authority resolution/);
    fs.writeFileSync(path.join(repo, 'owner.py'), 'value = 1\n');
    fs.writeFileSync(path.join(repo, 'new.py'), 'extra = True\n');
    assert.throws(() => assertUnchanged(observed, repo), /changed after authority resolution/);
  } finally {
    if (previousBin === undefined) delete process.env.PI_BIN;
    else process.env.PI_BIN = previousBin;
  }
  const artifacts = path.join(root, 'artifacts');
  const result = { id: 'blind-a', runtime: 'pi', arm: 'baseline',
    experimentalAuthority: { execution_id: 'immutable-run' },
    stdout: 'first output', stderr: '' };
  assertReceiptVacant(artifacts, 'pi', 'baseline', 'blind-a');
  persist(result, artifacts);
  const receiptPath = path.join(artifacts, 'pi', 'baseline', 'blind-a', 'result.json');
  const firstReceipt = fs.readFileSync(receiptPath, 'utf8');
  assert.throws(() => persist({ ...result, stdout: 'replacement' }, artifacts),
    /Execution artifact already exists/);
  assert.throws(() => assertReceiptVacant(artifacts, 'pi', 'baseline', 'blind-a'),
    /Execution artifact already exists/);
  assert.equal(fs.readFileSync(receiptPath, 'utf8'), firstReceipt);
  assert.equal(fs.readFileSync(path.join(path.dirname(receiptPath), 'stdout.txt'), 'utf8'),
    'first output');
  console.log('skill execution authority regressions PASS');
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}
