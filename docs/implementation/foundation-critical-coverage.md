# Foundation critical coverage

Date: 2026-08-02
Policy: `tests/critical-coverage-policy.json`
Gate: `make check-critical-coverage`
Long-term goals: at least 95% line coverage and 90% branch coverage for every critical unit

## Policy

Critical coverage is evaluated per safety-sensitive unit. Whole-repository line coverage is not used as a substitute.

The policy stores exact covered and total counters as the no-regression floor. Ratio comparison uses integer cross multiplication, so rounding cannot hide a regression. The gate rejects denominator shrinkage, covered-count shrinkage and ratio regression. Critical source identities and required safety-case identities are sealed by the checker so a policy-only edit cannot remove a trust boundary.

Coverage.py 7.10.6 runs through an ephemeral `uv` overlay. The project dependency graph and lockfile remain unchanged. Dashboard coverage uses Node's native test coverage and LCOV reporters. The gate requires Node 22 or newer before collecting dashboard evidence.

## Ratchet baseline and current evidence

| Critical unit | Committed line floor | Current lines | Committed branch floor | Current branches | Next step |
|---|---:|---:|---:|---:|---|
| `packages/domain` | 665/699, 95.1359% | 95.1359% | 173/198, 87.3737% | 87.3737% | Branch 89%, then 90% |
| `packages/event_ledger` | 422/443, 95.2596% | 95.2596% | 137/152, 90.1316% | 90.1316% | Hold 95% and 90% |
| `services/job_worker/safety.py` | 119/137, 86.8613% | 86.8613% | 45/56, 80.3571% | 80.3571% | Lines 90%, 93%, 95%; branches 85%, 90% |
| Job state machine | 67/67, 100% | 100% | 16/16, 100% | 100% | Hold 100% |
| Transition authority | 209/242, 86.3636% | 86.3636% | 72/92, 78.2609% | 78.2609% | Lines 90%, 93%, 95%; branches 82%, 86%, 90% |
| Transition repositories | 365/427, 85.4801% | 85.4801% | 89/118, 75.4237% | 75.4237% | Lines 95%; branches 80%, 90% |
| Dashboard auth and mutation policy | 430/449, 95.7684% | 95.7684% | 141/159, 88.6792% | 88.6792% | Branch 90% |

Provider-free capability tests now cover scheduler enqueue authority validation and atomic heartbeat insertion, missing and invalid authority results, filtered read boundaries, empty worker claims, invalid lease authority responses, heartbeat validation and exact upserts, retry finalization metadata and artifact fencing, and the expired-lease process-observation matrix. The committed transition-repository ratchet was advanced to the exact fresh 365/427 line and 89/118 branch floor in the same change.

## Measured source scope

Python coverage includes:

- `packages/domain`
- `packages/event_ledger`
- `services/job_worker/safety.py`
- `packages/job_contracts/enums.py`
- `packages/job_contracts/transitions.py`
- `packages/job_authority`
- `services/job_store/repository.py`
- `services/job_store/worker_repository.py`

Dashboard coverage includes:

- `src/lib/trading/access-policy.ts`
- `src/lib/trading/auth.ts`
- `src/lib/trading/request-body.ts`
- `src/lib/trading/session.ts`
- `src/proxy.ts`

## Required safety cases

The gate requires successful execution observations for each mapped test. A typo, failure, not-run result, deselection or runtime skip fails required-case validation. Parameterized requirements use exact sealed variant node IDs; a passing base node or one passing variant cannot hide a missing or failed sibling. Each sealed Dashboard test file must emit at least one file-qualified TAP observation.

| Required behavior | Executed evidence |
|---|---|
| Paper mode | `test_only_complete_explicit_paper_evidence_passes` |
| Unknown mode and both live gates | `test_every_missing_or_noncanonical_safety_evidence_blocks` |
| Kill switch active and unknown | worker safety matrix and canonical kill-switch parser tests |
| Invalid manifest | placeholder hash and wrong catalog digest rejection tests |
| Stale safety evidence | stale, mismatched and non-safe snapshot matrix |
| Unsafe child environment | path/root override and exact child environment tests |
| Cancellation during heartbeat lifecycle | cancel race after process spawn and before start finalization |
| Lease and fence mismatch | pure repository capability tests that return fail-closed on stale or invalid lease and recovery authority evidence |
| Transition and event atomic failure | cancel event failure rollback test |
| Dashboard auth timeout | browser authentication fail-closed test |
| Dashboard origin failure | absent and cross-origin mutation rejection test |
| Dashboard body-size failure | bounded reader declared, streamed and invalid UTF-8 rejection test |

The disposable PostgreSQL lease integration tests remain in the managed skip inventory. The canonical critical suite uses host-independent repository capability tests for transaction rollback, validation-before-access, stale fences, invalid authority responses and lease recovery observations without pretending the PostgreSQL integration ran.

## Reports

Default evidence root:

```text
/tmp/trading-agent-test-evidence/critical-coverage/
```

Machine-readable outputs:

- `critical-coverage.json`
- `critical-coverage-error.json` when policy evaluation fails
- `critical-coverage-python.json`
- `critical-coverage-python-tests.json`
- `critical-coverage-dashboard.lcov`
- Python, dashboard and coverage-generation logs

Each run removes stale policy, test-observation, LCOV and merged-report artifacts before measurement. CI cannot upload an older successful report after a new gate fails early. Evidence directories must be real current-user-owned private directories; descriptor-relative temporary creation, replacement, cleanup and fsync bind report writes to the validated directory even during a same-user parent replacement attempt.

Run locally:

```bash
make check-critical-coverage
```

Use a private absolute destination when retaining evidence:

```bash
make check-critical-coverage TEST_EVIDENCE_DIR=/absolute/private/path
```

## Raising the ratchet

1. Run the canonical coverage target on the reviewed tree.
2. Confirm every required case has status `pass` in `critical-coverage.json`.
3. Update exact covered and total counters only upward by ratio.
4. Advance the next explicit step toward 95% lines and 90% branches.
5. Add tests for uncovered safety branches rather than excluding files or lowering assertions.
6. Run `make ci` before merge.
