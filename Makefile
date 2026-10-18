BENCHMARK_TARGETS := benchmark-check benchmark-check-all benchmark-doctor benchmark-campaign-audit benchmark-status benchmark benchmark-new benchmark-resume benchmark-runs benchmark-smoke benchmark-qualify-localization benchmark-oracle-review benchmark-oracle-review-check benchmark-report benchmark-score benchmark-reports benchmark-evidence-validate benchmark-tool-probe-prepare benchmark-tool-probe-check benchmark-tool-probe-smoke benchmark-tool-probe-smoke-gate benchmark-tool-probe benchmark-tool-probe-resume benchmark-tool-probe-status benchmark-tool-probe-score benchmark-openai-routing benchmark-openai-routing-new benchmark-openai-routing-check benchmark-openai-routing-runs benchmark-openai-routing-status benchmark-openai-routing-dogfood benchmark-context-invariance benchmark-context-invariance-check benchmark-context-invariance-check-all benchmark-context-invariance-qualify-check benchmark-context-invariance-qualify benchmark-context-invariance-qualify-resume benchmark-context-invariance-qualify-status benchmark-context-invariance-new benchmark-context-invariance-resume benchmark-context-invariance-runs benchmark-context-invariance-status benchmark-context-invariance-reports benchmark-harness-explain benchmark-harness-ablation benchmark-harness-find-ablation benchmark-harness-change-impact-ablation benchmark-harness-post-change-ablation benchmark-harness-correlate-evidence-ablation benchmark-harness-repository-declarations-ablation benchmark-harness-dependency-codemap-ablation benchmark-harness-verification-explanation-ablation benchmark-host-delivery-audit benchmark-intervention-audit benchmark-treatment-freeze benchmark-independent-campaign-qualify benchmark-provider-campaign-qualify benchmark-native-host-readiness benchmark-host-transport-provenance benchmark-empirical-campaign-decision benchmark-opencode-native-gateway benchmark-native-session-cohort-audit benchmark-opencode-native-netns benchmark-netns-pilot-readiness benchmark-native-chat-parity benchmark-native-chat-parity-qualify benchmark-native-text-readback benchmark-native-mcp-bridge-tests benchmark-native-mcp-live-smoke benchmark-native-mcp-wire-probe benchmark-native-execve-readback-tests
.PHONY: $(BENCHMARK_TARGETS)

BENCHMARK_REQUESTED_GOALS := $(filter benchmark benchmark-%,$(MAKECMDGOALS))
BENCHMARK_UNKNOWN_GOALS := $(filter-out $(BENCHMARK_TARGETS),$(BENCHMARK_REQUESTED_GOALS))
BENCHMARK_MAKE_SHORT_FLAGS := $(firstword $(MAKEFLAGS))

ifneq ($(strip $(BENCHMARK_UNKNOWN_GOALS)),)
$(error unknown benchmark target '$(firstword $(BENCHMARK_UNKNOWN_GOALS))'; did you mean one of the declared BENCHMARK_TARGETS?)
endif

ifneq ($(strip $(BENCHMARK_REQUESTED_GOALS)),)
ifneq ($(findstring n,$(BENCHMARK_MAKE_SHORT_FLAGS)),)
ifneq ($(findstring e,$(BENCHMARK_MAKE_SHORT_FLAGS)),)
ifneq ($(findstring w,$(BENCHMARK_MAKE_SHORT_FLAGS)),)
$(error benchmark targets refuse suspicious Make flags '-n -e -w'; if you meant 'make benchmark-new', remove the space)
endif
endif
endif
endif

PROBE_DIR = .benchmark-runs/tool-probes/$(PROBE_SUBJECT)/v2
PROBE_SUITE = $(PROBE_DIR)/suite
PROBE_ENV = $(PROBE_SUITE)/.env
PROBE_RUNS = $(PROBE_DIR)/runs
PROBE_SMOKE = $(PROBE_DIR)/smoke

OPENAI_ROUTING_ENV ?= .env
CONTEXT_INVARIANCE_ENV ?= .env.context-invariance
MATRIX ?= heldout
BENCHMARK_ENV ?= .env
BENCHMARK_RUN_ID_FLAG = $(if $(RUN_ID),--run-id "$(RUN_ID)",)
HARBOR_MATRIX ?= harbor-full
HARBOR_ABLATION_MATRIX ?= harbor-ablation-full
HARBOR_FIND_ABLATION_MATRIX ?= harbor-find-ablation-full
HARBOR_CHANGE_IMPACT_ABLATION_MATRIX ?= harbor-change-impact-ablation-full
HARBOR_POST_CHANGE_ABLATION_MATRIX ?= harbor-post-change-ablation-full
HARBOR_CORRELATE_EVIDENCE_ABLATION_MATRIX ?= harbor-correlate-evidence-ablation-full
HARBOR_REPOSITORY_DECLARATIONS_ABLATION_MATRIX ?= harbor-repository-declarations-ablation-full
HARBOR_DEPENDENCY_CODEMAP_ABLATION_MATRIX ?= harbor-dependency-codemap-ablation-full
HARBOR_VERIFICATION_EXPLANATION_ABLATION_MATRIX ?= harbor-verification-explanation-ablation-full

benchmark-harness-explain:
	@./benchmark explain --env-file "$(BENCHMARK_ENV)" \
		--matrix "$(HARBOR_MATRIX)" $(BENCHMARK_RUN_ID_FLAG)

