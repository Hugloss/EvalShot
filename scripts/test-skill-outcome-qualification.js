#!/usr/bin/env node
'use strict';
const assert = require('assert');
const { assessSkillOutcome } = require('./skill-outcome-qualification');

const common = { executionStatus: 0, parsed: { verdict: 'FINDING', scope: 'owner-review' },
  expected: 'FINDING', skill: 'owner-review' };
const located = assessSkillOutcome({ ...common, proof: { state: 'LOCATED_NOT_ADJUDICATED' } });
assert.strictEqual(located.pass, true);
assert.strictEqual(located.capability, 'SOURCE_LOCATED_CLASSIFICATION');
assert.strictEqual(located.semanticProof, 'NOT_ADJUDICATED');
assert.strictEqual(located.repositoryDiscovery, 'NOT_MEASURED');
assert.strictEqual(located.repair, 'NOT_MEASURED');
const scenario = assessSkillOutcome({ ...common, proof: { state: 'SCENARIO_ONLY' } });
assert.strictEqual(scenario.pass, true);
assert.strictEqual(scenario.capability, 'SCENARIO_CLASSIFICATION');
assert.strictEqual(scenario.semanticProof, 'NOT_ADJUDICATED');
const clean = assessSkillOutcome({
  ...common, parsed: { verdict: 'CLEAN', scope: 'NONE' },
  expected: 'CLEAN', proof: { state: 'NOT_APPLICABLE' },
});
assert.strictEqual(clean.pass, true);
assert.strictEqual(clean.capability, 'CLASSIFICATION_ONLY');
for (const bad of [
  { ...common, proof: { state: 'UNQUALIFIED' } },
  { ...common, executionStatus: 1, proof: { state: 'LOCATED_NOT_ADJUDICATED' } },
  { ...common, parsed: { verdict: 'CLEAN', scope: 'NONE' }, proof: { state: 'NOT_APPLICABLE' } },
  { ...common, parsed: { verdict: 'FINDING', scope: 'other' }, proof: { state: 'LOCATED_NOT_ADJUDICATED' } },
]) {
  const result = assessSkillOutcome(bad);
  assert.strictEqual(result.pass, false);
  assert.strictEqual(result.capability, 'UNQUALIFIED');
  assert.strictEqual(result.semanticProof, 'NOT_ADJUDICATED');
}
process.stdout.write('skill outcome qualification regressions PASS\n');
