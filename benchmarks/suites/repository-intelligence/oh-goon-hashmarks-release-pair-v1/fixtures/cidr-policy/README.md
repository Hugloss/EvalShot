# Offline IP access-policy lookup

Repair compile_policy(rules, *, default='deny') and the returned policy.decide(address).
Use only the Python standard library; ipaddress owns address/network parsing,
strict host-bit validation, network membership and canonical text formatting.
No DNS, networking or manual replacement parser is needed.

Rules are a one-pass synchronous iterable of dicts with required string fields
network and action. Actions/default are exactly 'allow' or 'deny'; reject all
other values with ValueError, validating default before consuming rules.
Require an explicit slash in network, reject scoped '%' forms, and use native
ipaddress.ip_network(network, strict=True) for the remaining accepted syntax.
Do not trim API strings. Ignore extra rule fields. Fully validate every rule,
including unreachable or redundant ones, while consuming the iterable once.
Identical canonical networks with equal actions are allowed; conflicting actions
for the same canonical network raise ValueError. Iterator exceptions propagate
unchanged, and validation stops at the first invalid rule.

decide accepts only an unscoped IP address string, using native ip_address.
Return a fresh dict {'action': ACTION, 'network': CANONICAL_NETWORK_OR_NONE}.
Choose the matching rule with the longest prefix, regardless of input order.
IPv4 and IPv6 stay separate; IPv4-mapped IPv6 remains IPv6. Without a matching
rule, return the configured default and network None. Empty policies are valid.
Never mutate caller input, retain mutable rule references or alias result dicts.
Later caller edits and failed compilations must not change a compiled policy.

CLI: python -m netpolicy POLICY QUERIES (exactly two arguments).
POLICY is a UTF-8 JSON object with required rules array and optional default.
Extra object fields are ignored. QUERIES is a UTF-8 text path, or '-' for stdin;
strip each query line and ignore blanks, preserving order and duplicate queries.
Emit exactly one JSON array plus newline. Each row contains address (native
canonical text), action and network. Validate all input and serialize the whole
array before stdout: any usage, I/O, decoding or validation failure exits 2,
prints a useful stderr diagnostic, and leaves stdout empty. Empty queries emit [].
Do not change input files. There is no global process-memory-bound claim.

Only netpolicy/policy.py and netpolicy/__main__.py may change. Keep __init__.py
and this README intact. Independent verification lives outside the source tree.