benchmark-harness-ablation:
	@./benchmark ablation --env-file "$(BENCHMARK_ENV)" \
		--matrix "$(HARBOR_ABLATION_MATRIX)" $(BENCHMARK_RUN_ID_FLAG)

benchmark-harness-find-ablation:
	@./benchmark ablation --env-file "$(BENCHMARK_ENV)" \
		--matrix "$(HARBOR_FIND_ABLATION_MATRIX)" $(BENCHMARK_RUN_ID_FLAG)

benchmark-harness-change-impact-ablation:
	@./benchmark ablation --env-file "$(BENCHMARK_ENV)" \
		--matrix "$(HARBOR_CHANGE_IMPACT_ABLATION_MATRIX)" $(BENCHMARK_RUN_ID_FLAG)

benchmark-harness-post-change-ablation:
	@./benchmark ablation --env-file "$(BENCHMARK_ENV)" \
		--matrix "$(HARBOR_POST_CHANGE_ABLATION_MATRIX)" $(BENCHMARK_RUN_ID_FLAG)

benchmark-harness-correlate-evidence-ablation:
	@./benchmark ablation --env-file "$(BENCHMARK_ENV)" \
		--matrix "$(HARBOR_CORRELATE_EVIDENCE_ABLATION_MATRIX)" $(BENCHMARK_RUN_ID_FLAG)

benchmark-harness-repository-declarations-ablation:
	@./benchmark ablation --env-file "$(BENCHMARK_ENV)" \
		--matrix "$(HARBOR_REPOSITORY_DECLARATIONS_ABLATION_MATRIX)" $(BENCHMARK_RUN_ID_FLAG)

benchmark-harness-dependency-codemap-ablation:
	@./benchmark ablation --env-file "$(BENCHMARK_ENV)" \
		--matrix "$(HARBOR_DEPENDENCY_CODEMAP_ABLATION_MATRIX)" $(BENCHMARK_RUN_ID_FLAG)

benchmark-harness-verification-explanation-ablation:
	@./benchmark ablation --env-file "$(BENCHMARK_ENV)" \
		--matrix "$(HARBOR_VERIFICATION_EXPLANATION_ABLATION_MATRIX)" $(BENCHMARK_RUN_ID_FLAG)

benchmark-context-invariance:
	@./benchmark run --auto --env-file "$(CONTEXT_INVARIANCE_ENV)"

benchmark-context-invariance-check:
	@./benchmark check --env-file "$(CONTEXT_INVARIANCE_ENV)"

benchmark-context-invariance-check-all:
	@./benchmark preflight --env-file "$(CONTEXT_INVARIANCE_ENV)"

benchmark-context-invariance-qualify-check:
	@./benchmark preflight --env-file "$(CONTEXT_INVARIANCE_ENV)" \
		--task locate-prefix-path-enumerator \
		--subject none --subject hashmarks

benchmark-context-invariance-qualify:
	@./benchmark run --new --env-file "$(CONTEXT_INVARIANCE_ENV)" \
		--task locate-prefix-path-enumerator \
		--subject none --subject hashmarks

benchmark-context-invariance-qualify-resume:
	@if [ -z "$(RUN_ID)" ]; then echo 'Set RUN_ID to the context-invariance run to resume' >&2; exit 2; fi
	@./benchmark run --resume --run-id "$(RUN_ID)" --env-file "$(CONTEXT_INVARIANCE_ENV)" \
		--task locate-prefix-path-enumerator \
		--subject none --subject hashmarks

benchmark-context-invariance-qualify-status:
	@./benchmark status --env-file "$(CONTEXT_INVARIANCE_ENV)" \
		--task locate-prefix-path-enumerator \
		--subject none --subject hashmarks \
		--require-qualified
	@./benchmark score --env-file "$(CONTEXT_INVARIANCE_ENV)" \
		--require-analysis-evidence

benchmark-context-invariance-new:
	@./benchmark run --new --env-file "$(CONTEXT_INVARIANCE_ENV)"

benchmark-context-invariance-resume:
	@if [ -z "$(RUN_ID)" ]; then echo 'Set RUN_ID to the context-invariance run to resume' >&2; exit 2; fi
	@./benchmark run --resume --run-id "$(RUN_ID)" --env-file "$(CONTEXT_INVARIANCE_ENV)"

benchmark-context-invariance-runs:
	@./benchmark runs --env-file "$(CONTEXT_INVARIANCE_ENV)"

benchmark-context-invariance-status:
	@./benchmark status --env-file "$(CONTEXT_INVARIANCE_ENV)"

benchmark-context-invariance-reports:
	@./benchmark reports --env-file "$(CONTEXT_INVARIANCE_ENV)"

benchmark-openai-routing: benchmark-openai-routing-new

benchmark-openai-routing-new:
	@./benchmark openai-routing-new --env-file "$(OPENAI_ROUTING_ENV)"

benchmark-openai-routing-check:
	@./benchmark openai-routing-check --env-file "$(OPENAI_ROUTING_ENV)"

benchmark-openai-routing-runs:
	@./benchmark openai-routing-runs --env-file "$(OPENAI_ROUTING_ENV)"

benchmark-openai-routing-status:
	@./benchmark openai-routing-status --env-file "$(OPENAI_ROUTING_ENV)"

benchmark-openai-routing-dogfood: benchmark-openai-routing-new

benchmark-tool-probe-prepare:
	@if [ -z "$(PROBE_SUBJECT)" ]; then \
		echo 'Set PROBE_SUBJECT to a non-control subject id from the selected suite' >&2; exit 2; fi
	@./benchmark tool-probe-prepare \
		--suite benchmarks/suites/repository-intelligence/heldout-v1 \
		--subject $(PROBE_SUBJECT) --output-suite $(PROBE_SUITE) \
		--runtime-env-file .env --reuse

