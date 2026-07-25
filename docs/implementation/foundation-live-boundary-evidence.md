# Foundation live-boundary evidence

## Decision gate

This document defines the Package 4 GO gate. It does not claim that an uncommitted candidate passed. Closure requires one exact Git tree to pass the listed tests, real build, relocation proof, physical inventory, seal checks, and two independent reviews.

The decision is limited to the canonical paper artifact boundary. It does not publish, promote, activate or authorize a production release. Production promotion retains separate approval and host-coupled acceptance gates.

## Requirement matrix

| Requirement | Enforcement | Executable evidence |
|---|---|---|
| Built paper artifact excludes live modules | Exact source projection plus dual artifact inspection | `tests/runtime_release/test_paper_boundary.py` |
| No live command catalog | Version 3 manifest with one fixed command | paper boundary and command-registry tests |
| No paper job resolves a live module | Immutable `COMMAND_REGISTRY` contains `SNAPSHOT` only | `tests/jobs/test_command_registry.py` |
| No live adapter registry | `exchange` package absent; AST imports denied | built artifact mutation tests |
| Venv cannot reveal live modules | system-site inheritance disabled; application files equal the wheel manifest; backend site-packages is empty | dependency-manifest and venv injection tests |
| Build tool cannot inject packages | code-owned `uv` identity and SHA-256; private copied executable | pinned-tool projection tests |
| Resealing cannot legitimize an implant | authority and standalone verifier pin the independent 546-file dependency closure | resealed-authority implant regression |
| No real order submission | order symbols and legacy files denied | built artifact mutation tests |
| No credential loader | secrets, dotenv and runtime env loaders absent | artifact inspection and inventory |
| No mode transition | no mode API and entrypoint accepts no arguments | command mutation tests |
| Trading credentials do not enter child | empty-start environment and negative key matrix | `tests/jobs/test_child_environment.py` |
| Live values forced off | four exact values added by child builder | child environment tests |
| Full Git evidence retained | excluded backend entries have `stage_path: null` | v2 source-proof tests |
| Standalone verifier agrees | independent schema, file, AST and command checks | `tests/runtime_release/test_v2.py` |
| Legacy code retained | files remain in Git but are not staged | source proof and inventory |

## Negative artifact cases

The built artifact suite constructs the canonical paper artifact from the repository-owned mixed source mapping and then injects each forbidden path in turn. Inspection must reject every mutation. It separately replaces the canonical command with:

- legacy `main.py` plus mode flags;
- a live mode argument;
- `execute_live.py`;
- a broker order command.

Each mutation must fail authority inspection.

## Verification commands

```bash
uv run pytest -q tests/runtime_release/test_paper_boundary.py
uv run pytest -q tests/runtime_release/test_v2.py
uv run pytest -q tests/runtime_release/test_offline_wheelhouse.py
uv run pytest -q tests/runtime_release/test_v2_provisioning.py
uv run pytest -q tests/jobs/test_command_registry.py tests/jobs/test_command_registry_v2.py tests/jobs/test_child_environment.py
uv run pytest -q tests/runtime_release/test_build.py -m 'not host_coupled'
make audit
make check-contracts
make test-all
make build-dashboard
make ci
```

Host-coupled production release construction remains a separate promotion gate when the approved offline wheelhouse and relocatable runtimes are configured. Package 4 does not waive or satisfy that future production gate.

## Closure record

The closure record must name the exact source commit or candidate tree, diff digest, real stage authority digest, relocation result, physical dependency inventory, verifier pin, and both independent review verdicts. A source, test, or documentation edit invalidates that record and requires a new candidate.

Temporary artifact paths are execution evidence, not published releases. Package validation must not activate a service, migrate a database, call a broker or exchange, or enable live execution.
