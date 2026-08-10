# Phase 6 06B Deterministic Execution Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pure deterministic reconciler that classifies sandbox execution evidence as reconciled, delivery-pending, or mismatched without creating execution, recovery, persistence, or halt authority.

**Architecture:** Extend the immutable sandbox snapshot with a canonical report inventory so the reconciler can identify delivered originals, queued reports, and duplicate deliveries without querying a ledger. Add strict request/result models and a pure replay module that consumes the snapshot plus caller-supplied canonical `OrderEvent`/`FillEvent` evidence, reuses `reduce_order`, and returns a canonical result instead of mutating any boundary.

**Tech Stack:** Python 3.11, Pydantic v2 strict/frozen models, existing domain order reducer, existing event-ledger canonical serializer, pytest, uv, and repository Make targets.

## Global Constraints

- Implement WS-06 packet 06B only; do not add crash/database recovery (06C), a paper adapter (06D), or legacy-execution quarantine (06E).
- Keep the reconciler pure and in-memory: no socket, HTTP, subprocess, thread, filesystem, database, service, provider, Nautilus runtime, paper adapter, or live-provider dependency/import.
- Do not invoke brokers, exchanges, accounts, private endpoints, providers, services, PostgreSQL, migrations, or runtime activation. Both live approvals remain false.
- Consume existing `OrderState`, `reduce_order`, `OrderEvent`, `FillEvent`, `EventEnvelope`, and event-ledger canonical codecs. Do not create a competing lifecycle reducer, report serializer, execution identity, financial calculation, or safety authority.
- 06B never accepts an `EventLedgerRepository`, datasource, callback, client, or provider. The caller supplies immutable evidence values.
- `MISMATCH` is a structured fail-closed reconciliation result; it never mutates a halt state, sends an alert, retries, resends, cancels, repairs, or persists data.
- Use `UV_OFFLINE=1 uv run` for root Python checks. Add no dependency and never hand-edit `uv.lock` or generated contract output.
- Generate artifacts only with `make generate-contracts`; verify them with `make check-contracts`.
- Preserve user-owned untracked files. Do not stage them.

---

## File Structure

- Modify: `packages/execution_sandbox/models.py` — strict snapshot report-inventory, reconciliation request/result, status, finding, reason, and bounded error contracts.
- Modify: `packages/execution_sandbox/client.py` — retain every generated canonical report in snapshot state; expose queue as the not-yet-delivered subset without changing command semantics.
- Modify: `packages/execution_sandbox/__init__.py` — export the new public contracts and pure entry point.
- Create: `packages/execution_sandbox/reconciliation.py` — canonical evidence ingestion, delivery-inventory comparison, order-state replay, fill validation, stable result construction.
- Modify: `tests/execution_sandbox/test_models.py` — strict/frozen contract and known-report inventory validation.
- Modify: `tests/execution_sandbox/test_client_lifecycle.py` — prove snapshot retention through original/duplicate creation and delivery.
- Create: `tests/execution_sandbox/test_reconciliation.py` — table-driven reconciliation, pending, mismatch, canonicalization, and mutation drills.
- Create: `.superpowers/sdd/2026-08-10-phase6-06b-execution-reconciliation/task-<n>-report.md` — ignored, sanitized RED/GREEN and verification evidence for each completed task.

## Public Interfaces Locked by This Plan

