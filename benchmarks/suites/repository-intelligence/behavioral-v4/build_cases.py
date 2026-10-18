"""Author the additional frozen v4 cases. Never called by benchmark execution."""

from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parent
V3 = ROOT.parent / "behavioral-v3"
REPOSITORY = {
    "url": "https://github.com/Hugloss/agentsCookbook.git",
    "commit": "844dfa06d50b2f4402306b16a217b29946a3dedf",
    "tree": "c9e4687f45836f0e9254f3d9e6258f737686e76a",
}


def _case(
    family, number, question, expected, files, *, edit=None, authority=(), provenance=()
):
    return {
        "id": f"{family}-{number:02d}",
        "family": family,
        "question": question,
        "expected": expected,
        "files": files,
        "edit": edit,
        "authority_fields": list(authority),
        "provenance_fields": list(provenance),
    }


NEW = [
    _case(
        "post_change",
        2,
        "The reported path was rewritten with identical bytes. Decide which prior evidence needs replacement.",
        {"change": "unchanged", "invalidated": [], "revision_reusable": True},
        {
            "src/owner.py": "def widget():\n    return 'stable'\n",
            "tests/test_owner.py": "from src.owner import widget\ndef test_widget(): assert widget() == 'stable'\n",
            "observations/report.json": '{"reported_changed_path":"src/owner.py","bytes_changed":false}\n',
        },
        authority=("change", "invalidated"),
    ),
    _case(
        "post_change",
        3,
        "The supplied prior packet has a changed context identity. Decide whether its retained ownership and verification can be reused.",
        {
            "prior_packet": "untrusted",
            "reuse": "rejected",
            "next_evidence": "fresh-task-evidence",
        },
        {
            "src/owner.py": "def widget():\n    return 'stable'\n",
            "observations/prior.json": '{"schema":"hashmarks.task-evidence.v2","evidence_packet_identity":"forged","context_identity":"wrong"}\n',
        },
        authority=("prior_packet", "reuse"),
    ),
    _case(
        "change_impact",
        2,
        "Fix engine.Value to return the required value, then identify the dependent implementation and focused Go test.",
        {
            "changed_path": "benchmark_case/change_impact_02/engine/engine.go",
            "implementation_impact": ["benchmark_case/change_impact_02/route/route.go"],
            "verification_impact": [
                "benchmark_case/change_impact_02/route/route_test.go"
            ],
        },
        {
            "go.mod": "module example.local/changeimpact\n\ngo 1.23\n",
            "engine/engine.go": 'package engine\nfunc Value() string { return "old" }\n',
            "route/route.go": 'package route\nimport "example.local/changeimpact/engine"\nfunc Value() string { return engine.Value() }\n',
            "route/route_test.go": 'package route\nimport "testing"\nfunc TestValue(t *testing.T) { if Value() != "new" { t.Fatal("wrong") } }\n',
        },
        edit=("engine/engine.go", 'return "new"'),
        authority=("implementation_impact", "verification_impact"),
    ),
    _case(
        "change_impact",
        3,
        "Update the shared OpenAPI version to 3.1.1, then report the directly and transitively affected declared projects. Preserve the declared provenance.",
        {
            "changed_path": "benchmark_case/change_impact_03/openapi.yaml",
            "affected_projects": [
                "maven:com.example:api",
                "npm:@demo/web",
                "npm:@demo/mobile",
            ],
            "provenance": "declared-project-links",
        },
        {
            "openapi.yaml": "openapi: 3.1.0\n",
            "backend/pom.xml": "<project><groupId>com.example</groupId><artifactId>api</artifactId><version>1</version></project>\n",
            "backend/src/Api.java": "class Api {}\n",
            "frontend/package.json": '{"name":"@demo/web"}\n',
            "frontend/src/api.ts": "export const api = 1;\n",
            "mobile/package.json": '{"name":"@demo/mobile"}\n',
            "mobile/src/api.ts": "export const api = 1;\n",
            "@root/.hashmarks-project-links.toml": "[[link]]\nsource='npm:@demo/web'\ntarget='maven:com.example:api'\nkind='api-client'\n[[link]]\nsource='npm:@demo/mobile'\ntarget='npm:@demo/web'\nkind='client-shell'\n[[shared_input]]\npath='benchmark_case/change_impact_03/openapi.yaml'\nprojects=['npm:@demo/web','maven:com.example:api']\nkind='contract'\n",
        },
        edit=("openapi.yaml", "openapi: 3.1.1"),
        authority=("affected_projects",),
        provenance=("provenance",),
    ),
    _case(
        "verification",
        2,
        "For the changed TypeScript source, choose the relevant native verification command and its scope.",
        {
            "path": "benchmark_case/verification_02/tests/widget.test.ts",
            "runner": "typescript-compiler",
            "argv": [
                "tsc",
                "--noEmit",
                "-p",
                "benchmark_case/verification_02/tsconfig.json",
            ],
            "scope": "typescript-project",
        },
        {
            "tsconfig.json": '{"compilerOptions":{"strict":true},"include":["src","tests"]}\n',
            "src/widget.ts": "export const widget = 1;\n",
            "tests/widget.test.ts": "import { widget } from '../src/widget';\nexport const observed = widget;\n",
            "tests/unrelated.test.ts": "export const unrelated = 1;\n",
        },
        authority=("runner", "scope"),
    ),
    _case(
        "verification",
        3,
        "Choose the focused native Node verification command for widget.test.js, not the unrelated test.",
        {
            "path": "benchmark_case/verification_03/tests/widget.test.js",
            "runner": "node-test",
            "argv": [
                "node",
                "--test",
                "benchmark_case/verification_03/tests/widget.test.js",
            ],
        },
        {
            "package.json": '{"name":"fixture-node","type":"module"}\n',
            "src/widget.js": "export const widget = () => 'ok';\n",
            "tests/widget.test.js": "import test from 'node:test';\nimport { widget } from '../src/widget.js';\ntest('widget', () => { if (widget() !== 'ok') throw Error('bad'); });\n",
            "tests/other.test.js": "import test from 'node:test';\ntest('other', () => {});\n",
        },
        authority=("runner",),
    ),
    _case(
        "dependency_delta",
        2,
        "Compare both uv changes. Separate same-version source selection from a marker-only relationship change.",
        {
            "source_change": "selection",
            "marker_change": "relationship-only",
            "version_change": False,
        },
        {
            "before/uv.lock": '[[package]]\nname="dummy-dep"\nversion="1.0.0"\nsource={registry="https://one.invalid"}\n',
            "after-source/uv.lock": '[[package]]\nname="dummy-dep"\nversion="1.0.0"\nsource={registry="https://two.invalid"}\n',
            "before/pyproject.toml": '[project]\nname="app"\ndependencies=["dummy-dep; python_version >= \'3.11\'"]\n',
            "after-marker/pyproject.toml": '[project]\nname="app"\ndependencies=["dummy-dep; python_version >= \'3.12\'"]\n',
        },
        authority=("source_change", "marker_change", "version_change"),
    ),
    _case(
        "dependency_delta",
        3,
        "Compare the Maven variants. Distinguish a classifier selection change from an effective-scope relationship change.",
        {
            "classifier_change": "selection",
            "scope_change": "relationship-only",
            "version_change": False,
        },
        {
            "before/tree.json": '{"artifactId":"app","children":[{"groupId":"example.fixture","artifactId":"dummy-dep","version":"1.0.0","type":"jar","classifier":"plain","scope":"compile"}]}\n',
            "after-classifier/tree.json": '{"artifactId":"app","children":[{"groupId":"example.fixture","artifactId":"dummy-dep","version":"1.0.0","type":"jar","classifier":"tests","scope":"compile"}]}\n',
            "after-scope/tree.json": '{"artifactId":"app","children":[{"groupId":"example.fixture","artifactId":"dummy-dep","version":"1.0.0","type":"jar","classifier":"plain","scope":"test"}]}\n',
        },
        authority=("classifier_change", "scope_change", "version_change"),
    ),
    _case(
        "correlation",
        0,
        "Map the external runtime path to a repository source. State the source-equivalence and causation authority of this observation.",
        {
            "path": "benchmark_case/correlation_00/src/worker.py",
            "resolution": "resolved-unique",
            "source_equivalence": "unknown",
            "causation": "not-inferred",
            "provenance": "runtime-log",
        },
        {
            "src/worker.py": "def process_output_data(value):\n    return value + 1\n",
            "observation.json": '{"provider":"runtime-log","path":"/app/benchmark_case/correlation_00/src/worker.py","symbol":"process_output_data","line":2,"mapping":"/app"}\n',
        },
        authority=("source_equivalence", "causation"),
        provenance=("provenance",),
    ),
    _case(
        "correlation",
        1,
        "The supplied line and symbol disagree. Decide whether either claim can be silently chosen as the owner.",
        {
            "resolution": "claim-conflict",
            "owner": None,
            "next_evidence": "inspect-conflicting-claims",
        },
        {
            "src/owner.py": "def first():\n    return 1\n\ndef second():\n    return 2\n",
            "observation.json": '{"provider":"runtime-log","path":"benchmark_case/correlation_01/src/owner.py","symbol":"second","line":2}\n',
        },
        authority=("resolution", "owner"),
    ),
    _case(
        "correlation",
        2,
        "Resolve both observations separately: an ambiguous symbol and an external path that maps to a missing repository member.",
        {
            "symbol_state": "resolved-ambiguous",
            "missing_path_state": "known-absent",
            "owner": None,
        },
        {
            "src/left.py": "def duplicate():\n    return 1\n",
            "src/right.py": "def duplicate():\n    return 2\n",
            "observation.json": '{"anchors":[{"symbol":"duplicate"},{"path":"/app/benchmark_case/correlation_02/src/missing.py"}],"mapping":"/app"}\n',
        },
        authority=("symbol_state", "missing_path_state", "owner"),
    ),
    _case(
        "correlation",
        3,
        "Compare the two independently supplied member revisions to the repository source and preserve the runtime event provenance. Do not infer incident cause.",
        {
            "matching_revision": "proven",
            "other_revision": "mismatch",
            "causation": "not-inferred",
            "provenance": "runtime-log",
        },
        {
            "src/owner.py": "VALUE = 1\n",
            "observation.json": '{"provider":"runtime-log","path":"benchmark_case/correlation_03/src/owner.py","matching_revision":"computed-from-source","other_revision":"0000000000000000000000000000000000000000000000000000000000000000"}\n',
        },
        authority=("matching_revision", "other_revision", "causation"),
        provenance=("provenance",),
    ),
    _case(
        "declarations",
        0,
        "Compare two independently sourced runtime declarations and retain both provider identities.",
        {
            "comparison": "equivalent",
            "winner": "not-selected",
            "providers": ["project-config", "container-config"],
        },
        {
            "pyproject.toml": '[project]\nrequires-python=">=3.12"\n',
            "Dockerfile": "FROM python:3.12\n",
            "observations.json": '{"concept":"python-runtime","providers":[{"id":"project-config","path":"pyproject.toml","value":"3.12"},{"id":"container-config","path":"Dockerfile","value":"3.12"}]}\n',
        },
        authority=("winner",),
        provenance=("providers",),
    ),
    _case(
        "declarations",
        1,
        "The provider declarations disagree. Report the disagreement without choosing one as authoritative.",
        {
            "comparison": "differing",
            "winner": "not-selected",
            "providers": ["project-config", "container-config"],
        },
        {
            "pyproject.toml": '[project]\nrequires-python=">=3.12"\n',
            "Dockerfile": "FROM python:3.13\n",
            "observations.json": '{"concept":"python-runtime","providers":[{"id":"project-config","path":"pyproject.toml","value":"3.12"},{"id":"container-config","path":"Dockerfile","value":"3.13"}]}\n',
        },
        authority=("winner", "comparison"),
        provenance=("providers",),
    ),
    _case(
        "declarations",
        2,
        "A provider declares A↔B and B↔C correspondence. Decide whether that alone proves A↔C or merges the provider namespaces.",
        {
            "a_c_correspondence": "not-proven",
            "namespaces_merged": False,
            "declared_pairs": ["A-B", "B-C"],
        },
        {
            "a.txt": "owner: alpha\n",
            "b.txt": "owner: alpha\n",
            "c.txt": "owner: alpha\n",
            "observations.json": '{"providers":["A","B","C"],"pairs":[["A","B"],["B","C"]],"concept":"ownership"}\n',
        },
        authority=("a_c_correspondence", "namespaces_merged"),
    ),
    _case(
        "declarations",
        3,
        "The declared coverage omits one expected declaration. Decide whether absence or equivalence is established and retain provider provenance.",
        {
            "coverage": "incomplete",
            "absence": "unknown",
            "winner": "not-selected",
            "provider": "deployment-config",
        },
        {
            "deploy.yaml": "runtime: 3.12\n",
            "observations.json": '{"provider":"deployment-config","observed":["runtime"],"expected":["runtime","build-runtime"],"truncated":true}\n',
        },
        authority=("coverage", "absence", "winner"),
        provenance=("provider",),
    ),
    _case(
        "freshness",
        0,
        "After an unsignaled rewrite of indexed source, state whether a cached symbol hit is known current and what evidence is needed.",
        {
            "cached_freshness": "unknown",
            "current_owner": "new_owner",
            "next_evidence": "exact-path-read",
        },
        {
            "src/app.py": "def old_owner():\n    return 1\n",
            "observations/indexed.json": '{"indexed_symbol":"old_owner","current_symbol":"new_owner","change_signaled":false}\n',
        },
        edit=("src/app.py", "def new_owner():"),
        authority=("cached_freshness",),
    ),
    _case(
        "freshness",
        1,
        "Use an exact path read after an unsignaled rewrite. Distinguish the observed current path content from global index freshness.",
        {"exact_path_symbol": "new_owner", "global_index_freshness": "unknown"},
        {
            "src/app.py": "def old_owner():\n    return 1\n",
            "observations/indexed.json": '{"indexed_symbol":"old_owner","change_signaled":false}\n',
        },
        edit=("src/app.py", "def new_owner():"),
        authority=("global_index_freshness",),
    ),
    _case(
        "freshness",
        2,
        "A cached hit was retained after its source changed without a freshness signal. Decide whether it proves unique ownership.",
        {
            "unique_owner_proven": False,
            "cached_hit_freshness": "unknown",
            "next_evidence": "refresh-index",
        },
        {
            "src/a.py": "def widget():\n    return 1\n",
            "src/b.py": "def other():\n    return 2\n",
            "observations/cache.json": '{"cached_hit":"src/a.py","changed_without_signal":"src/b.py"}\n',
        },
        edit=("src/b.py", "def widget():"),
        authority=("unique_owner_proven", "cached_hit_freshness"),
    ),
    _case(
        "freshness",
        3,
        "An unrelated path changed. Decide which evidence can remain useful, and which freshness claim still needs scope proof.",
        {
            "unrelated_path": "changed",
            "owner_evidence": "reusable-if-scope-proven",
            "global_freshness": "unknown",
        },
        {
            "src/owner.py": "def widget():\n    return 1\n",
            "src/unrelated.py": "VALUE = 1\n",
            "observations/change.json": '{"changed_path":"src/unrelated.py","owner_path":"src/owner.py","intersection_proven":false}\n',
        },
        edit=("src/unrelated.py", "VALUE = 2"),
        authority=("owner_evidence", "global_freshness"),
    ),
    _case(
        "negative_bounds",
        0,
        "Only one owner is visible under a narrow result limit. Decide whether uniqueness is established.",
        {
            "unique_owner": "unknown",
            "other_owner_absent": "unknown",
            "next_evidence": "exhaustive-owner-search",
        },
        {
            "src/a.py": "def collision_owner():\n    return 1\n",
            "src/b.py": "def collision_owner():\n    return 2\n",
            "observations/query.json": '{"query":"collision_owner","limit":1,"visible":["src/a.py"],"truncated":true}\n',
        },
        authority=("unique_owner", "other_owner_absent"),
    ),
    _case(
        "negative_bounds",
        1,
        "A bounded verifier search returns no candidate. Decide whether no relevant verifier exists.",
        {
            "no_verifier": "unknown",
            "verification_search": "incomplete",
            "next_evidence": "expand-verification-search",
        },
        {
            "src/owner.py": "def target():\n    return 1\n",
            "tests/test_owner.py": "from src.owner import target\ndef test_target(): assert target() == 1\n",
            "observations/query.json": '{"candidate_limit":1,"visible":[],"truncated":true}\n',
        },
        authority=("no_verifier", "verification_search"),
    ),
    _case(
        "negative_bounds",
        2,
        "The task query omitted identifiers after an input cap. Decide whether an absent hit proves no matching entry point.",
        {
            "entry_point_absent": "unknown",
            "query_complete": False,
            "next_evidence": "repeat-with-complete-identifiers",
        },
        {
            "src/owner.py": "def alpha():\n    return 1\n",
            "observations/query.json": '{"query":"alpha cue00 cue01 cue02 cue03 cue04 cue05 cue06 cue07 cue08 cue08 cue09 cue10 cue11 cue12 cue13 cue14 cue15 cue16 cue17 cue18 cue19 cue20 cue21 cue22 cue23 cue24 cue25 cue26 cue27 cue28 cue29 cue30 cue31 cue32 cue33","omitted_identifiers":true}\n',
        },
        authority=("entry_point_absent", "query_complete"),
    ),
    _case(
        "negative_bounds",
        3,
        "A presentation limit hides a competing source and the cached hit may be stale. Decide whether the visible owner is uniquely current.",
        {
            "unique_current_owner": "unknown",
            "negative_evidence_admissible": False,
            "next_evidence": "refresh-and-exhaust-search",
        },
        {
            "src/a.py": "def owner():\n    return 1\n",
            "src/b.py": "def owner():\n    return 2\n",
            "observations/query.json": '{"visible":["src/a.py"],"presentation_limit":1,"truncated":true,"cached_hit_current":null}\n',
        },
        authority=("unique_current_owner", "negative_evidence_admissible"),
    ),
]


