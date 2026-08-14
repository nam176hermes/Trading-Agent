# Foundation test governance evidence

Date: 2026-08-02
Package: Test skip governance and critical coverage

## Implemented controls

- Exact node-level pytest reporter for passed, failed, skipped, deselected and not-run tests.
- Collection-integrity detection for individual tests removed by repository hooks.
- Fail-closed session status for any collected test that never executes.
- Shared recursive Dashboard inventory consumed by `npm test` and governance, with file-qualified TAP and shell integration observations plus per-file zero-observation rejection.
- Committed 259-entry skip and deselection allowlist sealing component, exact node ID, outcome and normalized reason.
- Strict approved-category, field, ownership, expiry and approval validation.
- New skip, stale allowlist, outcome drift and runtime-reason drift rejection.
- Security-critical approval-metadata enforcement based on canonical component, path and category rules.
- Machine-readable merged governance report.
- Fail-closed stale-artifact removal and machine-readable error reports.
- Private evidence directories and descriptor-relative atomic report writes resistant to symlink and parent-replacement races.
- Per-unit Python and dashboard branch coverage ratchets.
- Exact parameterized executed-test proof for the required safety cases.
- Sealed critical source and required-case identities with denominator and covered-count shrinkage rejection.
- Canonical `make check-test-skips` and `make check-critical-coverage` targets.
- Both gates added to `make ci`, which is already called by `.github/workflows/foundation.yml`.
- CI artifact publication for `/tmp/trading-agent-test-evidence`, retained for 14 days.
- Runtime evidence stored outside the checkout to preserve secret-hygiene scanning.

## Test-first evidence

The first focused governance run failed during import because the checker modules did not exist. After implementation and one message-contract correction, the focused policy suite passed:

```text
7 passed in 0.09s
```

A host-independent stale lease fence test and report-integrity cases were then added. Focused verification passed:

```text
18 passed in 0.29s
```

No existing failure was converted into a skip. No selection filter was broadened. No safety assertion was lowered.

## Critical coverage evidence

Fresh canonical checker result:

```text
692 passed, 44 skipped, 5 deselected in 10.35s
packages/domain: lines=95.1359% branches=87.3737%
packages/event_ledger: lines=95.2596% branches=90.1316%
services/job_worker/safety.py: lines=86.8613% branches=80.3571%
job state machine: lines=100.0000% branches=100.0000%
transition authority: lines=86.3636% branches=78.2609%
transition repositories: lines=85.4801% branches=75.4237%
dashboard auth and mutation policy: lines=95.7684% branches=88.6792%
```

Exit status: 0.

Machine report:

```text
/tmp/trading-agent-test-evidence/critical-coverage/critical-coverage.json
```

The report records status `pass` only when every required safety case appears in successful test observations. Failed, skipped and deselected nodes do not satisfy this requirement. Every collected variant of a parametrized required test must pass.

## Inventory evidence

| Check | Result |
|---|---:|
| Managed entries | 259 |
| Root skips | 228 |
| Root deselections | 29 |
| Legacy skips | 2 |
| Dashboard skips | 0 |
| Unknown categories | 0 |
| Duplicate component/node pairs | 0 |
| Security-critical entries | 248 |
| Security-critical entries without approval metadata | 0 |

The historical supplied aggregate said 226 root skips. The current full-suite observation found 228. The node-level live result is committed and the discrepancy is visible rather than normalized away.

## Security hygiene evidence

The first in-checkout report exposed a deliberate interaction with the repository secret scanner: synthetic security-test node IDs contain pattern names such as credential URI and private key. The scanner correctly rejected those untracked report files.

The implementation now writes runtime evidence under `/tmp/trading-agent-test-evidence`. Fresh targeted verification returned:

```text
1 passed in 0.97s
```

This preserves the scanner and changes report placement instead of weakening secret detection.

## Latest production-gate evidence

