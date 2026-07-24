# Foundation test governance evidence

Date: 2026-07-24
Package: Test skip governance and critical coverage

## Implemented controls

- Exact node-level pytest reporter for passed, failed, skipped and deselected tests.
- Dashboard TAP inventory plus shell integration observation.
- Committed 242-entry skip and deselection allowlist.
- Strict approved-category, field, ownership, expiry and approval validation.
- New skip and stale allowlist rejection.
- Security-critical approval enforcement.
- Machine-readable merged governance report.
- Fail-closed stale-artifact removal and machine-readable error reports.
- Per-unit Python and dashboard branch coverage ratchets.
- Executed-test proof for the required safety cases.
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

Canonical checker result:

```text
652 passed, 44 skipped, 5 deselected in 10.11s
packages/domain: lines=95.1359% branches=87.3737%
packages/event_ledger: lines=95.2596% branches=90.1316%
services/job_worker/safety.py: lines=86.8613% branches=80.3571%
job state machine: lines=100.0000% branches=100.0000%
transition authority: lines=86.3636% branches=78.2609%
transition repositories: lines=58.7822% branches=28.8136%
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
| Managed entries | 242 |
| Root skips | 227 |
| Root deselections | 13 |
| Legacy skips | 2 |
| Dashboard skips | 0 |
| Unknown categories | 0 |
| Duplicate component/node pairs | 0 |
| Security-critical entries | 238 |
| Security-critical entries without approval metadata | 0 |

The supplied aggregate said 226 root skips. Two live full-suite observations found 227. The node-level live result is committed and the discrepancy is visible rather than normalized away.

## Security hygiene evidence

The first in-checkout report exposed a deliberate interaction with the repository secret scanner: synthetic security-test node IDs contain pattern names such as credential URI and private key. The scanner correctly rejected those untracked report files.

The implementation now writes runtime evidence under `/tmp/trading-agent-test-evidence`. Fresh targeted verification returned:

```text
1 passed in 0.97s
```

This preserves the scanner and changes report placement instead of weakening secret detection.

## Final verification

`make check-critical-coverage` exited zero. It executed 652 Python tests and 52 focused dashboard tests, validated every required case and held every exact line and branch ratchet.

`make check-test-skips` collected all three components and validated the exact 242-entry allowlist with no new, stale, duplicate, unknown, expired or reason-drift entry. Its merged report recorded 2,530 passing tests, zero failures, 229 skips, 13 deselections and 238 approval-blocked observations. The command exited zero.

Portable source-authority integration now separates strict and portable verification. Strict import, proposal and standalone verification still require every external Git object. Portable audit is selected only when every declared external repository is absent. Partial availability, modified component bytes, manifest identity drift, aggregate drift and coordinated authority-plus-manifest tampering all fail closed. The complete consolidation suite passed `168` tests.

`make ci` exited zero. It passed the canonical audit, closure and contract checks, secret hygiene, root tests, legacy tests, dashboard tests, exact skip governance, critical coverage, dashboard production build, Bandit, Python dependency audits and npm dependency audits.

The independent adversarial review did not execute. The delegated reviewer returned `HTTP 404: No active credentials for provider: openai`, and standalone `codex doctor` confirmed that no Codex credentials are available. The operator explicitly waived this review gate and accepted the machine-green state on 2026-07-24. This waiver is recorded as an acceptance decision, not as a successful review.

## Decision rule

Use `GO -- TEST EVIDENCE IS MANAGED` only when:

- the allowlist comparison passes with no unknown or stale entry;
- required critical cases are observed as executed;
- every coverage ratio is at or above its exact committed ratchet;
- machine reports are generated;
- `make ci` exits zero;
- the high-risk provenance change receives an independent adversarial review with no unresolved finding, unless the operator explicitly waives that gate;
- no unrelated safety or security assertion was weakened.

Current decision: `GO -- TEST EVIDENCE IS MANAGED; INDEPENDENT REVIEW WAIVED BY OPERATOR`.