benchmark-tool-probe-check: benchmark-tool-probe-prepare
	@./benchmark oracle-review-check \
		--suite $(PROBE_SUITE) --require-complete
	@./benchmark check \
		--env-file $(PROBE_ENV) --agent opencode-native

benchmark-tool-probe-smoke: benchmark-tool-probe-check
	@./benchmark run --new \
		--env-file $(PROBE_ENV) --root $(PROBE_SMOKE) \
		--task locate-prefix-path-enumerator

benchmark-tool-probe-smoke-gate:
	@./benchmark tool-probe-smoke-gate \
		--root $(PROBE_SMOKE) --subject $(PROBE_SUBJECT)

benchmark-tool-probe: benchmark-tool-probe-check benchmark-tool-probe-smoke-gate
	@./benchmark run --new --env-file $(PROBE_ENV)

benchmark-tool-probe-resume:
	@if [ -z "$(RUN_ID)" ]; then echo 'Set RUN_ID to the tool-probe run to resume' >&2; exit 2; fi
	@./benchmark run --resume --run-id "$(RUN_ID)" --env-file $(PROBE_ENV)

benchmark-tool-probe-status:
	@./benchmark status --env-file $(PROBE_ENV)

benchmark-tool-probe-score:
	@./benchmark score --env-file $(PROBE_ENV)

benchmark-oracle-review:
	@./benchmark oracle-review \
		--suite benchmarks/suites/repository-intelligence/heldout-v1 \
		--execute

benchmark-oracle-review-check:
	@./benchmark oracle-review-check \
		--suite benchmarks/suites/repository-intelligence/heldout-v1 \
		--require-complete

benchmark-check:
	@./benchmark check --env-file "$(BENCHMARK_ENV)" --matrix "$(MATRIX)"

benchmark-doctor:
	@./benchmark doctor --env-file "$(BENCHMARK_ENV)" --matrix "$(MATRIX)"

benchmark-check-all:
	@./benchmark preflight --env-file "$(BENCHMARK_ENV)" --matrix "$(MATRIX)"

benchmark-campaign-audit:
	@./benchmark campaign-audit --env-file "$(BENCHMARK_ENV)" --matrix "$(MATRIX)"

benchmark-status:
	@./benchmark status --env-file "$(BENCHMARK_ENV)" --matrix "$(MATRIX)" $(BENCHMARK_RUN_ID_FLAG)

benchmark-reports:
	@./benchmark reports --env-file "$(BENCHMARK_ENV)" --matrix "$(MATRIX)" $(BENCHMARK_RUN_ID_FLAG)

benchmark:
	@./benchmark run --auto --env-file "$(BENCHMARK_ENV)" --matrix "$(MATRIX)"

benchmark-new:
	@./benchmark run --new --env-file "$(BENCHMARK_ENV)" --matrix "$(MATRIX)" --no-json-results

benchmark-resume:
	@if [ -z "$(RUN_ID)" ]; then echo 'Set RUN_ID to a saved run ID from make benchmark-runs MATRIX=$(MATRIX)' >&2; exit 2; fi
	@./benchmark run --resume --run-id "$(RUN_ID)" --env-file "$(BENCHMARK_ENV)" --matrix "$(MATRIX)"

benchmark-runs:
	@./benchmark runs --env-file "$(BENCHMARK_ENV)" --matrix "$(MATRIX)"

benchmark-smoke:
	@./benchmark run --new --env-file .env \
		--subject none --subject hashmarks --subject enola \
		--task locate-prefix-path-enumerator

benchmark-qualify-localization:
	@mkdir -p .benchmark-runs/heldout-v1; \
		qualification_root=$$(mktemp -d .benchmark-runs/heldout-v1/qualification.XXXXXX) || exit 2; \
			printf 'qualification root: %s\n' "$$qualification_root"; \
			if ./benchmark run --new --env-file .env \
			--root "$$qualification_root" \
			--subject none --subject hashmarks --subject enola \
			--task locate-prefix-path-enumerator \
			--task locate-stale-index-removal \
			--task locate-directory-pruning \
			--task locate-mcp-task-evidence \
			--task locate-repository-content-identity \
			--task locate-terminal-run-check; then \
		./benchmark status --env-file .env \
			--root "$$qualification_root" --require-qualified \
			--subject none --subject hashmarks --subject enola \
			--task locate-prefix-path-enumerator \
			--task locate-stale-index-removal \
			--task locate-directory-pruning \
			--task locate-mcp-task-evidence \
			--task locate-repository-content-identity \
			--task locate-terminal-run-check; \
		else exit 1; fi

benchmark-evidence-validate:
	@uv run --no-project python -m benchmarks.evidence validate \
		--corpus benchmarks/suites/repository-intelligence/multidomain-v2/evidence.json

benchmark-report:
	@./benchmark report --env-file "$(BENCHMARK_ENV)" --matrix "$(MATRIX)" $(BENCHMARK_RUN_ID_FLAG)

benchmark-score:
	@./benchmark score --env-file "$(BENCHMARK_ENV)" --matrix "$(MATRIX)" $(BENCHMARK_RUN_ID_FLAG)

.PHONY: benchmark-eval-readiness
benchmark-eval-readiness:
	@uv run --no-project python -m benchmarks.harness.evaluation_readiness

