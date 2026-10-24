# Independent semantic source review — blinded handoff protocol

**Purpose:** decide whether each frozen *question* has a single defensible
source owner, multiple defensible owners, or insufficient evidence. This is a
first-pass **source review**, not an oracle approval or an agent benchmark.

## What you may inspect

The only authorized inputs for the first pass are `questions.json` and the
frozen repository snapshot under `source/`. `manifest.json` binds the bytes;
`verify.py` checks received integrity. `start_review.py` can create a private,
deliberately incomplete response draft. No model or agent must be invoked by
the kit itself.

**Do not inspect or request** EvalShot source, benchmark task definitions,
historical oracle results, previous reviewer decisions, source-audit reports,
grader outputs, or any supplied expected path/symbol. The reviewer should use
a separate clean agent session and workspace with no prior exposure to the
expected answers. If you already know any expected answer, report that
exposure rather than claiming a blind review.

The snapshot contains historical source, not an executable trust qualification.
Inspect text **read-only**. Do not run repository scripts, install dependencies,
contact external services, modify `source/`, or add new files to the kit.
An agent may use its own ordinary file-search and text-reading tools outside
the kit's content tree; EvalShot does not own the agent loop or tool selection.

## Review each question independently

1. Read exactly one entry in `questions.json`. Preserve its `packet_id`,
   `task_digest`, and pinned `repository.commit` / `repository.tree`.
2. Parse the *behavioral meaning* of `frozen_prompt`. The supplied prompt may
   reveal a symbol spelling; that is question input, **not** evidence that a
   particular file or method is the semantic owner.
3. Search the pinned `source/` text yourself. Inspect definitions, callers,
   aliases, dispatch, delegation, result contracts, and relevant tests where
   visible. Distinguish *identical names* from *identical responsibilities*.
4. Challenge the leading candidate. Identify other plausible owners,
   competing layers or interpretable task boundaries. A single matching
   declaration does **not** establish unique behavioral ownership.
5. Ground findings in repository-relative paths and **positive** line numbers
   from the frozen source. For **each proposed owner file**, cite at least
   one actual line in that same file; additional caller, test, and delegation
   evidence may cite other files. These known-symbol tasks expect Python
   definitions: a README or other non-Python file can be background evidence,
   but the current checker cannot establish its claimed symbol declaration.
   Cite at least one line **inside each named Python declaration's own
   span** (including its decorators or body), not just an unrelated class
   definition, import, comment or helper elsewhere in the same file.
   A matching name defined only inside another function is a local helper,
   not a supported module-level or class-method owner; multiple observable
   declarations of the same name in one file are not syntactically unique.
   A matching declaration under an `if` / `TYPE_CHECKING`, `try`, `match`,
   loop, or `with` block is also **guarded**: its presence in the AST does
   not prove unconditional availability. A separately unguarded definition
   with a guarded same-name alternative must not be called unique.
   Also inspect whether a candidate's module-level name or class attribute
   is reassigned, imported over, deleted or monkeypatched via a statement
   such as `ClassName.method = replacement`. The presence of a `def`
   does not prove that its binding remains intact on all paths. A
   same-name local variable in another function, or a binding in a
   different class, is not automatically an override of the candidate.
   Check the **source order**: an unconditional definition after an
   assignment or explicit import normally supersedes that earlier binding.
   A potential assignment/import/delete **after** the observed definition
   remains unresolved. A later `from package import *` at module scope is
   also unresolved, because the checker cannot infer what the wildcard
   brings into the namespace. Do not infer runtime execution or ownership
   from static source order alone.
   Also check implicit binding targets: `for name in ...`, `with resource
   as name`, `except Exception as name`, and `match` captures (including
   starred and mapping-rest captures) bind names without an ordinary
   assignment. A same-name binding target **after** a candidate `def`
   remains an unresolved potential overwrite. A target before a later
   plain declaration, or inside an unrelated local function, is not by
   itself a later override. The checker observes syntax only; it does
   not evaluate whether a loop, exception, context manager or case runs.
   Starred unpacking targets (`*name`) in assignments, `for`, and
   `with ... as` also bind their wrapped names. Inspect each nested
   tuple/list target, including class-body writes and function-body writes
   declared `global`; ignoring the star can hide a competing binding.
   Inspect definition-time expressions too: function/method decorators,
   parameter defaults and annotations, plus class decorators, bases and
   metaclass keywords are evaluated in their *enclosing* module/class
   namespace. A binding expression there may replace an earlier owner,
   while a binding inside an unrelated function body or class body does
   not automatically replace a name in the enclosing namespace.
   A function's own decorator/default/annotation binding occurs *before*
   its final `def` name assignment, and so is superseded by that function
   declaration; a later binding in a separate statement remains uncertain.
   Lambda defaults also evaluate in their enclosing scope, whereas the
   lambda body is local and is not executed by merely defining it.
   Search for deferred writes inside functions declaring `global name`:
   an assignment, import, deletion, loop target or nested definition of
   that name can replace an otherwise observed *module-level* candidate
   if the function is called. Merely reading a global name, assigning to
   a same-spelled local variable without `global`, or assigning to an
   unrelated class attribute must not be treated as a module mutation.
   The checker cannot determine when such functions run.
   Python also permits `global name` inside a **class body**. Assignments,
   imports, deletes, loop/match targets and even `def name` within that
   body then bind the *module* name instead of a class attribute. If the
   class is nested inside an uncalled function, those writes are deferred.
   Without an explicit class-body global declaration, a same-name class
   attribute does not automatically overwrite the module owner. A function
   definition redirected by class-body `global` is not a regular method.
   Also inspect explicit writes to `Owner.method` inside another class
   suite or inside a deferred function body (assignment, deletion, augmented
   assignment or binding target). Unlike an unrelated class-local name,
   this attribute spelling could potentially alter the nominated method.
   The checker cannot prove runtime receiver identity or callback execution.
   Explain the specific semantic relationship at each citation. These rules
   observe only source syntax, not whether the declaration owns the behavior.
