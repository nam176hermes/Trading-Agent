.PHONY: audit audit-release audit-portable check-p0-baseline audit-python-source audit-dependencies-production \
	audit-dependencies-dev audit-dependencies generate-contracts check-contracts \
	check-d0-closure check-broad-handler-inventory check-test-skips check-critical-coverage \
	check-secrets test test-portable-embedded-proof test-core test-consolidation test-production \
	test-runtime-release prepare-runtime-release-wheelhouse test-runtime-release-host test-runtime-postgres \
	test-event-ledger-runtime-postgres test-market-data-runtime-postgres test-package6-paper-runtime \
	build-package6-custodian test-package6-custodian-native \
	build-nautilus-engine verify-nautilus-engine qualify-nautilus-sealed-imports \
	test-runtime-dual-read test-security \
	test-backend test-dashboard typecheck-dashboard lint-dashboard \
	build-dashboard prepare-root-test-install test-all-private test-all-portable-private \
	test-all-portable-topology-private test-all ci ci-private ci-portable ci-portable-private ci-common-private \
	ci-host-authority ci-host-authority-private artifact-firewall-check audit-delivery-contract \
	ci-portable-topology test-portable-root-remainder test-portable-source test-native-capabilities test-external-authorities \
	check-test-governance-topology check-portable-defect-closure check-p0-ci-closure

RUNTIME_RELEASE_LOCK_SHA256 := $(shell sha256sum uv.lock | cut -d' ' -f1)
PYTHON ?= uv run python
RUNTIME_RELEASE_WHEELHOUSE_ROOT ?= $(HOME)/.cache/trading-agent/runtime-release-wheelhouse
RUNTIME_RELEASE_WHEELHOUSE := $(RUNTIME_RELEASE_WHEELHOUSE_ROOT)/$(RUNTIME_RELEASE_LOCK_SHA256)
TEST_EVIDENCE_DIR ?= /tmp/trading-agent-test-evidence
NAUTILUS_ENGINE_CONTROLLER_PYTHON ?= python3.11
NAUTILUS_ENGINE_SANDBOX ?= /usr/bin/bwrap
NAUTILUS_IMPORT_POLICY ?= $(CURDIR)/engines/nautilus/runtime-closure-policy.json
NAUTILUS_IMPORT_BASE_RUNTIME ?=
NAUTILUS_IMPORT_ARTIFACT_DIRECTORY ?=
NAUTILUS_IMPORT_SANDBOX ?= $(NAUTILUS_ENGINE_SANDBOX)
NAUTILUS_IMPORT_RECEIPT ?=

audit:
	uv run python scripts/audit_canonical_repo.py --root "$(CURDIR)"

audit-portable:
	uv run python scripts/audit_canonical_repo.py --root "$(CURDIR)" --portable

check-p0-baseline:
	$(PYTHON) scripts/audit_canonical_repo.py --portable --check-p0-baseline

check-p0-ci-closure:
	$(PYTHON) scripts/check_p0_ci_closure.py \
	  --matrix docs/implementation/p0-ci-closure-matrix.json

audit-release:
	uv run python scripts/audit_canonical_repo.py --root "$(CURDIR)" --release

audit-python-source:
	uvx --from bandit==1.9.4 bandit -q -lll -r \
		apps/control_api packages services legacy/research-backend scripts \
		-x '*/tests/*,*/.venv/*'

audit-dependencies-production:
	@set -eu; \
		root_requirements=$$(mktemp); \
		legacy_requirements=$$(mktemp); \
		trap 'rm -f "$$root_requirements" "$$legacy_requirements"' EXIT; \
		uv export --frozen --no-dev --no-emit-project --format requirements-txt --no-hashes > "$$root_requirements"; \
		uvx --from pip-audit==2.9.0 pip-audit -r "$$root_requirements" --no-deps --disable-pip; \
		cd legacy/research-backend; \
		uv export --frozen --no-dev --no-emit-project --format requirements-txt --no-hashes > "$$legacy_requirements"; \
		uvx --from pip-audit==2.9.0 pip-audit -r "$$legacy_requirements" --no-deps --disable-pip; \
		cd ../../apps/dashboard; \
		npm audit --omit=dev --audit-level=moderate

