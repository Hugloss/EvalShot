'use strict';

// Outcome qualification is a projection of existing runner/evidence authorities.
// This module does not decide semantic truth or create a second scorer.
function assessSkillOutcome({ executionStatus, parsed, expected, skill, proof }) {
  const expectedScope = expected === 'FINDING' ? skill : 'NONE';
  const classificationPass = executionStatus === 0 &&
    parsed.verdict === expected && parsed.scope === expectedScope;
  const evidencePass = proof && proof.state !== 'UNQUALIFIED';
  const pass = classificationPass && evidencePass;
  const evidenceQualification = proof ? proof.state : 'UNQUALIFIED';
  // Exact source localization does not establish causal or semantic proof.
  const semanticProof = 'NOT_ADJUDICATED';
  const capability = pass
    ? evidenceQualification === 'LOCATED_NOT_ADJUDICATED'
      ? 'SOURCE_LOCATED_CLASSIFICATION'
      : evidenceQualification === 'SCENARIO_ONLY'
        ? 'SCENARIO_CLASSIFICATION'
        : 'CLASSIFICATION_ONLY'
    : 'UNQUALIFIED';
  return {
    pass, expectedScope, classificationPass, evidencePass: !!evidencePass,
    evidenceQualification, semanticProof, capability,
    repositoryDiscovery: 'NOT_MEASURED', repair: 'NOT_MEASURED',
  };
}
module.exports = { assessSkillOutcome };