# E229–E232: require explicit campaign identity; a green coverage audit is
# intentionally not a model-delivery or empirical-causality certificate.
.PHONY: benchmark-eval-review-queue benchmark-factorial-campaign-audit
benchmark-eval-review-queue:
	@uv run --no-project python -m benchmarks.harness.independent_review_queue

benchmark-factorial-campaign-audit:
	@if [ -z "$(CAMPAIGN_ID)" ] || [ -z "$(RESULTS_ROOT)" ]; then \
		echo 'Set CAMPAIGN_ID and RESULTS_ROOT; optional MODE=smoke|matrix (default matrix)' >&2; exit 2; fi
	@uv run --no-project python -m benchmarks.harness.factorial_campaign_audit \
		--results-root "$(RESULTS_ROOT)" --campaign-id "$(CAMPAIGN_ID)" \
		--mode "$(or $(MODE),matrix)" --require-complete


# E233–E236: independent host MAC key file must be outside any model-visible
# workspace, and no receipt is implicitly harvested from agent/ATIF prose.
.PHONY: benchmark-host-delivery-audit benchmark-intervention-audit
benchmark-host-delivery-audit:
	@if [ -z "$(TRAJECTORY)" ] || [ -z "$(HOST_ATTESTATION)" ] || [ -z "$(HOST_KEY_FILE)" ] || [ -z "$(CAMPAIGN_ID)" ] || [ -z "$(TRIAL_ID)" ]; then \
		echo 'Set TRAJECTORY HOST_ATTESTATION HOST_KEY_FILE CAMPAIGN_ID TRIAL_ID' >&2; exit 2; fi
	@uv run --no-project python -m benchmarks.harness.host_input_attestation \
		--trajectory "$(TRAJECTORY)" --attestation "$(HOST_ATTESTATION)" \
		--host-key-file "$(HOST_KEY_FILE)" --campaign-id "$(CAMPAIGN_ID)" \
		--trial-id "$(TRIAL_ID)" --require-delivery

benchmark-intervention-audit:
	@if [ -z "$(DESIGN)" ] || [ -z "$(RESULTS_ROOT)" ] || [ -z "$(ATTESTATIONS_ROOT)" ] || [ -z "$(HOST_KEY_FILE)" ]; then \
		echo 'Set DESIGN RESULTS_ROOT ATTESTATIONS_ROOT HOST_KEY_FILE' >&2; exit 2; fi
	@uv run --no-project python -m benchmarks.harness.intervention_audit \
		--design "$(DESIGN)" --bundles-root "$(RESULTS_ROOT)" \
		--attestations-root "$(ATTESTATIONS_ROOT)" \
		--host-key-file "$(HOST_KEY_FILE)" --require-complete

# E237–E240: pre-run freeze and independent custodial qualification.
# There is deliberately no CLI which self-signs host model input or approves
# reviews; only privileged provider hosts can invoke the capture API.
.PHONY: benchmark-treatment-freeze benchmark-independent-campaign-qualify
benchmark-treatment-freeze:
	@if [ -z "$(DESIGN)" ] || [ -z "$(MANIFEST_OUTPUT)" ] || [ -z "$(SOURCE_CONTRACT_ID)" ] || [ -z "$(HOST_BUILD_SHA256)" ] || [ -z "$(ASSIGNMENT_SEED_SHA256)" ]; then \
		echo 'Set DESIGN MANIFEST_OUTPUT SOURCE_CONTRACT_ID HOST_BUILD_SHA256 ASSIGNMENT_SEED_SHA256' >&2; exit 2; fi
	@uv run --no-project python -m benchmarks.harness.trusted_treatments \
		--design "$(DESIGN)" --output "$(MANIFEST_OUTPUT)" \
		--source-contract-identity "$(SOURCE_CONTRACT_ID)" \
		--host-build-sha256 "$(HOST_BUILD_SHA256)" \
		--assignment-seed-sha256 "$(ASSIGNMENT_SEED_SHA256)"

benchmark-independent-campaign-qualify:
	@if [ -z "$(MANIFEST)" ] || [ -z "$(RESULTS_ROOT)" ] || [ -z "$(ATTESTATIONS_ROOT)" ] || [ -z "$(HOST_KEY_FILE)" ] || [ -z "$(SEAL)" ] || [ -z "$(INDEPENDENT_KEY_FILE)" ]; then \
		echo 'Set MANIFEST RESULTS_ROOT ATTESTATIONS_ROOT HOST_KEY_FILE SEAL INDEPENDENT_KEY_FILE' >&2; exit 2; fi
	@uv run --no-project python -m benchmarks.harness.independent_campaign_qualification \
		--manifest "$(MANIFEST)" --bundles-root "$(RESULTS_ROOT)" \
		--attestations-root "$(ATTESTATIONS_ROOT)" \
		--host-key-file "$(HOST_KEY_FILE)" --seal "$(SEAL)" \
		--independent-key-file "$(INDEPENDENT_KEY_FILE)" --require-admitted