audit-dependencies-dev:
	@set -eu; \
		root_requirements=$$(mktemp); \
		legacy_requirements=$$(mktemp); \
		trap 'rm -f "$$root_requirements" "$$legacy_requirements"' EXIT; \
		uv export --frozen --all-groups --no-emit-project --format requirements-txt --no-hashes > "$$root_requirements"; \
		uvx --from pip-audit==2.9.0 pip-audit -r "$$root_requirements" --no-deps --disable-pip; \
		cd legacy/research-backend; \
		uv export --frozen --extra test --no-emit-project --format requirements-txt --no-hashes > "$$legacy_requirements"; \
		uvx --from pip-audit==2.9.0 pip-audit -r "$$legacy_requirements" --no-deps --disable-pip; \
		cd ../../apps/dashboard; \
		npm audit --audit-level=moderate

audit-dependencies: audit-dependencies-production audit-dependencies-dev

generate-contracts:
	uv run python scripts/generate_contracts.py

check-contracts:
	uv run python scripts/generate_contracts.py --check

check-d0-closure:
	uv run pytest -q tests/foundation/test_d0_closure.py

check-broad-handler-inventory:
	uv run python scripts/check_broad_handler_inventory.py --check

check-test-skips:
	@set -eu; \
		build_dir=$$(mktemp -d /tmp/package6-custodian-governance.XXXXXXXXXX); \
		chmod 0700 "$$build_dir"; \
		test "$$(stat -c '%u:%a' -- "$$build_dir")" = "$$(id -u):700"; \
		$(MAKE) -C native/package6_custodian \
			"BUILD_DIR=$$build_dir" build; \
		set -- "$$build_dir"/python/_package6_fd_custody*.so; \
		if test "$$#" -ne 1 || test -L "$$1" || ! test -f "$$1"; then \
			printf '%s\n' "test governance requires exactly one regular native custody extension" >&2; \
			exit 2; \
		fi; \
		extension=$$1; \
		digest_line=$$(sha256sum -- "$$extension"); \
		expected_sha256=$${digest_line%% *}; \
		PACKAGE6_FD_CUSTODY_EXTENSION_PATH="$$extension" \
		PACKAGE6_FD_CUSTODY_EXTENSION_SHA256="$$expected_sha256" \
			uv run python -m scripts.check_test_governance \
				--report-dir "$(TEST_EVIDENCE_DIR)/test-governance"

check-test-governance-topology:
	@set +e; \
		test -n "$${GITHUB_RUN_ID:?}" && \
		test -n "$${FOUNDATION_CONTEXT_PATH:?}" && \
		uv run python -m scripts.check_test_governance \
			--topology-audit \
			--report-dir "$(TEST_EVIDENCE_DIR)/test-governance-topology" \
			--topology-evidence-root "$(TEST_EVIDENCE_DIR)" \
			--inventory "tests/fixtures/t-g03a-hosted-failure-inventory.tsv" \
			--foundation-context-path "$$FOUNDATION_CONTEXT_PATH"; \
		governance_status=$$?; \
		set -e; \
		if test "$$governance_status" -ne 0; then \
			uv run python -m scripts.check_artifact_firewall publish-error \
				--raw-root "$(TEST_EVIDENCE_DIR)" \
				--destination "$${PORTABLE_CI_ARTIFACT_ROOT:?}" \
				--inventory "tests/fixtures/t-g03a-hosted-failure-inventory.tsv" \
				--foundation-context-path "$$FOUNDATION_CONTEXT_PATH" \
				--repository-root "$(CURDIR)" || true; \
			exit "$$governance_status"; \
		fi

check-critical-coverage:
	uv run python scripts/check_critical_coverage.py \
		--report-dir "$(TEST_EVIDENCE_DIR)/critical-coverage"

check-secrets:
	uv run python scripts/verify_secret_hygiene.py --root "$(CURDIR)"

