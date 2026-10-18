#!/usr/bin/env node
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { resolveCookbookSource } = require('./cookbook-source');

const opencodeBin = process.env.OPENCODE_BIN || 'opencode';
const {
  exportSession,
  extractFinalAnswer,
  findSessionId,
  runCommand,
  runSession,
} = require('./opencode-runtime.js');

const FINAL_CONTRACTS = {
  'ping-pong-plan': [
    '# Final Plan',
    '## Subagent Run Summary',
    '## Assumptions',
    '## Steps',
    '## Files / Areas to Inspect',
    '## Risks and Edge Cases',
    '## Validation',
    '## Rollback / Recovery',
    '## Remaining Open Questions',
  ],
  'subagent-router': [
    '# Subagent Router Result',
    '## Selected Reviewer',
    '## Task Status',
    '## Reviewer Feedback',
    '## Router Notes',
  ],
};

const BENCHMARKS = {
  'ping-pong-plan': [
    {
      name: 'prompt-contract-checker',
      title: 'Prompt / Config Edit',
      prompt: `Improve the \`plan-contract-guard\` skill so \`plan-contract-checker\` fails final plans with implementation-blocking open questions. Do not add another agent.`,
      expectedContract: 'ping-pong-plan',
    },
    {
      name: 'flow-documentation',
      title: 'Flow Documentation',
      prompt: `Create documentation that explains the ping-pong planning flow, all agents, and the final plan ownership rules.`,
      expectedContract: 'ping-pong-plan',
    },
    {
      name: 'permission-audit',
      title: 'Permission Audit',
      prompt: `Review whether all ping-pong subagents have permissions that match their read-only roles, and plan fixes for any mismatch.`,
      expectedContract: 'ping-pong-plan',
    },
    {
      name: 'validation-hardening',
      title: 'Validation Hardening',
      prompt: `Make the planning flow stricter about validation quality and rollback verification without adding more agents.`,
      expectedContract: 'ping-pong-plan',
    },
    {
      name: 'multi-file-prompt-refactor',
      title: 'Multi-File Prompt Refactor',
      prompt: `Update the ping-pong flow so subagents independently verify central repo claims instead of relying only on coordinator context.`,
      expectedContract: 'ping-pong-plan',
    },
  ],
  'subagent-router': [
    {
      name: 'explicit-fact-auditor',
      title: 'Explicit Fact Auditor',
      prompt: `Ask \`plan-fact-auditor\` to check this plan for unsupported repo claims: <plan text>.`,
      expectedSubagent: 'plan-fact-auditor',
      expectedContract: 'subagent-router',
    },
    {
      name: 'best-fit-validation',
      title: 'Best-Fit Validation',
      prompt: `Find missing tests, manual checks, and acceptance criteria in this plan: <plan text>.`,
      expectedSubagent: 'plan-validation-designer',
      expectedContract: 'subagent-router',
    },
    {
      name: 'best-fit-coverage-design',
      title: 'Best-Fit Coverage Design',
      prompt: `Review whether these tests follow real usage and would catch realistic failures instead of merely executing covered lines: <test summary>.`,
      expectedSubagent: 'plan-coverage-reviewer',
      expectedContract: 'subagent-router',
    },
    {
      name: 'best-fit-risk-review',
      title: 'Best-Fit Risk Review',
      prompt: `Review this plan for blockers, hidden assumptions, and scope creep: <plan text>.`,
      expectedSubagent: 'plan-red-team-gate',
      expectedContract: 'subagent-router',
    },
    {
      name: 'build-review-mode',
      title: 'Build Review Mode',
      prompt: `Use one reviewer to review this implementation evidence for missing validation: changed files, diff summary, validation output, and remaining risks are provided inline.`,
      expectedSubagent: 'plan-validation-designer',
      expectedContract: 'subagent-router',
    },
    {
      name: 'full-flow-requested',
      title: 'Full Flow Requested',
      prompt: `Run the full nine-reviewer ping-pong planning flow on this request.`,
      expectedNoSubagent: true,
      expectedContract: 'subagent-router',
    },
  ],
};

