# LOCAL independent-review repairs

Approved scope: repair L1, AUTH-01/L2 and L3 from the independent review of
8a6d152126eb21e5a26d416af9f2ce55268ac848 (tree5898a552fe01f00ce9ea1991f83e91777dc7692e).
The frozen candidate and all prior evidence remain intact. Work in an external
standalone checkout because qualification requires a real .git directory.

1. L3: recheck source prerequisites independently of phase readback; invalidate
   source and dependent phase projections if the final read fails or changes.
   Keep a valid source PASS when only phase evidence is unavailable.
2. AUTH-01/L2: add a forward migration for a narrow accepted-authority reader,
   preserving old migrations and table privileges. Reuse protected LOCAL request
   validation inside JobRepository's enqueue transaction before SQL writes and
   before return/commit. Roll back job/events/binding on revocation or mismatch.
   Explicitly reject obsolete LOCAL catalogs; preserve hosted0026/0030 behavior.
   These checkpoints do not make filesystem revocation atomic with SQL commit;
   workers and custody retain their own current-authority fences.
3. L1: support a complete sealed standalone source checkout at the staging
   application root, using its actual attested Python and frozen root dependencies.
   Reuse Package6 staging loader/attester; bind its static authority, approval,
   source/root/interpreter/UID to independent protected LOCAL authority. Do not
   broaden the paper projection or implement production Release Authority v2.
   Pin the runtime approval digest through shared safety preflight and all session
   stages; reject revocation/replacement using the existing blocked-worker path.
   Session runtime uses the workflow deadline while stage approval expiry remains
   independently checked.
4. Verify focused regressions first; actual disposable PostgreSQL migration,
   rollback, roles and cross-lane single disclosure; actual runtime loader/attester
   and source composition including tamper negatives; applicable full source
   gates and independent re-review. Update local runbook and host packet with
   exact tested commands and remaining installed-host approval requirements.

No root/service/installed database changes, protected issuance, official campaign,
holdout, remote mutation or live authority are included. No additional dependency.

| Finding | Exit evidence |
|---|---|
| L3 | Revoked/expired/changed prerequisite -> sourceHELD and no phasePASS, including missing phase; unrelated phase failure retains fresh sourcePASS. |
| AUTH-01/L2 | Accepted LOCAL then revoked before enqueue or during SQL insertion -> no committed job/events/binding, including direct API replay and dedup; exact valid LOCAL works; wrong principal/source/intent rejected. |
| L1 | Actual sealed runtime Python loads complete P3 source/Git and passes existing attester plus protected binding; changed source/runtime/path/UID/approval rejected without credentials/jobs; installed cross-UID qualification remains separate. |