test:
	@set -eu; \
		build_dir=$$(mktemp -d /tmp/package6-custodian-test.XXXXXXXXXX); \
		chmod 0700 "$$build_dir"; \
		test "$$(stat -c '%u:%a' -- "$$build_dir")" = "$$(id -u):700"; \
		$(MAKE) -C native/package6_custodian \
			"BUILD_DIR=$$build_dir" build; \
		set -- "$$build_dir"/python/_package6_fd_custody*.so; \
		if test "$$#" -ne 1 || test -L "$$1" || ! test -f "$$1"; then \
			printf '%s\n' "canonical test requires exactly one regular native custody extension" >&2; \
			exit 2; \
		fi; \
		extension=$$1; \
		digest_line=$$(sha256sum -- "$$extension"); \
		expected_sha256=$${digest_line%% *}; \
		PACKAGE6_FD_CUSTODY_EXTENSION_PATH="$$extension" \
		PACKAGE6_FD_CUSTODY_EXTENSION_SHA256="$$expected_sha256" \
		uv run pytest $(ROOT_PYTEST_ARGS) -q -m "not runtime_postgres and not host_coupled" tests

test-portable-embedded-proof: ROOT_PYTEST_ARGS := --portable-embedded-proof
test-portable-embedded-proof: test

test-core:
	uv run pytest -q -m "not runtime_postgres" tests \
		--ignore=tests/runtime_release --ignore=tests/consolidation \
		--ignore=tests/production --ignore=tests/security

test-consolidation:
	uv run pytest -q tests/consolidation

test-production:
	uv run pytest -q tests/production

test-runtime-release:
	uv run pytest -q -m "not host_coupled" tests/runtime_release

prepare-runtime-release-wheelhouse:
	uv run python scripts/prepare_runtime_release_wheelhouse.py \
		--repo "$(CURDIR)" --destination "$(RUNTIME_RELEASE_WHEELHOUSE)"

test-runtime-release-host:
	TRADING_RUNTIME_RELEASE_WHEELHOUSE="$(RUNTIME_RELEASE_WHEELHOUSE)" \
		uv run pytest -q -m "host_coupled" tests/runtime_release

test-runtime-postgres:
	@set -eu; \
		postgres_evidence_dir="$$(mktemp -d /tmp/foundation-postgres-evidence-XXXXXXXXXX)"; \
		cleanup_postgres_evidence_dir() { \
			find -P "$$postgres_evidence_dir" -xdev -type d -exec chmod u+rwx -- {} +; \
			rm -rf -- "$$postgres_evidence_dir"; \
		}; \
		trap 'cleanup_postgres_evidence_dir' EXIT; \
		chmod 0700 "$$postgres_evidence_dir"; \
		test "$$(stat -c '%u:%a' -- "$$postgres_evidence_dir")" = "$$(id -u):700"; \
		TRADING_TEST_POSTGRES_EVIDENCE_DIR="$$postgres_evidence_dir" \
		uv run python scripts/run_required_runtime_pytest.py \
		tests/control_api/test_postgres_api.py \
		tests/control_api/test_postgres_repositories.py \
		tests/control_api/test_alembic_schema.py \
		tests/control_api/test_foundation_postgres_runtime_parity.py \
		tests/jobs/test_engine_event_postgres_runtime.py

test-package6-paper-runtime:
	uv run python scripts/run_required_runtime_pytest.py \
		tests/foundation/test_package6_runtime_integration.py

build-package6-custodian:
	$(MAKE) -C native/package6_custodian build

test-package6-custodian-native:
	@set -eu; \
		build_dir=$$(mktemp -d /tmp/package6-custodian-native-test.XXXXXXXXXX); \
		chmod 0700 "$$build_dir"; \
		test "$$(stat -c '%u:%a' -- "$$build_dir")" = "$$(id -u):700"; \
		$(MAKE) -C native/package6_custodian \
			"BUILD_DIR=$$build_dir" test