6. Select one provisional decision:
   - `unique`: exactly one defensible owner, with evidence and alternatives
     explicitly challenged.
   - `ambiguous`: at least two defensible owners; preserve both.
   - `invalid`: the frozen question or proposed owner interpretation is not
     defensible on source evidence; explain the defect.
   - `unable_to_review`: source or evidence is insufficient; do **not**
     fabricate a source-backed decision.
7. Repeat independently for the second question. Do not use an answer from the
   first question as the second question's oracle.

## Returning private findings

After inspection, use the standalone `start_review.py` to create a new
private response file **outside this kit**, using the producer-provided
manifest SHA-256. It carries only task, packet, and source identities. All
decision fields are deliberately invalid/unreviewed until you fill them.

Complete each `reviews[]` record with your actual `reviewer_id`,
`decision`, `prior_expected_answer_access` (boolean, declared truthfully),
`source_inspected` (boolean), `candidate_locations` (`path`, `symbol`),
a concise `reason`, and `source_evidence` entries (`path`, `line`,
`observation`). For `unique` provide **exactly one** candidate location;
for `ambiguous` provide **at least two** distinct locations. Any concrete
decision requires genuine source inspection and source evidence. For
`unable_to_review`, leave candidate locations/evidence empty and mark
`source_inspected=false`.

Do not attach the private answers to a GitHub PR, CI artifact, EvalShot
repository, agent trial workspace, or the source kit. Return them directly
through the designated private review channel. The first-pass return parser
only checks shape and source identity; separate source-citation checks only
verify that cited lines and declarations exist. Neither attests your identity
nor turns a decision into an approved EvalShot oracle.

## Scope and stop conditions

If you recognize a prior expected answer, disclose exposure. If the task is
under-specified, record ambiguity or inability instead of making an answer
look unique. Do not rewrite prompts, frozen source, benchmark oracles, grading
rules, or reviewer records. Do not mark an experiment ready.

**End state:** two source-backed *provisional* first-pass findings, or
explicitly unresolved cases. `benchmarks.harness.oracle_reviews` remains the
only EvalShot authority for independent oracle review, and there is still
**no** native baseline, agent-effectiveness claim, or proof of reviewer
independence from merely preparing this kit.
