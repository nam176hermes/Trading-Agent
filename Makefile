.PHONY: audit audit-release audit-python-source audit-dependencies-production \
	audit-dependencies-dev audit-dependencies generate-contracts check-contracts \
	check-d0-closure \
	check-secrets test test-core test-consolidation test-production \
	test-runtime-release prepare-runtime-release-wheelhouse test-runtime-release-host test-runtime-postgres \
	test-event-ledger-runtime-postgres \
	test-runtime-dual-read test-security \
	test-backend test-dashboard typecheck-dashboard lint-dashboard \
	build-dashboard test-all ci

RUNTIME_RELEASE_LOCK_SHA256 := $(shell sha256sum uv.lock | cut -d' ' -f1)
RUNTIME_RELEASE_WHEELHOUSE_ROOT ?= $(HOME)/.cache/trading-agent/runtime-release-wheelhouse
RUNTIME_RELEASE_WHEELHOUSE := $(RUNTIME_RELEASE_WHEELHOUSE_ROOT)/$(RUNTIME_RELEASE_LOCK_SHA256)

audit:
	uv run python scripts/audit_canonical_repo.py --root "$(CURDIR)"

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

check-secrets:
	uv run python scripts/verify_secret_hygiene.py --root "$(CURDIR)"

test:
	uv run pytest -q -m "not runtime_postgres and not host_coupled" tests

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
	uv run python scripts/run_required_runtime_pytest.py \
		tests/control_api/test_postgres_api.py \
		tests/control_api/test_postgres_repositories.py \
		tests/control_api/test_alembic_schema.py \
		tests/control_api/test_foundation_postgres_runtime_parity.py

test-event-ledger-runtime-postgres:
	uv run python scripts/run_required_runtime_pytest.py \
		tests/event_ledger/test_snapshot_postgres_runtime.py

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

test-all: audit check-d0-closure check-contracts check-secrets test test-backend test-dashboard typecheck-dashboard lint-dashboard

ci: test-all build-dashboard audit-python-source audit-dependencies