```python
class SandboxKnownReport(SandboxModel):
    report_id: UUID
    event: EventEnvelope[object]

class SandboxReconciliationStatus(str, Enum):
    RECONCILED = "RECONCILED"
    DELIVERY_PENDING = "DELIVERY_PENDING"
    MISMATCH = "MISMATCH"

class SandboxReconciliationReason(str, Enum):
    UNKNOWN_ORDER_REPORT = "UNKNOWN_ORDER_REPORT"
    OBSERVED_ORDER_REPLAY_FAILED = "OBSERVED_ORDER_REPLAY_FAILED"
    OBSERVED_STATE_MISMATCH = "OBSERVED_STATE_MISMATCH"
    PENDING_ORDER_REPLAY_FAILED = "PENDING_ORDER_REPLAY_FAILED"
    VENUE_STATE_MISMATCH = "VENUE_STATE_MISMATCH"
    FILL_EVIDENCE_MISMATCH = "FILL_EVIDENCE_MISMATCH"
    UNEXPECTED_OBSERVED_REPORT = "UNEXPECTED_OBSERVED_REPORT"

class SandboxReconciliationRequest(SandboxModel):
    snapshot: SandboxSnapshot
    observed_reports: tuple[EventEnvelope[object], ...]

class SandboxOrderReconciliation(SandboxModel):
    order_id: UUID
    observed_state: OrderState
    expected_venue_state: OrderState
    observed_report_ids: tuple[UUID, ...]
    pending_report_ids: tuple[UUID, ...]
    reason_codes: tuple[SandboxReconciliationReason, ...]

class SandboxReconciliationResult(SandboxModel):
    status: SandboxReconciliationStatus
    snapshot_time: datetime
    orders: tuple[SandboxOrderReconciliation, ...]
    pending_report_ids: tuple[UUID, ...]
    unattributed_event_ids: tuple[UUID, ...]
    unattributed_reason_codes: tuple[SandboxReconciliationReason, ...]

    @property
    def digest(self) -> str:
        return canonical_model_digest(self)

def reconcile_execution_state(
    request: SandboxReconciliationRequest,
) -> SandboxReconciliationResult:
    return _reconcile_canonical_request(request)
```

`SandboxSnapshot` gains `known_reports: tuple[SandboxKnownReport, ...] = ()`.
Known IDs are unique; every queued report ID occurs in known inventory; known
reports refer only to snapshot order IDs. A report is delivered exactly when
its ID is in `known_reports` and absent from `queued_reports`.

### Task 1: Retain canonical report inventory in the immutable snapshot

**Files:**
- Modify: `packages/execution_sandbox/models.py: SandboxReportPlan, SandboxSnapshot`
- Modify: `packages/execution_sandbox/client.py: _RetainedReport, _planned_reports, drain_reports, _replace_state`
- Modify: `packages/execution_sandbox/__init__.py`
- Test: `tests/execution_sandbox/test_models.py`
- Test: `tests/execution_sandbox/test_client_lifecycle.py`

**Interfaces:**
- Consumes: existing `SandboxReportPlan`, `SandboxSnapshot`, canonical event codec, and 06A `_retained_reports` lifecycle.
- Produces: `SandboxKnownReport` and `SandboxSnapshot.known_reports`, with known reports in generation order and queue IDs as their exact undelivered subset.

- [ ] **Step 1: Write failing snapshot-inventory tests**

```python
def test_snapshot_requires_each_queued_id_to_be_known(submitted_envelope):
    known = SandboxKnownReport(report_id=uid(20), event=submitted_envelope)
    queued = SandboxReportPlan(report_id=uid(21), deliver_at=NOW, event=submitted_envelope)
    with pytest.raises(ValueError, match="queued report_id"):
        SandboxSnapshot(
            connection_state=SandboxConnectionState.CONNECTED,
            current_time=NOW,
            known_reports=(known,),
            queued_reports=(queued,),
        )

def _original_duplicate_client(
    prepared_case: PreparedCase,
    safety_verifier: object,
    submitted_envelope: EventEnvelope[OrderEvent],
) -> SandboxExecutionClient:
    submitted = order_envelope(
        submitted_envelope, event_id=10, envelope_sequence=1,
        order_sequence=1, status=OrderStatus.SUBMITTED,
    )
    accepted = order_envelope(
        submitted_envelope, event_id=11, envelope_sequence=2,
        order_sequence=2, status=OrderStatus.ACCEPTED,
    )
    return client_for(
        scenario(
            commands=(command(100, SandboxCommandKind.SUBMIT, (20, 21, 22)),),
            reports=(
                original(20, submitted), original(21, accepted),
                duplicate(22, 21, at=NOW + timedelta(seconds=1)),
            ),
        ),
        safety_verifier,
    )

def test_snapshot_retains_original_and_duplicate_after_delivery(
    prepared_case: PreparedCase,
    safety_verifier: object,
    submitted_envelope: EventEnvelope[OrderEvent],
) -> None:
    client = _original_duplicate_client(prepared_case, safety_verifier, submitted_envelope)
    client.submit(valid_submit_request(prepared_case))
    client.drain_reports()
    client.advance_time(to=NOW + timedelta(seconds=1))
    client.drain_reports()
    snapshot = client.snapshot()
    assert [item.report_id for item in snapshot.known_reports] == [uid(20), uid(21), uid(22)]
    assert snapshot.queued_reports == ()
    assert snapshot.known_reports[1].event == snapshot.known_reports[2].event
```