build-nautilus-engine:
	@set -eu; \
		test -n "$(NAUTILUS_ENGINE_PYTHON)"; \
		test -n "$(NAUTILUS_ENGINE_INPUT_CACHE)"; \
		test -n "$(NAUTILUS_ENGINE_WHEEL_CACHE)"; \
		test -n "$(NAUTILUS_ENGINE_WHEEL_CACHE_MANIFEST_SHA256)"; \
		test -n "$(NAUTILUS_ENGINE_CARGO)"; \
		test -n "$(NAUTILUS_ENGINE_LLVM_TOOLCHAIN)"; \
		test -n "$(NAUTILUS_ENGINE_ARTIFACTS)"; \
		$(NAUTILUS_ENGINE_CONTROLLER_PYTHON) scripts/build_nautilus_engine.py \
			--policy engines/nautilus/engine-build-policy.json \
			--python "$(NAUTILUS_ENGINE_PYTHON)" \
			--input-cache "$(NAUTILUS_ENGINE_INPUT_CACHE)" \
			--wheel-cache "$(NAUTILUS_ENGINE_WHEEL_CACHE)" \
			--wheel-cache-manifest-sha256 "$(NAUTILUS_ENGINE_WHEEL_CACHE_MANIFEST_SHA256)" \
			--cargo "$(NAUTILUS_ENGINE_CARGO)" \
			--llvm-toolchain "$(NAUTILUS_ENGINE_LLVM_TOOLCHAIN)" \
			--sandbox "$(NAUTILUS_ENGINE_SANDBOX)" \
			--artifacts "$(NAUTILUS_ENGINE_ARTIFACTS)" \
			--build --offline

verify-nautilus-engine:
	@set -eu; \
		test -n "$(NAUTILUS_ENGINE_PYTHON)"; \
		test -n "$(NAUTILUS_ENGINE_ARTIFACTS)"; \
		$(NAUTILUS_ENGINE_CONTROLLER_PYTHON) scripts/build_nautilus_engine.py \
			--policy engines/nautilus/engine-build-policy.json \
			--python "$(NAUTILUS_ENGINE_PYTHON)" \
			--artifacts "$(NAUTILUS_ENGINE_ARTIFACTS)" \
			--verify

qualify-nautilus-sealed-imports:
	@set -eu; \
		test -n "$(NAUTILUS_IMPORT_POLICY)"; \
		test -n "$(NAUTILUS_IMPORT_BASE_RUNTIME)"; \
		test -n "$(NAUTILUS_IMPORT_ARTIFACT_DIRECTORY)"; \
		test -n "$(NAUTILUS_IMPORT_SANDBOX)"; \
		test -n "$(NAUTILUS_IMPORT_RECEIPT)"; \
		$(NAUTILUS_ENGINE_CONTROLLER_PYTHON) scripts/qualify_nautilus_sealed_imports.py \
			--policy "$(NAUTILUS_IMPORT_POLICY)" \
			--base-runtime "$(NAUTILUS_IMPORT_BASE_RUNTIME)" \
			--artifact-directory "$(NAUTILUS_IMPORT_ARTIFACT_DIRECTORY)" \
			--sandbox "$(NAUTILUS_IMPORT_SANDBOX)" \
			--receipt "$(NAUTILUS_IMPORT_RECEIPT)"

test-event-ledger-runtime-postgres:
	uv run python scripts/run_required_runtime_pytest.py \
		tests/event_ledger/test_snapshot_postgres_runtime.py

test-market-data-runtime-postgres:
	uv run python scripts/run_required_runtime_pytest.py \
		tests/market_data/test_postgres_runtime.py

test-runtime-dual-read:
	uv run python scripts/run_required_runtime_pytest.py \
		tests/control_api/test_dual_read.py

test-security:
	uv run pytest -q tests/security

test-backend:
	cd legacy/research-backend && uv run --frozen --extra test pytest -q

test-dashboard:
	cd apps/dashboard && npm test

typecheck-dashboard:
	cd apps/dashboard && ./node_modules/.bin/tsc --noEmit

lint-dashboard:
	cd apps/dashboard && npm run lint