# E241–E244: additionally require real trusted-host HTTP submission receipts
# for every planned arm. This does not claim that provider processed/used them.
.PHONY: benchmark-provider-campaign-qualify
benchmark-provider-campaign-qualify:
	@if [ -z "$(MANIFEST)" ] || [ -z "$(RESULTS_ROOT)" ] || [ -z "$(ATTESTATIONS_ROOT)" ] || [ -z "$(HOST_KEY_FILE)" ] || [ -z "$(SEAL)" ] || [ -z "$(INDEPENDENT_KEY_FILE)" ]; then \
		echo 'Set MANIFEST RESULTS_ROOT ATTESTATIONS_ROOT HOST_KEY_FILE SEAL INDEPENDENT_KEY_FILE' >&2; exit 2; fi
	@uv run --no-project python -m benchmarks.harness.independent_campaign_qualification \
		--manifest "$(MANIFEST)" --bundles-root "$(RESULTS_ROOT)" \
		--attestations-root "$(ATTESTATIONS_ROOT)" \
		--host-key-file "$(HOST_KEY_FILE)" --seal "$(SEAL)" \
		--independent-key-file "$(INDEPENDENT_KEY_FILE)" \
		--require-provider-submission --require-admitted

# E245–E248: inspect native host gaps, check exact signed cross-trial
# transport provenance, and only then permit descriptive population reporting.
.PHONY: benchmark-native-host-readiness benchmark-host-transport-provenance benchmark-empirical-campaign-decision
benchmark-native-host-readiness:
	@if [ -z "$(DESIGN)" ]; then \
		echo 'Set DESIGN; native host request capture currently fails closed' >&2; exit 2; fi
	@uv run --no-project python -m benchmarks.harness.native_host_readiness \
		--design "$(DESIGN)"

benchmark-host-transport-provenance:
	@if [ -z "$(MANIFEST)" ] || [ -z "$(RESULTS_ROOT)" ] || [ -z "$(ATTESTATIONS_ROOT)" ] || [ -z "$(HOST_KEY_FILE)" ] || [ -z "$(APPROVED_ENDPOINT_SHA256)" ] || [ -z "$(EXPECTED_HOST_IDENTITY)" ]; then \
		echo 'Set MANIFEST RESULTS_ROOT ATTESTATIONS_ROOT HOST_KEY_FILE APPROVED_ENDPOINT_SHA256 EXPECTED_HOST_IDENTITY' >&2; exit 2; fi
	@uv run --no-project python -m benchmarks.harness.host_transport_provenance \
		--manifest "$(MANIFEST)" --bundles-root "$(RESULTS_ROOT)" \
		--attestations-root "$(ATTESTATIONS_ROOT)" \
		--host-key-file "$(HOST_KEY_FILE)" \
		--approved-endpoint-sha256 "$(APPROVED_ENDPOINT_SHA256)" \
		--expected-host-identity "$(EXPECTED_HOST_IDENTITY)" --require-qualified

benchmark-empirical-campaign-decision:
	@if [ -z "$(MANIFEST)" ] || [ -z "$(RESULTS_ROOT)" ] || [ -z "$(ATTESTATIONS_ROOT)" ] || [ -z "$(HOST_KEY_FILE)" ] || [ -z "$(SEAL)" ] || [ -z "$(INDEPENDENT_KEY_FILE)" ] || [ -z "$(APPROVED_ENDPOINT_SHA256)" ] || [ -z "$(EXPECTED_HOST_IDENTITY)" ]; then \
		echo 'Set MANIFEST RESULTS_ROOT ATTESTATIONS_ROOT HOST_KEY_FILE SEAL INDEPENDENT_KEY_FILE APPROVED_ENDPOINT_SHA256 EXPECTED_HOST_IDENTITY' >&2; exit 2; fi
	@uv run --no-project python -m benchmarks.harness.empirical_campaign_decision \
		--manifest "$(MANIFEST)" --bundles-root "$(RESULTS_ROOT)" \
		--attestations-root "$(ATTESTATIONS_ROOT)" \
		--host-key-file "$(HOST_KEY_FILE)" --seal "$(SEAL)" \
		--independent-key-file "$(INDEPENDENT_KEY_FILE)" \
		--approved-endpoint-sha256 "$(APPROVED_ENDPOINT_SHA256)" \
		--expected-host-identity "$(EXPECTED_HOST_IDENTITY)" \
		--require-descriptive-population

# E249–E252: opt-in SHA/version-pinned native OpenCode trial through a
# host-owned loopback provider. Real model input / process provenance still
# needs immutable native ATIF and independent host-bound capture verification.
.PHONY: benchmark-opencode-native-gateway
benchmark-opencode-native-gateway:
	@if [ "$(ALLOW_UNCONFINED_NATIVE_TEST)" != "1" ]; then \
		echo 'This standalone native runner has no OS-level sandbox. Explicitly set ALLOW_UNCONFINED_NATIVE_TEST=1; never use a sensitive workstation or repository.' >&2; exit 2; fi
	@if [ -z "$(MANIFEST)" ] || [ -z "$(TRIAL_ID)" ] || [ -z "$(OPENCODE_BIN)" ] || [ -z "$(OPENCODE_SHA256)" ] || [ -z "$(OPENCODE_VERSION)" ] || [ -z "$(RUN_ROOT)" ] || [ -z "$(WORKSPACE)" ] || [ -z "$(PROMPT_FILE)" ] || [ -z "$(CURRENT_SOURCE)" ] || [ -z "$(HOST_KEY_FILE)" ] || [ -z "$(HOST_IDENTITY)" ] || [ -z "$(UPSTREAM)" ] || [ -z "$(APPROVED_ORIGIN)" ] || [ -z "$(CATALOG_SHA256)" ] || [ -z "$(ORACLE_SHA256)" ] || [ -z "$(WORKSPACE_SHA256)" ]; then \
		echo 'Set MANIFEST TRIAL_ID OPENCODE_BIN OPENCODE_SHA256 OPENCODE_VERSION RUN_ROOT WORKSPACE PROMPT_FILE CURRENT_SOURCE HOST_KEY_FILE HOST_IDENTITY UPSTREAM APPROVED_ORIGIN CATALOG_SHA256 ORACLE_SHA256 WORKSPACE_SHA256; export BENCHMARK_UPSTREAM_API_KEY' >&2; exit 2; fi
	@uv run --no-project python -m benchmarks.harness.opencode_native_trial \
		--manifest "$(MANIFEST)" --trial-id "$(TRIAL_ID)" \
		--binary "$(OPENCODE_BIN)" --binary-sha256 "$(OPENCODE_SHA256)" \
		--version "$(OPENCODE_VERSION)" --run-root "$(RUN_ROOT)" \
		--workspace "$(WORKSPACE)" --prompt-file "$(PROMPT_FILE)" \
		--current "$(CURRENT_SOURCE)" \
		$(if $(REPLACED_SOURCE),--replaced "$(REPLACED_SOURCE)",) \
		--host-key-file "$(HOST_KEY_FILE)" --host-identity "$(HOST_IDENTITY)" \
		--upstream "$(UPSTREAM)" --approved-origin "$(APPROVED_ORIGIN)" \
		--catalog-sha256 "$(CATALOG_SHA256)" --oracle-sha256 "$(ORACLE_SHA256)" \
		--workspace-sha256 "$(WORKSPACE_SHA256)" \
		--allow-unconfined-native-test