function usage() {
  process.stdout.write(`Usage: scripts/run-opencode-benchmarks.js --cookbook-root DIR [--suite ping-pong-plan|subagent-router|all] [--benchmark NAME] [--repo DIR] [--artifacts-dir DIR] [--list] [--help]\n\n`);
  process.stdout.write('Replay the fixed local OpenCode benchmark set, capture session IDs, validate session structure, and check final-answer contracts.\n');
  process.stdout.write('--cookbook-root selects the agentsCookbook checkout supplying agents, skills and installation/session helpers. --repo defaults to that checkout. --list and --help do not require a checkout.\n');
}

function parseArgs(argv) {
  const options = {
    suite: 'all',
    benchmarks: [],
    repoDir: '',
    cookbookRoot: '',
    artifactsDir: '',
    list: false,
    help: false,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--help' || arg === '-h') {
      options.help = true;
    } else if (arg === '--list') {
      options.list = true;
    } else if (arg === '--suite') {
      i += 1;
      if (i >= argv.length) {
        throw new Error('--suite requires ping-pong-plan, subagent-router, or all.');
      }
      options.suite = argv[i];
    } else if (arg === '--benchmark') {
      i += 1;
      if (i >= argv.length) {
        throw new Error('--benchmark requires a benchmark name.');
      }
      options.benchmarks.push(argv[i]);
    } else if (arg === '--cookbook-root') {
      i += 1;
      if (i >= argv.length) {
        throw new Error('--cookbook-root requires a directory path.');
      }
      options.cookbookRoot = argv[i];
    } else if (arg === '--repo') {
      i += 1;
      if (i >= argv.length) {
        throw new Error('--repo requires a directory path.');
      }
      options.repoDir = path.resolve(argv[i]);
    } else if (arg === '--artifacts-dir') {
      i += 1;
      if (i >= argv.length) {
        throw new Error('--artifacts-dir requires a directory path.');
      }
      options.artifactsDir = path.resolve(argv[i]);
    } else {
      throw new Error(`Unknown option: ${arg}`);
    }
  }

  return options;
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function contractCheck(agentName, text) {
  const requiredHeadings = FINAL_CONTRACTS[agentName] || [];
  const missing = requiredHeadings.filter((heading) => !text.includes(heading));
  const startsCorrectly = requiredHeadings.length > 0 ? text.trimStart().startsWith(requiredHeadings[0]) : true;
  return {
    pass: startsCorrectly && missing.length === 0,
    missing,
    startsCorrectly,
  };
}

function selectBenchmarks(options) {
  let selectedSuites;
  if (options.suite === 'all') {
    selectedSuites = Object.keys(BENCHMARKS);
  } else if (Object.prototype.hasOwnProperty.call(BENCHMARKS, options.suite)) {
    selectedSuites = [options.suite];
  } else {
    throw new Error(`Unknown suite: ${options.suite}`);
  }

  const selected = [];
  for (const suite of selectedSuites) {
    for (const benchmark of BENCHMARKS[suite]) {
      if (options.benchmarks.length === 0 || options.benchmarks.includes(benchmark.name)) {
        selected.push({ suite, ...benchmark });
      }
    }
  }

  if (options.benchmarks.length > 0) {
    const available = new Set(selected.map((benchmark) => benchmark.name));
    const missing = options.benchmarks.filter((name) => !available.has(name));
    if (missing.length > 0) {
      const known = Object.values(BENCHMARKS).flat().map((benchmark) => benchmark.name).sort().join(', ');
      throw new Error(`Unknown benchmark name(s): ${missing.join(', ')}. Known benchmarks: ${known}`);
    }
  }

  return selected;
}

function printList(benchmarks) {
  for (const benchmark of benchmarks) {
    const agent = benchmark.suite === 'ping-pong-plan' ? 'ping-pong-plan' : 'subagent-router';
    const expectation = benchmark.expectedNoSubagent
      ? 'expected=none'
      : benchmark.expectedSubagent
        ? `expected=${benchmark.expectedSubagent}`
        : 'expected=ping-pong-plan';
    process.stdout.write(`BENCHMARK suite=${benchmark.suite} name=${benchmark.name} agent=${agent} ${expectation} title=${benchmark.title}\n`);
  }
}

