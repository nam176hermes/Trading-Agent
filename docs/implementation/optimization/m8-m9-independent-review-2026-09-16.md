# M8/M9 independent source review and repair

Review target: `86197e50a9c76c6d6fbe889255caae9745d1354c`.
Tree: `4fc9f17b83193e519345c97b0d151fc6499bf5ec`.
Two fresh-context reviewers separately traced SQL/lifecycle and
native/session/data custody. Neither changed the reviewed checkout.

## Confirmed findings

| Finding | Failure | Repair and regression |
| --- | --- | --- |
| P1 executable entry | Workflow used `python -m services.job_worker` without `__main__.py` | Minimal forwarding entry; execute the package with a stubbed main |
| P1 sealed inventory | HOLDOUT and calculation child imports were absent from the admitted release | Include HOLDOUT module, remove operator-service dependency; import and calculate from the exact projected file list |
| P1 anonymous view | Bubblewrap `--ro-bind-data` creates link count zero; operator-state reader requires one | Dedicated bounded snapshot reader, actual anonymous read-only mount check, unsafe metadata rejection; shared protected-state reader unchanged |
| P1 terminal expiry | PARITY finalize and PHASE_EXIT publication could finish after lease/authority expiry during writes | Forward migration 0030 adds final transactional checks; delayed terminal-write rollback tests |
| P1 session enqueue profile (root follow-up) | Existing Job API rejects session schema 0029/0030, blocking later stage enqueue | Explicit session profile with fresh role/revision/catalog checks; HTTP and real SQL admission/drift regressions; old profiles preserved |
| P1 catalog coverage | Session catalog omitted publication heads and append/retention dependencies | Expanded session-only catalog and separately measured pin; table ACL drift tests; old custodian pin preserved |

Hermes follow-up source review reproduced a paper artifact import regression:
new session imports ran before profile selection. Session dependencies now load
only in the explicit session branch. A subprocess imports the exact paper mapping
and rejects fallback to full-checkout modules; the paper inventory is unchanged.

Repair review also reproduced an access-time false rejection in the new reader.
The comparison now covers file identity, permissions, size, nanosecond modification
and change times, excluding access time. A named snapshot with old access time
is a regression case.

## Evidence scope

Exact candidate identity, command logs, independent re-review verdicts and closure
status are recorded in the external campaign evidence ledger. Source closure
requires those checks to pass; this document alone is not an approval receipt.

The Bubblewrap probe uses synthetic bytes and transient namespaces. Calculation
tests use fixture inputs and projected source; native process tests retain their
explicit synthetic-admission limits. Disposable PostgreSQL checks use synthetic
authorizations and real database capabilities, then verify cluster cleanup.
None establishes protected-host qualification, an official HOLDOUT result,
production activation or live execution authority.
