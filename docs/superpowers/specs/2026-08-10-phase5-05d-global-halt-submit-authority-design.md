# Phase 5 05D — Durable Global Halt and Submit Authority

**Status:** Approved design, pending written-spec review

**Date:** 2026-08-10

**Base:** `main` at `dfbd925beb167a0bb22fd059ff5b277c43fcb96e`

## 1. Purpose

Phase 5 05D completes WS-05 by adding the authority that 05C intentionally
deferred:

- one durable, replayable global halt state;
- deterministic daily-loss, drawdown, and canonical kill-switch transitions;
- explicit, fail-closed recovery;
- short-lived submit permits bound to the durable 05C approval and current
  halt generation;
- compare-and-append consumption that rejects evaluation-to-submit races.

The packet remains source-only and paper-only. It does not call an execution
client, broker, exchange, account, order, withdrawal, or private endpoint. It
does not activate a service, change runtime config, run a migration, mutate
PostgreSQL, or enable either live gate.

## 2. Scope Decision

05D implements **global halt only**. Strategy, instrument, venue, account, and
asset-class kill switches are deliberately deferred. Adding dormant hierarchy
now would enlarge public contracts without being needed for the WS-05 exit
gate, which requires that global halt fail closed.

The existing canonical `.kill_switch` remains operational safety evidence. 05D
does not write, move, replace, or reinterpret that file. Absence remains
`INACTIVE`; unsafe objects or unreadable/invalid content remain `UNKNOWN`.

## 3. Authority Boundaries

### 3.1 Trading-Agent remains authoritative for

- operator recovery authorization;
- durable event-ledger append and replay;
- canonical safety evidence;
- target-policy and runtime-risk policy identities;
- cross-service audit and later promotion decisions.

### 3.2 05D owns

- the deterministic global breaker transition function;
- the event-sourced global halt state;
- recovery validation and generation rotation;
- submit-permit preparation and one-shot consumption;
- exact re-attestation of the 05C approval, policy, observation, safety, and
  authority-stream head.

### 3.3 05D does not own

- order transport or an execution provider;
- cancel, reconciliation, reduce-only, or unknown-order-state behavior;
- safety rotation after permit consumption while an external send is in
  progress;
- hierarchical kill switches;
- reservations, database migrations, dashboard mutations, or Release
  Authority v2.

The Phase 6 execution boundary must consume a valid 05D authority immediately
before its transport action. A safety rotation after consumption but during an
external send is a Phase 6 failure drill; 05D does not claim atomicity with a
network.

## 4. Design Choice

The accepted design uses an **event-sourced halt stream plus a two-stage submit
permit**.

Rejected alternatives:

1. A mutable halt snapshot cannot prove the complete rotation and recovery
   history and risks becoming a second state authority.
2. Extending `.kill_switch` cannot represent deterministic P&L breakers,
   permit consumption, or durable recovery lineage without turning a safety
   sentinel into an application database.

## 5. Public Contracts

All contracts are strict, immutable Pydantic models, reject extra fields,
revalidate nested model instances, use UTC datetimes, and serialize through the
existing bounded canonical runtime-risk helpers. UUIDs and lowercase SHA-256
digests are identities; free-form messages are not authority.

### 5.1 `GlobalHaltStatus`

Closed enum:

- `ACTIVE`
- `HALTED`

There is no implicit active state. An empty authority stream must be explicitly
initialized by a durable transition.

### 5.2 `GlobalHaltReasonCode`

Closed, canonical order:

1. `SAFETY_AUTHORITY_UNKNOWN`
2. `KILL_SWITCH_ACTIVE`
3. `DAILY_LOSS_LIMIT`
4. `DRAWDOWN_LIMIT`
5. `RECOVERY_AUTHORIZED`
6. `INITIALIZED_SAFE`

The first four codes are breaker causes. `RECOVERY_AUTHORIZED` is valid only on
a `HALTED -> ACTIVE` transition. `INITIALIZED_SAFE` is valid only for the first
`ACTIVE` transition.

### 5.3 `GlobalSafetyObservation`

Fields:

- canonical safety-source fingerprint;
- `CanonicalKillSwitchState`;
- `observed_at`;
- schema version.

The observation is obtained through a small read-only adapter around the
existing `resolve_kill_switch` and `safety_source_fingerprint` functions. Tests
use private temporary sentinels. Production paths are not read by normal CI.

### 5.4 `GlobalHaltState`

Fields:

- stable authority `stream_id`;
- `generation`, starting at one and increasing by exactly one per transition;
- `status`;
- current transition event ID and canonical event digest;
- prior transition event ID/digest when generation is greater than one;
- exact runtime policy, runtime observation, portfolio, and safety digests;
- canonical reason tuple;
- `transitioned_at`;
- schema version.

The state is a replay result, never a separately writable snapshot authority.

### 5.5 `GlobalHaltTransition`

A typed event payload containing the inputs needed to derive the next state:

- transition ID;
- prior generation and prior transition digest;
- next generation and next status;
- reason tuple;
- runtime policy/observation/portfolio and safety bindings;
- optional recovery-authorization digest;
- decision time and schema version.

The payload does not contain its own envelope event ID or digest. Replay
combines the validated payload with the containing envelope ID/digest to build
`GlobalHaltState`, avoiding a circular self-digest.

Transition event envelopes use a dedicated authority stream and contiguous
sequence numbers. Their canonical event type, source, timeline, correlation,
causation, and expiry bindings are fixed and verified on write and read-back.

### 5.6 `GlobalHaltRecoveryAuthorization`

An external operator-authority input with:

- authorization ID and canonical digest;
- exact halted generation and transition digest;
- exact safe runtime policy, runtime observation, portfolio, and safety
  digests;
- issued and expiry timestamps;
- operator-authority digest;
- schema version.

It is not itself permission to execute. It can authorize only one exact
`HALTED -> ACTIVE` transition, and that transition durably retains its digest.
The recovery entrypoint also requires an injected
`GlobalHaltRecoveryAuthorityVerifier` to validate the external operator
approval and return this exact canonical authorization. Callers cannot recover
by merely constructing the model. Control-plane issuance of that operator
approval is outside 05D; the verifier boundary is mandatory and fail-closed.

### 5.7 `SubmitPermitPrepared` and `PreparedSubmitPermit`

`SubmitPermitPrepared` is the typed event payload. It contains:

- permit ID;
- exact durable 05C approval-reference digest and event ID;
- intent, target-policy decision, 05C runtime decision, policy, observation,
  portfolio, and safety digests;
- exact halt stream ID, generation, transition event ID, and transition
  digest;
- `prepared_at` and fixed five-second `expires_at`;
- schema version.

It does not contain its own envelope digest. `PreparedSubmitPermit` is the
returned content-addressed reference, built after canonical append and exact
read-back; it adds the prepared envelope event ID/digest to every payload
binding.

### 5.8 `SubmitPermitConsumed` and `ConsumedSubmitAuthority`

`SubmitPermitConsumed` is the typed event payload. It contains:

- permit ID and prepared event digest;
- exact halt stream ID, generation, and transition digest;
- consumed-at timestamp;
- schema version.

`ConsumedSubmitAuthority` is the returned content-addressed reference and adds
the consumed envelope event ID/digest after append and exact read-back. It is
returned once. Reuse, conflicting content, expiry, or any authority drift is
rejected.

## 6. Breaker Semantics

The breaker uses the same exact accounting semantics as 05C:

- daily loss breaches only when
  `daily_pnl < -max_daily_loss`;
- drawdown is `max(peak_equity - current_equity, 0)` and breaches only when it
  is greater than `max_drawdown`;
- all three money values must use the observation reporting-currency singleton;
- arithmetic is exact and independent of the ambient Decimal context;
- kill switch `ACTIVE` and `UNKNOWN` both fail closed;
- kill switch `INACTIVE` contributes no breaker reason.

Multiple simultaneous causes are retained once in canonical reason order.

### 6.1 Initialization

- Safe facts create generation one `ACTIVE` with `INITIALIZED_SAFE`.
- Any breaker cause creates generation one `HALTED` with the exact causes.
- Missing, stale, malformed, or inconsistent authority cannot initialize an
  active stream.