function printSummary(results, artifactsDir) {
  const passed = results.filter((result) => result.status === 'pass').length;
  const failed = results.length - passed;
  process.stdout.write(`SUMMARY status=${failed === 0 ? 'pass' : 'fail'} benchmarks=${results.length} passed=${passed} failed=${failed} artifacts=${artifactsDir}\n`);
}

async function main() {
  let options;
  try {
    options = parseArgs(process.argv.slice(2));
  } catch (error) {
    process.stderr.write(`Error: ${error.message}\n`);
    usage();
    process.exit(2);
  }

  if (options.help) {
    usage();
    return;
  }

  const selectedBenchmarks = selectBenchmarks(options);
  if (options.list) {
    printList(selectedBenchmarks);
    process.stdout.write(`TOTAL benchmarks=${selectedBenchmarks.length}\n`);
    return;
  }

  const cookbook = resolveCookbookSource(options.cookbookRoot);
  const linkScript = cookbook.file('scripts/link-opencode-local.sh');
  const checkScript = cookbook.file('scripts/check-opencode-session.sh');
  fs.accessSync(linkScript, fs.constants.X_OK);
  fs.accessSync(checkScript, fs.constants.X_OK);
  options.repoDir = options.repoDir || cookbook.root;

  const runId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  const artifactsBase = options.artifactsDir || fs.mkdtempSync(path.join(os.tmpdir(), 'agents-cookbook-benchmarks-'));
  const artifactsDir = options.artifactsDir ? path.join(artifactsBase, runId) : artifactsBase;
  ensureDir(artifactsDir);

  const tempHome = path.join(artifactsDir, 'home');
  const globalDir = path.join(tempHome, '.config', 'opencode');
  ensureDir(globalDir);

  const setupResult = runCommand(linkScript, ['--global-dir', globalDir], {
    cwd: cookbook.root,
    env: { HOME: tempHome },
  });

  const setupLine = `SETUP status=${setupResult.status === 0 ? 'pass' : 'fail'} home=${tempHome} global_dir=${globalDir}`;
  process.stdout.write(`${setupLine}\n`);
  if (setupResult.status !== 0) {
    process.stdout.write(setupResult.stdout);
    process.stderr.write(setupResult.stderr);
    process.exit(1);
  }

  const results = [];

  for (const benchmark of selectedBenchmarks) {
    const benchDir = path.join(artifactsDir, benchmark.suite, benchmark.name);
    ensureDir(benchDir);

    const title = `agents-cookbook-bench:${benchmark.suite}:${benchmark.name}:${runId}`;
    process.stdout.write(`BENCHMARK suite=${benchmark.suite} name=${benchmark.name} agent=${benchmark.expectedSubagent ? benchmark.expectedSubagent : benchmark.expectedNoSubagent ? 'none' : 'ping-pong-plan'} status=running title=${title}\n`);

    const started = runSession({
      opencodeBin,
      repoDir: options.repoDir,
      agent: benchmark.suite === 'ping-pong-plan'
        ? 'ping-pong-plan'
        : 'subagent-router',
      title,
      prompt: benchmark.prompt,
      homeDir: tempHome,
      pure: false,
    });
    const runStartedAt = started.startedAt;
    const runResult = started.command;
    fs.writeFileSync(path.join(benchDir, 'run.stdout'), runResult.stdout);
    fs.writeFileSync(path.join(benchDir, 'run.stderr'), runResult.stderr);

    const sessionId = await findSessionId({
      opencodeBin,
      homeDir: tempHome,
      repoDir: options.repoDir,
      title,
      startedAt: runStartedAt,
      pure: false,
    });
    fs.writeFileSync(path.join(benchDir, 'session-id.txt'), `${sessionId}\n`);

    let sessionCheck = {
      status: 1,
      stdout: '',
      stderr: '',
    };
    if (sessionId) {
      const checkArgs = ['--scope', 'session'];
      if (benchmark.expectedSubagent) {
        checkArgs.push('--expect-subagent', benchmark.expectedSubagent);
      } else if (benchmark.expectedNoSubagent) {
        checkArgs.push('--expect-no-subagent');
      }
      checkArgs.push(sessionId);

      sessionCheck = runCommand(checkScript, checkArgs, {
        cwd: options.repoDir,
        env: { HOME: tempHome },
      });
      fs.writeFileSync(path.join(benchDir, 'session-check.stdout'), sessionCheck.stdout);
      fs.writeFileSync(path.join(benchDir, 'session-check.stderr'), sessionCheck.stderr);
    } else {
      fs.writeFileSync(path.join(benchDir, 'session-check.stdout'), '');
      fs.writeFileSync(path.join(benchDir, 'session-check.stderr'), 'Unable to locate session ID for benchmark run.\n');
    }

    let exportStatus = 1;
    let exportOutput = '';
    let finalText = '';
    let contract = { pass: false, missing: [], startsCorrectly: false };
    if (sessionId) {
      const exportResult = exportSession({
        opencodeBin,
        repoDir: options.repoDir,
        sessionId,
        homeDir: tempHome,
        pure: false,
      });
      exportStatus = exportResult.status;
      exportOutput = exportResult.stdout;
      fs.writeFileSync(path.join(benchDir, 'session-export.txt'), exportResult.stdout);
      fs.writeFileSync(path.join(benchDir, 'session-export.stderr'), exportResult.stderr);

      if (exportResult.status === 0) {
        try {
          const parsed = extractFinalAnswer(exportResult.stdout);
          finalText = parsed.text;
          fs.writeFileSync(path.join(benchDir, 'final-answer.txt'), `${finalText}\n`);
          contract = contractCheck(benchmark.expectedContract, finalText);
        } catch (error) {
          finalText = '';
          fs.writeFileSync(path.join(benchDir, 'final-answer.txt'), `ERROR: ${error.message}\n`);
        }
      } else {
        fs.writeFileSync(path.join(benchDir, 'final-answer.txt'), 'ERROR: opencode export failed.\n');
      }
    } else {
      fs.writeFileSync(path.join(benchDir, 'session-export.txt'), '');
      fs.writeFileSync(path.join(benchDir, 'session-export.stderr'), 'No session ID was found.\n');
      fs.writeFileSync(path.join(benchDir, 'final-answer.txt'), 'ERROR: no session ID was found.\n');
    }

    const runPass = runResult.status === 0;
    const sessionPass = sessionId && sessionCheck.status === 0;
    const contractPass = contract.pass;
    const pass = runPass && sessionPass && exportStatus === 0 && contractPass;

    results.push({
      suite: benchmark.suite,
      name: benchmark.name,
      agent: benchmark.suite === 'ping-pong-plan' ? 'ping-pong-plan' : 'subagent-router',
      title,
      sessionId: sessionId || 'none',
      runStatus: runResult.status,
      sessionCheckStatus: sessionCheck.status,
      exportStatus,
      contractPass,
      contractMissing: contract.missing,
      status: pass ? 'pass' : 'fail',
    });

    process.stdout.write(
      `BENCHMARK suite=${benchmark.suite} name=${benchmark.name} agent=${results[results.length - 1].agent} status=${pass ? 'pass' : 'fail'} session_id=${sessionId || 'none'} run=${runPass ? 'pass' : 'fail'} session_check=${sessionPass ? 'pass' : 'fail'} contract=${contractPass ? 'pass' : 'fail'} artifacts=${benchDir}\n`,
    );

    if (!pass) {
      if (!runPass) {
        process.stdout.write(`RUN_FAILURE suite=${benchmark.suite} name=${benchmark.name} exit_code=${runResult.status}\n`);
      }
      if (!sessionPass) {
        process.stdout.write(`SESSION_FAILURE suite=${benchmark.suite} name=${benchmark.name} exit_code=${sessionCheck.status}\n`);
      }
      if (!contractPass) {
        process.stdout.write(`CONTRACT_FAILURE suite=${benchmark.suite} name=${benchmark.name} missing=${contract.missing.join('|') || 'none'}\n`);
      }
    }
  }

  printSummary(results, artifactsDir);
  if (results.some((result) => result.status === 'fail')) {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  process.stderr.write(`Error: ${error.stack || error.message}\n`);
  process.exit(1);
});
