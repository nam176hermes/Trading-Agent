# Package 3 - Test Skip Governance and Critical Coverage

## Goal

Turn the current skipped-test population into a managed, reviewed inventory and enforce coverage thresholds on critical safety and state-machine branches.

Current baseline:

```text
Root suite: 226 skipped
Legacy backend: 2 skipped
```

The aim is not to force every test to run on every host. The aim is to make every skip attributable, owned and visible.

## In scope

- Inventory all skipped/deselected tests.
- Classify reason and required environment.
- Add a committed allowlist with owner and target phase.
- Fail CI on new unapproved skips.
- Add branch coverage thresholds for critical packages.
- Publish machine-readable reports.

## Skip categories

Use only approved categories:

```text
APPROVAL_REQUIRED
DISPOSABLE_POSTGRES_REQUIRED
MISSING_HOST_BINARY
MISSING_HOST_CAPABILITY
EXTERNAL_INTEGRATION
PROVIDER_CREDENTIAL_REQUIRED
PLATFORM_SPECIFIC
INTENTIONALLY_DEFERRED
QUARANTINED_FLAKY
UNKNOWN
```

`UNKNOWN` must fail the governance gate.

## Required inventory fields

```text
test_node_id
component
reason_category
reason
owner
approval_record_type
required_binary_or_service
target_phase
review_by
security_critical
allowed_in_ci
```

## CI policy

Use a committed JSON inventory parsed by the Python standard library. Do not use YAML from the legacy backend dependency graph.

Create a canonical target such as:

```bash
make check-test-skips
```

It must:

- run the relevant suites in collection/report mode;
- compare actual skips against the allowlist;
- fail on a new skip;
- fail on removed/renamed tests that leave stale allowlist entries;
- fail when `review_by` expires;
- fail when a security-critical skip has no explicit approval reason.

## Coverage tooling gate

The root project currently has no pytest coverage dependency, and the dashboard has no c8/Istanbul dependency. Before changing any protected dependency manifest:

1. Measure available built-in tooling and current coverage.
2. Prefer Python standard-library-compatible reporting and Node 22 built-in test coverage where it satisfies branch reporting.
3. If a new dev dependency is required, present a separate dependency approval with exact package, pinned version, lockfile impact and rollback.
4. Never import PyYAML from `legacy/research-backend` into root tooling.
5. Establish the measured baseline first; then ratchet without lowering existing safety assertions.

Exact commands and supported flags must be proven on Python 3.11 and CI Node 22 before editing CI.

## Critical coverage scope

Add branch coverage thresholds for:

```text
packages/domain
packages/event_ledger
services/job_worker/safety.py
job state machine
transition authority/repository
dashboard auth
dashboard mutation policy
```

Do not use only whole-repository line coverage.

Long-term target, not an assumed starting baseline:

```text
branch coverage ≥ 90% for critical modules
line coverage ≥ 95% for critical modules
```

If current evidence is below target, establish a ratchet:

```text
current measured value becomes minimum
target increases in explicit steps
no regression allowed
```

## Required coverage cases

Safety branches must include:

- paper mode;
- unknown mode;
- both live gates;
- kill switch active/unknown;
- invalid manifest;
- stale safety evidence;
- unsafe child environment;
- cancellation during heartbeat;
- lease/fence mismatch;
- transition/event atomic failure;
- dashboard auth timeout/origin/body-size failure.

## Acceptance

- Every skip is in the managed inventory.
- No unknown skip.
- New unapproved skips fail CI.
- Critical coverage is measured in canonical CI.
- Critical coverage cannot regress.
- Report distinguishes executed, skipped, deselected and approval-blocked tests.
- `make ci` remains green.

## Stop conditions

Stop if governance changes hide skips, convert failures into skips, or lower existing safety assertions.

## Deliverables

```text
tests/skip-allowlist.json
docs/implementation/foundation-skip-inventory.md
docs/implementation/foundation-critical-coverage.md
docs/implementation/foundation-test-governance-evidence.md
```

## Final decision

```text
GO - TEST EVIDENCE IS MANAGED
```

or:

```text
NO-GO - TEST EVIDENCE REMAINS AMBIGUOUS
```
