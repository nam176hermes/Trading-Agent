# Foundation warning cleanup

## Scope

Package 05 removes three targeted warning families without broad upgrades or warning suppression:

1. Starlette TestClient and HTTP client compatibility.
2. Deprecated TypeScript factory calls during OpenAPI contract generation.
3. Node `MODULE_TYPELESS_PACKAGE_JSON` warnings in the Dashboard.

## Starlette TestClient

### Cause

Starlette `1.3.1` prefers the `httpx2` compatibility package. Without it, TestClient falls back to `httpx` and emits a deprecation warning.

### Remediation

`httpx2==2.9.1` was added as a root development dependency through `uv add --dev`. Lock resolution added:

| Package | Version |
|---|---:|
| `httpx2` | `2.9.1` |
| `httpcore2` | `2.9.1` |
| `truststore` | `0.10.4` |

Fresh lock comparison against `HEAD` found zero removed packages and zero changed package versions. The watched framework versions remain:

| Package | Before | Candidate |
|---|---:|---:|
| FastAPI | `0.139.0` | `0.139.0` |
| Starlette | `1.3.1` | `1.3.1` |
| `httpx` | `0.28.1` | `0.28.1` |
| Pydantic | `2.13.4` | `2.13.4` |
| AnyIO | `4.14.1` | `4.14.1` |

## TypeScript factory compatibility

### Cause

Contract generation calls `openapi-zod-client@1.18.3`, which reaches `tanu@0.1.13` and a deprecated five-argument TypeScript `createTypeAliasDeclaration` signature.

### Remediation

`scripts/typescript_factory_compat.cjs` is a repository-owned preload bridge. `scripts/generate_contracts.py` starts Node with the bridge through `--require` before invoking the generator.

The bridge adapts only the deprecated signature. It does not edit `node_modules`, suppress warnings, or replace generated output.

## Dashboard module metadata

### Cause

Dashboard JavaScript tests use ES modules while `apps/dashboard/package.json` lacked explicit module type metadata. Node reparsed the files and emitted `MODULE_TYPELESS_PACKAGE_JSON`.

### Remediation

`apps/dashboard/package.json` now declares:

```json
"type": "module"
```

This matches existing ESM test usage and adds no dependency.

## Governance

`tests/governance/test_warning_governance.py` proves:

- Starlette TestClient does not emit the targeted HTTP client deprecation;
- contract generation does not emit the deprecated TypeScript factory warning;
- Dashboard Node tests do not emit `MODULE_TYPELESS_PACKAGE_JSON`;
- each underlying command succeeds.

No broad warning filter or allowlist was added.

## Fresh verification

### Warning and contract tests

```bash
uv run --frozen pytest -q \
  tests/governance/test_warning_governance.py \
  tests/control_api/test_generation.py
```

Result:

```text
6 passed in 8.48s
```

### Contract generation

```bash
make check-contracts
```

Result: exit `0`. Both OpenAPI TypeScript and Zod generation completed without the targeted warning.

### Dashboard tests

```bash
cd apps/dashboard && npm test
```

Result:

```text
158 passed, 0 failed
```

No module-type warning appeared.

### Dashboard build

```bash
cd apps/dashboard && npm run build
```

Result: exit `0`. Next.js compiled, TypeScript completed, and all static pages generated.

## Constraints

Package 05 does not authorize:

- unrelated FastAPI, Starlette, `httpx`, TypeScript, Next.js, or dependency upgrades;
- direct edits under `node_modules`;
- broad warning suppression;
- an allowlist without exact identity, owner, reason, and expiry.