Add strict/frozen/extra-field rejection, forged nested envelope rejection,
known report IDs duplicated, and known-report order ID not present in snapshot.

- [ ] **Step 2: Run the focused tests to verify RED**

Run: `UV_OFFLINE=1 uv run pytest -q tests/execution_sandbox/test_models.py tests/execution_sandbox/test_client_lifecycle.py -k 'known_report or report_inventory'`

Expected: FAIL during collection because `SandboxKnownReport` and
`SandboxSnapshot.known_reports` do not exist.

- [ ] **Step 3: Add the strict contract and retain every generated report**

```python
class SandboxKnownReport(SandboxModel):
    report_id: UUID
    event: EventEnvelope[object]

    @model_validator(mode="after")
    def _canonical_event(self) -> "SandboxKnownReport":
        object.__setattr__(self, "event", _canonical_report_event(self.event))
        return self
```

Extend `SandboxSnapshot` with `known_reports=()`. In its post-validator,
canonicalize every record, reject duplicate known IDs, require every queued
report ID to occur once in known IDs, and require the known envelope payload's
order ID to be in `orders`. Preserve tuple order.

Change `_RetainedReport` retention so `_planned_reports` appends one retained
canonical record for both originals and duplicates. In `drain_reports`, leave
the inventory untouched when queue entries are removed. In `_replace_state`,
project retained canonical strings to `SandboxKnownReport` through
`_canonical_envelope`; never expose mutable internal state. Export
`SandboxKnownReport` from `__init__.py`.

- [ ] **Step 4: Run focused GREEN and the 06A lifecycle regression**

Run: `UV_OFFLINE=1 uv run pytest -q tests/execution_sandbox/test_models.py tests/execution_sandbox/test_client_lifecycle.py tests/execution_sandbox/test_submit_authority.py`

Expected: PASS. Existing duplicate delivery remains one canonical ledger event
while snapshot inventory retains both report IDs.

- [ ] **Step 5: Record evidence and commit Task 1**

Write the ignored sanitized report with the exact RED and GREEN commands,
counts, scope, and no-I/O confirmation.

```bash
git add packages/execution_sandbox/models.py packages/execution_sandbox/client.py \
  packages/execution_sandbox/__init__.py tests/execution_sandbox/test_models.py \
  tests/execution_sandbox/test_client_lifecycle.py
git commit -m "feat: retain sandbox reconciliation evidence"
```

### Task 2: Define reconciliation contracts and canonical request boundary

**Files:**
- Modify: `packages/execution_sandbox/models.py: after SandboxSnapshot`
- Modify: `packages/execution_sandbox/__init__.py`
- Create: `packages/execution_sandbox/reconciliation.py`
- Create: `tests/execution_sandbox/test_reconciliation.py`