# E253–E256: native export + real host-transport bound cohort read-back.
# Observational consistency is not proof of native kernel process origin,
# independent session custody, real review or causal Hashmarks uplift.
.PHONY: benchmark-native-session-cohort-audit
benchmark-native-session-cohort-audit:
	@if [ -z "$(MANIFEST)" ] || [ -z "$(NATIVE_RESULTS_ROOT)" ] || [ -z "$(SOURCES_ROOT)" ] || [ -z "$(WORKSPACE)" ] || [ -z "$(HOST_KEY_FILE)" ] || [ -z "$(HOST_IDENTITY)" ] || [ -z "$(APPROVED_ENDPOINT_SHA256)" ]; then \
		echo 'Set MANIFEST NATIVE_RESULTS_ROOT SOURCES_ROOT WORKSPACE HOST_KEY_FILE HOST_IDENTITY APPROVED_ENDPOINT_SHA256' >&2; exit 2; fi
	@uv run --no-project python -m benchmarks.harness.native_session_cohort_audit \
		--manifest "$(MANIFEST)" --results-root "$(NATIVE_RESULTS_ROOT)" \
		--sources-root "$(SOURCES_ROOT)" --workspace "$(WORKSPACE)" \
		--host-key-file "$(HOST_KEY_FILE)" --host-identity "$(HOST_IDENTITY)" \
		--approved-endpoint-sha256 "$(APPROVED_ENDPOINT_SHA256)" --require-qualified

# E257-E260: explicit deny-egress bubblewrap native pilot. The network
# relay uses a host-private Unix socket; upstream credentials never enter
# the evaluated native process. No unconfined fallback is permitted.
.PHONY: benchmark-opencode-native-netns benchmark-netns-pilot-readiness
benchmark-opencode-native-netns:
	@if [ -z "$(MANIFEST)" ] || [ -z "$(TRIAL_ID)" ] || [ -z "$(OPENCODE_BIN)" ] || [ -z "$(OPENCODE_SHA256)" ] || [ -z "$(OPENCODE_VERSION)" ] || [ -z "$(HASHMARKS_BIN)" ] || [ -z "$(HASHMARKS_SHA256)" ] || [ -z "$(RUN_ROOT)" ] || [ -z "$(WORKSPACE)" ] || [ -z "$(PROMPT_FILE)" ] || [ -z "$(CURRENT_SOURCE)" ] || [ -z "$(HOST_KEY_FILE)" ] || [ -z "$(HOST_IDENTITY)" ] || [ -z "$(UPSTREAM)" ] || [ -z "$(APPROVED_ORIGIN)" ] || [ -z "$(CATALOG_SHA256)" ] || [ -z "$(ORACLE_SHA256)" ] || [ -z "$(WORKSPACE_SHA256)" ]; then \
		echo 'Set MANIFEST TRIAL_ID OPENCODE_BIN OPENCODE_SHA256 OPENCODE_VERSION HASHMARKS_BIN HASHMARKS_SHA256 RUN_ROOT WORKSPACE PROMPT_FILE CURRENT_SOURCE HOST_KEY_FILE HOST_IDENTITY UPSTREAM APPROVED_ORIGIN CATALOG_SHA256 ORACLE_SHA256 WORKSPACE_SHA256; export BENCHMARK_UPSTREAM_API_KEY' >&2; exit 2; fi
	@uv run --no-project python -m benchmarks.harness.opencode_native_trial \
		--manifest "$(MANIFEST)" --trial-id "$(TRIAL_ID)" \
		--binary "$(OPENCODE_BIN)" --binary-sha256 "$(OPENCODE_SHA256)" \
		--version "$(OPENCODE_VERSION)" --run-root "$(RUN_ROOT)" \
		--workspace "$(WORKSPACE)" --prompt-file "$(PROMPT_FILE)" \
		--current "$(CURRENT_SOURCE)" \
		$(if $(REPLACED_SOURCE),--replaced "$(REPLACED_SOURCE)",) \
		--host-key-file "$(HOST_KEY_FILE)" --host-identity "$(HOST_IDENTITY)" \
		--upstream "$(UPSTREAM)" --approved-origin "$(APPROVED_ORIGIN)" \
		--catalog-sha256 "$(CATALOG_SHA256)" --oracle-sha256 "$(ORACLE_SHA256)" \
		--workspace-sha256 "$(WORKSPACE_SHA256)" \
		--hashmarks-binary "$(HASHMARKS_BIN)" \
		--hashmarks-sha256 "$(HASHMARKS_SHA256)" --require-netns-isolation \
		$(if $(VERIFIED_NATIVE_MCP_BRIDGE),--verified-native-mcp-bridge,) \
		$(if $(filter 1,$(REQUIRE_NATIVE_EXECVE_OBSERVATION)),--require-native-execve-observation --native-execve-tracer-bin "$(NATIVE_EXECVE_TRACER)" --native-execve-tracer-sha256 "$(NATIVE_EXECVE_TRACER_SHA256)",)

