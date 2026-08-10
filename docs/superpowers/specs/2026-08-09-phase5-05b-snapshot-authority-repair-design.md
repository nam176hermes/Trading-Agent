# Phase 5 05B Snapshot Authority Repair Design

## Status

Approved architecture for the 05B snapshot-integrity repair. This repair
replaces self-attested snapshot-tail replay with an explicitly anchored trust
boundary. It does not add persistence, an API, a provider, key management,
risk evaluation, order execution, runtime mutation, or live authority.

## Problem

A PortfolioSnapshotRecord currently contains its own canonical JSON and SHA-256.
Those fields detect accidental or partial corruption, but they do not prove
provenance: an attacker can alter the accounting state, source event metadata,
execution effects, or business-identity indexes and then recompute every hash
inside the same record.

Fresh review probes demonstrated two consequences:

- a rewritten active fill effect changed snapshot-tail cash from the full
  replay value 1000 to 901 while the modified record remained internally
  hash-consistent;
- removing funding, consumed-execution, or reconciliation identity records
  allowed those business identities to be reused in a tail even though full
  ledger replay rejected them.

The defect is architectural. A digest stored only beside the bytes it digests
cannot authenticate those bytes. Adding more validators or retaining more
event material inside the same rewriteable record cannot create an independent
trust anchor.

## Chosen Architecture

Full replay produces two separate values:

1. PortfolioSnapshotRecord remains a deterministic cache containing the
   self-contained accounting projection needed for fast tail reduction.
2. PortfolioSnapshotAuthority is the independently retained ledger commitment
   required to trust that cache.

Tail replay requires both values:

    replay_portfolio(tail, snapshot=record, authority=trusted_authority)

Passing a snapshot without authority fails closed. The reducer first validates
the authority against the record, account, stream, cursor, reducer/schema
versions, state hash, and prefix-history commitment. Only then may it apply the
tail.

The authority is not embedded in the snapshot record and is not accepted from
snapshot-controlled fields. In 05B it is an immutable pure value returned by
full replay and supplied independently by the caller. A future persistence
packet may store or attest it through the append-only ledger boundary; this
repair does not create that adapter.

## Public Contracts

### PortfolioSnapshotAuthority

A strict frozen public model with:

- schema_version and reducer_version literals;
- account_id;
- stream_id and positive cursor sequence;
- snapshot_state_hash;
- prefix_history_hash.

prefix_history_hash is a deterministic rolling SHA-256 commitment over every
canonical EventEnvelope in the accepted prefix, in strict stream sequence.
Each step binds the prior history hash, sequence, event ID, event type, and
canonical event digest. No event payload is silently omitted.

The authority is produced only from a successful full replay result by:

    snapshot_authority_from_result(result) -> PortfolioSnapshotAuthority

The replay result therefore retains the verified prefix history commitment
needed to construct the authority.

### Applied Event Completeness

PortfolioAppliedEvent is extended additively with event_type and an optional
business_identity_id. Exact payload classes determine the identity binding:

- PortfolioFillEntry -> fill.execution_id;
- PortfolioFundingEntry -> funding_id;
- PortfolioReconciliationEntry -> reconciliation_id;
- other portfolio entries -> no business identity.

Record validation requires a one-to-one correspondence between applicable
PortfolioAppliedEvent entries and the execution, funding, and reconciliation
identity indexes. An identity cannot be omitted, duplicated, reassigned to
another event type, or retained without its applied event.

This structural check supplements the external authority. It makes malformed
records fail clearly and keeps the record self-consistent, while the authority
provides provenance.

## Replay Data Flow

Full history:

    canonical envelopes
        -> strict sequence/account validation
        -> rolling prefix history commitment
        -> deterministic accounting state
        -> PortfolioReplayResult
        -> snapshot record + separate snapshot authority

Snapshot plus tail:

    trusted authority + untrusted snapshot record
        -> exact authority/record/account/stream/cursor/version/hash match
        -> internal state and business-identity completeness validation
        -> strict canonical tail sequence
        -> continue rolling history commitment with each tail event
        -> deterministic result

A result from full history and a result from the corresponding verified
snapshot plus tail must remain exactly equal, including canonical snapshot,
canonical state JSON, state hash, cursor, and prefix history hash.

## Trust Boundary

PortfolioSnapshotAuthority is a caller-supplied trust anchor, not a signature
and not proof against a caller who deliberately supplies an attacker-created
authority. The public contract is explicit:

- snapshot bytes are untrusted cache material;
- authority must come from a separately trusted ledger/custody channel;
- supplying snapshot and authority from the same untrusted source is invalid
  integration;
- 05B verifies equality and deterministic commitments but does not own storage,
  key management, or remote attestation.

This boundary is the minimum honest repair. A self-hash cannot authenticate
itself, while HMAC/signatures would introduce secret management and runtime
authority outside 05B.

## Failure Semantics

PortfolioReplayError is raised before tail mutation when:

- snapshot is supplied without authority;
- authority is supplied without snapshot;
- authority schema/reducer version is unsupported;
- authority account, stream, cursor, state hash, or prefix-history hash differs
  from the validated record/result;
- applied-event business identity metadata is missing, duplicated, conflicting,
  or mapped to the wrong event type;
- any existing snapshot/tail invariant fails.

No fallback accepts an unanchored snapshot. No partial result is returned.

## Compatibility

- Full-history replay remains available without authority.
- Snapshot-tail callers must adopt the new required authority argument. This
  intentional fail-closed API change affects only the unreleased 05B reducer.
- Existing Phase 3/4 EventEnvelope, FillEvent, order, risk, legacy portfolio,
  and ledger repository contracts remain unchanged.
- Generated PortfolioReplayResult and PortfolioSnapshotRecord schemas are
  regenerated; a new PortfolioSnapshotAuthority schema is generated.
- No lockfile or dependency change is permitted.

## TDD and Review

RED tests must reproduce:

- the multiplier/source/effect rewrite with all internal hashes recomputed;
- omission of funding, consumed-execution, and reconciliation identities with
  internal hashes recomputed;
- missing authority, wrong state hash, wrong prefix-history hash, wrong
  account/stream/cursor, and authority reuse across another record;
- incomplete or incorrectly typed applied-event identity metadata;
- full-history versus authority-verified snapshot-tail equality across normal
  fills, marks, corrections, busts, funding, and reconciliation.

GREEN requires those forgeries to fail with PortfolioReplayError while the
existing 05B focused suite remains green. An independent task review and a new
whole-branch review must pass before local integration.

## Acceptance Criteria

1. No snapshot-tail replay is accepted without an independently supplied
   PortfolioSnapshotAuthority.
2. Recomputing every field and hash inside a forged snapshot does not bypass
   the original trusted authority.
3. Applied fill, funding, and reconciliation facts have complete durable
   business-identity bindings.
4. Full replay and authority-verified snapshot plus tail are byte-for-byte and
   hash-for-hash equivalent.
5. The repair remains pure and introduces no persistence, API, provider,
   runtime, risk, execution, secret, deployment, or live boundary.
