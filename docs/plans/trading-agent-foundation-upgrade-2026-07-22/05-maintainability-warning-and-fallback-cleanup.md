# Package 5 - Maintainability, Warning and Silent-Fallback Cleanup

## Goal

Remove current warning noise and replace broad exception handling or silent fallback in critical legacy paths with typed, observable behavior.

## Current issues

- Starlette/httpx TestClient deprecation.
- TypeScript factory deprecation in contract generation.
- Repeated `MODULE_TYPELESS_PACKAGE_JSON`.
- TODO for alpha benchmark in `reflection_engine.py`.
- Broad exceptions that silently fall back in legacy modules.

## Priority order

```text
silent fallback in correctness/safety paths
→ warnings that hide future breakage
→ module/package metadata
→ non-critical TODO documentation
```

## Workstream A - Broad-exception inventory

Search for:

```python
except Exception:
except:
```

and fallback patterns such as:

```text
return default
return empty
continue
pass
log warning and treat as success
```

Classify each:

```text
SAFETY_CRITICAL
DATA_CORRECTNESS
RESEARCH_QUALITY
OBSERVABILITY_ONLY
BENIGN_BOUNDARY
```

## Required remediation behavior

For safety/data-correctness paths:

- catch specific exceptions;
- return typed error/status;
- preserve trace ID;
- emit bounded structured log;
- do not manufacture valid-looking output;
- do not convert source failure into `FRESH`, `PASS`, or successful job state.

For optional enrichments:

- explicit `PARTIAL`/`UNAVAILABLE`;
- include reason code;
- preserve base pipeline output if policy allows.

## Workstream B - Deprecation cleanup

Resolve warnings without broad upgrades:

- pin/update compatible Starlette/httpx usage or test client invocation;
- update deprecated TypeScript factory APIs;
- set package module metadata to remove `MODULE_TYPELESS_PACKAGE_JSON`;
- add tests to ensure warnings do not return.

Introduce a warning allowlist if unavoidable. New warnings should fail the warning-governance target.

## Workstream C - Benchmark TODO

Do not invent an alpha benchmark in this foundation package.

Replace ambiguous TODO with one of:

```text
tracked backlog item with owner and acceptance
explicit NotImplemented/UNKNOWN status
feature flag disabled
```

The reflection engine must not imply benchmark completion.

## Acceptance

- Critical broad exceptions are eliminated or justified.
- Silent fallback cannot produce false success/freshness.
- Targeted deprecation warnings are removed.
- Module-type warning is removed.
- `make ci` passes.
- No unrelated dependency upgrades.
- Residual broad exceptions have documented ownership and reason.

## Stop conditions

Stop if cleanup changes trading strategy semantics, requires provider calls, or upgrades major frameworks without a separate plan.

## Deliverables

```text
docs/implementation/foundation-exception-inventory.md
docs/implementation/foundation-fallback-policy.md
docs/implementation/foundation-warning-cleanup.md
docs/implementation/foundation-maintainability-evidence.md
```

## Final decision

```text
GO - FOUNDATION FAILURE MODES ARE EXPLICIT
```

or:

```text
NO-GO - SILENT FAILURE RISK REMAINS
```