**Interfaces:**
- Consumes: Task 1 `SandboxKnownReport` / `SandboxSnapshot`, `OrderState`, `OrderEvent`, `FillEvent`, `EventEnvelope`, `serialize_event`, `deserialize_event`, and `canonical_model_digest`.
- Produces: all locked public reconciliation model types and canonical ingress helpers. Task 3 introduces the public `reconcile_execution_state` entry point only when its non-empty replay behavior is complete.

- [ ] **Step 1: Write failing public-contract and invalid-input tests**

```python
def test_request_rejects_forged_snapshot_and_non_execution_envelope(prepared_case):
    forged_snapshot = SandboxSnapshot.model_construct(
        connection_state=SandboxConnectionState.CONNECTED,
        current_time=NOW,
        orders=object(),
    )
    with pytest.raises(ValueError, match="snapshot"):
        SandboxReconciliationRequest(snapshot=forged_snapshot, observed_reports=())

def test_same_event_id_with_different_canonical_bytes_is_rejected(snapshot, submitted_envelope):
    altered = submitted_envelope.model_copy(update={"source": "other"})
    with pytest.raises(ValueError, match="conflicting observed event"):
        SandboxReconciliationRequest(
            snapshot=snapshot,
            observed_reports=(submitted_envelope, altered),
        )
```

Also test exact enum order, frozen/forbid-extra contracts, result digest
stability, status/result invariant rejection, and non-`OrderEvent`/
`FillEvent` payload rejection.  At the top of this new module, create a local
`reconciliation_request_factory` fixture.  It builds the five Task 3 cases
from existing `scenario`, `original`, `duplicate`, `order_envelope`, and
`client_for` helpers, then returns `SandboxReconciliationRequest` using the
client snapshot plus the exact envelopes already delivered by `drain_reports`.
Import `Callable` from `collections.abc` in this module.

- [ ] **Step 2: Run focused tests to verify RED**

Run: `UV_OFFLINE=1 uv run pytest -q tests/execution_sandbox/test_reconciliation.py -k 'contract or invalid_input or canonical'`

Expected: FAIL during collection because reconciliation public models do not
exist.

- [ ] **Step 3: Implement models and canonical ingress helpers**

Add `SandboxReconciliationError(SandboxExecutionError)`, the closed enums,
request/result/finding models, and their validators. `SandboxOrderReconciliation`
must canonicalize both states, require their order IDs to equal `order_id`,
require report ID tuples to be unique, and require reason codes in enum order.
`SandboxReconciliationResult` must sort records by `order_id.int`, derive its
status consistency from per-order/unattributed finding and pending content,
require ordered unique unattributed event IDs, and expose:

```python
@property
def digest(self) -> str:
    return canonical_model_digest(self)
```

In `reconciliation.py`, add only the ingress helpers in this task:

```python
def _canonical_envelope(value: object) -> EventEnvelope[object]:
    if not isinstance(value, EventEnvelope):
        raise SandboxReconciliationError("invalid observed envelope")
    canonical = deserialize_event(serialize_event(value))
    if type(canonical.payload) not in (OrderEvent, FillEvent):
        raise SandboxReconciliationError("invalid observed envelope")
    return canonical

def _canonical_request(value: object) -> SandboxReconciliationRequest:
    if type(value) is not SandboxReconciliationRequest:
        raise SandboxReconciliationError("invalid reconciliation request")
    return SandboxReconciliationRequest.model_validate(value.model_dump(mode="python"))

def _observed_by_event_id(
    reports: tuple[EventEnvelope[object], ...],
) -> dict[UUID, EventEnvelope[object]]:
    observed: dict[UUID, EventEnvelope[object]] = {}
    for report in reports:
        canonical = _canonical_envelope(report)
        prior = observed.get(canonical.event_id)
        if prior is not None and serialize_event(prior) != serialize_event(canonical):
            raise SandboxReconciliationError("conflicting observed event")
        observed[canonical.event_id] = canonical
    return observed
```

