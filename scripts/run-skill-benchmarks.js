#!/usr/bin/env node
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');
const { qualifyEvidence } = require('./skill-evidence-contract');
const { assessSkillOutcome } = require('./skill-outcome-qualification');
const { resolveCookbookSource } = require('./cookbook-source');
const { resolveAuthority, assertUnchanged } = require('./skill-execution-authority');

const repoRoot = path.resolve(path.dirname(fs.realpathSync(__filename)), '..');
const defaultCorpus = path.join(repoRoot, 'evals', 'sharp-skill-cases.json');
const specialistSections = new Set([
  'Skill authoring',
  'Concurrency',
  'Execution integrity',
  'Data handling',
  'Evidence integrity',
  'Semantic authority',
  'Qualification integrity',
  'Structural simplicity',
  'Test-derived architecture',
  'Discovery',
]);

function usage() {
  process.stdout.write(`Usage:
  scripts/run-skill-benchmarks.js --cookbook-root DIR --validate-corpus
  scripts/run-skill-benchmarks.js --cookbook-root DIR --list
  scripts/run-skill-benchmarks.js --cookbook-root DIR --runtime opencode|pi (--all | --skill NAME | --case ID) [options]

Options:
  --cookbook-root DIR  Required agentsCookbook checkout supplying skills and their catalog.
  --corpus FILE        Alternate corpus JSON.
  --repo DIR           Working repository for prompt-only cases; defaults to cookbook root.
  --artifacts-dir DIR  Persist output and result JSON per case.
  --model ID           Model override; defaults to SKILL_EVAL_MODEL.
  --skill NAME         Run every case for one skill; repeatable.
  --case ID            Run one case; repeatable.
  --all                Run every corpus case.
  --list               List cases without model execution.
  --validate-corpus    Validate ids, coverage, fixtures, and skill references.
  --without-skill      Baseline arm: withhold canonical skill text (requires blinded cases).
  --replicate-id ID    Explicit replicate identity shared by paired arms.
`);
}

function parseArgs(argv) {
  const out = {
    runtime: '', all: false, skills: [], cases: [], repoDir: '', cookbookRoot: '',
    corpus: defaultCorpus, artifactsDir: '', model: process.env.SKILL_EVAL_MODEL || '',
    list: false, validateCorpus: false, withoutSkill: false, replicateId: '',
  };
  const take = (argvRef, indexRef, name) => {
    indexRef.value += 1;
    if (indexRef.value >= argvRef.length) throw new Error(`${name} requires a value`);
    return argvRef[indexRef.value];
  };
  const indexRef = { value: 0 };
  for (; indexRef.value < argv.length; indexRef.value += 1) {
    const arg = argv[indexRef.value];
    if (arg === '--runtime') out.runtime = take(argv, indexRef, arg);
    else if (arg === '--all') out.all = true;
    else if (arg === '--without-skill') out.withoutSkill = true;
    else if (arg === '--replicate-id') out.replicateId = take(argv, indexRef, arg);
    else if (arg === '--skill') out.skills.push(take(argv, indexRef, arg));
    else if (arg === '--case') out.cases.push(take(argv, indexRef, arg));
    else if (arg === '--cookbook-root') out.cookbookRoot = take(argv, indexRef, arg);
    else if (arg === '--repo') out.repoDir = path.resolve(take(argv, indexRef, arg));
    else if (arg === '--corpus') out.corpus = path.resolve(take(argv, indexRef, arg));
    else if (arg === '--artifacts-dir') out.artifactsDir = path.resolve(take(argv, indexRef, arg));
    else if (arg === '--model') out.model = take(argv, indexRef, arg);
    else if (arg === '--list') out.list = true;
    else if (arg === '--validate-corpus') out.validateCorpus = true;
    else if (arg === '--help' || arg === '-h') { usage(); process.exit(0); }
    else throw new Error(`Unknown option: ${arg}`);
  }
  return out;
}

