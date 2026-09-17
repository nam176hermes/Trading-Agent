# M8/M9 bounded session source

Continuation parent: `c86eae84346c4f0cea18ab6bb09f6a5c5e590d89`.
This document supersedes the **source wiring** gaps in the earlier continuation
notes. Protected-host qualification, real holdout execution and production
activation remain separate; local synthetic evidence grants none of them.
See the [independent review and repair dossier](m8-m9-independent-review-2026-09-16.md).

## Entrypoint and ownership

`python -m services.job_worker`, with the existing `p3-official-v1` profile and
`p3-holdout-primary-v1` operation, performs the existing enqueue/host/closure/
startup checks and enters `p3_official.run_session`. Other early operations
keep their existing single-job route. Standalone official PARITY/PHASE_EXIT
entry remains rejected: they cannot reconstruct a lost released view.

One parent owns the same `P3HoldoutSession` and released view for:

1. Exact private HOLDOUT claim and independently protected attempt admission.
2. One custodian release attempt, real driver/three replicas, recomputation,
   result commit and exact job/attempt/worker/token/result-hash readback.
3. Independently approved PARITY job, six native processes (three per role),
   parent proof, both comparisons, pair retention, result commit and readback.
4. Independently approved PHASE_EXIT job, recomputation with that view,
   EXIT_DECISION publication, immutable SQL readback and receipt recovery.
5. View closure on completion or any failure. A restart cannot repeat HOLDOUT.

The coordinator does not create reviews, enqueue later jobs or write protected
profiles. The host authority must present each next accepted job in
`/run/trading-agent-p3/<run>-<attempt>/profile.json`, then bind the freshly
claimed attempt in `stage.json`. For PARITY it must also supply `native.json`
before admitting the stage. `session.json` remains immutable. Waiting is bounded
by its monotonic deadline, protected expiry and enclosing workflow deadline.
Missing authority stays held; neither a local test nor prior success admits it.

## SQL and failures

Migration 0029 preserves the exact pinned 0028 parent and creates a private
claim/start/control/finalize/recovery lane. Workflow identity, job/attempt,
current lease, cancellation and fresh approval remain fenced. Start additionally
requires committed disclosure. Start/control/successful finalize recheck expiry
after writes and roll back a transition that crosses the deadline. Recovery
never schedules another HOLDOUT attempt, even if max_attempts is corrupted.

Migration **0030** preserves both the original and expanded parent fingerprints
and adds final transactional lease/authority checks to ordinary PARITY finalize
and PHASE_EXIT publication. Expiry during terminal writes rolls back the complete
transition. Immutable publication replay remains available after a lost response.

Session Job API, worker and custodian admission require the measured **0030** catalog:
`506071c06cdd91fe7e9506a03c15df10949e41f97f80dbbdd27c305aef3a82d4`.
The session snapshot additionally covers publication heads/projection, append
idempotency, publication retention, job artifacts and worker heartbeats.
The existing custodian profile still requires 0027; the explicit
`p3-custodian-session-profile-v1` selects 0030. Neither runtime learns its own
acceptable catalog nor upgrades/migrates a protected database.

The explicit `TRADING_JOB_API_PROFILE=p3-session-v1` selects the same revision and
rechecks the restricted API role and full catalog on readiness and every mutation.
The original `p3-v1` and paper profiles retain their old pins. This supplies the
source enqueue/cancel route for later session stages; activating the profile and
migration on a protected host remains separately qualified operator work.

On lost commit response, worker results reconcile only the exact terminal
attempt/hash. Publication reuses the existing atomic SQL capability and
request/commit/output-inventory readback. Unknown outcome stops the session.
Lease loss, crash and cancellation close the view; terminal cleanup/recovery
cannot reopen the experiment. No source test provisions a custodian service.

## Retained evidence and data boundaries

Native parent evidence binds source, session/profile digests, job, attempt,
worker, hashed lease token, role-specific request commitment, actual PID/group/
start ticks, command/capability/closure fingerprints, actual sandbox policy,
result and receipt. The pair contains both existing ParityResult references
and this private proof. Public result/receipt schemas remain unchanged.

The HOLDOUT driver receives a sealed calculation view. Before research CAS
retention, the parent validates pinned output descriptors, recomputes exact
outputs and rejects extras (including copied raw bars). It reuses that view
for exit recomputation. Raw holdout bars and native requests are never written
to the research CAS by this chain. Test fixtures explicitly remove raw inputs
from the CAS and check their continued absence.

## Verification scope

Focused checks cover private SQL lifecycle/recovery and cross-write expiry,
explicit catalog/profile separation, real HOLDOUT calculation children with a
synthetic sandbox, six real native child processes emitting synthetic outputs,
parent proof/parity, coordinator ordering/lifetime, cancellation, crash, lease
loss and lost commit response. SQL tests use the existing disposable PostgreSQL
harness and verify teardown. No test is protected-host qualification.

Source completion also requires the repository's static, audit/contracts,
aggregate tests and dashboard build against the resulting candidate. Detailed
commands/results are retained in the local optimization evidence directory.
Security review and hosted qualification must be reported separately from
these local checks; an unavailable review is not a PASS.

The worker package has an executable `__main__` forwarding entry. The HOLDOUT
module is in the exact driver inventory, and both driver and evaluation child
use a bounded calculation-view reader that accepts the anonymous read-only
Bubblewrap mount. Operator-state file validation remains unchanged. Projected
source tests cover real calculations; `P3_SEALED_VIEW_SOURCE_TEST=1` explicitly
selects the transient Bubblewrap transport checks outside portable CI.