### 6.2 Active state

- Safe facts retain the same generation and append no transition.
- Any breaker cause appends `ACTIVE -> HALTED`, rotating generation by one.

### 6.3 Halted state

- Without valid recovery authority, the state remains halted and no active
  transition is possible, even when current values become safe.
- Repeated breaker observations are idempotent state observations, not new
  generations.

### 6.4 Recovery

Recovery is allowed only when all of the following are exact:

- current state is `HALTED`;
- the recovery authorization names the current generation and transition
  digest;
- it is unexpired and has not been used;
- kill switch is `INACTIVE`;
- current policy, observation, portfolio, and safety digests match the
  authorization;
- daily loss and drawdown no longer breach;
- append and exact read-back succeed.

Success appends `HALTED -> ACTIVE` with `RECOVERY_AUTHORIZED` and a new
generation. Every pre-recovery permit is thereby invalid.

## 7. Durable Replay

The authority stream permits only registered 05D transition, prepared-permit,
and consumed-permit event payloads. Replay rejects:

- sequence gaps, duplicates, or reordering;
- a foreign event type in the dedicated stream;
- wrong prior event ID/digest or non-monotonic generation;
- impossible status transitions;
- consume-before-prepare, duplicate consume, or conflicting permit identity;
- canonical event or payload drift.

The replay result consists of the exact current `GlobalHaltState`, prepared
unconsumed permits, and consumed permit IDs. Restarting from the same event
bytes yields byte-identical state.

No new table or migration is required. 05D uses the existing event-ledger
event/outbox append boundary. Transition and permit events share one stream, so
their contiguous sequence is the compare-and-append serialization point.

## 8. Submit Authority Flow

### 8.1 Prepare

1. Strictly canonicalize every input.
2. Verify the durable 05C approval from the trusted event ledger using the
   original intent, target-policy decision, observation, and runtime policy.
3. Require current policy, observation, portfolio, and safety digests to match
   the approved bindings exactly. Any drift requires a new 05C approval rather
   than silent reuse.
4. Replay the dedicated halt stream and require an initialized `ACTIVE` state.
5. Require the state bindings to equal the current inputs and safety evidence.
6. Append a prepared-permit event at the next contiguous sequence, append its
   canonical outbox intent, load back exactly one event, and compare bytes and
   digests.
7. Return `PreparedSubmitPermit` only after every check succeeds.

### 8.2 Consume

1. Canonicalize and load the exact prepared permit.
2. Re-read safety, policy, observation, portfolio, and the authority stream.
3. Require the permit to be unexpired and unconsumed.
4. Require all current digests, stream head, generation, status, and transition
   digest to match the permit.
5. Append the consumed-permit event at the next contiguous sequence and load it
   back exactly.
6. Return `ConsumedSubmitAuthority` once.

A concurrent halt, recovery, prepared permit, or consume races for the same
next sequence. A sequence conflict always triggers a complete replay before
another append attempt. A halt/recovery rotation or same-permit consumption
rejects the old permit. If only an unrelated permit event won and the global
generation, transition digest, safety, policy, observation, and expiry remain
exact, the implementation may perform one bounded compare-and-append retry at
the new head. This is a cause-checked retry, not a blind retry or unbounded
loop.

## 9. Errors and Idempotency

### 9.1 Deterministic outcomes

Breaker causes and a retained halt are normal deterministic state results, not
exceptions.

### 9.2 Bounded failures

Separate bounded public exceptions cover:

- invalid or unavailable halt authority;
- durable transition/replay failure;
- prepare failure;
- consume failure.

Repository, canonicalization, outbox, read-back, and sequence failures are
chained only at narrow boundaries. Unrelated programming errors are not
relabelled as expected repository faults. No function returns a partial state,
permit, or authority after a failure.

### 9.3 Idempotency

An exact retry with the same event ID and byte-identical canonical event/outbox
may return the same durable result. Same ID with different content is a
conflict. Permit consumption is never idempotently reusable: a second consume
returns a bounded rejection even if its requested bytes match.

