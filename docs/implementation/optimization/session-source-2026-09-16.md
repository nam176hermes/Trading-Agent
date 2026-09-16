# M8/M9 bounded session source

Continuation parent: `c86eae84346c4f0cea18ab6bb09f6a5c5e590d89`.
This document supersedes the **source wiring** gaps in the earlier continuation
notes. Protected-host qualification, real holdout execution and production
activation remain separate; local synthetic evidence grants none of them.

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

Session worker and custodian admission require the measured **0029** catalog:
`bb704c68e3d2e60c01142ace4346f26ca4fc9b3b8b42cddb323f6aa45563225f`.
The existing custodian profile still requires 0027; the explicit
`p3-custodian-session-profile-v1` selects 0029. Neither runtime learns its own
acceptable catalog nor upgrades/migrates a protected database.

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
