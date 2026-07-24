# Phase 4 Task 9 hardening evidence

Date: 2026-07-12

## Scope and safety

This work changes only the durable research-job worker, its fenced repository
methods, and mocked/disposable tests. It does not start a production process,
touch an exchange, submit or cancel an order, change paper mode, or edit a
linked project.

## Fix wave 1

Commits `721d67f` and `9c495a7` closed the first review set: protected stream
artifacts, bounded result reads, stricter active-attempt mappings, immediate
spawn capability consumption, post-spawn identity checks, bounded reader
cleanup, and heartbeat renewal during descendant-held drain grace.

The follow-up review found that the thread-based implementation still reaped
the leader before descendant pipes were resolved. Once reaped, the original
PID/PGID could be reused, so a later cancellation or drain-time group signal
could not be safely attributed. It also found incomplete total scan bounds,
non-atomic result publication, and a cancellation gap between claim and spawn.

## Fix wave 2

The runner now uses nonblocking pipe descriptors and one selector/heartbeat
loop. Linux `waitid(P_PID, ..., WEXITED | WNOHANG | WNOWAIT)` observes leader
exit without reaping it. The zombie retains the exact PID/PGID allocation until
both pipes reach EOF or bounded group cleanup completes. A leader-exited group
whose descendants retain pipes receives TERM and then KILL against the anchored
PGID; output draining and cancellation observation continue throughout. The
leader is reaped only at the final bounded wait. Any unproved identity or
cleanup failure produces `PROCESS_GROUP_CLEANUP_UNPROVEN`, which the worker
maps to `BLOCKED`.

Cleanup is nested and best effort: signal, selector, descriptor, and wait
failures are recorded while remaining cleanup actions are still attempted.
Safety is checked before full prepare attestation and again at the immediate
consume/environment/Popen boundary.

The repository now provides a fenced `pre_spawn_control` operation. A cancel
observed after claim but before start finalizes the job and still-CLAIMED
attempt as `CANCELLED` without spawning or recording a child identity. Runtime
and drain-time cancellation continues to finalize the RUNNING attempt as
`CANCELLED` only after cleanup is proved. Every finalize boolean controls the
worker's final IDLE versus UNHEALTHY heartbeat.

Result discovery streams `scandir`, caps every examined directory entry,
retains no unbounded candidate list, counts only fresh attributable matches,
and calls a progress callback during directory and file work. Sealing uses a
verified worker-owned dirfd chain, an unpredictable `O_EXCL` temp file, a
complete short-write loop, file fsync, dirfd-relative atomic rename to the
content digest, directory fsync, full-loop verification of an existing digest,
and temp unlink plus descriptor closure on failures.

## Fix wave 3

The final review established that pipe EOF is not proof that a process group is
empty: a background member can close stdout/stderr and continue running. While
the leader remains an unreaped WNOWAIT zombie, the runner now uses an
injectable group inspector. Its Linux implementation scans `/proc` with
bounded stat reads and requires the exact session and process-group IDs while
ignoring zombies. Normal leader exit with a background member, cancellation,
timeout, and safety termination send TERM, recheck group membership through a
fixed grace period, and send mandatory KILL when a live member remains. The
loop returns a successful or `CANCELLED` outcome only after the group is proved
empty; persistent or uninspectable membership becomes
`PROCESS_GROUP_CLEANUP_UNPROVEN`. Pipe drain remains independently bounded.

If `start_attempt` loses its CLAIMED fence after Popen, the worker now performs
a fenced control reread. A concurrent `CANCEL_REQUESTED` terminates the locally
anchored group and finalizes the still-CLAIMED attempt as `CANCELLED`; every
other lost-fence result remains stale and unhealthy. Spawn ordering is now
exactly strict worker-ID validation, preflight, full prepare attestation,
final preflight, one consume, empty-start environment revalidation and
worker-owned lineage injection, then immediate Popen.

Original stdout and stderr handles are retained for final cleanup before any
`fileno`, nonblocking, selector, or stream-state setup. Result sealing closes a
new child dirfd on either fstat or fchmod failure and fsyncs the parent
immediately after each job/attempt mkdir, followed by the existing file and
leaf-directory fsync around atomic publication.

## Fix wave 4

