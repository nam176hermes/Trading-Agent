# Job Plane Recovery Gate Residual Risks

**Evidence date:** 2026-07-16
**Scope:** risks remaining after source-level containment and runbook review

## Ranked risks

| Rank | Severity | Residual risk | Failure scenario | Required closure |
|---:|---|---|---|---|
| 1 | P0 | A natural-language task request could be mistaken for the runbook's exact recovery approval. | The original cluster is started, stopped, copied, or migrated without the distinct reviewer, bound identities, destinations, digests, and time window required by the reviewed procedure. | Accept only the exact authenticated dual-reviewed recovery transcript. Until then, permit read-only diagnosis only. |
| 2 | P1 | The frozen recovery runbook requires a clean hardcoded canonical repository, while that original worktree contains preserved dirty changes. | An operator cleans or overwrites user work to satisfy the gate, or executes different source than the approved commit/tree. | Classify all paths, preserve ownership, then revise and independently review the clean-source binding or approve a non-destructive method to make the exact canonical path clean. |
| 3 | P1 | A clean staging candidate does not satisfy the recovery runbook's hardcoded source path. | A valid candidate commit is cited as recovery authority even though the runbook actually verifies another dirty checkout. | Bind the exact execution source path in a newly reviewed runbook and approval transcript. Do not infer equivalence from matching patches or manifests. |
| 4 | P1 | Runtime database identity, head, integrity, and current counts remain unverified until an authorized recovery run. | A later migration is prepared against a damaged, unexpected, recovering, or non-`0004` database. | Complete the approved recovery gate and independently accept its PostgreSQL 16 identity, exact `0004`, count, integrity, ACL, backup, and isolated-restore evidence. |
| 5 | P1 | Recovery hands off with PostgreSQL stopped, whereas the runtime role-split procedure requires it already healthy. | The cluster is restarted ad hoc between reviewed procedures, breaking source/process/endpoint provenance. | Create a separate exact PostgreSQL start/handoff procedure and approval, or revise the reviewed procedures to cover the boundary explicitly. |
| 6 | P1 | Runtime Release Authority v2 needs root-owned installed paths; a staging candidate alone is insufficient. | Runtime migration or units trust a user-owned staging tree, mutable interpreter, or verifier not bound by production authority. | Obtain separate approval for the exact root-owned `/opt`, `/etc`, and `/usr/libexec` installation and verify every ancestor, interpreter, manifest, and digest before runtime `0005`. |
| 7 | P1 | The final source gate is not clean or wholly green. | A release is cut from 27 modified/5 untracked paths, or the three canonical contract failures are hidden by reusing an undeclared dependency from the legacy dashboard. | Approve and declare the exact canonical AJV dev dependency, rerun the full gate, review the diff, then create a new clean commit. Otherwise keep the source frozen and uncommitted. |
| 8 | P1 | Release Authority v2 cannot currently produce the requested hermetic minimal artifact. | A mutable operator-owned Python base, ordinary venv, broad repository archive, or missing manifest/promotion value is represented as immutable authority. | Add a reviewed digest-pinned relocatable CPython 3.11 input, deterministic minimal selection, pinned build tooling, protected promotion lifecycle, successful real build, and tamper test before installation. |
| 9 | P2 | Source `0006` closes direct runtime-role transition/event splitting, but recovery observation and result sealing retain application-level trust. | A compromised worker supplies a false non-null process observation, or a future repository change splits artifact/result finalization. | Preserve fixed capabilities and direct-DML denials; separate recovery authority and move/attest result sealing more deeply at the database boundary in a later reviewed hardening phase. |
| 10 | P2 | The recovery procedure may stop application consumers beyond PostgreSQL. | An active dashboard, control plane, safety exporter, or semantic refresh unit is stopped without its exact approved maintenance scope. | The recovery transcript must explicitly contain `ALLOW_STOP_ALL_LISTED_UNITS=YES`; verify every listed unit and obtain operator/reviewer acceptance before any stop. |
| 11 | P2 | Recovery sub-gate success would still not close backup/DR maturity. | The recovered cluster has no proven PITR/WAL archive, off-host retention, missed-backup alert, checksums, or current restore cadence. | Retain recovery as a sub-gate only; complete the separately reviewed backup/PITR, retention, alerting, and restore-drill plan before production-readiness claims. |

## Next exact authority required

The next original-cluster mutation requires one access-controlled recovery
record containing the exact field set specified by Section 4 of
`postgresql-preserve-recover.md`. At minimum, it must provide:

- `DECISION=APPROVED_POSTGRESQL16_RECOVERY_SUBGATE`;
- distinct authenticated operator and reviewer identities and attestations;
- an approval window no longer than four hours;
- exact change/incident IDs and the protected change-control artifact digest;
- the newly reviewed runbook digest and exact clean source commit/tree;
- exact `0004` migration and independently reviewed clean catalog digests;
- approved original and isolated PostgreSQL system identities and path metadata;
- approved private, independent evidence/preservation/backup destinations;
- the exact isolated endpoint and protected admin input identities;
- explicit approval for all listed unit stops, one original start, one original
  stop, cold preservation, immediate backup, isolated restore, and every stated
  risk acceptance.

The transcript must be generated only after resolving the canonical clean-path
cycle. If the runbook changes, the approval must bind the new digest. A staging
candidate commit, even when clean and fully tested, is evidence input only and
does not replace this authority.

Separate later authorities are still required for:

1. a controlled PostgreSQL healthy-start/handoff after recovery;
2. root-owned Release Authority v2 installation;
3. the exact `0005` role split and runtime rolled-back permission probes;
4. any forward transition-authority migration such as `0006`; and
5. Job API/worker rollout.

No authority listed above permits starting the scheduler or timer, inserting a
job, running SNAPSHOT, or making a broker, exchange, or provider call.

## Current operational boundary

- Runtime database migration to `0005`: **not performed and not authorized**.
- Job API, worker, and scheduler: **must remain inactive**.
- Scheduler timer: **must remain disabled**.
- Job insertion and SNAPSHOT: **not authorized**.
- Original-cluster writes: **blocked pending exact recovery authority**.
- Dirty original worktree: **preserve; do not reset, clean, or bulk-stage**.

The current residual-risk decision is **NO-GO for database mutation and runtime
rollout**.