function catalogSpecialists(cookbook) {
  const lines = fs.readFileSync(cookbook.file('skills/README.md'), 'utf8').split(/\r?\n/);
  const names = [];
  let active = false;
  for (const line of lines) {
    const heading = line.match(/^## (.+)$/);
    if (heading) { active = specialistSections.has(heading[1]); continue; }
    if (!active) continue;
    const bullet = line.match(/^- `([^`]+)` — /);
    if (bullet) names.push(bullet[1]);
  }
  return names;
}

function validateCorpus(corpus, cookbook, specialists) {
  const errors = [];
  if (!corpus || corpus.schema_version !== 1 || !Array.isArray(corpus.cases)) {
    return ['corpus must contain schema_version=1 and a cases array'];
  }
  const ids = new Set();
  const coverage = new Map();
  for (const item of corpus.cases) {
    if (!item || typeof item !== 'object') { errors.push('case entry must be an object'); continue; }
    if (!item.id || typeof item.id !== 'string') errors.push('case missing string id');
    else if (ids.has(item.id)) errors.push(`duplicate case id: ${item.id}`);
    else ids.add(item.id);
    if (!item.skill || !specialists.has(item.skill)) errors.push(`${item.id || '<unknown>'}: unknown specialist ${item.skill || '<missing>'}`);
    else {
      try { cookbook.file(path.join('skills', item.skill, 'SKILL.md')); }
      catch (error) { errors.push(`${item.id}: ${error.message}`); }
      const kinds = coverage.get(item.skill) || new Set();
      kinds.add(item.kind); coverage.set(item.skill, kinds);
    }
    if (!['positive', 'control', 'confusion'].includes(item.kind)) errors.push(`${item.id}: invalid kind ${item.kind}`);
    if (!['FINDING', 'CLEAN'].includes(item.expected)) errors.push(`${item.id}: expected must be FINDING or CLEAN`);
    if (!item.scenario || typeof item.scenario !== 'string') errors.push(`${item.id}: missing scenario`);
    if (item.blind !== undefined && typeof item.blind !== 'boolean') errors.push(`${item.id}: blind must be boolean`);
    if (item.fixture && item.expected === 'FINDING') {
      const contract = item.evidence_contract;
      if (!contract || contract.status !== 'pinned-fixture-anchor'
        || !Array.isArray(contract.anchors) || contract.anchors.length === 0) {
        errors.push(`${item.id}: positive fixture needs pinned fixture anchors`);
      } else {
        const fixtureDir = path.resolve(repoRoot, item.fixture);
        const proof = qualifyEvidence({
          verdict: 'FINDING', evidence: 'preflight qualification of reviewed anchors',
          fixture: fixtureDir, expected: 'FINDING',
          anchors: contract.anchors,
          location: contract.anchors.map((row) => row.path + ':' + row.line).join(' ||| '),
          quote: contract.anchors.map((row) => row.quote).join(' ||| '),
        });
        if (proof.state !== 'LOCATED_NOT_ADJUDICATED') {
          errors.push(`${item.id}: invalid pinned fixture anchors (${proof.reason})`);
        }
      }
    }
    if (item.fixture) {
      const fixture = path.resolve(repoRoot, item.fixture);
      if (!fixture.startsWith(`${repoRoot}${path.sep}`) || !fs.existsSync(fixture)) errors.push(`${item.id}: invalid fixture ${item.fixture}`);
    }
  }
  for (const skill of specialists) {
    const kinds = coverage.get(skill) || new Set();
    if (!kinds.has('positive')) errors.push(`${skill}: missing positive case`);
    if (!kinds.has('control')) errors.push(`${skill}: missing control case`);
  }
  return errors;
}

function selectedCases(corpus, options) {
  if (options.all) return corpus.cases;
  const skills = new Set(options.skills), cases = new Set(options.cases);
  if (skills.size === 0 && cases.size === 0) throw new Error('Choose --all, --skill NAME, or --case ID.');
  for (const skill of skills) if (!corpus.cases.some((x) => x.skill === skill)) throw new Error(`Unknown skill in corpus: ${skill}`);
  for (const id of cases) if (!corpus.cases.some((x) => x.id === id)) throw new Error(`Unknown case id: ${id}`);
  return corpus.cases.filter((x) => skills.has(x.skill) || cases.has(x.id));
}

function ensureDir(dir) { fs.mkdirSync(dir, { recursive: true }); }

function evaluatorSystem(skillText, skillName) {
  return `${skillText}\n\n# Behavioral evaluation overlay\n\nApply only the invariant owned by \`${skillName}\`.\nDo not manufacture a finding because this is an evaluation.\nDo not claim a neighboring specialist's defect as this skill's finding.\nUse repository evidence when a fixture is supplied.\nAfter the normal skill-defined review append exactly:\n\nEVAL_VERDICT: FINDING|CLEAN\nEVAL_SCOPE: ${skillName}|NONE\nEVAL_EVIDENCE: <one concise evidence statement>\nEVAL_EVIDENCE_LOCATION: relative/path:line | NONE\nEVAL_EVIDENCE_QUOTE: <exact substring from that one line> | NONE\n\nFor a FINDING in a supplied repository fixture, give exact file lines and quotes.\nIf two separate source locations prove the finding, separate locations and quotes\nwith the literal separator ||| in matching order.\nDo not invent a path, line, or quote. A citation is only localization, not proof\nthat the cited text establishes the finding. For CLEAN return NONE for both.\n\nUse FINDING only when this skill's own invariant is violated. Use CLEAN when the scenario is safe or belongs to another specialist.`;
}

function casePrompt(item) {
  if (item.blind) return 'Inspect the entire current repository for reachable defects in the declared review scope. Discover and justify the outcome from repository evidence. Do not assume a defect exists. Report only the strongest proven finding or a scoped clean result. No gold expectations or file locations are supplied.';
  const source = item.fixture
    ? 'Inspect the files in the current working directory; they are the complete fixture for this case.'
    : 'The scenario below is the complete evidence for this bounded case.';
  return `Evaluate this case using the loaded specialist skill.\n\nCASE ID: ${item.id}\n${source}\n\nSCENARIO:\n${item.scenario}\n\nDo not infer the expected answer from case metadata. Follow the skill invariant and false-positive controls.`;
}

function run(command, args, cwd, env) {
  const started = process.hrtime.bigint();
  const result = spawnSync(command, args, { cwd, env, encoding: 'utf8', maxBuffer: 50 * 1024 * 1024 });
  return {
    status: typeof result.status === 'number' ? result.status : 1,
    stdout: result.stdout || '', stderr: result.stderr || '',
    error: result.error ? String(result.error.message || result.error) : '',
    elapsedMs: Math.round(Number(process.hrtime.bigint() - started) / 1e6),
  };
}

function runPi(systemPrompt, prompt, cwd, model) {
  const args = ['--tools', 'read,grep,find,ls', '--system-prompt', systemPrompt];
  if (model) args.push('--model', model);
  args.push('-p', prompt);
  return run(process.env.PI_BIN || 'pi', args, cwd, process.env);
}

function copyOpenCodeConfig(tempHome) {
  const source = process.env.XDG_CONFIG_HOME ? path.join(process.env.XDG_CONFIG_HOME, 'opencode')
    : process.env.HOME ? path.join(process.env.HOME, '.config', 'opencode') : '';
  const target = path.join(tempHome, '.config', 'opencode');
  ensureDir(target);
  if (source && fs.existsSync(source)) fs.cpSync(source, target, { recursive: true, force: false, errorOnExist: false });
  return target;
}

function openCodeAgent(systemPrompt) {
  return `---\ndescription: Read-only behavioral evaluator for one canonical cookbook skill.\nname: skill-benchmark\nmode: primary\npermission:\n  "*": deny\n  read: allow\n  grep: allow\n  glob: allow\n  list: allow\n  find: allow\n  ls: allow\n---\n\n${systemPrompt}\n`;
}

function runOpenCode(systemPrompt, prompt, cwd, model) {
  const tempHome = fs.mkdtempSync(path.join(os.tmpdir(), 'agents-cookbook-skill-eval-'));
  try {
    const configDir = copyOpenCodeConfig(tempHome);
    const agentDir = path.join(configDir, 'agents'); ensureDir(agentDir);
    fs.writeFileSync(path.join(agentDir, 'skill-benchmark.md'), openCodeAgent(systemPrompt));
    const env = { ...process.env, HOME: tempHome, XDG_CONFIG_HOME: path.join(tempHome, '.config') };
    const args = ['run', '--dir', cwd, '--agent', 'skill-benchmark'];
    // Only an explicit operator selection overrides native model resolution.
    if (model) args.push('--model', model);
    args.push(prompt);
    return run(process.env.OPENCODE_BIN || 'opencode', args, cwd, env);
  } finally { fs.rmSync(tempHome, { recursive: true, force: true }); }
}

function parseResult(output) {
  const verdict = output.match(/^EVAL_VERDICT:\s*(FINDING|CLEAN)\s*$/mi);
  const scope = output.match(/^EVAL_SCOPE:\s*([A-Za-z0-9_-]+|NONE)\s*$/mi);
  const evidence = output.match(/^EVAL_EVIDENCE:\s*(.+)\s*$/mi);
  const location = output.match(/^EVAL_EVIDENCE_LOCATION:\s*(.+)\s*$/mi);
  const quote = output.match(/^EVAL_EVIDENCE_QUOTE:\s*(.+)\s*$/mi);
  return {
    verdict: verdict ? verdict[1].toUpperCase() : '',
    scope: scope ? scope[1] : '',
    evidence: evidence ? evidence[1].trim() : '',
    location: location ? location[1].trim() : '',
    quote: quote ? quote[1].trim() : '',
  };
}

function persist(result, base) {
  if (!base) return;
  const dir = path.join(base, result.runtime, result.arm || 'skill', result.id); ensureDir(dir);
  const receiptPath = path.join(dir, 'result.json');
  // Never reinterpret a past execution by overwriting its canonical receipt.
  // An explicit new artifacts directory is required for another replicate.
  if (fs.existsSync(receiptPath)) {
    throw new Error('Execution receipt already exists (choose a fresh --artifacts-dir): ' + receiptPath);
  }
  const copy = { ...result }; delete copy.stdout; delete copy.stderr;
  fs.writeFileSync(receiptPath, `${JSON.stringify(copy, null, 2)}\n`, { flag: 'wx' });
  fs.writeFileSync(path.join(dir, 'stdout.txt'), result.stdout, { flag: 'wx' });
  fs.writeFileSync(path.join(dir, 'stderr.txt'), result.stderr, { flag: 'wx' });
}

function executeCase(item, options, cookbook) {
  const skillText = fs.readFileSync(cookbook.file(path.join('skills', item.skill, 'SKILL.md')), 'utf8');
  const systemPrompt = options.withoutSkill
    ? 'You are a read-only code reviewer. Investigate repository identity authority and report only proven defects. Do not guess. Finish with EVAL_VERDICT: FINDING|CLEAN, EVAL_SCOPE: NONE, EVAL_EVIDENCE: <evidence>, EVAL_EVIDENCE_LOCATION: <path:line or NONE>, EVAL_EVIDENCE_QUOTE: <exact quote or NONE>.'
    : evaluatorSystem(skillText, item.skill);
  const prompt = casePrompt(item);
  const cwd = item.fixture ? path.resolve(repoRoot, item.fixture) : options.repoDir;
  const experimentalAuthority = resolveAuthority({ item, options, cookbook, cwd, prompt, skillText });
  const execution = options.runtime === 'pi' ? runPi(systemPrompt, prompt, cwd, options.model) : runOpenCode(systemPrompt, prompt, cwd, options.model);
  assertUnchanged(experimentalAuthority, cwd);
  const parsed = parseResult(execution.stdout), expectedScope = options.withoutSkill ? 'NONE' : item.expected === 'FINDING' ? item.skill : 'NONE';
  const proof = qualifyEvidence({
    verdict: parsed.verdict,
    evidence: parsed.evidence,
    location: parsed.location,
    quote: parsed.quote,
    fixture: item.fixture ? cwd : null,
    expected: item.expected,
    anchors: item.evidence_contract ? item.evidence_contract.anchors : null,
  });
  const qualified = assessSkillOutcome({
    executionStatus: execution.status, parsed, expected: item.expected,
    skill: options.withoutSkill ? 'NONE' : item.skill, proof,
  });
  const pass = qualified.pass;
  return { id: item.id, skill: item.skill, kind: item.kind, arm: options.withoutSkill ? 'baseline' : 'skill', blinded: !!item.blind, runtime: options.runtime, model: options.model || null,
    expected: item.expected, expectedScope, status: pass ? 'pass' : 'fail', exitCode: execution.status,
    elapsedMs: execution.elapsedMs, outputChars: execution.stdout.length, verdict: parsed.verdict || null,
    scope: parsed.scope || null, evidence: parsed.evidence || null,
    evidenceQualification: proof.state, evidenceReason: proof.reason,
    evidenceLocation: proof.location || null,
    qualification: {
      capability: qualified.capability,
      classificationPass: qualified.classificationPass,
      evidencePass: qualified.evidencePass,
      semanticProof: qualified.semanticProof,
      repositoryDiscovery: qualified.repositoryDiscovery,
      repair: qualified.repair,
    },
    error: execution.error || null,
    experimentalAuthority,
    stdout: execution.stdout, stderr: execution.stderr };
}

function main() {
  let options;
  try { options = parseArgs(process.argv.slice(2)); }
  catch (error) { process.stderr.write(`Error: ${error.message}\n`); usage(); process.exit(2); }
  const cookbook = resolveCookbookSource(options.cookbookRoot);
  options.repoDir = options.repoDir || cookbook.root;
  const specialists = new Set(catalogSpecialists(cookbook));
  const corpus = JSON.parse(fs.readFileSync(options.corpus, 'utf8'));
  const errors = validateCorpus(corpus, cookbook, specialists);
  if (errors.length) { for (const error of errors) process.stderr.write(`CORPUS_ERROR ${error}\n`); process.exit(1); }
  if (options.validateCorpus) {
    const confusionSkills = new Set(corpus.cases.filter((item) => item.kind === 'confusion').map((item) => item.skill));
    const fixtureCases = corpus.cases.filter((item) => !!item.fixture);
    process.stdout.write(`SUMMARY status=pass cases=${corpus.cases.length} specialists=${specialists.size} confusion_skills=${confusionSkills.size} skills_without_confusion=${specialists.size - confusionSkills.size} fixture_cases=${fixtureCases.length} scenario_only_cases=${corpus.cases.length - fixtureCases.length}\n`);
    return;
  }
  if (options.list) { for (const item of corpus.cases) process.stdout.write(`CASE id=${item.id} skill=${item.skill} kind=${item.kind} expected=${item.expected} fixture=${item.fixture || 'none'}\n`); return; }
  if (!['opencode', 'pi'].includes(options.runtime)) { process.stderr.write('Error: --runtime must be opencode or pi.\n'); process.exit(2); }
  const cases = selectedCases(corpus, options);
  if (options.withoutSkill && cases.some(item => !item.blind)) throw new Error('--without-skill requires all selected cases to be blinded');
  if (options.artifactsDir) ensureDir(options.artifactsDir);
  const results = [];
  for (const item of cases) {
    process.stdout.write(`CASE id=${item.id} skill=${item.skill} runtime=${options.runtime} status=running\n`);
    const result = executeCase(item, options, cookbook); persist(result, options.artifactsDir); results.push(result);
    process.stdout.write(`CASE id=${result.id} skill=${result.skill} runtime=${result.runtime} status=${result.status} expected=${result.expected} verdict=${result.verdict || 'missing'} scope=${result.scope || 'missing'} elapsed_ms=${result.elapsedMs} output_chars=${result.outputChars}\n`);
  }
  const passed = results.filter((x) => x.status === 'pass').length, failed = results.length - passed;
  process.stdout.write(`SUMMARY status=${failed ? 'fail' : 'pass'} runtime=${options.runtime} cases=${results.length} passed=${passed} failed=${failed}\n`);
  if (failed) process.exitCode = 1;
}

try { main(); }
catch (error) { process.stderr.write(`Error: ${error.message}\n`); process.exitCode = 1; }
