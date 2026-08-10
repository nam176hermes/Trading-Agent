# Phase 5 05B Snapshot Authority Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require a separately supplied ledger commitment for every 05B snapshot-tail replay and completeness-bind all durable business identities to applied events.

**Architecture:** PortfolioSnapshotRecord remains untrusted cache material. Full replay calculates a rolling prefix-history commitment and can issue a separate PortfolioSnapshotAuthority; tail replay requires that authority and checks it before applying events. Applied-event metadata names its event type and business identity so execution, funding, and reconciliation indexes are provably complete within the anchored record.

**Tech Stack:** Python 3.11, Pydantic v2 strict frozen models, canonical EventEnvelope JSON, SHA-256, pytest, existing contract generator.

## Global Constraints

- Snapshot-tail replay must reject a snapshot when independently supplied PortfolioSnapshotAuthority is missing or mismatched.
- PortfolioSnapshotAuthority is separate from PortfolioSnapshotRecord and binds schema/reducer versions, account, stream, cursor, state hash, and rolling prefix-history hash.
- The rolling prefix-history hash binds every canonical prefix event in strict sequence, including prior history hash, sequence, event ID, event type, and canonical event digest.
- PortfolioAppliedEvent must completeness-bind fill execution IDs, funding IDs, and reconciliation IDs to the corresponding applied event type.
- Full replay and authority-verified snapshot plus tail must produce identical result, canonical JSON, state hash, cursor, and prefix-history hash.
- Do not add persistence, API, database, provider, key management, HMAC/signature, risk, order execution, runtime mutation, deployment, or live authority.
- Preserve existing Phase 3/4 contracts and generated artifacts except the unreleased 05B replay schemas; do not change dependencies or lockfiles.
- Follow TDD: observe every new regression fail before production edits.
- Use the existing isolated feature worktree; do not push.

---

### Task 1: Rolling History Commitment and External Snapshot Authority

**Files:**
- Modify: packages/portfolio_reducer/models.py
- Modify: packages/portfolio_reducer/replay.py
- Modify: packages/portfolio_reducer/__init__.py
- Modify: scripts/generate_contracts.py
- Modify: tests/portfolio_reducer/test_replay.py
- Modify: tests/domain/test_contract_generation.py

**Interfaces:**
- Consumes: canonical EventEnvelope codec, PortfolioReplayResult, PortfolioSnapshotRecord, and existing state hash.
- Produces: PortfolioSnapshotAuthority, snapshot_authority_from_result(result) -> PortfolioSnapshotAuthority, PortfolioReplayResult.prefix_history_hash, PortfolioSnapshotRecord.prefix_history_hash, and replay_portfolio(events, *, snapshot=None, authority=None).
- Authority is required exactly when snapshot is supplied; authority without snapshot also fails.

- [ ] **Step 1: Write focused RED tests for authority custody**

~~~python
def test_snapshot_tail_requires_independent_authority(portfolio_events):
    prefix = replay_portfolio(portfolio_events[:3])
    record = snapshot_from_portfolio_result(prefix)
    with pytest.raises(PortfolioReplayError, match="authority"):
        replay_portfolio(portfolio_events[3:], snapshot=record)


def test_recomputed_forged_record_rejects_original_authority(portfolio_events):
    prefix = replay_portfolio(portfolio_events[:3])
    record = snapshot_from_portfolio_result(prefix)
    authority = snapshot_authority_from_result(prefix)
    forged = rewrite_effect_and_recompute_all_record_hashes(record)
    with pytest.raises(PortfolioReplayError, match="authority"):
        replay_portfolio(portfolio_events[3:], snapshot=forged, authority=authority)
~~~

Add table cases for wrong account, stream, cursor, state hash, prefix-history hash, schema/reducer version, authority reuse with another record, and authority supplied without snapshot.

- [ ] **Step 2: Verify RED**

Run: PYTHONDONTWRITEBYTECODE=1 uv run pytest -q -p no:cacheprovider tests/portfolio_reducer/test_replay.py -k "authority or forged_record"

Expected: collection or assertion failures because authority models/API do not exist and snapshot-only replay remains accepted.

- [ ] **Step 3: Implement the rolling history commitment**

Use one canonical function for each event step:

~~~python
def _extend_history_hash(previous: str, event: EventEnvelope[object]) -> str:
    event_json = serialize_event(event)
    material = _canonical_json({
        "event_digest": event_digest(event_json),
        "event_id": str(event.event_id),
        "event_type": event.event_type,
        "previous": previous,
        "sequence": event.sequence,
    })
    return event_digest(material)
~~~

Use the fixed genesis digest event_digest("") before sequence 1. Full replay extends it for every canonical event. Tail replay starts from the authority-verified prefix hash and extends it once per accepted tail event.

Add prefix_history_hash to result/record canonical state document so full and tail results remain equal.

- [ ] **Step 4: Implement authority issuance and fail-closed validation**

Define the strict frozen model:

~~~python
class PortfolioSnapshotAuthority(DomainModel):
    schema_version: Literal["portfolio-replay-v1"]
    reducer_version: Literal["portfolio-reducer-v1"]
    account_id: CanonicalPortfolioIdentifier
    stream_id: UUID
    cursor_sequence: Annotated[int, Field(gt=0)]
    snapshot_state_hash: Sha256
    prefix_history_hash: Sha256
~~~

snapshot_authority_from_result must fully revalidate its result first, then copy only trusted commitment fields. In replay_portfolio:

~~~python
if (snapshot is None) != (authority is None):
    raise PortfolioReplayError("snapshot and authority must be supplied together")
if snapshot is not None:
    validated_snapshot = _validate_document(snapshot, record=True)
    validated_authority = _validate_authority(authority, validated_snapshot)
~~~

Compare every authority field before validating/applying the tail. Authority must not be read from or reconstructed from the snapshot during tail validation.

- [ ] **Step 5: Generate schemas, run GREEN, and commit**

Run:

~~~bash
uv run python scripts/generate_contracts.py
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q -p no:cacheprovider tests/portfolio_reducer/test_replay.py tests/domain/test_contract_generation.py
uv run python scripts/generate_contracts.py --check
git diff --check
~~~

Expected: PASS; generated changes are limited to PortfolioReplayResult, PortfolioSnapshotRecord, new PortfolioSnapshotAuthority, and generator inventory.

Commit:

~~~bash
git add packages/portfolio_reducer scripts/generate_contracts.py schemas tests/portfolio_reducer/test_replay.py tests/domain/test_contract_generation.py
git commit -m "feat: anchor portfolio snapshot tail replay"
~~~

### Task 2: Applied-Event Identity Completeness and Forgery Regression Matrix

**Files:**
- Modify: packages/portfolio_reducer/models.py
- Modify: packages/portfolio_reducer/reducer.py
- Modify: packages/portfolio_reducer/replay.py
- Modify: tests/portfolio_reducer/test_execution_accounting.py
- Modify: tests/portfolio_reducer/test_replay.py
- Modify: docs/implementation/foundation-handler-inventory.md only when the inventory checker requires mechanical line updates

**Interfaces:**
- Consumes: Task 1 authority-verified replay and existing PortfolioBusinessIdentity tuples.
- Produces: PortfolioAppliedEvent.event_type and PortfolioAppliedEvent.business_identity_id.
- Produces: exact one-to-one completeness validation between applied fill/funding/reconciliation events and their corresponding identity indexes.

- [ ] **Step 1: Write RED tests for identity omission and mapping errors**

~~~python
@pytest.mark.parametrize("identity_kind", ["funding", "execution", "reconciliation"])
def test_authority_rejects_hash_consistent_identity_omission(identity_kind, history):
    prefix = replay_portfolio(history.prefix(identity_kind))
    record = snapshot_from_portfolio_result(prefix)
    authority = snapshot_authority_from_result(prefix)
    forged = omit_identity_and_recompute_internal_hashes(record, identity_kind)
    with pytest.raises(PortfolioReplayError, match="identity"):
        replay_portfolio(history.reuse_tail(identity_kind), snapshot=forged, authority=authority)
~~~

Add direct malformed-state cases: missing business_identity_id for an applicable event, identity on a mark/opening/conversion event, wrong identity kind/event type, duplicate identity binding, and identity event metadata not matching applied event.

- [ ] **Step 2: Verify RED**