Round-trip envelopes via `serialize_event` / `deserialize_event`, require an
exact `OrderEvent` or `FillEvent` payload, collapse byte-identical repeated
event IDs, and raise `SandboxReconciliationError` only for canonical boundary
failures. Export the public model names. Do not export a non-functional
`reconcile_execution_state` entry point in this task.

- [ ] **Step 4: Run focused GREEN**

Run: `UV_OFFLINE=1 uv run pytest -q tests/execution_sandbox/test_reconciliation.py -k 'contract or invalid_input or canonical'`

Expected: PASS. Confirm model canonicalization rejects forged input and the
digest of an equivalently reconstructed result is stable.

- [ ] **Step 5: Record evidence and commit Task 2**

```bash
git add packages/execution_sandbox/models.py packages/execution_sandbox/reconciliation.py \
  packages/execution_sandbox/__init__.py tests/execution_sandbox/test_reconciliation.py
git commit -m "feat: define sandbox reconciliation contracts"
```

### Task 3: Replay delivery evidence and classify order-state reconciliation

**Files:**
- Modify: `packages/execution_sandbox/reconciliation.py`
- Modify: `tests/execution_sandbox/test_reconciliation.py`

**Interfaces:**
- Consumes: Task 2 canonical request/event maps; Task 1 known report inventory; existing `OrderState`, `OrderEvent`, and `reduce_order`.
- Produces: the exported `reconcile_execution_state` behavior for all order-state paths and aggregate `RECONCILED` / `DELIVERY_PENDING` / `MISMATCH` status.

- [ ] **Step 1: Write the failing order replay table**

```python
@pytest.mark.parametrize(
    ("scenario_kind", "expected_status"),
    [
        ("settled_ack", SandboxReconciliationStatus.RECONCILED),
        ("delayed_ack", SandboxReconciliationStatus.DELIVERY_PENDING),
        ("lost_response", SandboxReconciliationStatus.DELIVERY_PENDING),
        ("disconnected_queue", SandboxReconciliationStatus.DELIVERY_PENDING),
        ("missing_observed_ack", SandboxReconciliationStatus.MISMATCH),
    ],
)
def test_reconcile_execution_state_classifies_delivery_state(
    scenario_kind: str,
    expected_status: SandboxReconciliationStatus,
    reconciliation_request_factory: Callable[[str], SandboxReconciliationRequest],
) -> None:
    result = reconcile_execution_state(reconciliation_request_factory(scenario_kind))
    assert result.status is expected_status

def test_reconcile_execution_state_marks_forged_observed_state_mismatch(
    reconciliation_request_factory: Callable[[str], SandboxReconciliationRequest],
) -> None:
    result = reconcile_execution_state(reconciliation_request_factory("forged_observed_state"))
    assert result.status is SandboxReconciliationStatus.MISMATCH
    assert result.orders[0].reason_codes == (
        SandboxReconciliationReason.OBSERVED_STATE_MISMATCH,
    )

def test_reconcile_execution_state_keeps_unknown_report_unattributed(
    reconciliation_request_factory: Callable[[str], SandboxReconciliationRequest],
) -> None:
    result = reconcile_execution_state(reconciliation_request_factory("unknown_order"))
    assert result.status is SandboxReconciliationStatus.MISMATCH
    assert result.unattributed_event_ids == (uid(901),)
    assert result.unattributed_reason_codes == (
        SandboxReconciliationReason.UNKNOWN_ORDER_REPORT,
    )
```

Add cases for fill-before-ACK, cancel/fill and modify/fill reducer edges,
invalid observed lifecycle, invalid pending lifecycle, unexpected observed
report, and input-order permutation.

- [ ] **Step 2: Run the order-state table to verify RED**

Run: `UV_OFFLINE=1 uv run pytest -q tests/execution_sandbox/test_reconciliation.py -k 'classifies_delivery_state or observed_state_mismatch or order_replay'`

Expected: FAIL during collection because Task 2 deliberately has no public
reconciliation entry point yet.