Error text contains fixed classifications, IDs, generations, and reason codes
only. It excludes credentials, environment variables, raw private paths,
sentinel contents, and private runtime evidence.

## 10. Package Boundaries

Expected focused modules:

- `packages/domain/runtime_halt.py` — strict public contracts;
- `packages/runtime_risk/halt.py` — pure breaker, transitions, and replay;
- `packages/runtime_risk/submit_authority.py` — prepare/consume durability;
- `packages/domain/events.py` — typed event registration only;
- `scripts/generate_contracts.py` and generated domain schemas;
- focused domain, halt, replay, race, and submit-authority tests.

Existing 05A–05C contracts remain compatible. `RiskDecision` remains
target-policy risk; `RuntimeOrderRiskDecision` remains 05C order risk. Neither
is repurposed as the global halt state.

## 11. TDD and Review Packets

The implementation should be split into bounded SDD tasks:

1. contracts, schemas, and pure breaker semantics;
2. event-sourced halt transitions, recovery, and replay;
3. durable permit preparation and consumption;
4. adversarial race, replay, and failure-drill closure;
5. whole-branch review and fresh clean-candidate gates.

Every task starts with a meaningful failing test, is implemented by a fresh
subagent, and receives an independent SPEC/QUALITY review before the next task.
The final branch receives a separate adversarial whole-branch review.

## 12. Required Tests

At minimum:

- daily loss and drawdown below, exactly at, and above thresholds;
- hostile Decimal contexts and reporting-currency identity;
- kill switch `INACTIVE`, `ACTIVE`, and `UNKNOWN`;
- safe and unsafe initialization;
- `ACTIVE -> HALTED`, repeated halt, and replay restart;
- recovery absent, expired, stale, forged, conflicting, and valid;
- monotonic generation and exact prior digest lineage;
- prepare/consume happy path and five-second boundary;
- halt or recovery between approval, prepare, and consume;
- observation, policy, portfolio, safety, and 05C approval drift;
- duplicate prepare, duplicate consume, and same-ID conflicting content;
- persistence, outbox, read-back, canonicalization, and sequence failures;
- trusted byte-identical ledger replica behavior;
- forged nested models and bounded canonical nesting;
- generated-contract determinism;
- existing order, risk, event-ledger, portfolio, 05A, 05B, and 05C regressions.

## 13. Completion Gates

The exact implementation plan must include:

- focused 05D contract/breaker/replay/permit tests;
- all Phase 5 portfolio and runtime-risk suites;
- event and event-ledger suites;
- generated-contract generation and `--check`;
- broad-handler inventory, secret hygiene, and `git diff --check`;
- independent task reviews;
- a whole-branch review with no open Critical or Important finding;
- a fresh standalone clean-candidate `make test-all` gate;
- local fast-forward integration only after all gates pass.

No test may be converted to a skip, deselection, weakened assertion, or
environment exception to obtain a green result.

## 14. Acceptance Criteria

1. The same canonical stream bytes always replay to the same halt state and
   permit-consumption set.
2. Global halt is never implicitly active and fails closed on unknown safety
   evidence.
3. Daily loss and drawdown transition only at their exact approved boundaries.
4. Recovery requires exact, current, unexpired operator authority and rotates
   generation.
5. Every prior permit is invalid after any halt or recovery rotation.
6. Permit preparation proves the exact durable 05C approval and current active
   authority.
7. Permit consumption is one-shot and rejects every sequence, generation,
   digest, expiry, or state race.
8. Persistence failure occurs before any submit authority is returned.
9. Existing Phase 3–05C contracts, replay, and generated schemas remain
   compatible.
10. The diff introduces no provider, broker, exchange, network, service,
    migration, dashboard mutation, runtime activation, live gate, or deployment
    behavior.
11. Independent reviews and fresh clean-candidate gates pass before local
    integration.

## 15. Explicit Deferrals

- hierarchical kill-switch scopes;
- exposure reservations;
- execution-client integration;
- cancel/reconciliation behavior while halted;
- safety rotation after consume during external transport;
- paper or live adapters;
- PostgreSQL migration or runtime activation;
- Release Authority v2, promotion, and deployment.