build-dashboard:
	cd apps/dashboard && npm run build

prepare-root-test-install:
	uv sync --frozen --reinstall-package trading-agent-control-api

test-all-private: audit check-d0-closure check-contracts check-secrets test test-backend test-dashboard typecheck-dashboard lint-dashboard

test-all-portable-private: audit-portable check-d0-closure check-contracts check-secrets test-portable-embedded-proof test-backend test-dashboard typecheck-dashboard lint-dashboard

test-all-portable-topology-private: audit-portable check-d0-closure check-contracts check-secrets test-backend test-dashboard typecheck-dashboard lint-dashboard ci-portable-topology

test-all:
	@set -eu; \
		test_tmpdir=$$(mktemp -d /tmp/trading-agent-test-all.XXXXXXXXXX); \
		chmod 0700 "$$test_tmpdir"; \
		test "$$(stat -c '%u:%a' -- "$$test_tmpdir")" = "$$(id -u):700"; \
		cleanup_test_tmpdir() { find -P "$$test_tmpdir" -xdev -type d -exec chmod u+rwx -- {} +; rm -rf -- "$$test_tmpdir"; }; \
		trap 'cleanup_test_tmpdir' EXIT; \
		TMPDIR="$$test_tmpdir" TEMP="$$test_tmpdir" TMP="$$test_tmpdir" \
			$(MAKE) prepare-root-test-install; \
		TMPDIR="$$test_tmpdir" TEMP="$$test_tmpdir" TMP="$$test_tmpdir" \
			$(MAKE) test-all-private

ci: ci-portable

ci-private:
	$(MAKE) prepare-root-test-install
	$(MAKE) test-all-private check-test-skips check-critical-coverage build-dashboard audit-python-source audit-dependencies

ci-portable:
	@set -eu; \
		test -n "$${GITHUB_RUN_ID:?}"; \
		test -n "$${GITHUB_RUN_ATTEMPT:?}"; \
		case "$$GITHUB_RUN_ID" in *[!0-9]*|'') printf '%s\n' "portable CI run ID is invalid" >&2; exit 2;; esac; \
		case "$$GITHUB_RUN_ATTEMPT" in *[!0-9]*|'') printf '%s\n' "portable CI run attempt is invalid" >&2; exit 2;; esac; \
		case "$$GITHUB_RUN_ID:$$GITHUB_RUN_ATTEMPT" in 0*:*|*:0*) printf '%s\n' "portable CI artifact identity is noncanonical" >&2; exit 2;; esac; \
		artifact_root="$${RUNNER_TEMP:?}/trading-agent-ci-portable-artifact.$${GITHUB_RUN_ID:?}.$${GITHUB_RUN_ATTEMPT:?}"; \
		export PORTABLE_CI_ARTIFACT_ROOT="$$artifact_root"; \
		if test -e "$$artifact_root" || test -L "$$artifact_root"; then \
			printf '%s\n' "portable CI artifact destination already exists" >&2; \
			exit 2; \
		fi; \
		raw_evidence_root=$$(mktemp -d "$${RUNNER_TEMP:?}/trading-agent-ci-portable-evidence.XXXXXXXXXX"); \
		chmod 0700 "$$raw_evidence_root"; \
		test "$$(stat -c '%u:%a' -- "$$raw_evidence_root")" = "$$(id -u):700"; \
		foundation_context_path=$$(TEST_EVIDENCE_DIR="$$raw_evidence_root" uv run python -c 'import sys; from pathlib import Path; from scripts.t_g03_capability_topology import _capture_foundation_context; print(_capture_foundation_context(Path(sys.argv[1])))' "$$raw_evidence_root"); \
		export FOUNDATION_CONTEXT_PATH="$$foundation_context_path"; \
		ci_tmpdir=$$(mktemp -d "$${RUNNER_TEMP:?}/trading-agent-ci-portable.XXXXXXXXXX"); \
		chmod 0700 "$$ci_tmpdir"; \
		test "$$(stat -c '%u:%a' -- "$$ci_tmpdir")" = "$$(id -u):700"; \
		cleanup_ci_tmpdir() { \
			find -P "$$ci_tmpdir" -xdev -type d -exec chmod u+rwx -- {} +; \
			rm -rf -- "$$ci_tmpdir"; \
		}; \
		trap 'cleanup_ci_tmpdir' EXIT; \
		TMPDIR="$$ci_tmpdir" TEMP="$$ci_tmpdir" TMP="$$ci_tmpdir" \
			TEST_EVIDENCE_DIR="$$raw_evidence_root" $(MAKE) ci-portable-private || { \
			original_status=$$?; \
			failure_diagnostic="$$raw_evidence_root/capability-topology/portable-root-remainder.failure-diagnostic.json"; \
			if test -e "$$failure_diagnostic" || test -L "$$failure_diagnostic"; then \
				uv run python -m scripts.check_artifact_firewall publish-failure \
						--raw-root "$$raw_evidence_root" \
						--destination "$${PORTABLE_CI_ARTIFACT_ROOT:?}" \
						--inventory "tests/fixtures/t-g03a-hosted-failure-inventory.tsv" \
						--foundation-context-path "$$FOUNDATION_CONTEXT_PATH" \
						--repository-root "$(CURDIR)" || :; \
			fi; \
			exit "$$original_status"; \
		}