- [ ] **Step 3: Implement finite known/delivered inventory replay**

```python
def _known_reports(snapshot: SandboxSnapshot) -> dict[UUID, EventEnvelope[object]]:
    return {report.report_id: report.event for report in snapshot.known_reports}

def _queued_reports(snapshot: SandboxSnapshot) -> tuple[tuple[SandboxReportPlan, EventEnvelope[object]], ...]:
    known = _known_reports(snapshot)
    pairs = tuple((plan, known[plan.report_id]) for plan in snapshot.queued_reports)
    return tuple(
        pair for _, pair in sorted(enumerate(pairs), key=lambda item: (item[1][0].deliver_at, item[0]))
    )

def _delivered_event_ids(snapshot: SandboxSnapshot) -> frozenset[UUID]:
    queued_ids = {plan.report_id for plan in snapshot.queued_reports}
    return frozenset(
        report.event.event_id for report in snapshot.known_reports if report.report_id not in queued_ids
    )

def _replay_order_events(
    order_id: UUID,
    events: tuple[EventEnvelope[object], ...],
) -> tuple[OrderState, tuple[SandboxReconciliationReason, ...]]:
    state = OrderState(order_id=order_id)
    for event in sorted(events, key=lambda item: item.payload.sequence):
        state = reduce_order(state, event.payload)
    return state, ()
```

Require observed evidence IDs to equal the unique event IDs represented by
delivered known reports. Report events for unknown order IDs, or evidence
events missing from delivered inventory, create ordered
`unattributed_event_ids` / `unattributed_reason_codes` mismatch findings
rather than a transport exception. Sort queued plans by
`(deliver_at, tuple_position)`; replay their `OrderEvent` payloads from the
proven observed state. Never alter a `FillEvent` in an order-state replay.

For every snapshot order, produce one record. Compare replayed observed state
to `observed_state`, then observed-plus-queue expected state to `venue_state`.
Use only `reduce_order`; map `OrderReductionError` to the corresponding closed
replay reason. Derive aggregate status exactly as specified: any reason is
`MISMATCH`; otherwise a non-empty queue is `DELIVERY_PENDING`; otherwise it is
`RECONCILED`.

Only after these paths are implemented, add the public entry point and export:

```python
def reconcile_execution_state(
    request: SandboxReconciliationRequest,
) -> SandboxReconciliationResult:
    canonical_request = _canonical_request(request)
    return _reconcile_canonical_request(canonical_request)
```

- [ ] **Step 4: Run order replay GREEN plus 06A regressions**

Run: `UV_OFFLINE=1 uv run pytest -q tests/execution_sandbox/test_reconciliation.py tests/execution_sandbox/test_client_lifecycle.py tests/execution_sandbox/test_submit_authority.py`

Expected: PASS. Confirm the reconciliation call changes neither snapshot,
client, in-memory ledger, nor evidence tuple.

- [ ] **Step 5: Record evidence and commit Task 3**

```bash
git add packages/execution_sandbox/reconciliation.py tests/execution_sandbox/test_reconciliation.py
git commit -m "feat: replay sandbox reconciliation evidence"
```

### Task 4: Validate fill/duplicate inventory and complete 06B verification

**Files:**
- Modify: `packages/execution_sandbox/reconciliation.py`
- Modify: `tests/execution_sandbox/test_reconciliation.py`
- Modify: `tests/execution_sandbox/test_models.py` only if a public contract invariant requires an additional model-level test

**Interfaces:**
- Consumes: completed Task 3 order classification and exact known/delivered inventory.
- Produces: complete fill and duplicate evidence validation, final source-test evidence, and no new runtime integration surface.

- [ ] **Step 1: Write the failing fill and duplicate drills**

