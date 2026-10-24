#!/usr/bin/env node
'use strict';

// Derived observations over existing skill-evaluator receipts. Never regrades trials.
const fs = require('node:fs');
const path = require('node:path');

function compareReceipts(receipts) {
  if (!Array.isArray(receipts)) throw new Error('receipts must be an array');
  const pairs = new Map();
  const ignored = [];
  for (const r of receipts) {
    if (!r || typeof r !== 'object' || !r.id || !r.runtime ||
        !['baseline', 'skill'].includes(r.arm)) {
      ignored.push({ id: r && r.id || null, reason: 'missing-pair-identity' });
      continue;
    }
    const key = JSON.stringify([r.id, r.runtime, r.model ?? null]);
    const slot = pairs.get(key) || { id: r.id, runtime: r.runtime, model: r.model ?? null };
    if (slot[r.arm]) throw new Error('duplicate receipt for ' + key + '/' + r.arm);
    slot[r.arm] = r;
    pairs.set(key, slot);
  }
  const comparisons = [];
  for (const pair of pairs.values()) {
    const { baseline, skill } = pair;
    if (!baseline || !skill) {
      comparisons.push({ id: pair.id, runtime: pair.runtime, model: pair.model,
        comparability: 'INCOMPLETE_PAIR', reason: 'missing-arm', baseline: !!baseline, skill: !!skill });
      continue;
    }
    // These independent sources are mandatory for causal comparison; missing is unknown.
    const fields = ['expected', 'kind', 'blinded'];
    const mismatch = fields.find(k => baseline[k] !== skill[k]);
    if (mismatch || baseline.blinded !== true || skill.blinded !== true) {
      comparisons.push({ id: pair.id, runtime: pair.runtime, model: pair.model,
        comparability: 'NOT_COMPARABLE', reason: mismatch ? 'mismatch-' + mismatch : 'not-blinded' });
      continue;
    }
    // Evidence from the execution itself, never from report-time guesses.
    // Legacy receipts stay descriptive and carry explicit missing authority.
    const bindings = ['repository_sha256', 'task_sha256', 'skill_revision',
      'runtime_executable_sha256', 'runtime_config_sha256', 'oracle_revision',
      'replicate_id', 'model_identity'];
    const authority = Object.fromEntries(bindings.map(field => {
      const b = baseline.experimentalAuthority?.[field];
      const a = skill.experimentalAuthority?.[field];
      if (typeof b !== 'string' || !b || typeof a !== 'string' || !a)
        return [field, 'UNKNOWN'];
      return [field, b === a ? 'MATCH' : 'MISMATCH'];
    }));
    // An intervention deliberately varies loaded skill text; equality of its
    // revision identifies a source, not equivalence of agent-visible prompts.
    const conflicts = Object.entries(authority).filter(([, v]) => v === 'MISMATCH')
      .map(([field]) => field);
    if (conflicts.length) {
      comparisons.push({ id: pair.id, runtime: pair.runtime, model: pair.model,
        comparability: 'NOT_COMPARABLE', reason: 'authority-mismatch',
        mismatched_authority: conflicts, authority });
      continue;
    }
    // Unlike historical descriptive receipts, experimental comparability
    // requires positive evidence for every governing input. Unknown is never
    // silently promoted to a matched identity.
    const unknown = Object.entries(authority).filter(([, v]) => v === 'UNKNOWN')
      .map(([field]) => field);
    if (unknown.length) {
      comparisons.push({ id: pair.id, runtime: pair.runtime, model: pair.model,
        comparability: 'NOT_COMPARABLE', reason: 'authority-unknown',
        unknown_authority: unknown, authority });
      continue;
    }
    if (baseline.experimentalAuthority.intervention !== 'WITHOUT_CANONICAL_SKILL' ||
        skill.experimentalAuthority.intervention !== 'WITH_CANONICAL_SKILL' ||
        baseline.experimentalAuthority.execution_id === skill.experimentalAuthority.execution_id ||
        !baseline.experimentalAuthority.execution_id ||
        !skill.experimentalAuthority.execution_id) {
      comparisons.push({ id: pair.id, runtime: pair.runtime, model: pair.model,
        comparability: 'NOT_COMPARABLE', reason: 'invalid-intervention-or-execution-identity', authority });
      continue;
    }
    const qualified = arm => ({
      status: arm.status,
      qualification: arm.qualification?.capability ?? 'UNKNOWN',
      semanticProof: arm.qualification?.semanticProof ?? 'UNKNOWN',
      evidence: arm.evidenceQualification ?? 'UNKNOWN',
      success: arm.status === 'pass' &&
        arm.exitCode === 0 &&
        arm.qualification?.classificationPass === true &&
        arm.qualification?.evidencePass === true,
    });
    const b = qualified(baseline), s = qualified(skill);
    comparisons.push({ id: pair.id, runtime: pair.runtime, model: pair.model,
      comparability: 'DESCRIPTIVE_ONLY', authority, baseline: b, skill: s,
      observed_transition: (b.success ? 'PASS' : 'NOT_QUALIFIED') + '_TO_' +
        (s.success ? 'PASS' : 'NOT_QUALIFIED') });
  }
  const complete = comparisons.filter(x => x.comparability === 'DESCRIPTIVE_ONLY');
  return {
    schema: 'evalshot.skill-paired-observation.v1',
    claim: 'DESCRIPTIVE_ONLY_NO_CAUSAL_EFFECTIVENESS_CLAIM',
    assigned_pairs: comparisons.length,
    comparable_pairs: complete.length,
    incomplete_pairs: comparisons.filter(x => x.comparability === 'INCOMPLETE_PAIR').length,
    not_comparable_pairs: comparisons.filter(x => x.comparability === 'NOT_COMPARABLE').length,
    ignored_receipts: ignored,
    comparisons,
  };
}

function main(args) {
  let root = '', output = '';
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--artifacts-dir' && args[i + 1]) root = args[++i];
    else if (args[i] === '--output' && args[i + 1]) output = args[++i];
    else throw new Error('Usage: node scripts/skill-paired-report.js --artifacts-dir DIR [--output FILE]');
  }
  if (!root) throw new Error('--artifacts-dir is required');
  const rows = [];
  for (const runtime of fs.readdirSync(root).sort()) {
    const rt = path.join(root, runtime);
    if (!fs.statSync(rt).isDirectory()) continue;
    for (const arm of ['baseline', 'skill']) {
      const armDir = path.join(rt, arm);
      if (!fs.existsSync(armDir)) continue;
      for (const id of fs.readdirSync(armDir).sort()) {
        const name = path.join(armDir, id, 'result.json');
        if (!fs.existsSync(name)) continue;
        const record = JSON.parse(fs.readFileSync(name, 'utf8'));
        if (record.runtime !== runtime || record.arm !== arm || record.id !== id)
          throw new Error('receipt path/identity mismatch: ' + name);
        rows.push(record);
      }
    }
  }
  const result = JSON.stringify(compareReceipts(rows), null, 2) + '\n';
  if (output) { fs.mkdirSync(path.dirname(path.resolve(output)), { recursive: true }); fs.writeFileSync(output, result); }
  else process.stdout.write(result);
}

if (require.main === module) {
  try { main(process.argv.slice(2)); }
  catch (e) { process.stderr.write('REPORT_ERROR ' + e.message + '\n'); process.exitCode = 1; }
}
module.exports = { compareReceipts };
