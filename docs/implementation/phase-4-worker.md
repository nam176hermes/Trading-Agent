# Phase 4 Single Durable Worker

## Implemented execution path

`services/job_worker` composes one worker that claims one eligible job at a
time, closes the claim transaction, performs paper-safety preflight, consumes
an allowlisted command capability immediately before `Popen`, and runs with
`shell=False` in a new session. The worker, not the API or dashboard, is the
only Phase 4 component allowed to start a research child.

Every child receives a newly constructed allowlisted environment. It forces
`TRADING_MODE=paper`, `LIVE_EXECUTION_ENABLED=false` and
`LIVE_TRADING_APPROVED=false`; dashboard, Job API and trading credentials are
not forwarded. Safety is checked again at the spawn boundary and during the
heartbeat loop. Cancellation, timeout or unsafe runtime drift terminates the
owned session conservatively and cannot be recorded as success without proven
cleanup.

Output streams are drained without blocking, capped at 1 MiB per stream and
written as protected artifacts. Exit code zero is insufficient: the fixed
validator must find a fresh, schema-valid, exactly attributed result before
the worker can finalize `SUCCEEDED`.

At process startup, expired-lease recovery runs before any claim. An exception
stops the service. A live, mismatched or unverifiable old child is blocked;
only positively absent work may enter the fixed retry policy.

## Runtime status and blockers

The worker has not been installed or started and has claimed no job. It remains
deliberately stopped because all of the following deployment authorities are
unresolved:

- the root-owned immutable backend release and external manifest do not exist;
- the code-approved manifest digest is intentionally `None`, so command
  attestation fails closed;
- the reviewed backend pin is `51de1cf06b3d595a336e19390230d0c09b608585`,
  but its external semantic-input manifest/digest are not provisioned;
- the canonical `.mode` and dynamically created/removed `.kill_switch` must be
  visible on every heartbeat without exposing credentials, exchange code or
  the rest of the active legacy tree.

Copying safety sentinels into a private input directory is rejected because a
copy can become stale. Binding the whole active legacy root and masking a
finite list is also rejected: unmasked and future source remains visible, and
`NoExecPaths` does not prevent Python from reading/importing source. Until a
reviewed dynamic safety-evidence boundary exists, worker activation is
**NO-GO**.

References: [allowlist](phase-4-command-allowlist.md),
[lease recovery](phase-4-lease-recovery.md),
[artifact ADR](../adr/ADR-phase-4-job-result-artifacts.md), and
[hardening evidence](phase-4-task-9-hardening-evidence.md).