```python
@pytest.mark.parametrize(
    "mutation",
    ["missing_fill", "foreign_fill", "wrong_execution_id", "wrong_report_sequence"],
)
def test_reconciliation_rejects_unexplained_fill_evidence(
    mutation: str,
    reconciliation_request_factory: Callable[[str], SandboxReconciliationRequest],
) -> None:
    result = reconcile_execution_state(reconciliation_request_factory(f"fill_{mutation}"))
    assert result.status is SandboxReconciliationStatus.MISMATCH
    assert SandboxReconciliationReason.FILL_EVIDENCE_MISMATCH in result.orders[0].reason_codes

def test_queued_duplicate_of_delivered_original_is_pending_not_mismatch(
    reconciliation_request_factory: Callable[[str], SandboxReconciliationRequest],
) -> None:
    result = reconcile_execution_state(reconciliation_request_factory("queued_duplicate"))
    assert result.status is SandboxReconciliationStatus.DELIVERY_PENDING
    assert result.pending_report_ids == (uid(22),)
```

Add original-queued/duplicate-delivered ordering, exact same-byte evidence
repeat, changed bytes under the same ID, duplicate generated report IDs,
mutation safety, and canonical digest stability after a hostile Decimal context
change.

- [ ] **Step 2: Run fill/duplicate drills to verify RED**

Run: `UV_OFFLINE=1 uv run pytest -q tests/execution_sandbox/test_reconciliation.py -k 'fill or duplicate or decimal_context'`

Expected: FAIL because Task 3 has not yet validated fill execution/report
inventory or duplicate delivery identity.

- [ ] **Step 3: Implement fill and duplicate inventory validation**

```python
def _validate_fill_evidence(
    *,
    known: dict[UUID, EventEnvelope[object]],
    queued_ids: frozenset[UUID],
    observed: dict[UUID, EventEnvelope[object]],
) -> dict[UUID, tuple[SandboxReconciliationReason, ...]]:
    reasons: dict[UUID, list[SandboxReconciliationReason]] = {}
    delivered_fill_ids = {
        expected.event_id
        for report_id, expected in known.items()
        if report_id not in queued_ids and type(expected.payload) is FillEvent
    }
    for actual in observed.values():
        if type(actual.payload) is FillEvent and actual.event_id not in delivered_fill_ids:
            reasons.setdefault(actual.payload.order_id, []).append(
                SandboxReconciliationReason.FILL_EVIDENCE_MISMATCH
            )
    for report_id, expected in known.items():
        if type(expected.payload) is not FillEvent or report_id in queued_ids:
            continue
        actual = observed.get(expected.event_id)
        if actual is None or serialize_event(actual) != serialize_event(expected):
            reasons.setdefault(expected.payload.order_id, []).append(
                SandboxReconciliationReason.FILL_EVIDENCE_MISMATCH
            )
    return {order_id: tuple(values) for order_id, values in reasons.items()}
```

For every known report identity, compare canonical event bytes to observed
evidence when its report is delivered. Treat several report identities with
the same canonical event ID as one idempotent observed event. Validate each
fill's exact execution ID, report sequence, and order ID against the known
inventory; never calculate price, quantity, commission, or cash. A foreign
fill whose order ID is absent from the snapshot is an unattributed event with
`FILL_EVIDENCE_MISMATCH`, rather than a synthetic order finding. Queue-only
reports remain pending. Add `FILL_EVIDENCE_MISMATCH` to the affected order
without suppressing any order-state reason. Keep reason ordering enum-stable
and result construction sorted by UUID integer.

- [ ] **Step 4: Run the complete relevant verification sequence**

Run:

```bash
UV_OFFLINE=1 uv run pytest -q tests/execution_sandbox tests/event_ledger tests/domain/test_orders.py
make generate-contracts
make check-contracts
make check-broad-handler-inventory
make check-secrets
git diff --check
```

Expected: all commands exit 0. If generator output changes, inspect it,
include only generator-produced tracked artifacts, and rerun `make
check-contracts`; never hand-edit generated files.

- [ ] **Step 5: Record evidence and commit Task 4**