Run: PYTHONDONTWRITEBYTECODE=1 uv run pytest -q -p no:cacheprovider tests/portfolio_reducer/test_replay.py -k "identity_omission or business_identity"

Expected: FAIL because PortfolioAppliedEvent has no event type/business identity fields and completeness is not enforceable.

- [ ] **Step 3: Record typed applied-event facts at ingress**

When a canonical event is applied, derive metadata from its exact payload type:

~~~python
def _business_identity(payload: object) -> UUID | None:
    if type(payload) is PortfolioFillEntry:
        return payload.fill.execution_id
    if type(payload) is PortfolioFundingEntry:
        return payload.funding_id
    if type(payload) is PortfolioReconciliationEntry:
        return payload.reconciliation_id
    return None
~~~

Create PortfolioAppliedEvent with event_id, digest, event_type, and business_identity_id. Existing duplicate/conflict checks continue using event_id/digest; do not infer identity from mutable downstream accounting state.

- [ ] **Step 4: Enforce one-to-one completeness during record validation**

Build expected maps from applied events:

~~~python
expected_execution = {
    item.business_identity_id: item
    for item in state.applied_events
    if item.event_type == "PortfolioFillEntry"
}
~~~

Repeat for funding and reconciliation. Reject missing IDs, unexpected IDs, duplicate mappings, wrong event type, mismatched source event/digest/stream/sequence, and identity tuples not represented in applied events. Opening, mark, conversion, and valuation events must have business_identity_id None.

Keep the external authority check first so a record rewritten consistently throughout still fails against the trusted commitment.

- [ ] **Step 5: Run full repair suite and commit**

Run:

~~~bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q -p no:cacheprovider tests/domain/test_portfolio_events.py tests/portfolio_reducer tests/domain/test_contract_generation.py
make check-contracts
make check-broad-handler-inventory
make check-secrets
git diff --check
~~~

Expected: PASS with all original 05B accounting, mark, flat-position, snapshot-tail, and new authority/identity tests.

Commit:

~~~bash
git add packages/portfolio_reducer tests/portfolio_reducer docs/implementation/foundation-handler-inventory.md
git commit -m "fix: completeness-bind portfolio business identities"
~~~

### Task 3: Independent Repair Review and Clean-Clone Final Gate

**Files:**
- Create: .superpowers/sdd/2026-08-09-phase5-05b-snapshot-authority-repair/task-3-review.md (ignored receipt)
- Modify: no tracked file unless a verified review finding opens a TDD fix round.

**Interfaces:**
- Consumes: Task 1-2 commits and the approved repair design.
- Produces: independent SPEC/QUALITY verdict and merge authorization only when C/I/M is zero.

- [ ] **Step 1: Independently reproduce the original forgeries**

Using only public APIs, create a legitimate prefix/record/authority. Rewrite the multiplier/effect/source metadata and recompute all snapshot-controlled hashes; verify the original authority rejects it. Remove each durable identity and recompute all snapshot-controlled hashes; verify original authority rejects all three reuse tails.

- [ ] **Step 2: Independently inspect trust-boundary honesty**

Verify snapshot-only and authority-only calls fail, authority is not embedded in the record, no secret/signature claim exists, and docs/API state that the caller must source authority from separate trusted custody. Confirm no persistence/API/provider/runtime/risk/live code was added.

- [ ] **Step 3: Run a fresh detached clean-clone gate**

Bootstrap all owning components offline:

~~~bash
UV_OFFLINE=1 uv sync --frozen
(cd legacy/research-backend && UV_OFFLINE=1 uv sync --frozen --extra test)
(cd apps/dashboard && npm ci --offline)
~~~

Then run:

~~~bash
make audit-release
make check-contracts
make check-broad-handler-inventory
make check-secrets
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q -p no:cacheprovider tests/domain/test_portfolio_events.py tests/portfolio_reducer tests/domain/test_contract_generation.py
git diff --check
git status --short
~~~

Expected: every gate passes and status is empty.

- [ ] **Step 4: Record verdict**

Write exact candidate range, public forgery probe evidence, clean-clone commands, C/I/M, SPEC/QUALITY verdict, and merge recommendation. Only zero-finding PASS authorizes the final whole-branch review and local fast-forward; do not push.