def _patch(files: dict[str, str], case_id: str) -> tuple[str, list[str]]:
    lines: list[str] = []
    paths: list[str] = []
    for relative, content in sorted(files.items()):
        path = (
            relative.removeprefix("@root/")
            if relative.startswith("@root/")
            else f"benchmark_case/{case_id.replace('-', '_')}/{relative}"
        )
        paths.append(path)
        body = content.splitlines()
        lines.extend(
            [
                f"diff --git a/{path} b/{path}",
                "new file mode 100644",
                "--- /dev/null",
                f"+++ b/{path}",
                f"@@ -0,0 +1,{len(body)} @@",
                *(f"+{line}" for line in body),
            ]
        )
    return "\n".join(lines) + "\n", paths


def main() -> None:
    older = runpy.run_path(str(V3 / "oracle.py"))["CASES"]
    manifest: dict[str, dict] = {}
    for case_id, row in older.items():
        manifest[case_id] = {
            "family": case_id.rsplit("-", 1)[0],
            "expected": row["expected"],
            "file_checks": row["file_checks"],
            "authority_fields": [],
            "provenance_fields": [],
        }
    for case in NEW:
        case_id = case["id"]
        patch, paths = _patch(case["files"], case_id)
        patch_path = ROOT / "mutations" / f"{case_id}.patch"
        patch_path.write_text(patch, encoding="utf-8")
        edit = case["edit"]
        prefix = f"benchmark_case/{case_id.replace('-', '_')}/"
        file_checks = [{"path": prefix + edit[0], "contains": edit[1]}] if edit else []
        manifest[case_id] = {
            "family": case["family"],
            "expected": case["expected"],
            "file_checks": file_checks,
            "authority_fields": case["authority_fields"],
            "provenance_fields": case["provenance_fields"],
        }
        expected_keys = ", ".join(case["expected"])
        prompt = (
            f"Inspect benchmark_case/{case_id.replace('-', '_')}. {case['question']} "
            + (f"Change only {prefix + edit[0]}. " if edit else "")
            + f"Return exactly one JSON object with keys {expected_keys}; use repository-relative paths and no other text."
        )
        task = {
            "id": case_id,
            "version": 1,
            "family": case["family"],
            "mode": "edit" if edit else "read_only",
            "repository": REPOSITORY,
            "prompt": prompt,
            "mutation": {
                "id": f"{case_id}-baseline",
                "artifact": f"mutations/{case_id}.patch",
                "sha256": hashlib.sha256(patch.encode()).hexdigest(),
                "changed_paths": paths,
            },
            "fixtures": [],
            "oracle": {
                "adapter": "command",
                "identity": {"id": f"{case_id}-oracle", "version": "1"},
                "configuration": {
                    "health_argv": ["{python}", "{suite}/oracle.py", "health", case_id],
                    "grade_argv": ["{python}", "{suite}/oracle.py", "grade", case_id],
                    "valid_exit_codes": [0, 1],
                    "result_format": "lexigram-v1",
                    "timeout_seconds": 30,
                },
            },
            "budgets": {
                "timeout_seconds": 600,
                "max_tool_calls": 100,
                "max_output_bytes": 50_000_000,
            },
            "contamination": {
                "allowed_change_globs": [prefix + edit[0]] if edit else [],
                "allowed_generated_globs": ["**/__pycache__/**", ".pytest_cache/**"],
            },
        }
        (ROOT / "tasks" / f"{case_id}.json").write_text(
            json.dumps(task, indent=2) + "\n", encoding="utf-8"
        )
    for case_id in older:
        path = ROOT / "tasks" / f"{case_id}.json"
        task = json.loads(path.read_text(encoding="utf-8"))
        task["oracle"]["configuration"]["result_format"] = "lexigram-v1"
        path.write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")
    (ROOT / "cases.json").write_text(
        json.dumps(
            {"schema": "agents-cookbook-behavioral-cases.v4", "cases": manifest},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    experiment = json.loads((ROOT / "experiment.json").read_text(encoding="utf-8"))
    experiment["id"] = "repository-intelligence-behavioral-v4"
    experiment["version"] = 4
    experiment["tasks"] = sorted(manifest)
    experiment["scoring"]["id"] = "repository-intelligence-lexigram"
    experiment["scoring"]["version"] = 1
    experiment["scoring"]["metrics"] = [
        "admissibility",
        "authority",
        "resolution",
        "evidence",
        "paired_benefit",
    ]
    (ROOT / "experiment.json").write_text(
        json.dumps(experiment, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