benchmark-netns-pilot-readiness:
	@if [ -z "$(MANIFEST)" ] || [ -z "$(NATIVE_RESULTS_ROOT)" ] || [ -z "$(SOURCES_ROOT)" ] || [ -z "$(WORKSPACE)" ] || [ -z "$(HOST_KEY_FILE)" ] || [ -z "$(HOST_IDENTITY)" ] || [ -z "$(APPROVED_ENDPOINT_SHA256)" ] || [ -z "$(BWRAP_SHA256)" ] || [ -z "$(HASHMARKS_SHA256)" ]; then \
		echo 'Set MANIFEST NATIVE_RESULTS_ROOT SOURCES_ROOT WORKSPACE HOST_KEY_FILE HOST_IDENTITY APPROVED_ENDPOINT_SHA256 BWRAP_SHA256 HASHMARKS_SHA256' >&2; exit 2; fi
	@uv run --no-project python -m benchmarks.harness.native_netns_pilot_readiness \
		--manifest "$(MANIFEST)" --results-root "$(NATIVE_RESULTS_ROOT)" \
		--sources-root "$(SOURCES_ROOT)" --workspace "$(WORKSPACE)" \
		--host-key-file "$(HOST_KEY_FILE)" --host-identity "$(HOST_IDENTITY)" \
		--approved-endpoint-sha256 "$(APPROVED_ENDPOINT_SHA256)" \
		--bwrap-sha256 "$(BWRAP_SHA256)" \
		--hashmarks-sha256 "$(HASHMARKS_SHA256)" --require-ready

# E261–E264: exercise the actual Linux network namespace before any agent
# or provider is contacted. Kernel proof is a point-in-time diagnostic only.
.PHONY: benchmark-native-kernel-probe
benchmark-native-kernel-probe:
	@uv run --no-project python -m benchmarks.harness.native_netns_probe

# E265-E268: pure read-only frozen tool-message wire compatibility analysis.
# The strict variant intentionally fails when one arm is not deliverable.
.PHONY: benchmark-native-chat-parity benchmark-native-chat-parity-qualify
benchmark-native-chat-parity:
	@if [ -z "$(MANIFEST)" ] || [ -z "$(SOURCES_ROOT)" ]; then \
		echo 'Set MANIFEST and SOURCES_ROOT to frozen externally held authorities' >&2; exit 2; fi
	@uv run --no-project python -m benchmarks.harness.native_chat_transport_parity \
		--manifest "$(MANIFEST)" --sources-root "$(SOURCES_ROOT)"

benchmark-native-chat-parity-qualify:
	@if [ -z "$(MANIFEST)" ] || [ -z "$(SOURCES_ROOT)" ]; then \
		echo 'Set MANIFEST and SOURCES_ROOT to frozen externally held authorities' >&2; exit 2; fi
	@uv run --no-project python -m benchmarks.harness.native_chat_transport_parity \
		--manifest "$(MANIFEST)" --sources-root "$(SOURCES_ROOT)" \
		--require-paired-compatible

# E271/E272: read-only host-attested, exact text Chat tool-message parity.
# Requires completed native run artifacts and a separately custodied host key.
.PHONY: benchmark-native-text-readback
benchmark-native-text-readback:
	@if [ -z "$(MANIFEST)" ] || [ -z "$(NATIVE_RESULTS_ROOT)" ] || [ -z "$(SOURCES_ROOT)" ] || [ -z "$(WORKSPACE)" ] || [ -z "$(HOST_KEY_FILE)" ] || [ -z "$(HOST_IDENTITY)" ] || [ -z "$(APPROVED_ENDPOINT_SHA256)" ] || [ -z "$(BWRAP_SHA256)" ] || [ -z "$(HASHMARKS_SHA256)" ]; then \
		echo 'Set MANIFEST NATIVE_RESULTS_ROOT SOURCES_ROOT WORKSPACE HOST_KEY_FILE HOST_IDENTITY APPROVED_ENDPOINT_SHA256 BWRAP_SHA256 HASHMARKS_SHA256' >&2; exit 2; fi
	@uv run --no-project python -m benchmarks.harness.native_text_presentation_readback \
		--manifest "$(MANIFEST)" --results-root "$(NATIVE_RESULTS_ROOT)" \
		--sources-root "$(SOURCES_ROOT)" --workspace "$(WORKSPACE)" \
		--host-key-file "$(HOST_KEY_FILE)" --host-identity "$(HOST_IDENTITY)" \
		--approved-endpoint-sha256 "$(APPROVED_ENDPOINT_SHA256)" \
		--bwrap-sha256 "$(BWRAP_SHA256)" \
		--hashmarks-sha256 "$(HASHMARKS_SHA256)" --require-host-transport

