# Phase 6 06A — Deterministic Execution Sandbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Build a deterministic, in-memory execution sandbox that consumes Phase 5 submit authority immediately before a local submit and simulates canonical order/fill reports without network, process, database, paper-adapter, or live-provider access.

**Architecture:** A new packages.execution_sandbox package exposes strict scenario/request/result contracts and SandboxExecutionClient. The client owns immutable local state, an injected logical clock, a supplied event ledger, a Phase 5 safety verifier, and a closed scenario script. It reduces the existing OrderState separately for scripted venue state and for delivered observed state; it emits only existing canonical OrderEvent and FillEvent envelopes.

**Tech Stack:** Python 3.11, Pydantic 2 strict/frozen models, packages.domain order/event models, packages.event_ledger, packages.runtime_risk.submit_authority, pytest, uv, Make.

## Global Constraints

- Implement WS-06 packet 06A only. Do not add reconciliation (06B), crash/database recovery (06C), a paper adapter (06D), or legacy-execution quarantine (06E).
- Keep the package fully in-memory: no socket, HTTP, subprocess, thread, database, service, provider, Nautilus runtime, paper adapter, or live provider dependency/import.
- Do not invoke brokers, exchanges, accounts, private endpoints, providers, services, PostgreSQL, migrations, or runtime activation. Both live approvals remain false.
- Use existing OrderState/reduce_order, OrderEvent, FillEvent, EventEnvelope, EventLedgerRepository, and consume_submit_permit contracts. Do not create a competing lifecycle/authority reducer.
- All public models are strict, frozen, extra-forbid, UTC validated, and canonicalized at ingress. Scenario prices, quantities, and money use exact domain Decimal objects, never float.
- Preserve client order ID after a lost response. Do not add automatic resend/retry.
- Use TDD: focused RED, minimum GREEN, task regression, then a scoped commit. No new skips, deselects, xfails, dependencies, or lockfile edits.
- Use make generate-contracts if public schemas need generated output; never hand-edit generated contracts. Verify with make check-contracts.
- Preserve user-owned untracked files and unrelated worktrees.

---

## File Structure

| File | Responsibility |
| --- | --- |
| packages/execution_sandbox/__init__.py | Narrow public exports only, with no side-effect imports. |
| packages/execution_sandbox/models.py | Strict scenario, request, report-plan, queue, result, snapshot, enum, and bounded error contracts. |
| packages/execution_sandbox/client.py | Deterministic command execution, state reduction, report queue delivery, ledger append/read-back, and Phase 5 submit consumption. |
| tests/execution_sandbox/conftest.py | Hermetic fixed UTC/UUID/order/fill/envelope and valid Phase 5 prepared-permit fixtures. |
| tests/execution_sandbox/test_models.py | Scenario uniqueness, canonicality, immutability, and invalid-script contracts. |
| tests/execution_sandbox/test_client_lifecycle.py | Lifecycle, queue, cancel/fill, modify/fill, reconnect, and duplicate-report drills. |
| tests/execution_sandbox/test_submit_authority.py | One-shot permit, authority drift, lost-response, and no-resend drills. |
| tests/execution_sandbox/test_boundary.py | AST/import and runtime hermetic-boundary tests. |

The package must not be registered as an engine/job command, provider, service, dashboard route, or new event payload. It uses only already-registered OrderEvent and FillEvent payloads.

---

### Task 1: Strict Scenario and Client Contracts

**Files:**

- Create: packages/execution_sandbox/__init__.py
- Create: packages/execution_sandbox/models.py
- Create: tests/execution_sandbox/conftest.py
- Create: tests/execution_sandbox/test_models.py

**Interfaces:**

- Consumes: EventEnvelope, OrderEvent, FillEvent, OrderIntent, PreparedSubmitPermit, RuntimeRiskObservation, RuntimeRiskPolicy, GlobalSafetyObservation, and ConsumedSubmitAuthority.
- Produces: SandboxExecutionError, SandboxLostResponse, SandboxConnectionState, SandboxCommandKind, SandboxResponseDisposition, SandboxReportPlan, SandboxCommandPlan, SandboxScenario, SandboxSubmitRequest, SandboxModifyRequest, SandboxCancelRequest, SandboxCommandResult, SandboxOrderSnapshot, and SandboxSnapshot.
- Later tasks use these exact client operations:

~~~
class SandboxExecutionClient:
    def submit(self, request: SandboxSubmitRequest) -> SandboxCommandResult: ...
    def modify(self, request: SandboxModifyRequest) -> SandboxCommandResult: ...
    def cancel(self, request: SandboxCancelRequest) -> SandboxCommandResult: ...
    def disconnect(self, *, command_id: UUID, at: datetime) -> SandboxCommandResult: ...
    def reconnect(self, *, command_id: UUID, at: datetime) -> SandboxCommandResult: ...
    def advance_time(self, *, to: datetime) -> SandboxSnapshot: ...
    def drain_reports(self) -> tuple[EventEnvelope[object], ...]: ...
    def snapshot(self) -> SandboxSnapshot: ...
~~~

- [ ] **Step 1: Write failing model-contract tests**

Create conftest fixtures with fixed 2026-08-10 UTC time, deterministic UUIDs, a valid OrderIntent/instrument/fill/envelope, and a valid prepared permit built through the public Phase 5 approval, active halt, and prepare_submit_permit APIs. Do not import helper functions from another test module.

In test_models.py, first write these tests:

~~~
def test_scenario_requires_unique_command_and_report_ids() -> None:
    report = original_report(report_id=uid(10), event=submitted_envelope())
    with pytest.raises(ValueError, match="duplicate command_id"):
        SandboxScenario(
            command_plans=(submit_plan(uid(1), (uid(10),)),
                           submit_plan(uid(1), (uid(10),))),
            report_plans=(report,),
        )


def test_duplicate_report_references_one_prior_original_without_new_event() -> None:
    original = original_report(report_id=uid(11), event=submitted_envelope())
    duplicate = duplicate_report(report_id=uid(12), original_report_id=uid(11))
    scenario = SandboxScenario(
        command_plans=(submit_plan(uid(2), (uid(11), uid(12))),),
        report_plans=(original, duplicate),
    )
    assert scenario.report_plans[1].duplicate_of_report_id == uid(11)


def test_submit_request_rejects_naive_time() -> None:
    with pytest.raises(ValueError):
        SandboxSubmitRequest(**{**valid_submit_values(), "submitted_at": datetime(2026, 8, 10)})
~~~

Also cover: exact enum values; frozen models and extra-forbid; exactly one of an original event or duplicate reference; duplicate points only to an earlier original; command report IDs exist and occur once; concrete event payload is exactly OrderEvent/FillEvent; all report order IDs match their command's order; and SandboxLostResponse is a bounded sandbox error.

- [ ] **Step 2: Run focused RED**

Run:

~~~bash
UV_OFFLINE=1 uv run pytest -q tests/execution_sandbox/test_models.py
~~~

Expected: collection fails because packages.execution_sandbox does not exist. Record this result before production edits.

- [ ] **Step 3: Implement contracts and exports**

Create strict frozen SandboxModel with ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always"). Define:

~~~
class SandboxConnectionState(str, Enum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"

class SandboxCommandKind(str, Enum):
    SUBMIT = "SUBMIT"
    MODIFY = "MODIFY"
    CANCEL = "CANCEL"
    DISCONNECT = "DISCONNECT"
    RECONNECT = "RECONNECT"

class SandboxResponseDisposition(str, Enum):
    ACKNOWLEDGED = "ACKNOWLEDGED"
    LOST_RESPONSE = "LOST_RESPONSE"
~~~

SandboxReportPlan contains report_id, deliver_at, optional concrete EventEnvelope[object], and optional duplicate_of_report_id. Canonicalize every original envelope through existing serialize_event/deserialize_event helpers and reject any payload other than concrete OrderEvent/FillEvent.

SandboxCommandPlan contains command_id, command kind, response disposition, order_id, and an ordered nonempty tuple of report IDs. SandboxScenario validates all cross references and duplicate ordering.

Define exact request models:

~~~
class SandboxSubmitRequest(SandboxModel):
    command_id: UUID
    order_id: UUID
    order_intent: OrderIntent
    permit: PreparedSubmitPermit
    current_observation: RuntimeRiskObservation
    current_policy: RuntimeRiskPolicy
    current_safety: GlobalSafetyObservation
    consumed_event_id: UUID
    submitted_at: datetime

class SandboxModifyRequest(SandboxModel):
    command_id: UUID
    order_id: UUID
    replacement_order_intent: OrderIntent
    requested_at: datetime

class SandboxCancelRequest(SandboxModel):
    command_id: UUID
    order_id: UUID
    requested_at: datetime
~~~

Choose and test one identity rule: either require submit.order_id to equal intent_id, or keep it independent and require every planned report to use request.order_id. Do not silently translate identities. SandboxCommandResult includes command_id, response, and optional consumed authority. SandboxSnapshot must use ordered frozen tuples, never caller-owned mutable mappings. Export only these public contracts.

- [ ] **Step 4: Run focused GREEN**

Run:

~~~bash
UV_OFFLINE=1 uv run pytest -q tests/execution_sandbox/test_models.py
~~~

Expected: PASS. Forged nested instances and invalid duplicate/event plans fail through the bounded contract boundary.

- [ ] **Step 5: Run regression and commit**

Run:

~~~bash
UV_OFFLINE=1 uv run pytest -q tests/execution_sandbox/test_models.py tests/domain/test_orders.py tests/domain/test_event_contracts.py
git diff --check
~~~

Expected: PASS.

~~~bash
git add packages/execution_sandbox/__init__.py packages/execution_sandbox/models.py tests/execution_sandbox/conftest.py tests/execution_sandbox/test_models.py
git commit -m "feat: define deterministic execution sandbox contracts"
~~~

---

### Task 2: Deterministic Lifecycle, Queue, and Ledger Delivery

**Files:**

- Create: packages/execution_sandbox/client.py
- Create: tests/execution_sandbox/test_client_lifecycle.py
- Modify: packages/execution_sandbox/__init__.py

**Interfaces:**

- Consumes: Task 1 contracts, EventLedgerRepository, OrderState, reduce_order, existing event envelope canonicalization, and OutboxIntent.
- Produces: SandboxExecutionClient constructor:

~~~
SandboxExecutionClient(
    *,
    repository: EventLedgerRepository,
    safety_verifier: GlobalSafetyAuthorityVerifier,
    scenario: SandboxScenario,
    initial_time: datetime,
)
~~~

- Later tasks rely on original reports changing venue OrderState when a scripted command executes, and drain_reports changing observed OrderState only after exact report delivery.

- [ ] **Step 1: Write lifecycle RED tests**

Use canonical existing OrderEvent/FillEvent envelope fixtures. The accepting submit script includes submitted then accepted reports; a fill script includes a matching order lifecycle event before its FillEvent.

~~~
def test_delayed_acceptance_advances_venue_not_observed_until_drain() -> None:
    client = client_for(scenario_with_delayed_accept(NOW + timedelta(seconds=2)))
    client.submit(valid_submit_request())
    assert client.snapshot().orders[0].venue_state.status is OrderStatus.ACCEPTED
    assert client.snapshot().orders[0].observed_state.status is OrderStatus.INITIALIZED
    client.advance_time(to=NOW + timedelta(seconds=2))
    client.drain_reports()
    assert client.snapshot().orders[0].observed_state.status is OrderStatus.ACCEPTED


def test_duplicate_delivery_reuses_exact_envelope_and_is_idempotent() -> None:
    client = client_for(scenario_with_original_and_delayed_duplicate())
    client.submit(valid_submit_request())
    first = client.drain_reports()
    client.advance_time(to=NOW + timedelta(seconds=1))
    second = client.drain_reports()
    assert serialize_event(second[0]) == serialize_event(first[-1])
    assert client.snapshot().orders[0].observed_state.last_sequence == 2


def test_fill_before_ack_uses_existing_reducer_edge() -> None:
    client = client_for(scenario_with_submitted_then_partial_fill_before_accept())
    client.submit(valid_submit_request())
    client.drain_reports()
    assert client.snapshot().orders[0].observed_state.status is OrderStatus.PARTIALLY_FILLED
~~~

Add table-driven reject, partial/full fill quantity progression, forbidden transition, altered duplicate conflict, FIFO reports at equal delivery time, backwards clock, and bad ledger stream-sequence cases. Add cancel/fill and modify/fill cases that differ only by declared plan order.

- [ ] **Step 2: Run lifecycle RED**

Run:

~~~bash
UV_OFFLINE=1 uv run pytest -q tests/execution_sandbox/test_client_lifecycle.py
~~~

Expected: FAIL because SandboxExecutionClient is absent.

- [ ] **Step 3: Implement pure state and delivery**

Canonicalize initial_time, scenario, requests, and queued envelopes at every public boundary. Store only ordered tuple state; use temporary local maps only to build the next snapshot.

For original reports, reduce their OrderEvent into venue state at command time and queue their envelope. Do not change observed state. advance_time validates UTC and to >= current time. drain_reports selects due reports in (deliver_at, insertion_ordinal) order while connected, appends each envelope with an OutboxIntent referencing the same event ID, demands exact canonical append/read-back semantics, and then reduces OrderEvent into observed state.

For a duplicate plan, load retained original canonical envelope bytes; never reconstruct it from mutable command input. Identical ledger retries do not advance observed sequence. Changed content with existing ID, malformed append outcome, unknown order ID, or invalid transition raises SandboxExecutionError and retains the pre-call snapshot.

disconnect/reconnect must be scenario-gated. While disconnected, submit/modify/cancel/drain_reports all fail without mutation. Reconnect only restores connection; it does not drain or repair.

- [ ] **Step 4: Run lifecycle GREEN**

Run:

~~~bash
UV_OFFLINE=1 uv run pytest -q tests/execution_sandbox/test_client_lifecycle.py
~~~

Expected: PASS. Confirm delayed/duplicate envelopes are byte-identical and invalid operations preserve the immutable snapshot.

- [ ] **Step 5: Run regression and commit**

Run:

~~~bash
UV_OFFLINE=1 uv run pytest -q tests/execution_sandbox/test_models.py tests/execution_sandbox/test_client_lifecycle.py tests/domain/test_orders.py tests/domain/test_event_contracts.py tests/event_ledger/test_repository.py
git diff --check
~~~

Expected: PASS.

~~~bash
git add packages/execution_sandbox/client.py packages/execution_sandbox/__init__.py tests/execution_sandbox/test_client_lifecycle.py
git commit -m "feat: simulate deterministic execution lifecycle"
~~~

---

### Task 3: Phase 5 Submit Authority and Ambiguous Response Drills

**Files:**

- Modify: packages/execution_sandbox/client.py
- Modify: packages/execution_sandbox/models.py
- Modify: tests/execution_sandbox/conftest.py
- Create: tests/execution_sandbox/test_submit_authority.py

**Interfaces:**

- Consumes: SandboxExecutionClient, SandboxSubmitRequest, PreparedSubmitPermit, ConsumedSubmitAuthority, consume_submit_permit, and Task 1 fixtures.
- Produces: submit results with the exact ConsumedSubmitAuthority and SandboxLostResponse with no retry capability.

- [ ] **Step 1: Write authority RED tests**

~~~
def test_submit_consumes_exact_permit_before_creating_venue_order() -> None:
    case = prepared_case()
    client = client_for_submit(case)
    result = client.submit(submit_request(case))
    assert result.consumed_authority is not None
    assert result.consumed_authority.permit_id == case.permit.permit_id
    assert any(type(event.payload) is SubmitPermitConsumed for event in case.ledger.load_events())
    assert client.snapshot().orders[0].order_id == submit_request(case).order_id


def test_failed_consumption_creates_no_order_report_or_response() -> None:
    case = prepared_case(with_expired_permit=True)
    client = client_for_submit(case)
    with pytest.raises(SandboxExecutionError):
        client.submit(submit_request(case))
    assert client.snapshot().orders == ()
    assert client.snapshot().queued_reports == ()


def test_lost_response_never_resends_or_reuses_permit() -> None:
    case = prepared_case()
    client = client_for(scenario_with_lost_submit_response(), case)
    with pytest.raises(SandboxLostResponse):
        client.submit(submit_request(case))
    with pytest.raises(SandboxExecutionError):
        client.submit(submit_request(case))
    assert client.snapshot().orders[0].client_order_id == case.intent.client_order_id
~~~

Also cover altered permit, rotated/stale safety, halted authority, duplicate client-order ID, lost response after acceptance, delayed report only after reconnect, cancel/fill, and modify/fill.

- [ ] **Step 2: Run authority RED**

Run:

~~~bash
UV_OFFLINE=1 uv run pytest -q tests/execution_sandbox/test_submit_authority.py
~~~

Expected: FAIL because submit does not yet call the Phase 5 consumption boundary and cannot model a lost response.

- [ ] **Step 3: Implement authoritative submit**

Use this exact effect order:

~~~
request = _canonical_submit_request(request)
plan = _require_plan(request.command_id, SandboxCommandKind.SUBMIT)
_require_connected_and_unused_client_order_id(request.order_intent.client_order_id)
_validate_all_planned_reports_before_effect(plan, request)
authority = consume_submit_permit(
    repository=self._repository,
    permit=request.permit,
    current_observation=request.current_observation,
    current_policy=request.current_policy,
    current_safety=request.current_safety,
    safety_verifier=self._safety_verifier,
    consumed_event_id=request.consumed_event_id,
    consumed_at=request.submitted_at,
)
_apply_venue_reports_and_enqueue(plan, request.order_id)
if plan.response_disposition is SandboxResponseDisposition.LOST_RESPONSE:
    raise SandboxLostResponse("sandbox response was intentionally lost")
return SandboxCommandResult(
    command_id=request.command_id,
    response=plan.response_disposition,
    consumed_authority=authority,
)
~~~

Wrap only the documented Phase 5 canonical/repository/consumption boundary errors in SandboxExecutionError. Never catch broad Exception. Never retry consumption or replay the command after SandboxLostResponse.

modify/cancel require connection, nonterminal target order, and matching scenario plan. Modify retains current accepted intent until scripted PENDING_UPDATE -> ACCEPTED is valid; cancel preserves existing cancel/fill race edges. Neither gains a transport, a permit reuse, or an implicit retry in 06A.

- [ ] **Step 4: Run authority GREEN**

Run:

~~~bash
UV_OFFLINE=1 uv run pytest -q tests/execution_sandbox/test_submit_authority.py tests/execution_sandbox/test_client_lifecycle.py
~~~

Expected: PASS. Every lost response has exactly one consumed permit and no second submit/report.

- [ ] **Step 5: Run regression and commit**

Run:

~~~bash
UV_OFFLINE=1 uv run pytest -q tests/execution_sandbox tests/runtime_risk/test_submit_authority.py tests/runtime_risk/test_submit_authority_drills.py tests/domain/test_orders.py tests/domain/test_event_contracts.py tests/event_ledger/test_repository.py
git diff --check
~~~

Expected: PASS with no Phase 5 behaviour change.

~~~bash
git add packages/execution_sandbox/client.py packages/execution_sandbox/models.py tests/execution_sandbox/conftest.py tests/execution_sandbox/test_submit_authority.py
git commit -m "feat: bind sandbox submit to one-shot authority"
~~~

---

### Task 4: Hermetic Boundary Proof and 06A Exit Gate

**Files:**

- Create: tests/execution_sandbox/test_boundary.py
- Modify: tests/execution_sandbox/test_models.py
- Modify: tests/execution_sandbox/test_client_lifecycle.py
- Modify: tests/execution_sandbox/test_submit_authority.py

**Interfaces:**

- Consumes: complete sandbox package and existing order/event/ledger contracts.
- Produces: deterministic, in-memory, source-only proof. No new production API.

- [ ] **Step 1: Write final boundary RED tests**

AST-inspect only packages/execution_sandbox and reject imports of socket, ssl, http, urllib, requests, websockets, subprocess, asyncio, threading, sqlalchemy, psycopg, packages.runtime_release, and packages.nautilus_*.

Also monkeypatch socket/process constructors to fail while running a complete submit/partial-fill/duplicate/lost-response scenario:

~~~
def test_equivalent_runs_produce_identical_snapshots_and_event_bytes() -> None:
    left = run_complete_script(fresh_prepared_case())
    right = run_complete_script(fresh_prepared_case())
    assert left.snapshot == right.snapshot
    assert [serialize_event(event) for event in left.delivered] == [
        serialize_event(event) for event in right.delivered
    ]


def test_disconnected_client_has_no_command_backdoor() -> None:
    client = client_for(disconnect_then_reconnect_scenario())
    client.disconnect(command_id=uid(70), at=NOW)
    before = client.snapshot()
    for operation in (client.submit, client.modify, client.cancel):
        with pytest.raises(SandboxExecutionError):
            invoke(operation)
        assert client.snapshot() == before
~~~

Add immutable snapshot, backwards-clock, report-consumed-at-most-as-declared, and no engine/job/dashboard/provider registration checks.

- [ ] **Step 2: Run boundary RED**

Run:

~~~bash
UV_OFFLINE=1 uv run pytest -q tests/execution_sandbox/test_boundary.py
~~~

Expected: FAIL until imports, deterministic replay, and immutable boundary handling meet the contract.

- [ ] **Step 3: Make minimal hermetic fixes**

Remove forbidden imports and implicit I/O. Use only initial_time, advance_time, and request timestamps; do not introduce datetime.now, time.time, random UUIDs, threads, async scheduling, skip/xfail/allowlist changes, or mocks that avoid the sandbox path. Canonicalize/sort all returned snapshot/report structures by declared order.

- [ ] **Step 4: Run focused 06A GREEN**

Run:

~~~bash
UV_OFFLINE=1 uv run pytest -q tests/execution_sandbox
~~~

Expected: PASS, with every 06A drill local and no service/runtime prerequisite.

- [ ] **Step 5: Run final relevant gates and commit**

Run:

~~~bash
UV_OFFLINE=1 make generate-contracts
UV_OFFLINE=1 make check-contracts
UV_OFFLINE=1 uv run pytest -q tests/execution_sandbox tests/domain/test_orders.py tests/domain/test_event_contracts.py tests/event_ledger/test_repository.py tests/event_ledger/test_reducer.py tests/runtime_risk/test_submit_authority.py tests/runtime_risk/test_submit_authority_drills.py
UV_OFFLINE=1 make check-broad-handler-inventory
UV_OFFLINE=1 make check-secrets
git diff --check
git status --short
~~~

Expected: generator output is either correctly produced or unchanged; all tests and static gates pass; status contains only intended 06A files.

~~~bash
git add packages/execution_sandbox tests/execution_sandbox
git commit -m "test: verify deterministic sandbox boundary"
~~~

---

## Plan Self-Review

### Spec coverage

- Submit/accept/reject/partial/full fill/cancel/modify: Tasks 1–3.
- Disconnect/reconnect, delayed reports, duplicate reports, lost responses: Tasks 2–4.
- Immediate Phase 5 permit consumption and no resend: Task 3.
- Separate venue/observed states, canonical envelopes, ledger idempotency, logical clock, immutable snapshots, and race ordering: Task 2.
- No network/process/database/paper/live dependency: Task 4.
- No 06B–06E scope: Global Constraints and every task boundary.

### Placeholder scan

No placeholder, generic error-handling instruction, unbounded test step, or undefined interface is used. Each task includes exact files, public names, focused RED, GREEN, regression, and commit commands.

### Type consistency

Tasks 2–4 use the exact Task 1 model names and client signatures. Task 2 creates the client. Task 3 restricts Phase 5 consumption to submit, leaving modify/cancel as explicitly local deterministic lifecycle commands. Task 4 adds no production interface.