ci-portable-private:
	$(MAKE) ci-common-private ci-portable-topology check-portable-defect-closure check-p0-baseline check-test-governance-topology check-p0-ci-closure artifact-firewall-check audit-delivery-contract

ci-common-private:
	$(MAKE) prepare-root-test-install
	$(MAKE) audit-portable check-d0-closure check-contracts check-secrets test-backend test-dashboard typecheck-dashboard lint-dashboard build-dashboard audit-python-source audit-dependencies

artifact-firewall-check:
	uv run python -m scripts.check_artifact_firewall publish \
		--raw-root "$(TEST_EVIDENCE_DIR)" \
		--destination "$${PORTABLE_CI_ARTIFACT_ROOT:?}" \
		--inventory "tests/fixtures/t-g03a-hosted-failure-inventory.tsv" \
		--foundation-context-path "$$FOUNDATION_CONTEXT_PATH" \
		--repository-root "$(CURDIR)"

audit-delivery-contract: check-critical-coverage

ci-host-authority: check-p0-baseline
	@set -eu; \
		foundation_context_path=$$(uv run python -c 'import sys; from pathlib import Path; from scripts.t_g03_capability_topology import _capture_foundation_context; print(_capture_foundation_context(Path(sys.argv[1])))' "$(TEST_EVIDENCE_DIR)"); \
		export FOUNDATION_CONTEXT_PATH="$$foundation_context_path"; \
		ci_tmpdir=$$(mktemp -d /tmp/trading-agent-ci-host-authority.XXXXXXXXXX); \
		chmod 0700 "$$ci_tmpdir"; \
		test "$$(stat -c '%u:%a' -- "$$ci_tmpdir")" = "$$(id -u):700"; \
		cleanup_ci_tmpdir() { find -P "$$ci_tmpdir" -xdev -type d -exec chmod u+rwx -- {} +; rm -rf -- "$$ci_tmpdir"; }; \
		trap 'cleanup_ci_tmpdir' EXIT; \
		TMPDIR="$$ci_tmpdir" TEMP="$$ci_tmpdir" TMP="$$ci_tmpdir" \
			$(MAKE) ci-host-authority-private

ci-host-authority-private:
	$(MAKE) ci-portable-topology
	uv run python -m scripts.t_g03_capability_topology validate-native --require-pass --evidence-root "$(TEST_EVIDENCE_DIR)" --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"
	uv run python -m scripts.t_g03_capability_topology validate-external --require-pass --evidence-root "$(TEST_EVIDENCE_DIR)" --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"
	$(MAKE) test-runtime-release-host