# E273–E276: real local stdio bridge roundtrip against deterministic executable.
# No upstream model/real Hashmarks release is used by the test.
.PHONY: benchmark-native-mcp-bridge-tests
benchmark-native-mcp-bridge-tests:
	@uv run --no-project python -m unittest \
		benchmarks.tests.test_native_mcp_bridge \
		benchmarks.tests.test_native_mcp_request_ledger -v

# E276: optional real installed Hashmarks stdio contract smoke.
# It never contacts the model provider; an operator supplies pinned args.
.PHONY: benchmark-native-mcp-live-smoke
benchmark-native-mcp-live-smoke:
	@if [ -z "$(MANIFEST)" ] || [ -z "$(TRIAL_ID)" ] || [ -z "$(HASHMARKS_BIN)" ] || [ -z "$(HASHMARKS_SHA256)" ] || [ -z "$(WORKSPACE)" ] || [ -z "$(CURRENT_SOURCE)" ] || [ -z "$(TOOL_NAME)" ] || [ -z "$(TOOL_ARGS_FILE)" ]; then \
		echo 'Set MANIFEST TRIAL_ID HASHMARKS_BIN HASHMARKS_SHA256 WORKSPACE CURRENT_SOURCE TOOL_NAME TOOL_ARGS_FILE' >&2; exit 2; fi
	@uv run --no-project python -m benchmarks.harness.native_mcp_live_smoke \
		--manifest "$(MANIFEST)" --trial-id "$(TRIAL_ID)" \
		--hashmarks-binary "$(HASHMARKS_BIN)" \
		--hashmarks-sha256 "$(HASHMARKS_SHA256)" \
		--workspace "$(WORKSPACE)" --current "$(CURRENT_SOURCE)" \
		$(if $(REPLACED_SOURCE),--replaced "$(REPLACED_SOURCE)",) \
		--tool-name "$(TOOL_NAME)" --tool-args-file "$(TOOL_ARGS_FILE)"

# Host-owned native Linux process observation. This does not assert that each
# execution maps to a specific agent tool or that all nested calls were seen.
.PHONY: benchmark-native-host-execve-check
benchmark-native-host-execve-check:
	@uv run --no-project python -m unittest benchmarks.tests.test_native_host_execve -v

# E279/E280: read-only diagnostic probe of real pinned native MCP wire.
# No model, no OpenCode/provider, no synthetic source normalization.
.PHONY: benchmark-native-mcp-wire-probe
benchmark-native-mcp-wire-probe:
	@if [ -z "$(HASHMARKS_BIN)" ] || [ -z "$(HASHMARKS_SHA256)" ] || [ -z "$(WORKSPACE)" ] || [ -z "$(TOOL_NAME)" ] || [ -z "$(TOOL_ARGS_FILE)" ]; then \
		echo 'Set HASHMARKS_BIN HASHMARKS_SHA256 WORKSPACE TOOL_NAME TOOL_ARGS_FILE; optional ORIGINAL_PACKET' >&2; exit 2; fi
	@uv run --no-project python -m benchmarks.harness.native_mcp_wire_probe \
		--binary "$(HASHMARKS_BIN)" --sha256 "$(HASHMARKS_SHA256)" \
		--workspace "$(WORKSPACE)" --tool "$(TOOL_NAME)" \
		--arguments "$(TOOL_ARGS_FILE)" \
		$(if $(ORIGINAL_PACKET),--original-source "$(ORIGINAL_PACKET)",) \
		$(if $(REQUIRE_BRIDGE_COMPATIBLE),--require-bridge-compatible,)

# Host-owned positive read-back of independently authenticated nested tool
# observations. No agent/model execution; never infer completeness from ATIF.
.PHONY: benchmark-host-nested-search-check benchmark-host-nested-search-pair
benchmark-host-nested-search-check:
	@uv run --no-project python -m unittest benchmarks.tests.test_host_nested_search -v

benchmark-host-nested-search-pair:
	@if [ -z "$(BARE_BUNDLE)" ] || [ -z "$(HASHMARKS_BUNDLE)" ] || [ -z "$(BARE_NESTED_RECEIPT)" ] || [ -z "$(HASHMARKS_NESTED_RECEIPT)" ] || [ -z "$(HOST_KEY_FILE)" ] || [ -z "$(HOST_IDENTITY)" ] || [ -z "$(HOST_BUILD_SHA256)" ]; then \
		echo 'Set BARE_BUNDLE HASHMARKS_BUNDLE BARE_NESTED_RECEIPT HASHMARKS_NESTED_RECEIPT HOST_KEY_FILE HOST_IDENTITY HOST_BUILD_SHA256' >&2; exit 2; fi
	@uv run --no-project python -m benchmarks.harness.host_nested_search \
		--bare-bundle "$(BARE_BUNDLE)" --hashmarks-bundle "$(HASHMARKS_BUNDLE)" \
		--bare-attestation "$(BARE_NESTED_RECEIPT)" \
		--hashmarks-attestation "$(HASHMARKS_NESTED_RECEIPT)" \
		--host-key-file "$(HOST_KEY_FILE)" \
		--expected-host-identity "$(HOST_IDENTITY)" \
		--expected-host-build-sha256 "$(HOST_BUILD_SHA256)" --require-qualified

# E281–E284: existing E260 readiness also re-verifies host-execve HMAC
# and complete provider-issued native-session linkage (model-free attacks).
benchmark-native-execve-readback-tests:
	@uv run --no-project python -m unittest benchmarks.tests.test_native_netns_pilot_readiness -v