The containment inspector now returns the complete live membership of the
leader's original session, not a boolean for the leader's initial process
group. Each snapshot includes PID and current PGID, so descendants that call
`setpgid` remain contained. Cleanup sends TERM to every observed PGID, rescans
during grace, sends KILL to every still-observed PGID, and handles newly
appearing groups within one fixed hard deadline. Final success or cancellation
requires at least two consecutive empty-session snapshots while the leader is
still retained with WNOWAIT. Membership churn, inspection failures, persistent
members, or signal failures produce cleanup-unproven blocking.

Calling `setsid` to escape the original session is prohibited by the Phase 4
command contract. If an escape or containment ambiguity is observable, cleanup
is unproven and finalization blocks; the worker does not claim containment
beyond the original spawned session.

Directory-chain creation now fsyncs the containing parent immediately after
every successful `mkdirat`, including first creation of the sealed root. An
injected first-use parent-fsync failure proves descriptors close and sealing
fails rather than publishing without durable directory metadata.

## Fix wave 5

Session containment remains the discovery boundary, but signals are now exact
member operations. `SessionMember` carries PID, PGID, session ID, and Linux
start ticks. For each live snapshot the runner opens a pidfd, rereads `/proc`
after acquisition, requires all identity evidence to match, and then sends TERM
and bounded-grace KILL through `pidfd_send_signal`. PID reuse, PGID reuse, an
unrelated session, or changed start ticks can never redirect a signal. Bare
process-group signaling has been removed.

One bounded cleanup helper owns repeated scans, pidfd lifetime, TERM/KILL,
stable-empty proof, and uncertainty tracking. The normal event loop, exception
handler, and final cleanup all use it. Forks discovered on later scans receive
their own pidfds; open, revalidation, signal, close, or inspection errors mark
cleanup unproven while remaining members are still attempted and the leader is
still reaped.

This Python build lacks the high-level pidfd wrappers, so the worker includes a
small in-process Linux syscall adapter for reviewed x86_64 and aarch64 syscall
numbers. It translates errno, enforces CLOEXEC, probes capability with a self
pidfd and signal 0, and fails closed for ENOSYS or unsupported platforms. Tests
inject disposable file descriptors and signal callbacks; no process or signal
delivery occurs.

The create-enabled result directory helper now fsyncs the containing parent
after both a successful mkdir and `FileExistsError`. A failed first-use fsync
therefore fails sealing, and a retry fsyncs the same parent again before
continuing.

## Fix wave 6

The leader pidfd is now acquired immediately after post-Popen
`ProcessIdentity` validation. `/proc` is reread after acquisition and PID,
start ticks, session ID, and PGID must all match before selector setup,
`start_attempt`, heartbeat, or any other database operation. The retained
leader handle is the initial member of the same bounded cleanup state used by
the normal loop, exception handler, and finalizer. Open or revalidation failure
prevents execution from proceeding; the final fail-closed behavior for those
boundaries is specified in Fix wave 7 below.

Session-scan failure no longer prevents signals to retained pidfds. A held
member omitted from a later snapshot is reinspected: the same live identity in
the anchored session is retained, marked uncertain, and still receives the
current TERM/KILL phase without advancing empty proof. None/zombie or changed
start identity permits removal; a different session is an observable escape,
is marked cleanup-unproven, and receives an exact pidfd KILL attempt. Thus
cleanup and leader reaping remain bounded even when discovery repeatedly
fails.

## Fix wave 7

Leader revalidation now has a dedicated `/proc/<pid>/stat` reader that accepts
the retained leader's zombie state while still requiring matching PID, start
ticks, session ID, and PGID. Ordinary session discovery continues to exclude
zombies. A deterministic `/proc/stat` fixture plus mocked
`waitid(..., WNOWAIT)` proves the same zombie is accepted as leader evidence
and excluded from the general session snapshot. Task 9 never launches a real
subprocess.

The runner reserves a close-on-exec descriptor before `Popen` and releases it
immediately before opening the leader pidfd, reducing post-spawn descriptor
exhaustion risk. If pidfd acquisition itself still fails, the worker never
signals by PID or through `Popen.send_signal`, observes reap only for a bounded
interval, and reports `PROCESS_GROUP_CLEANUP_UNPROVEN`. If a pidfd was opened
but revalidation fails, cleanup targets only that exact opened pidfd, closes
it, performs the same bounded reap observation, and still reports cleanup
unproven. Neither branch can falsely claim cleanup.

