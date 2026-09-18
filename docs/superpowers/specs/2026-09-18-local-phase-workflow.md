# Local development and publication by phase

Status: operator-approved direction, 2026-09-18. Process changes apply now.
The implementation plan now records the local source adapters and their tests.
Protected host activation and authenticated qualification remain separate gates;
this document does not activate them. Existing machine-derived gates remain truthful.

## Current implementation baseline

The 2026-09-18 reconciliation uses candidate
`977ff03dc163e2ef471c095f7e69286ba0a2050e`, tree
`cb4fa39da4ae1e60f8208876d3c1a92bdf5d3335`, in
`/home/thenam176/projects/trading-agent-m4-recheck-20260918`.
Workflow documents were copied from the dirty `df5f4a3` local-phase checkout;
that checkout and canonical user changes remain intact. PR #69 does not need
to merge first. The [plan](../plans/2026-09-18-local-phase-workflow.md) contains
the transfer receipt and the [requirement matrix](../plans/2026-09-18-local-phase-acceptance.md)
separates implementation, test, environment and certification gaps.

This turn changes documents only. Before qualification, implement the full
local provenance path including `packages/p3_provenance.py`,
`packages/p3_status.py` and committed phase-exit readback/receipt production.
Local completion must remain separate from hosted promotion and economic success.

## Goal and scope

Complete development, review and qualification locally for one phase, publish
one reviewable batch, incorporate external review, then plan the next phase.
GitHub is the publication and independent verification surface. Routine source
edits must not require pushes or hosted qualification runs.

The operator approved this direction in the conversation with
“Đồng ý thiết kế này”. This approves scoped source work on the local lane;
it is not approval to push, install services, modify protected host files,
migrate a running database, activate paper execution or enable live trading.

## Phase cycle

1. Freeze the phase scope, dependencies and acceptance criteria.
2. Develop locally in small reviewable changes. Use local commits as recovery
   points when authorized; commit frequency is independent of push frequency.
3. Run affected checks during development and integration checks at meaningful
   milestones. Investigate failures before building dependent work.
4. Freeze the candidate and run the full applicable local qualification gates.
   Preserve command, exit status, source identity, inputs, environment and
   evidence. A changed candidate needs an explicit impact assessment.
5. Prepare the phase review package and publish one batch after separate push
   authorization. Obtain hosted verification and external review.
6. Resolve substantive review findings, publishing a correction batch if needed.
   Only then finalize the next phase plan against the reviewed baseline.

The goal is one main publication per phase, not a prohibition on correcting
published defects. Do not compress an entire phase into one unreviewable local
commit. Keep recovery copies outside the development machine; a push is not the
only possible backup mechanism, and a local commit alone is not an off-machine
backup.

## Independent verdicts

| Verdict | Required evidence |
| --- | --- |
| Local source complete | Accepted scope implemented, affected checks and review complete |
| Local qualified | Exact frozen candidate passed the authenticated local qualification lane |
| Phase objective met | Product/research acceptance criteria actually satisfied |
| Published | Exact candidate made available remotely |
| Externally reviewed | Findings resolved against that candidate and applicable hosted checks complete |

These are independent observations, not interchangeable names for PASS.
Historical P0/P1 milestones retain their original evidence and scope. Changes
to their dependencies require current regression/qualification evidence;
historical completion does not qualify a different candidate.

For P3, running the pipeline successfully does not prove a viable alpha.
Keep the fixed research/holdout windows, full family disclosure, independent
review, one-primary selection, single holdout disclosure, costs, native parity
and phase-exit policy. If no alpha meets the economic criteria, report that
outcome and retain the North Star restrictions on P4/P5/P7 expansion.

## Local authority design

Reuse existing proof producers, validators, Job API, worker, PostgreSQL fences,
sandbox, CAS, custodian and canonical result readback. Do not build a second
execution engine, replace PostgreSQL with an in-memory store, or add a generic
workflow framework.

Introduce a versioned run identity with an explicit issuer kind: LOCAL or
GITHUB. Local identity includes a unique run and attempt, exact source/closure,
host and boot identity, issuer, validity interval and approved operation/input
bindings. Qualification additionally binds the policy, environment and retained
evidence. Local records must be authenticated by the existing protected host
authority boundary; a checksum, environment variable or researcher-written
JSON file is not sufficient authentication.

Use the existing protected-file reader and independent operator/reviewer
boundary. The source checkout cannot mint its own authority. Bind the local
issuer to the host and source before execution, reread current authority at
admission and lease boundaries, and reject revocation, expiry, source drift,
wrong-host reuse and stale attempts. Host provisioning remains a separate
operation after source implementation and review.

Version the affected receipt/profile/session contracts without rewriting old
receipts. Preserve existing GitHub provenance and validation as a separate lane.
Never manufacture GITHUB_RUN_ID, relabel local evidence as hosted evidence, or
disable protected-main validation in the GitHub adapter.

Local source readiness must be derived from validated local HWC, P1-H/LTS, P2
and Pre-P3 evidence without requiring a protected-main promotion. Published
promotion remains independently derived. Every official P3 admission surface,
including the custodian, must use the same lane-aware source decision.

Run identity must remain unambiguous through request, profile, session, claim,
heartbeat, recovery, terminal result and readback. GitHub and local runs with
otherwise identical identifiers must never share authority. Do not fit a UUID
into the existing numeric workflow fields or infer issuer kind from its size.
Use a forward migration if persistent identity must change; preserve prior
disclosure records and deny a second holdout through another lane.

Both live approvals and broker/live/production authority remain false. Local
qualification is not release activation or permission to trade.

## Validation cost

Use `uv run python scripts/dev.py test <affected-path>` during development.
Run `uv run python scripts/dev.py static` for affected Python changes and the
owning contract checks when contracts change. At candidate freeze run the
required full gates once for unchanged inputs; do not repeat prerequisites
already covered by `make test-all` without a distinct evidence need.

Prepare the dashboard build before tests that require `.next/BUILD_ID`:

```bash
make build-dashboard
make test-all
```

This is the local source gate, not a substitute for security review, native
proofs, disposable PostgreSQL qualification or the real research campaign.
Keep hosted `make ci-portable NONINTERACTIVE=1` in genuine hosted context at
publication. Never call it with invented GitHub metadata locally.

Evidence reuse requires unchanged source/dependency/policy/input/environment
bindings and still-valid authority. Preserve failed runs even after a repair;
record the new result separately. An aggregate failure is not renamed PASS
because narrower follow-up checks passed.

## Transition and acceptance

The process policy is effective immediately. Official local qualification is
available only after all producer/consumer/storage changes and adversarial
tests pass. Until then, local development continues, while existing official
gates may accurately remain HELD. A document change cannot issue a receipt.

Acceptance of the local lane requires an actual frozen local candidate through
HWC/P1-H/P2/Pre-P3 and the P3 Job API/worker path, without GitHub environment
variables, with retained authenticated evidence. Negative tests must cover
forged/unprotected evidence, wrong source/policy/host, expired/revoked identity,
replay, mixed issuers, interrupted recovery and a second holdout attempt.
The existing hosted lane must retain its rejection behavior.

Implementation sequence and exact owners are in the
[implementation plan](../plans/2026-09-18-local-phase-workflow.md).
