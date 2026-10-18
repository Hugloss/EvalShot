# Security policy

EvalShot owns executable benchmark/evaluation harnesses and the Agent
Economics support toolkit. Security reports involving these tools, including
execution of repository-declared commands, belong in this repository.
Prompt-library installation and OpenCode/Pi reviewer adapter defects belong
in [agentsCookbook](https://github.com/Hugloss/agentsCookbook/blob/main/SECURITY.md).

## Reporting

Use GitHub's private **Report a vulnerability** option when available.
Otherwise request a private reporting path in a minimal public issue. Do not
publish credentials, sensitive data, or a working exploit in a public issue.

Include affected source/revision, the violated boundary, reproduction,
impact, expected behavior and a minimal proposed mitigation.

Until tagged releases and a backport policy exist, fixes target current
`main`; older commits are not automatically supported.

## Agent Economics command trust boundary

The Agent Economics capability bridge may execute repository-declared commands.
**Selecting a command manifest authorizes its argv entries to run.** Treat a
manifest from an untrusted repository like other executable repository code.

The bridge reduces accidental authority by using argv arrays rather than shell
strings, confining command cwd to the repository, bounding stdout/stderr and
wall time, terminating process trees where the platform capability exists,
and comparing tracked bytes before and after verification commands.

These controls do not remove the child's environment variables, credentials,
filesystem access outside cwd or network access. Host and sandbox policy own
those capabilities. Unexpected tracked mutation is a policy failure, not
automatic rollback. Explicitly declare allowed generated/cache paths.

Capability output reports whether process-tree termination, mutation guarding,
network isolation and sandbox isolation are actually available. Do not infer
an unsupported control from a successful command.

## Benchmark and evaluation boundary

A product under test does not grade itself. Subject-controlled output cannot
replace frozen task/oracle identity or the evaluator's evidence and scoring
authority. Benchmarks that require model, native tooling or external hosts
must report missing or unverified observations as unknown/incomplete rather
than claim an independent security or correctness guarantee.
