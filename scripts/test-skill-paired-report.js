#!/usr/bin/env node
'use strict';
const assert = require('node:assert/strict');
const { compareReceipts } = require('./skill-paired-report');
const receipt = (arm, extra = {}) => ({
  id: 'case-1', runtime: 'pi', model: 'model-a', arm, blinded: true,
  expected: 'FINDING', kind: 'positive', status: 'pass', exitCode: 0,
  evidenceQualification: 'LOCATED_NOT_ADJUDICATED',
  qualification: { capability: 'SOURCE_LOCATED_CLASSIFICATION',
    semanticProof: 'NOT_ADJUDICATED', classificationPass: true, evidencePass: true },
  ...extra,
});
const matched = compareReceipts([receipt('baseline'), receipt('skill')]);
assert.equal(matched.comparable_pairs, 0);
assert.equal(matched.comparisons[0].reason, 'authority-unknown');
assert.equal(matched.claim, 'DESCRIPTIVE_ONLY_NO_CAUSAL_EFFECTIVENESS_CLAIM');
assert.equal(matched.comparisons[0].observed_transition, undefined);
assert.equal(matched.comparisons[0].authority.task_sha256, 'UNKNOWN');
const missing = compareReceipts([receipt('baseline')]);
assert.equal(missing.incomplete_pairs, 1);
assert.equal(missing.comparable_pairs, 0);
const changed = compareReceipts([receipt('baseline'), receipt('skill', { expected: 'CLEAN' })]);
assert.equal(changed.not_comparable_pairs, 1);
assert.equal(changed.comparisons[0].reason, 'mismatch-expected');
const notBlind = compareReceipts([receipt('baseline'), receipt('skill', { blinded: false })]);
assert.equal(notBlind.not_comparable_pairs, 1);
const unqualified = compareReceipts([receipt('baseline'), receipt('skill', {
  status: 'fail', exitCode: 1, qualification: { semanticProof: 'NOT_ADJUDICATED' },
})]);
assert.equal(unqualified.comparisons[0].comparability, 'NOT_COMPARABLE');
assert.equal(compareReceipts([{ id: 'unknown', arm: 'baseline' }]).ignored_receipts.length, 1);
assert.throws(() => compareReceipts([receipt('baseline'), receipt('baseline')]), /duplicate receipt/);
assert.throws(() => compareReceipts(null), /receipts must be an array/);
const bound = {
  repository_sha256: 'repo-1', task_sha256: 'task-1', skill_revision: 'skill-1',
  runtime_executable_sha256: 'executable-1', runtime_config_sha256: 'runtime-1',
  model_identity: 'model-a', oracle_revision: 'oracle-1', replicate_id: 'rep-1',
};
const authorityMatch = compareReceipts([
  receipt('baseline', { experimentalAuthority: { ...bound, intervention: 'WITHOUT_CANONICAL_SKILL', execution_id: 'run-1' } }),
  receipt('skill', { experimentalAuthority: { ...bound, intervention: 'WITH_CANONICAL_SKILL', execution_id: 'run-2' } }),
]);
assert.equal(authorityMatch.comparisons[0].authority.repository_sha256, 'MATCH');
assert.equal(authorityMatch.comparisons[0].comparability, 'DESCRIPTIVE_ONLY');
const authorityMismatch = compareReceipts([
  receipt('baseline', { experimentalAuthority: { ...bound, intervention: 'WITHOUT_CANONICAL_SKILL', execution_id: 'run-1' } }),
  receipt('skill', { experimentalAuthority: { ...bound, repository_sha256: 'repo-2', intervention: 'WITH_CANONICAL_SKILL', execution_id: 'run-2' } }),
]);
assert.equal(authorityMismatch.comparable_pairs, 0);
assert.equal(authorityMismatch.not_comparable_pairs, 1);
assert.deepEqual(authorityMismatch.comparisons[0].mismatched_authority, ['repository_sha256']);
const missingAuthority = compareReceipts([receipt('baseline'), receipt('skill')]);
assert.equal(missingAuthority.comparisons[0].authority.repository_sha256, 'UNKNOWN');
assert.equal(missingAuthority.comparisons[0].comparability, 'NOT_COMPARABLE');
assert.equal(missingAuthority.comparisons[0].reason, 'authority-unknown');
const badIntervention = compareReceipts([
  receipt('baseline', { experimentalAuthority: { ...bound, intervention: 'WITH_CANONICAL_SKILL', execution_id: 'run-1' } }),
  receipt('skill', { experimentalAuthority: { ...bound, intervention: 'WITH_CANONICAL_SKILL', execution_id: 'run-2' } }),
]);
assert.equal(badIntervention.comparisons[0].reason, 'invalid-intervention-or-execution-identity');
const replayed = compareReceipts([
  receipt('baseline', { experimentalAuthority: { ...bound, intervention: 'WITHOUT_CANONICAL_SKILL', execution_id: 'reused' } }),
  receipt('skill', { experimentalAuthority: { ...bound, intervention: 'WITH_CANONICAL_SKILL', execution_id: 'reused' } }),
]);
assert.equal(replayed.comparisons[0].reason, 'invalid-intervention-or-execution-identity');
process.stdout.write('skill paired report regressions PASS\n');