The transition-repository evidence now includes 23 additional provider-free cases for scheduler enqueue rollback and authority validation, read-filter boundaries, empty claims, lease-control and heartbeat validation, retry finalization, and expired-lease recovery observations. The measured transition-repository floor is 365/427 lines and 89/118 branches; the policy advances the next steps to 95% lines and 80% branches.

The final `make check-critical-coverage` run executed 692 Python tests and 52 focused dashboard tests, validated every exact required case, and held every line, branch, denominator and covered-count ratchet. Exit status: 0.

`make check-test-skips` collected all three components and validated the exact 259-entry allowlist. Its merged report recorded 5,085 passing tests, zero failures, 230 skips, 29 deselections, zero not-run tests and 245 approval-blocked observations. Dashboard evidence came from the same recursive inventory used by `npm test`: 14 Node test files plus `dashboard-security.integration.sh` and `mode-auth.integration.sh`. The command exited zero.

Portable source-authority integration separates strict and portable verification. Strict mode verifies source authority and imported bytes at each unique atomic introduction commit, while permitting legitimate later monorepo evolution. Portable audit is explicit and available only when every declared external repository is absent; it verifies introduction and HEAD against immutable embedded evidence. Partial authority availability, modified imported bytes, manifest identity drift, aggregate drift and coordinated authority-plus-manifest tampering fail closed. The complete consolidation suite passed 171 tests.

The canonical `make ci` result and independent-review verdict are candidate-bound evidence. They must be generated after the final repository bytes are sealed and retained outside the repository. Earlier green CI or review output never accepts a modified candidate.

## Decision rule

Use `GO -- TEST EVIDENCE IS MANAGED` only when:

- the allowlist comparison passes with no unknown, stale, changed-outcome or changed-reason entry;
- no collected test is hidden or left not-run;
- required critical cases are observed as executed under their exact sealed identities;
- every coverage ratio and exact counter is at or above its committed ratchet;
- current private machine reports are generated;
- canonical `make ci` exits zero on the exact candidate;
- a fresh isolated read-only review of the same candidate has no unresolved high or medium finding;
- candidate bytes do not drift between review, CI and the acceptance decision;
- no unrelated safety or security assertion is weakened.

The final GO or NO-GO decision belongs in the external exact-candidate acceptance record, not this source document.

## P0-07 native-capability evidence boundary

Native capability evidence is now distinct from external-authority evidence.
The two native codes use strict receipt schema
`t-g03a-native-capability-receipt/v2`; external codes continue to use v1.
Native v2 binds Foundation run/head/date/context, the locked active-inventory
digest, exact 16/8 node lists, one closed retained-FD probe, exact execution
counts, outcome, completeness, and receipt hashes.

The implementation executes real inert namespace operations through retained
root-owned `/usr/bin/bwrap` and `/usr/bin/unshare` descriptors. Bubblewrap uses
the committed sealed-UV policy validator. Absence and only exact reviewed host
namespace-policy denial can defer. Invalid identity, misleading or partial
output, timeout, execution error, test failure, and executable replacement
publish FAIL and fail the lane. No synthetic executable establishes native
availability.

Native receipt/governance evidence is private, no-clobber, descriptor-retained,
and replacement-postchecked. PASS requires exact governance bytes matching the
baseline's sealed custody and all expected nodes. DEFERRED requires no
governance. Portable aggregation accepts and surfaces explicit DEFERRED, while
the future-facing `validate-native --require-pass` caller rejects it. That host
qualification mode is not wired into portable CI in P0-07.

TDD covers native-v1 rejection with external-v1 preservation; strict context,
probe, count, code and node binding; real retained-FD argv; narrow denial and
timeout classification; absent versus present-invalid authority; strict FAIL
publication; exact 16-plus-eight execution; retained executable replacement at
multiple boundaries; symlink, mode, owner, parent and leaf artifact mutation;
forged PASS; and no-clobber retry behavior. Exact final-head runtime evidence
and independent review remain candidate-bound and belong in the external task
report; this tracked document does not convert a pre-commit run into final
acceptance and does not claim full CI.
