# P3 GITHUB official lane

This runbook covers the hosted (`p3-authority.yml`) lane. The
[LOCAL official runbook](local-official-runbook.md) still covers the LOCAL lane. Both
lanes use the same worker, session, transport and validators. They differ in three
things: who issues authority, how the run is authenticated, and where protected files
live.

The following are separate outcomes:
- a qualified source;
- a successful job;
- a committed `PASS`;
- protected promotion.

`NONE_QUALIFIED` and economic `FAIL` keep P3 HELD. Nothing here grants broker, live,
network or production authority.

## Trust model

The GitHub job itself holds no authority. It has no secrets, credentials, sudo or
`uv run`. It only waits for the outbox and uploads public-safe results.

A task-owned root **host controller** (`scripts/p3_github_controller.py`, run from the
staged release interpreter) does the following:

1. **Before a run starts.** It observes queued `p3-authority.yml` runs through the GitHub
   REST API. It checks the workflow path, `workflow_dispatch`, `main`, the head SHA,
   the actor and the `run-name` operation. Only then does it register a just-in-time
   runner whose labels include `p3-run-<run>-<attempt>`.
2. **Once the run is in progress.** It authenticates the run (`authenticate_run`): the
   run is executing on that runner, the runner took no other job, and `main` is
   protected. It then publishes root-owned `0444`
   `/run/trading-agent-p3/<run>-<attempt>/github-run.json`. This file pins the digest
   of the reviewed research-input receipt.
3. **Refresh and revocation.** It refreshes the attestation while the job runs and
   deletes it on completion or cancellation.

Worker-side consumers read only `github-run.json`; they never read `GITHUB_*`.

- **Admission points** (accept, enqueue, worker startup, next-stage install, custodian
  release) require an observation younger than 120 s.
- **In-stage fences** require only that the attestation is present and unchanged.
- **Recorded-API rehearsals** (`mode: FAKE`) are admitted only with a `REHEARSAL`
  research-input receipt. They can never export a v1 phase receipt.

## Before dispatch

- **Main frozen.** `main` equals the frozen candidate `M2`. `PRE_P3_READY` and
  `HWC_SOURCE_READY` must be PASS in tracked status. No merge of any kind to `main` is
  allowed from the first P3 dispatch through the PHASE_EXIT export.
- **Reviewed research inputs.** The reviewed research-input receipt and its review are
  installed root-protected:

  ```text
  /etc/trading-agent-p3/github/research-inputs.json
  /etc/trading-agent-p3/github/research-inputs-review.json
  ```

  The review must cover the InputSet digest and the receipt binding digest, and the
  operator and reviewer must differ. An OFFICIAL receipt is never replaced after the
  first official enqueue.
- **Reviewed operations.** Each operation intent and its independent review are prepared
  and reviewed before the run. The controller seals the `RunAuthorization` only after
  authenticating the run. It publishes the request at the run-derived path:

  ```text
  /run/trading-agent-p3/<run>-<attempt>/operations/<nonce>.json
  ```
- **Fork pull requests.** Approval for fork pull-request workflows is set to all
  external contributors. No workflow triggered by a pull request or a comment may
  target a self-hosted label; a governance test checks this.
- **Runner network isolation.** The runner UID is rejected for every local destination
  in both IPv4 and IPv6 (`-m owner --uid-owner <runner> -m addrtype --dst-type LOCAL
  -j REJECT`). A refused connection attempt is recorded before the runner starts.
- **Paths.** The runner, release, CAS, PG and outbox live under `/srv`, so no private
  home path reaches a receipt.

## Per operation

1. Dispatch:

   ```bash
   gh workflow run p3-authority.yml --ref main -f operation=<operation>
   ```

   Dispatch one at a time; `concurrency: p3-authority` keeps one run.
2. The controller authenticates the run and admits the operation:
   - the request;
   - the staged manifest and review;
   - SQL acceptance with `scripts/p3_authority.py accept`, run as the authority UID;
   - enqueue, run as the worker UID.

   It then runs the existing transport. The worker uses `P3_ISSUER_KIND=GITHUB`, the
   `OfficialHostProfileV2` image profile, the staging-scope runtime approval at
   `runtime.json`, and the session catalog (0033).
3. The controller reads the canonical terminal job. It writes `status.json`, plus v2/v1
   phase files when they apply, to `/srv/trading-agent-p3/outbox/<run>-<attempt>/`.
   The job copies only those names and uploads them.

## HOLDOUT → PARITY → PHASE_EXIT

All three stages run in the HOLDOUT run and attempt, with one worker and one released
view. `session.json` binds the forked worker process. PARITY and PHASE_EXIT are
admitted only inside that protected session.

- **Session bound.** `HOSTED_SESSION_MINUTES` is 150. The HOLDOUT job timeout is 180
  minutes.
- **Mid-session reviews.** Reviews arrive within the reviewer contract's per-stage
  cutoff, or the controller stops before admission.
- **No replay.** A session failure never authorizes a second holdout.

After a committed PASS, the exporter runs:

```bash
scripts/export_p3_phase_exit.py --v1-output <file> --expected-run <run>-<attempt>
```

It reads the protected attestation and the OFFICIAL research-input receipt itself. The
`attest-phase-exit` job (GitHub-hosted) then attests exactly those bytes. Import them
unchanged through a receipt-only PR to `docs/implementation/p3/receipts/p3-phase-exit-v1.json`.
Foundation then records and attests the promotion.

## Accepted residual risks

- **Token.** The controller uses a classic `repo`-scoped token that only root can read.
  A fine-grained single-repository token needs interactive Owner creation.
- **Action pins.** The repository pins actions by tag (`@v4`), including on the
  self-hosted job. The job holds no secrets and only reads the outbox.