# Capability topology is separate from strict ci/audit and never releases runtime proof.
test-portable-source:
	@set -eu; test -n "$${GITHUB_RUN_ID:?}"; \
		build_dir=$$(mktemp -d /tmp/package6-custodian-portable-source.XXXXXXXXXX); \
		chmod 0700 "$$build_dir"; \
		cleanup_build_dir() { find -P "$$build_dir" -xdev -type d -exec chmod u+rwx -- {} +; rm -rf -- "$$build_dir"; }; \
		trap 'cleanup_build_dir' EXIT; \
		$(MAKE) -C native/package6_custodian "BUILD_DIR=$$build_dir" build; \
		set -- "$$build_dir"/python/_package6_fd_custody*.so; \
		if test "$$#" -ne 1 || test -L "$$1" || ! test -f "$$1"; then \
			printf '%s\n' "portable source requires exactly one regular native custody extension" >&2; \
			exit 2; \
		fi; \
		digest_line=$$(sha256sum -- "$$1"); \
		export PACKAGE6_FD_CUSTODY_EXTENSION_PATH="$$1"; \
		export PACKAGE6_FD_CUSTODY_EXTENSION_SHA256=$${digest_line%% *}; \
		uv run python -m scripts.t_g03_capability_topology reserve --evidence-root "$(TEST_EVIDENCE_DIR)" --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"; \
		uv run python -m scripts.t_g03_capability_topology collect-baseline --evidence-root "$(TEST_EVIDENCE_DIR)" --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"; \
		TMPDIR=/tmp TMP=/tmp TEMP=/tmp \
			uv run python -m scripts.t_g03_capability_topology check-closure --evidence-root "$(TEST_EVIDENCE_DIR)" --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"

check-portable-defect-closure:
	@set -eu; \
		test -n "$${GITHUB_RUN_ID:?}"; \
		TMPDIR=/tmp TMP=/tmp TEMP=/tmp \
			uv run python -m scripts.t_g03_capability_topology check-closure --evidence-root "$(TEST_EVIDENCE_DIR)" --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"

test-native-capabilities:
	@set -eu; test -n "$${GITHUB_RUN_ID:?}"; \
		test -n "$${FOUNDATION_CONTEXT_PATH:?}"; \
		build_dir=$$(mktemp -d /tmp/package6-custodian-native-capabilities.XXXXXXXXXX); \
		chmod 0700 "$$build_dir"; \
		cleanup_build_dir() { find -P "$$build_dir" -xdev -type d -exec chmod u+rwx -- {} +; rm -rf -- "$$build_dir"; }; \
		trap 'cleanup_build_dir' EXIT; \
		$(MAKE) -C native/package6_custodian "BUILD_DIR=$$build_dir" build; \
		set -- "$$build_dir"/python/_package6_fd_custody*.so; \
		if test "$$#" -ne 1 || test -L "$$1" || ! test -f "$$1"; then \
			printf '%s\n' "native capabilities require exactly one regular native custody extension" >&2; \
			exit 2; \
		fi; \
		digest_line=$$(sha256sum -- "$$1"); \
		export PACKAGE6_FD_CUSTODY_EXTENSION_PATH="$$1"; \
		export PACKAGE6_FD_CUSTODY_EXTENSION_SHA256=$${digest_line%% *}; \
		uv run python -m scripts.t_g03_capability_topology reserve --evidence-root "$(TEST_EVIDENCE_DIR)" --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"; \
		uv run python -m scripts.t_g03_capability_topology collect-baseline --evidence-root "$(TEST_EVIDENCE_DIR)" --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"; \
		TMPDIR=/tmp TMP=/tmp TEMP=/tmp \
			uv run python -m scripts.t_g03_capability_topology run-lane --lane native-capabilities --evidence-root "$(TEST_EVIDENCE_DIR)" --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"

