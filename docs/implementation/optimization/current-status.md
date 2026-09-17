# Optimization status — current integration ledger

Observed base: main `0ba0229ab972642360530eb7483a060b28898be2` (2026-09-17).
This ledger supersedes earlier checkpoint descriptions of missing M7 data,
unwired M8/M9 source and absent HWC qualification. Earlier files remain history.

| Work | Verified state | Remaining acceptance |
| --- | --- | --- |
| Worktree cleanup | 79 old checkouts archived, restore-verified and removed; historical free-space delta 41.45 GB | Retention-aware inventory of later temporary checkouts; no blanket cleanup |
| Static/bootstrap/dead code | Integrated via PR61; 22 dashboard/debug files and seven private helpers removed | Maintain behavioral and static gates |
| Baseline | Base core 5,352; candidate 5,345; legacy 13,241 unchanged | Encoder refactor removes 23 production lines; 182 focused tests pass; broad candidate gates pending |
| M7 | PR64 merged; retained 2,800-day exact-main inventory/readback PASS | Preserve source/policy/parser binding; no redownload of unchanged data |
| M8/M9 source | Entrypoint/session/lifecycle/native proof/publication/recovery integrated and reviewed | Protected-host qualification, distinct-UID custody and actual sandbox/native campaign |
| HWC | PR65 merged; genuine signed receipt validated after actual main commit | Any new source/closure change requires fresh qualification; no rebinding |
| M4 | [Source amendment](m4-source-root-amendment.md) approved; relocated Phase4 producer/consumer source candidate implemented; V2 worker paths already attested | Final candidate source/hosted gates, review and separately verified deployment/rollback |
| Pre-P3 | HWC prerequisite now PASS | P1 bridge/LTS, P2 source/runtime/final, P3 foundation, candidate and promotion receipts |
| M12 | Fresh local read-only preflight collected externally | Target identities/authority, rehearsal, protected fault matrix and final receipts |
| Official campaign | NOT RUN by this continuation | Validated source/data/qualification plus genuine per-stage approvals |
| Codex Security | Deferred by operator, NOT REVIEWED | Do not label tool unavailability or skipped review as approval |

The post-merge Foundation run `35272579772` failed its production dependency
audit on legacy `soupsieve 2.8.4` (GHSA-j934-xhv5-fg8f and
GHSA-gjv8-xp57-g29c); its attestation jobs were skipped. This is distinct from
the previously verified signed HWC receipt and is not a green main CI verdict.
The candidate updates only that existing transitive package to `2.9` using
`uv lock --upgrade-package soupsieve==2.9.0`; all three production dependency
audits now pass. No audit exception or rule change was added.

The candidate merges two duplicate container encoders in the same module while
preserving each caller's inventory, exception type/message and byte-size limit.
Five pruned diagnostics belong to that module; the existing pruning tool also
found two obsolete entries in process_runner.py, whose source is unchanged.
The total candidate baseline is 18,586, not zero and not a count of runtime bugs.
Initial broader tests failed six cases because native custody was unavailable;
the repository's `--native-custody` wrapper then passed all 182 module tests.

Main0ba0229 still has its valid HWC receipt. This source-changing candidate keeps
the original signed receipt in private evidence/history and removes its active
copy. Its generated HWC/Pre-P3 states remain HELD until fresh qualification.

## Fresh local preflight

Read-only SQL confirmed the local database at revision 0004, shared
`trading_jobs` role, no P3 tables and catalog mismatch with required session0030.
Transaction ended with ROLLBACK. No business rows were read.
Protected session directory, semantic manifest and safety snapshot are absent.
The new root Python environment has no native engine extension; that says nothing
about availability of the separately pinned native cache/release.

A 60-second probe recorded 5,924 samples, no backward wall-clock step, and maximum
wall-versus-monotonic delta 130,356 ns. This does not establish UTC accuracy under
load or qualification after WSL resume. NTP reports synchronized; the independent
timesync-status sample reports +159.722 ms offset. Observe both again on the target.

Private evidence: `/home/thenam176/projects/trading-agent-optimization-evidence/m4-m12-20260917`.
It contains sanitized host metadata and task identities, never credential values.
No DB migration, service/profile installation, official research or live action
was performed on the installed runtime. In the existing disposable SQL source
harness, 33 tests passed, including the migration chain through 0030, session
catalog/role fences, lifecycle checks and verified owned-cluster cleanup. This
is synthetic source evidence, not a protected-host receipt. A subsequent physical
backup/restore rehearsal matched the exact 0030 catalog and 49 tables / 514
synthetic rows; both servers were stopped and owned storage removed. Logical
restore is rejected because 12 CHECK definitions were reserialized; catalog
validation was not relaxed.
The 75 HWC/project/provenance regression tests and dashboard build also passed.
The operator selected isolated WSL rehearsal and approved the M4 amendment.
The actual independent reviewer and profile issuer identities remain required. The old f6c45d3 deployment proposal is historical.

GitHub currently reports zero repository self-hosted runners. The existing
Host Authority workflow needs a `trading-authority` runner and protected
environment. Use the [existing Pre-P3 runbook](../../operations/pre-p3-qualification.md)
after source freeze and HWC requalification; do not create local workflow IDs,
reviewer identities, or replacement PASS receipts. Review source-bound fixture
approval, protected runner installation and distinct runtime UIDs for the
selected host before dispatch. The retained 0004 runtime must not be used as
the disposable fixture or upgraded by this source test.

## Order of remaining work

1. Freeze the reviewed M4 contract; complete bounded source refactor with measured
   benefit and unchanged behavior. Tests precede baseline pruning.
2. Integrate source after focused and required broad gates. Record exact SHA/tree.
3. Retire any now-stale active HWC receipt without changing signed bytes; regenerate
   HELD status and qualify/import the actual new source through existing workflows.
4. Close Pre-P3 candidate/promotion via the official runbook. Required independent
   approvals and real workflow identity cannot be fabricated by the implementation.
5. Rehearse approved migration/restore and qualify the protected host with synthetic
   cancellation, crash, expiry, lost-response and raw-data-leak cases.
6. Run approved official stages once all gates pass; preserve genuine economic
   FAIL/REJECTED results. Finish with task-owned, reference-checked cleanup.
