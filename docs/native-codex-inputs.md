# Independent native Codex trials

Native Codex trials retain the selected host's configuration bytes, custom
profile files and recognized guidance (`AGENTS.md`, `AGENTS.override.md`,
`rules/`, `skills/`) in an independent, temporary `CODEX_HOME`. Optional native
managed/requirements configuration is transported as an input too. The adapter
does not rewrite model, provider, reasoning, compaction, explicit false or zero
settings. Native CLI resolution remains their authority.

An absent root configuration remains absent, with different input identity from
an empty configuration file. The evaluator does not manufacture model/provider
defaults. Executable guidance assets retain executable behavior with private
owner mode `0700`; other regular inputs use `0600`. Input identity and drift
checks include those effective private modes as well as bytes and membership.

Configuration/guidance transport is bounded to recognized regular inputs.
For explicitly configured plugin IDs and additional installed IDs returned by
the native loader, the adapter transports all bounded installed version
candidates from Codex's documented plugin-cache layout,
including their opaque resource bytes, executable modes, directories and
internal links. Codex chooses the installed version; directory ordering never
chooses a version. Matching marketplace catalogs and selected source packages
remain distinct from installed artifacts because native installation can add
runtime configuration. Plugin data, staging, conversation/session databases,
memories and unrelated cache entries do not seed a trial.

Custom file/path dependencies, user hook/notify dependencies, configured shared
database/log roots and personal marketplace descriptors outside the supported
transport fail readiness explicitly. Unsupported inputs cannot silently vanish
from a faithful native trial. Existing trial repository discovery and trust
remain native-owned; the harness does not grant trust or resolve precedence.

Native authentication selection is preserved. When the selected native home
contains `auth.json`, its bytes are copied opaquely with mode `0600`; no token or
credential value is parsed into evidence. Other authentication modes remain
CLI-owned. The private home is under the existing trial temporary
directory, so context disposal removes it. Receipt publication includes only
the explicit event and trace artifacts, never the private configuration/auth
directory.

For supported CLI diagnostics, `codex doctor --json` supplies the resolved model,
provider and configuration scope. Only those redacted fields and authentication
health status are retained. Unsupported/malformed diagnostics and unreported
fields remain `unknown`; root-file values and evaluator defaults cannot fill
them in. Ordinary diagnostics run in private state. The separate source plugin
selection reference permits source reads only under a read-only filesystem,
with writable scratch outside the source home. Source to private resolved
configuration equivalence remains unproved; input byte identity does not establish
full native-input fidelity, model attention or causal benefit. Resolved reasoning
effort is currently unreported and remains unknown.

Native execution uses `--no-daemon` and `--ephemeral` as well as the existing
workspace sandbox policy. Input membership, bytes/modes and executable identity
are checked around execution; drift makes the trial incomplete. Model-free
native help proves launch flag support before work. Transport-only source
pointers are removed from the model process environment.
Native client originator, CLI and MCP Node runtime inputs come explicitly from
the admitted source environment. Conversation/thread/session identifiers are
not transported. Changes to the admitted environment invalidate execution.

Plugin-enabled trials use a proven Bubblewrap filesystem namespace for the
entire native process tree. The original logical `CODEX_HOME` resolves to the
same private home inode, preserving absolute cached plugin commands and
environment without editing their JSON. The parent validates physical private
state paths; the child sees the original logical Codex home through this mapping.
The host filesystem is read-only;
workspace and trial control/state are writable. Exact installed Codex/runtime
helper executables under the aliased source home are separately read-only and
identity-bound. Networking and host read access remain available. This does not
prove isolation of remote or desktop services.

The native CLI's strict `plugin list --json` installed selection is compared
between the trial and a source reference with all original files read-only,
the same repository cwd and selected semantic environment, and private scratch.
This comparison retains native IDs, marketplaces, enabled state and versions;
physical input paths do not become configuration precedence. A configured
`enabled=true` entry that neither native selection includes remains a request
that the native inventory did not select. It is reported separately, rather
than being promoted into a loaded resource or silently removed from copied
configuration. Generic app-server catalogs and this CLI selection must join in
their shared identity scope.

Remote plugins use the exact backend identifier returned by native installed
inventory. Native `plugin/read` and `plugin/skill/read` qualify their detail and
skill contents. Only resource digests are published. Missing local paths are
valid for native remote delivery; missing content stays unavailable, and an
unreported version remains unknown. These are bounded observations, not a
promise that an external service can never change or that the model read a
resource. Local skill paths are checked through the private path alias.

Ambient plugins and MCP services retain native configuration. Only the declared
controlled subject is disabled for baseline or replaced with the exact admitted
exposure for treatment. Native effective configuration, selected plugin detail
and complete MCP inventory must agree after this final overlay. Missing native
selection, unknown enabled state, cached catalogs without runtime readiness,
alternate observed Hashmarks routes and stale canonical transport fields fail
qualification. Public plugin observations contain redacted identities,
capability names and digests; raw environment, commands, hooks, credentials and
native errors stay private. Fresh trial path prefixes are normalized only for
private comparison digests, never for configuration or executable behavior.

`diagnostic_required_tool` is available for native Codex just as for native
OpenCode. It adds an explicit, subject-bound required-use instruction and is
recorded in the agent definition. This arm is diagnostic and has a distinct
agent identity; it cannot enter the natural-availability comparison as voluntary
tool choice or establish a causal evidence effect.

The model-free qualification tests use an executable CLI fixture implementing
the observed version/doctor/MCP-list/exec interfaces. They exercise custom
configuration, false/zero preservation, private state, unknown diagnostics and
required-use prompts without calling a model.