test-external-authorities:
	@set -eu; test -n "$${GITHUB_RUN_ID:?}"; \
		test -n "$${FOUNDATION_CONTEXT_PATH:?}"; \
		build_dir=$$(mktemp -d /tmp/package6-custodian-external-authorities.XXXXXXXXXX); \
		chmod 0700 "$$build_dir"; \
		cleanup_build_dir() { find -P "$$build_dir" -xdev -type d -exec chmod u+rwx -- {} +; rm -rf -- "$$build_dir"; }; \
		trap 'cleanup_build_dir' EXIT; \
		$(MAKE) -C native/package6_custodian "BUILD_DIR=$$build_dir" build; \
		set -- "$$build_dir"/python/_package6_fd_custody*.so; \
		if test "$$#" -ne 1 || test -L "$$1" || ! test -f "$$1"; then \
			printf '%s\n' "external authorities require exactly one regular native custody extension" >&2; \
			exit 2; \
		fi; \
		digest_line=$$(sha256sum -- "$$1"); \
		export PACKAGE6_FD_CUSTODY_EXTENSION_PATH="$$1"; \
		export PACKAGE6_FD_CUSTODY_EXTENSION_SHA256=$${digest_line%% *}; \
		uv run python -m scripts.t_g03_capability_topology reserve --evidence-root "$(TEST_EVIDENCE_DIR)" --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"; \
		uv run python -m scripts.t_g03_capability_topology collect-baseline --evidence-root "$(TEST_EVIDENCE_DIR)" --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"; \
		TMPDIR=/tmp TMP=/tmp TEMP=/tmp \
		uv run python -m scripts.t_g03_capability_topology run-lane --lane external-authorities --evidence-root "$(TEST_EVIDENCE_DIR)" --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"

test-portable-root-remainder:
	@set -eu; \
		test -n "$${GITHUB_RUN_ID:?}"; \
		uv run python -m scripts.t_g03_capability_topology collect-baseline --evidence-root "$(TEST_EVIDENCE_DIR)" --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"; \
		uv run python -m scripts.t_g03_capability_topology prepare-remainder --evidence-root "$(TEST_EVIDENCE_DIR)" --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"; \
		uv run python -m scripts.t_g03_capability_topology run-remainder --evidence-root "$(TEST_EVIDENCE_DIR)" --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"

ci-portable-topology:
	@set -eu; \
		test -n "$${GITHUB_RUN_ID:?}"; \
		build_dir=$$(mktemp -d /tmp/package6-custodian-portable-topology.XXXXXXXXXX); \
		chmod 0700 "$$build_dir"; \
		cleanup_build_dir() { find -P "$$build_dir" -xdev -type d -exec chmod u+rwx -- {} +; rm -rf -- "$$build_dir"; }; \
		trap 'cleanup_build_dir' EXIT; \
		$(MAKE) -C native/package6_custodian "BUILD_DIR=$$build_dir" build; \
		set -- "$$build_dir"/python/_package6_fd_custody*.so; \
		if test "$$#" -ne 1 || test -L "$$1" || ! test -f "$$1"; then \
			printf '%s\n' "portable topology requires exactly one regular native custody extension" >&2; \
			exit 2; \
		fi; \
		extension=$$1; \
		digest_line=$$(sha256sum -- "$$extension"); \
		expected_sha256=$${digest_line%% *}; \
		export PACKAGE6_FD_CUSTODY_EXTENSION_PATH="$$extension"; \
		export PACKAGE6_FD_CUSTODY_EXTENSION_SHA256="$$expected_sha256"; \
		uv run python -m scripts.t_g03_capability_topology reserve --evidence-root "$(TEST_EVIDENCE_DIR)" --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"; \
		$(MAKE) test-portable-root-remainder; \
		TMPDIR=/tmp TMP=/tmp TEMP=/tmp \
			uv run python -m scripts.t_g03_capability_topology check-closure --evidence-root "$(TEST_EVIDENCE_DIR)" --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"; \
		uv run python -m scripts.t_g03_capability_topology run-lane --lane native-capabilities --evidence-root "$(TEST_EVIDENCE_DIR)" --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"; \
		uv run python -m scripts.t_g03_capability_topology run-lane --lane external-authorities --evidence-root "$(TEST_EVIDENCE_DIR)" --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"; \
		uv run python -m scripts.t_g03_capability_topology aggregate --evidence-root "$(TEST_EVIDENCE_DIR)" --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"