```bash
git add packages/execution_sandbox/reconciliation.py tests/execution_sandbox/test_reconciliation.py \
  tests/execution_sandbox/test_models.py
git commit -m "fix: validate sandbox reconciliation evidence"
```

### Task 5: Independent-ready candidate verification

**Files:**
- Create: `.superpowers/sdd/2026-08-10-phase6-06b-execution-reconciliation/final-gate-report.md` (ignored)

**Interfaces:**
- Consumes: all Task 1–4 source changes and the repository's offline validation targets.
- Produces: a sanitized verification receipt only; no source behavior or runtime state changes.

- [ ] **Step 1: Confirm no scope expansion before broad checks**

Run:

```bash
git diff --name-only main...HEAD
rg -n 'EventLedgerRepository|subprocess|socket|requests|httpx|threading|sqlalchemy' \
  packages/execution_sandbox/reconciliation.py
git diff --unified=0 main...HEAD -- packages/execution_sandbox/client.py | \
  rg '^[+-].*(EventLedgerRepository|subprocess|socket|requests|httpx|threading|sqlalchemy)' || true
```

Expected: `reconciliation.py` has no repository/provider/network/process/database
path. The existing 06A client retains its already-reviewed repository-backed
delivery method; the diff scan proves 06B adds none of those dependencies there.

- [ ] **Step 2: Create a fresh standalone clean candidate and bootstrap offline**

Run the repository's existing clean-candidate procedure from a new detached
clone. Use `UV_OFFLINE=1 uv sync --frozen --reinstall-package
trading-agent-control-api`, the preserved legacy frozen sync, and dashboard
`npm ci --offline` before any gate. Set a private Linux filesystem temporary
directory and umask `0022` so the known mode-sensitive source tests are not
affected by packet construction.

- [ ] **Step 3: Run gates once, fail closed, and retain raw logs privately**

Run, in this exact order:

```bash
make audit-release
make check-contracts
make check-broad-handler-inventory
make check-secrets
make test-all
make ci
git diff --check
git status --short
```

Expected: every command exits 0 and final status is empty. On the first
failure, stop; classify the candidate/environment/source failure without
rerunning in the same packet.

- [ ] **Step 4: Write sanitized final report and request independent review**

Record commit SHA, exact commands/counts, candidate identity, private-log
location, and the explicit absence of provider, broker, exchange, account,
database, and live actions. Do not include credentials, private paths, or raw
event data. Request an independent read-only review before integration; do not
merge or push from this task.

## Plan Self-Review

### Spec coverage

- Pure snapshot-plus-evidence boundary: Tasks 1–4.
- Known-report inventory required for historic original/duplicate/fill proof: Task 1 and Task 4.
- Strict canonical input and bounded error behavior: Task 2.
- Existing order reducer, queue ordering, observed/venue replay, and the three output statuses: Task 3.
- Fill identity/sequence/duplicate validation and deterministic digest: Task 4.
- Unknown-order and otherwise whole-request evidence contradictions use the
  result's unattributed IDs/reasons rather than corrupting a snapshot-order
  finding: Task 2 and Task 3.
- No repository, authority mutation, external I/O, paper/live, recovery, or legacy scope: Global Constraints plus Task 5 scope scan.
- Fresh offline clean-candidate evidence and independent-review handoff: Task 5.

### Placeholder scan

The plan contains no `TODO`, `TBD`, “implement later”, “add appropriate error
handling”, or unnamed test steps. Every task names exact files, locked public
types, red/green commands, concrete test assertions, and commit scope.

### Type consistency

All later tasks consume the names declared in **Public Interfaces Locked by
This Plan**. `SandboxKnownReport` is produced in Task 1 before the request and
replay code consume it; `SandboxReconciliationRequest`, result types, and
the public `reconcile_execution_state` entry point is introduced in Task 3
after Task 2 has established its request/result types; Task 5 changes no public
type.