## Deterministic evidence

All process creation, signal delivery, and `waitid` observations are mocked.
PostgreSQL coverage uses the repository's disposable database fixture.
The focused suite covers descendant-held pipes, descendants that ignore TERM
and close pipes, normal background descendants, WNOWAIT anchoring, `/proc`
session/group membership, PGID signal ordering, persistent-member blocking,
pipe-setup and cleanup exceptions, both cancellation races, bounded scan
progress, seal short writes/reads, parent fsync order, injected fstat/fchmod
failures, crash cleanup, and descriptor leaks.

Wave 4 adds alternate-PGID descendants that close pipes and ignore TERM,
membership churn across scans, multi-group signal-error continuation,
consecutive empty-session proof, and sealed-root first-use fsync ordering and
failure cleanup.

Wave 5 adds post-pidfd identity changes, PID/session reuse without wrong
signals, exception-path alternate-member termination, pidfd open/send failures,
unsupported-platform fail-closed behavior, and mkdir/FileExists fsync retry.

Wave 6 adds pre-heartbeat retained-leader acquisition, repeated session-scan
failure with retained TERM/KILL, and live snapshot omission that cannot produce
false empty-session completion.

Wave 7 adds deterministic fast-exit zombie-anchor evidence, leader-only
zombie-aware revalidation, exact opened-pidfd cleanup on mismatch, and
pidfd-open failure with no PID fallback or false cleanup claim.

An isolated Linux WNOWAIT/pidfd capability smoke is deliberately deferred to
Task 12 operations work. It may run only after the explicit deployment gate,
must use a fixture/no-op child, and must be recorded in the Phase 4 runtime
evidence; it is not part of the all-mocked Task 9 suite.

## Backend attribution synchronization

The worker is pinned to reviewed crypto-research commit
`0de20a05c6c1d44a91227b4d032403e99afc099e`. Its versioned `/opt` release and
`/etc` manifest paths use that full revision. The manifest digest remains
explicitly `None`, so startup fails closed until Task 12 provisions the exact
release and records its reviewed digest. No release, manifest, service, timer,
or process was provisioned or started here.

Opaque attestation, built-command, and process-outcome values carry the backend
revision without the runner importing a mutable approval global. Immediately
before `Popen`, the child receives strict backend-format job/attempt IDs,
`TRADING_JOB_ATTEMPT_ID`, the built command's backend revision, and the fixed
legacy scratchpad root. Client/source attempts to provide any of those values,
including legacy `TRADING_ATTEMPT_ID`, fail before process creation.

Result validation uses fixture samples copied from the reviewed backend
contract. Reports require exact job, attempt, backend commit, and
`research_only: true` lineage. Replay requires the backend's exact six-key
sidecar and bounded sanitized event metadata. The sidecar omits a duplicate
research-only flag because the reviewed backend can write it only from its
attributed research-only replay path; any extra raw-content key is rejected.

Fresh verification:

```text
Prior wave-2 baseline: timeout 240s uv run pytest -q tests/jobs
381 passed, 1 warning in 31.86s
```

```text
Final wave 3: timeout 240s uv run pytest -q tests/jobs
392 passed, 1 warning in 31.89s
```

```text
Final wave 4: timeout 240s uv run pytest -q tests/jobs
396 passed, 1 warning in 32.10s
```

```text
Final wave 5: timeout 240s uv run pytest -q tests/jobs
401 passed, 1 warning in 35.70s
```

```text
Final wave 6: timeout 240s uv run pytest -q tests/jobs
404 passed, 1 warning in 37.39s
```

```text
Final wave 7: timeout 240s uv run pytest -q tests/jobs
408 passed, 1 warning in 37.15s
```

The warning is the pre-existing FastAPI TestClient/httpx deprecation warning.
No production runtime smoke is appropriate for this unit and repository
hardening task.

## Rollback

Revert the Task 9 hardening commit. Do not restart or deploy any Phase 4
service as part of rollback; preserve durable job rows and artifact evidence.
