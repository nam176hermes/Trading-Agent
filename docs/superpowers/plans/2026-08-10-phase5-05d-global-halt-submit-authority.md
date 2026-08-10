# Phase 5 05D Global Halt and Submit Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete WS-05 with a replayable global halt authority and one-shot submit authority that rejects loss, drawdown, safety, persistence, and evaluation-to-consume races.

**Architecture:** One dedicated event-ledger stream serializes global halt transitions, permit preparation, and permit consumption. Pure breaker logic derives halt causes from the reviewed 05C observation/policy and canonical safety evidence; durable entrypoints append canonical typed events with outbox intents, read them back exactly, and return content-addressed references only after verification.

**Tech Stack:** Python 3.11, Pydantic 2 strict immutable models, the existing event-ledger repository/outbox boundary, exact Decimal/Fraction arithmetic, pytest, generated JSON Schema, `uv`, and root Make gates.

## Global Constraints

- Implement global halt only; do not add strategy, instrument, venue, account, or asset-class kill-switch enforcement.
- Keep both live-execution approvals false and perform no provider, broker, exchange, account, order, withdrawal, or private-endpoint action.
- Do not write, move, replace, or reinterpret the canonical `.kill_switch`; absence stays `INACTIVE`, unsafe evidence stays `UNKNOWN`.
- Do not add a dependency, database table, migration, runtime service, dashboard mutation, release-authority change, or deployment behavior.
- Use the existing event-ledger event/outbox append boundary; all 05D authority events share one dedicated stream and contiguous sequence.
- There is no implicit active state. An empty stream must receive one durable initialization transition.
- Daily loss breaches only when `daily_pnl < -max_daily_loss`; drawdown breaches only when `max(peak_equity - current_equity, 0) > max_drawdown`.
- Kill-switch `ACTIVE` and `UNKNOWN` both halt; `INACTIVE` contributes no breaker reason.
- Recovery is explicit, externally verified, current, unexpired, and rotates generation. Safe facts alone never recover a halted state.
- Prepared permits expire exactly five seconds after `prepared_at` and are one-shot.
- Current policy, observation, portfolio, and durable 05C approval bindings must match exactly; drift requires a new 05C approval.
- Safety re-attestation binds source fingerprint plus resolved state, excludes the read timestamp from the binding digest, and requires a newer consume-time read with the same binding.
- Narrowly translate repository/canonicalization faults into bounded public errors; do not mask unrelated programming errors.
- No test may be skipped, deselected, weakened, or made environment-dependent to obtain a pass.
- Every task starts with an observed RED, reaches GREEN, is committed, and receives an independent SPEC/QUALITY review before the next task.

---

## File and Interface Map

- `packages/domain/runtime_halt.py`: strict public 05D enums, transition payloads, replay state, recovery authorization, prepared/consumed event payloads, and returned content-addressed references.
- `packages/runtime_risk/safety.py`: read-only conversion from the existing canonical safety resolver into `GlobalSafetyObservation`.
- `packages/runtime_risk/halt.py`: exact breaker arithmetic, halt event construction, replay, initialization, halt, and recovery durability.
- `packages/runtime_risk/submit_authority.py`: durable 05C re-verification, permit preparation, consume-time re-attestation, one bounded cause-checked sequence retry, and replay audit.
- `packages/domain/events.py`: register the three new event payload types without changing existing event semantics.
- `packages/domain/__init__.py` and `packages/runtime_risk/__init__.py`: explicit public exports only.
- `scripts/generate_contracts.py`: include new public models in deterministic schema generation.
- `generated/domain/json-schema/*.json`: generated only by the repository generator.
- `tests/domain/test_runtime_halt_contracts.py`: strict contract, canonical identity, forgery, schema, timeline, and copy tests.
- `tests/runtime_risk/test_global_halt.py`: breaker, initialization, transition, recovery, replay, safety, and persistence tests.
- `tests/runtime_risk/test_submit_authority.py`: prepare, consume, expiry, drift, idempotency, failure, and race tests.
- `tests/runtime_risk/test_submit_authority_drills.py`: restart audit, long race matrix, forged stream, and bounded failure drills.

---

### Task 1: Define Strict 05D Contracts and Generated Schemas

**Files:**
- Create: `packages/domain/runtime_halt.py`
- Modify: `packages/domain/__init__.py`
- Modify: `scripts/generate_contracts.py`
- Create: `tests/domain/test_runtime_halt_contracts.py`
- Modify: `tests/domain/test_contract_generation.py`
- Generate: `generated/domain/json-schema/GlobalSafetyObservation.json`
- Generate: `generated/domain/json-schema/GlobalHaltState.json`
- Generate: `generated/domain/json-schema/GlobalHaltRecoveryAuthorization.json`
- Generate: `generated/domain/json-schema/GlobalHaltTransition.json`
- Generate: `generated/domain/json-schema/SubmitPermitPrepared.json`
- Generate: `generated/domain/json-schema/PreparedSubmitPermit.json`
- Generate: `generated/domain/json-schema/SubmitPermitConsumed.json`
- Generate: `generated/domain/json-schema/ConsumedSubmitAuthority.json`

**Interfaces:**
- Consumes: `RuntimeRiskModel`, `RuntimeRiskObservation`, `RuntimeRiskPolicy`, `DurableOrderApprovalRef`, `CanonicalKillSwitchState`, `Sha256`, `UUID`, and UTC clock validation.
- Produces: `GlobalHaltStatus`, `GlobalHaltReasonCode`, `GlobalSafetyObservation`, `GlobalHaltState`, `GlobalHaltRecoveryAuthorization`, `GlobalHaltTransition`, `SubmitPermitPrepared`, `PreparedSubmitPermit`, `SubmitPermitConsumed`, and `ConsumedSubmitAuthority`.

- [ ] **Step 1: Write the contract RED tests**

Create table-driven tests that import every public type, construct one exact valid instance of each, and assert strict failures for extra fields, string-to-int coercion, naive timestamps, invalid digest text, zero/non-monotonic generations, duplicate/reordered reasons, circular event self-digests, invalid status/reason combinations, permit expiry not equal to five seconds, and consumed/prepared binding mismatch.

Use fixed identities and an explicit valid payload shape:

```python
NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)

def safety_observation() -> GlobalSafetyObservation:
    return GlobalSafetyObservation(
        source_fingerprint="1" * 64,
        kill_switch_state=CanonicalKillSwitchState.INACTIVE,
        observed_at=NOW,
        schema_version="global-safety-observation-v1",
    )

def prepared_payload() -> SubmitPermitPrepared:
    return SubmitPermitPrepared(
        permit_id=UUID(int=101),
        approval_event_id=UUID(int=102),
        approval_reference_digest="2" * 64,
        intent_digest="3" * 64,
        policy_risk_decision_digest="4" * 64,
        runtime_risk_decision_digest="5" * 64,
        runtime_policy_digest="6" * 64,
        runtime_observation_digest="7" * 64,
        portfolio_digest="8" * 64,
        safety_binding_digest="9" * 64,
        halt_stream_id=UUID(int=103),
        halt_generation=3,
        halt_transition_event_id=UUID(int=104),
        halt_transition_digest="a" * 64,
        prepared_at=NOW,
        expires_at=NOW + timedelta(seconds=5),
        schema_version="submit-permit-prepared-v1",
    )
```

Assert `model_copy(update=forged_update)` and
`model_construct(state="forged")` cannot bypass nested strict validation, and
`model_copy(deep=True)` preserves canonical currency/safety enum identities.

- [ ] **Step 2: Run the contract tests and record RED**

Run:

```bash
UV_OFFLINE=1 uv run pytest -q \
  tests/domain/test_runtime_halt_contracts.py \
  tests/domain/test_contract_generation.py \
  -k 'runtime_halt or GlobalHalt or SubmitPermit'
```

Expected: collection fails because `packages.domain.runtime_halt` and the generated schema entries do not exist. Record the exact failure in the Task 1 report before writing production code.

- [ ] **Step 3: Implement the strict domain models**

Use literal schema versions and explicit post-init invariants. Keep envelope IDs/digests out of their own event payloads:

```python
class GlobalHaltStatus(str, Enum):
    ACTIVE = "ACTIVE"
    HALTED = "HALTED"


class GlobalHaltReasonCode(str, Enum):
    SAFETY_AUTHORITY_UNKNOWN = "SAFETY_AUTHORITY_UNKNOWN"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    DRAWDOWN_LIMIT = "DRAWDOWN_LIMIT"
    RECOVERY_AUTHORIZED = "RECOVERY_AUTHORIZED"
    INITIALIZED_SAFE = "INITIALIZED_SAFE"


_HALT_REASON_ORDER = {
    reason: index for index, reason in enumerate(GlobalHaltReasonCode)
}


class GlobalSafetyObservation(RuntimeRiskModel):
    source_fingerprint: Sha256
    kill_switch_state: CanonicalKillSwitchState
    observed_at: datetime
    schema_version: Literal["global-safety-observation-v1"]


class SubmitPermitPrepared(RuntimeRiskModel):
    permit_id: UUID
    approval_event_id: UUID
    approval_reference_digest: Sha256
    intent_digest: Sha256
    policy_risk_decision_digest: Sha256
    runtime_risk_decision_digest: Sha256
    runtime_policy_digest: Sha256
    runtime_observation_digest: Sha256
    portfolio_digest: Sha256
    safety_binding_digest: Sha256
    halt_stream_id: UUID
    halt_generation: Annotated[StrictInt, Field(gt=0)]
    halt_transition_event_id: UUID
    halt_transition_digest: Sha256
    prepared_at: datetime
    expires_at: datetime
    schema_version: Literal["submit-permit-prepared-v1"]

    def model_post_init(self, __context: Any) -> None:
        if not _is_complete(self):
            return
        require_utc(self.prepared_at)
        require_utc(self.expires_at)
        if self.expires_at - self.prepared_at != timedelta(seconds=5):
            raise ValueError("submit permit lifetime must be exactly five seconds")
```

Implement equivalent complete invariants for all models, including:

- generation one has no prior transition; later generations require both prior ID and digest;
- `ACTIVE` initialization uses only `INITIALIZED_SAFE`;
- `HALTED` transitions contain at least one breaker reason and never contain recovery/init reasons;
- recovery transition uses only `RECOVERY_AUTHORIZED` and a recovery digest;
- returned references add containing envelope IDs/digests but cannot disagree with their payload bindings;
- consumed payload/reference bind the same permit, prepared digest, generation, and transition digest.

- [ ] **Step 4: Export models and generate schemas**

Add explicit imports/`__all__` entries and register every public model in `DOMAIN_SCHEMA_MODELS`. Run the generator rather than editing JSON:

```bash
UV_OFFLINE=1 uv run python scripts/generate_contracts.py
UV_OFFLINE=1 uv run python scripts/generate_contracts.py --check
```

Add exact schema-file and invariant assertions to `test_contract_generation.py`, including enum values, digest regexes, `exclusiveMinimum` generation bounds, fixed schema versions, and required sets.

- [ ] **Step 5: Run Task 1 GREEN and regressions**

Run:

```bash
UV_OFFLINE=1 uv run pytest -q \
  tests/domain/test_runtime_halt_contracts.py \
  tests/domain/test_runtime_risk_contracts.py \
  tests/domain/test_contract_generation.py \
  tests/domain/test_event_contracts.py
UV_OFFLINE=1 uv run python scripts/generate_contracts.py --check
make check-broad-handler-inventory
make check-secrets
git diff --check
```

Expected: all pass with no database, provider, runtime, or network action.

- [ ] **Step 6: Commit Task 1**

```bash
git add packages/domain/runtime_halt.py packages/domain/__init__.py \
  scripts/generate_contracts.py generated/domain/json-schema \
  tests/domain/test_runtime_halt_contracts.py \
  tests/domain/test_contract_generation.py
git commit -m "feat: define global halt authority contracts"
```

Dispatch an independent Task 1 SPEC/QUALITY review before Task 2.

---

### Task 2: Implement Global Breaker, Durable Transitions, Recovery, and Replay

**Files:**
- Create: `packages/runtime_risk/safety.py`
- Create: `packages/runtime_risk/halt.py`
- Modify: `packages/runtime_risk/__init__.py`
- Modify: `packages/domain/events.py`
- Create: `tests/runtime_risk/test_global_halt.py`
- Modify: `tests/domain/test_event_contracts.py`
- Modify: `tests/event_ledger/test_replay.py`
- Modify: `tests/event_ledger/test_repository.py`

**Interfaces:**
- Consumes: every Task 1 contract, `RuntimeRiskObservation`, `RuntimeRiskPolicy`, `EventEnvelope`, `EventLedgerRepository`, `OutboxIntent`, `resolve_kill_switch`, `safety_source_fingerprint`, and canonical runtime-risk digest helpers.
- Produces:
  - `observe_global_safety(*, source_root: Path, observed_at: datetime) -> GlobalSafetyObservation`
  - `global_safety_binding_digest(safety: GlobalSafetyObservation) -> str`
  - `evaluate_global_breaker(*, observation: RuntimeRiskObservation, policy: RuntimeRiskPolicy, safety: GlobalSafetyObservation) -> tuple[GlobalHaltReasonCode, ...]`
  - `replay_global_halt_authority(*, events: tuple[EventEnvelope[object], ...], stream_id: UUID) -> GlobalHaltReplay`
  - `record_global_halt_observation(*, repository: EventLedgerRepository, stream_id: UUID, observation: RuntimeRiskObservation, policy: RuntimeRiskPolicy, safety: GlobalSafetyObservation, transition_id: UUID, event_id: UUID, decided_at: datetime) -> GlobalHaltState`
  - `recover_global_halt(*, repository: EventLedgerRepository, stream_id: UUID, observation: RuntimeRiskObservation, policy: RuntimeRiskPolicy, safety: GlobalSafetyObservation, authorization: GlobalHaltRecoveryAuthorization, verifier: GlobalHaltRecoveryAuthorityVerifier, transition_id: UUID, event_id: UUID, decided_at: datetime) -> GlobalHaltState`
  - `GlobalHaltRecoveryAuthorityVerifier.verify(*, authorization: GlobalHaltRecoveryAuthorization, state: GlobalHaltState, observation: RuntimeRiskObservation, policy: RuntimeRiskPolicy, safety: GlobalSafetyObservation, verified_at: datetime) -> GlobalHaltRecoveryAuthorization`
  - bounded `GlobalHaltAuthorityError` and `GlobalHaltRecoveryError`.

- [ ] **Step 1: Write breaker, replay, and recovery RED tests**

Create exact tables for threshold semantics and hostile Decimal contexts:

```python
@pytest.mark.parametrize(
    ("daily_pnl", "current_equity", "expected"),
    [
        ("-99", "901", ()),
        ("-100", "900", ()),
        ("-100.000001", "899.999999", (GlobalHaltReasonCode.DAILY_LOSS_LIMIT,)),
    ],
)
def test_global_breaker_daily_loss_boundary(
    daily_pnl: str,
    current_equity: str,
    expected: tuple[GlobalHaltReasonCode, ...],
) -> None:
    observation = runtime_observation(
        daily_pnl=money(daily_pnl),
        current_equity=money(current_equity),
        peak_equity=money("1000"),
    )
    assert evaluate_global_breaker(
        observation=observation,
        policy=runtime_policy(max_daily_loss=money("100"), max_drawdown=money("1000")),
        safety=safety_observation(),
    ) == expected
```

Add RED tests for:

- kill switch inactive/active/unknown and multi-cause canonical ordering;
- safe and unsafe generation-one initialization;
- active-safe no append, active-breach halt, repeated halt no generation change;
- replay restart equality and strict stream filtering;
- gaps, duplicate sequence, wrong prior digest, foreign payload, impossible transition;
- missing/expired/stale/forged recovery and exact valid recovery;
- verifier protocol raising, returning wrong type, or returning altered authority;
- append/outbox/read-back/canonical byte mismatch and trusted byte-identical replica;
- safety sentinel absent, valid private file, symlink, public mode, malformed line, and source fingerprint.

- [ ] **Step 2: Run Task 2 RED**

```bash
UV_OFFLINE=1 uv run pytest -q \
  tests/runtime_risk/test_global_halt.py \
  tests/domain/test_event_contracts.py \
  tests/event_ledger/test_replay.py \
  tests/event_ledger/test_repository.py \
  -k 'global_halt or GlobalHalt'
```

Expected: collection fails because the safety/halt modules, event registrations,
and public functions do not exist.

- [ ] **Step 3: Implement exact safety and breaker logic**

Use exact Fraction arithmetic rather than ambient Decimal operations:

```python
def evaluate_global_breaker(
    *,
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    safety: GlobalSafetyObservation,
) -> tuple[GlobalHaltReasonCode, ...]:
    observation = _canonical(observation, RuntimeRiskObservation, "observation")
    policy = _canonical(policy, RuntimeRiskPolicy, "policy")
    safety = _canonical(safety, GlobalSafetyObservation, "safety")
    reporting = observation.portfolio.reporting_currency
    if any(
        value.currency is not reporting
        for value in (
            observation.daily_pnl,
            observation.current_equity,
            observation.peak_equity,
            policy.max_daily_loss,
            policy.max_drawdown,
        )
    ):
        raise GlobalHaltAuthorityError("global halt accounting authority is invalid")
    drawdown = max(
        Fraction(observation.peak_equity.amount)
        - Fraction(observation.current_equity.amount),
        Fraction(0),
    )
    checks = (
        (GlobalHaltReasonCode.SAFETY_AUTHORITY_UNKNOWN,
         safety.kill_switch_state is CanonicalKillSwitchState.UNKNOWN),
        (GlobalHaltReasonCode.KILL_SWITCH_ACTIVE,
         safety.kill_switch_state is CanonicalKillSwitchState.ACTIVE),
        (GlobalHaltReasonCode.DAILY_LOSS_LIMIT,
         Fraction(observation.daily_pnl.amount)
         < -Fraction(policy.max_daily_loss.amount)),
        (GlobalHaltReasonCode.DRAWDOWN_LIMIT,
         drawdown > Fraction(policy.max_drawdown.amount)),
    )
    return tuple(reason for reason, failed in checks if failed)
```

`observe_global_safety` must call only the existing resolver/fingerprint and
must never write the root or sentinel.

- [ ] **Step 4: Register events and implement replay**

Register `GlobalHaltTransition`, `SubmitPermitPrepared`, and
`SubmitPermitConsumed` in `EVENT_TYPE_BY_PAYLOAD`. Replay must:

```python
@dataclass(frozen=True)
class GlobalHaltReplay:
    state: GlobalHaltState | None
    prepared: tuple[PreparedSubmitPermit, ...]
    consumed_permit_ids: tuple[UUID, ...]
    head_sequence: int
    head_event_id: UUID | None
    head_event_digest: str | None
```

Canonicalize each envelope, select the exact dedicated stream, require
contiguous sequence, reject foreign event types, derive transition event
IDs/digests from envelopes rather than payload self-reference, and reject every
lineage or permit-state contradiction.

- [ ] **Step 5: Implement durable initialize/halt/recovery**

Use helpers equivalent to 05C approval: canonical event construction, fixed
outbox topics, append boundary, load exactly one event, canonical bytes/digest
comparison, and bounded errors.

`record_global_halt_observation` must:

- initialize empty streams safe or halted;
- retain active-safe and repeated-halted state without an append;
- append exactly one generation rotation for active breaches;
- retain the last transition's evidence digests as lineage without requiring
  safe current facts to equal historical transition facts.

`recover_global_halt` must call the injected verifier, re-canonicalize its
return, check current halted generation/digest, exact safe bindings and expiry,
then append/read back one recovery transition.

- [ ] **Step 6: Run Task 2 GREEN and regressions**

```bash
UV_OFFLINE=1 uv run pytest -q \
  tests/runtime_risk/test_global_halt.py \
  tests/runtime_risk/test_evaluator.py \
  tests/domain/test_runtime_halt_contracts.py \
  tests/domain/test_event_contracts.py \
  tests/event_ledger \
  tests/portfolio_reducer
UV_OFFLINE=1 uv run python scripts/generate_contracts.py --check
make check-broad-handler-inventory
make check-secrets
git diff --check
```

- [ ] **Step 7: Commit Task 2**

```bash
git add packages/runtime_risk/safety.py packages/runtime_risk/halt.py \
  packages/runtime_risk/__init__.py packages/domain/events.py \
  tests/runtime_risk/test_global_halt.py tests/domain/test_event_contracts.py \
  tests/event_ledger/test_replay.py tests/event_ledger/test_repository.py
git commit -m "feat: persist global halt transitions"
```

Dispatch an independent Task 2 SPEC/QUALITY review before Task 3.

---

### Task 3: Prepare Durable Submit Permits

**Files:**
- Create: `packages/runtime_risk/submit_authority.py`
- Modify: `packages/runtime_risk/__init__.py`
- Create: `tests/runtime_risk/test_submit_authority.py`
- Modify: `tests/event_ledger/test_replay.py`
- Modify: `tests/event_ledger/test_repository.py`

**Interfaces:**
- Consumes: the reviewed Task 2 replay/authority functions, `verify_durable_order_approval`, canonical digest helpers, original 05C inputs, exact current safety, and `EventLedgerRepository`.
- Produces:
  - `prepare_submit_permit(*, repository: EventLedgerRepository, halt_stream_id: UUID, approval_reference: DurableOrderApprovalRef, intent: OrderIntent, policy_decision: RiskDecision, approval_observation: RuntimeRiskObservation, approval_policy: RuntimeRiskPolicy, current_observation: RuntimeRiskObservation, current_policy: RuntimeRiskPolicy, current_safety: GlobalSafetyObservation, permit_id: UUID, event_id: UUID, prepared_at: datetime) -> PreparedSubmitPermit`
  - bounded `SubmitPermitPreparationError`.

- [ ] **Step 1: Write permit-preparation RED tests**

The happy path must build a complete 05C approval event/reference in one
in-memory ledger, initialize the dedicated halt stream active, and assert the
prepared permit binds every exact digest and has a five-second lifetime.

Add one-mutation tests for:

- missing, rejected, forged, conflicting, duplicated, or wrong-ledger 05C
  approval;
- changed intent, target-policy decision, observation, runtime policy,
  portfolio, safety state, source fingerprint, or halt generation;
- safety observed after `prepared_at`, while preserving a stable binding digest
  that excludes only the read timestamp;
- uninitialized or halted stream;
- expired/malformed decision authority;
- append/outbox/read-back failure and wrong prepared event bytes;
- same-ID byte-identical idempotent prepare and same-ID conflicting prepare;
- returned reference event ID/digest matching the containing envelope rather
  than payload self-reference.

- [ ] **Step 2: Run Task 3 RED**

```bash
UV_OFFLINE=1 uv run pytest -q tests/runtime_risk/test_submit_authority.py \
  -k 'prepare_submit_permit'
```

Expected: collection fails because `prepare_submit_permit` and its bounded error
do not exist.

- [ ] **Step 3: Implement canonical prepare flow**

The entrypoint order is part of the contract:

```python
def prepare_submit_permit(
    *,
    repository: EventLedgerRepository,
    halt_stream_id: UUID,
    approval_reference: DurableOrderApprovalRef,
    intent: OrderIntent,
    policy_decision: RiskDecision,
    approval_observation: RuntimeRiskObservation,
    approval_policy: RuntimeRiskPolicy,
    current_observation: RuntimeRiskObservation,
    current_policy: RuntimeRiskPolicy,
    current_safety: GlobalSafetyObservation,
    permit_id: UUID,
    event_id: UUID,
    prepared_at: datetime,
) -> PreparedSubmitPermit:
    reference = _canonical_model(
        approval_reference, DurableOrderApprovalRef, "approval_reference"
    )
    intent = _canonical_model(intent, OrderIntent, "intent")
    policy_decision = _canonical_model(
        policy_decision, RiskDecision, "policy_decision"
    )
    approval_observation = _canonical_model(
        approval_observation, RuntimeRiskObservation, "approval_observation"
    )
    approval_policy = _canonical_model(
        approval_policy, RuntimeRiskPolicy, "approval_policy"
    )
    current_observation = _canonical_model(
        current_observation, RuntimeRiskObservation, "current_observation"
    )
    current_policy = _canonical_model(
        current_policy, RuntimeRiskPolicy, "current_policy"
    )
    current_safety = _canonical_model(
        current_safety, GlobalSafetyObservation, "current_safety"
    )
    verified_decision = verify_durable_order_approval(
        repository=repository,
        reference=reference,
        intent=intent,
        policy_decision=policy_decision,
        observation=approval_observation,
        policy=approval_policy,
    )
    _require_exact_current_bindings(
        verified_decision=verified_decision,
        approval_observation=approval_observation,
        approval_policy=approval_policy,
        current_observation=current_observation,
        current_policy=current_policy,
    )
    replay = _load_and_replay(repository, halt_stream_id)
    safety_binding = global_safety_binding_digest(current_safety)
    _require_active_exact_authority(
        state=replay.state,
        observation=current_observation,
        policy=current_policy,
        safety=current_safety,
        prepared_at=prepared_at,
    )
    payload = _prepared_payload(
        permit_id=permit_id,
        approval_reference=reference,
        verified_decision=verified_decision,
        observation=current_observation,
        policy=current_policy,
        safety_binding_digest=safety_binding,
        halt_state=replay.state,
        prepared_at=prepared_at,
    )
    event = _prepared_event(payload, sequence=replay.head_sequence + 1)
    loaded, text = _append_and_read_back(repository, event, _prepared_outbox(event))
    return _prepared_reference(loaded, text)
```

Do not catch evaluator/reference-construction programming errors broadly.
Translate only reviewed canonical, repository, event, and read-back failures.

- [ ] **Step 4: Extend replay for prepared permits**

Replay must retain every prepared permit by permit ID, allow exact event retry,
reject conflicting prepared content, and leave the global halt generation
unchanged. It must not consider a prepared permit consumed until a valid
`SubmitPermitConsumed` event appears.

- [ ] **Step 5: Run Task 3 GREEN and regressions**

```bash
UV_OFFLINE=1 uv run pytest -q \
  tests/runtime_risk/test_submit_authority.py \
  tests/runtime_risk/test_global_halt.py \
  tests/runtime_risk/test_approval.py \
  tests/runtime_risk/test_evaluator.py \
  tests/domain/test_event_contracts.py \
  tests/event_ledger
UV_OFFLINE=1 uv run python scripts/generate_contracts.py --check
make check-broad-handler-inventory
make check-secrets
git diff --check
```

- [ ] **Step 6: Commit Task 3**

```bash
git add packages/runtime_risk/submit_authority.py \
  packages/runtime_risk/__init__.py \
  tests/runtime_risk/test_submit_authority.py \
  tests/event_ledger/test_replay.py tests/event_ledger/test_repository.py
git commit -m "feat: prepare durable submit permits"
```

Dispatch an independent Task 3 SPEC/QUALITY review before Task 4.

---

### Task 4: Consume Permits and Close Race/Restart Drills

**Files:**
- Modify: `packages/runtime_risk/submit_authority.py`
- Modify: `packages/runtime_risk/__init__.py`
- Modify: `tests/runtime_risk/test_submit_authority.py`
- Create: `tests/runtime_risk/test_submit_authority_drills.py`
- Modify: `tests/event_ledger/test_replay.py`

**Interfaces:**
- Consumes: `PreparedSubmitPermit`, exact current 05C/halt/safety bindings, Task 2 replay, and the Task 3 durability helpers.
- Produces:
  - `consume_submit_permit(*, repository: EventLedgerRepository, permit: PreparedSubmitPermit, current_observation: RuntimeRiskObservation, current_policy: RuntimeRiskPolicy, current_safety: GlobalSafetyObservation, consumed_event_id: UUID, consumed_at: datetime) -> ConsumedSubmitAuthority`
  - `audit_submit_authority_stream(*, repository, stream_id) -> GlobalHaltReplay`
  - bounded `SubmitPermitConsumptionError`.

- [ ] **Step 1: Write consume and drill RED tests**

Write a deterministic race repository double whose first append attempt can
insert one reviewed event before raising a sequence conflict. Cover:

```python
@pytest.mark.parametrize(
    "intervening",
    [
        "halt-transition",
        "recovery-transition",
        "same-permit-consumed",
        "unrelated-permit-prepared",
        "unrelated-permit-consumed",
    ],
)
def test_consume_classifies_intervening_authority(
    intervening: str,
) -> None:
    repository, permit, current = prepared_authority_fixture()
    repository.inject_before_next_append(intervening)
    if intervening in {"unrelated-permit-prepared", "unrelated-permit-consumed"}:
        authority = consume_submit_permit(
            repository=repository,
            permit=permit,
            current_observation=current.observation,
            current_policy=current.policy,
            current_safety=current.safety,
            consumed_event_id=UUID(int=900),
            consumed_at=current.now,
        )
        assert authority.permit_id == permit.permit_id
        assert repository.consume_append_attempts == 2
    else:
        with pytest.raises(SubmitPermitConsumptionError):
            consume_submit_permit(
                repository=repository,
                permit=permit,
                current_observation=current.observation,
                current_policy=current.policy,
                current_safety=current.safety,
                consumed_event_id=UUID(int=900),
                consumed_at=current.now,
            )
```

Also cover exact time boundaries at prepared time, five seconds, and one
microsecond after; reject reuse of the preparation safety read; accept a newer
safety read with the same source/state binding; reject a changed binding;
duplicate consume; changed policy/observation/portfolio; wrong
stream/generation/transition; missing or forged prepared event;
malformed repository outputs; restart replay; event gaps/reordering; and no
partial authority on persistence failure.

- [ ] **Step 2: Run Task 4 RED**

```bash
UV_OFFLINE=1 uv run pytest -q \
  tests/runtime_risk/test_submit_authority.py \
  tests/runtime_risk/test_submit_authority_drills.py \
  -k 'consume or audit or race or restart'
```

Expected: collection fails because consume/audit APIs and race behavior are not
implemented.

- [ ] **Step 3: Implement one-shot consume**

Consume must canonicalize the permit/current inputs, replay, load the exact
prepared event, require `consumed_at <= expires_at`, require active exact halt
bindings, require `prepared_at <= current_safety.observed_at <= consumed_at`,
and append/read back `SubmitPermitConsumed` before returning a content-addressed
`ConsumedSubmitAuthority`.

On the first sequence conflict:

```python
try:
    return _append_consumption_against(replay, payload)
except (EventConflictError, SequenceError) as first_conflict:
    refreshed = _load_and_replay(repository, permit.halt_stream_id)
    if _halt_rotated(replay, refreshed) or permit.permit_id in refreshed.consumed_permit_ids:
        raise SubmitPermitConsumptionError("submit permit authority changed") from first_conflict
    if not _only_unrelated_permit_events_advanced(replay, refreshed):
        raise SubmitPermitConsumptionError("submit permit authority changed") from first_conflict
    return _append_consumption_against(refreshed, payload)
```

There is at most one cause-checked retry. A second conflict is bounded failure.

- [ ] **Step 4: Implement restart audit**

`audit_submit_authority_stream` loads canonical events once, runs full replay,
and verifies every returned state/reference can be reserialized and revalidated
without drift. It performs no writes and returns the replay object only on a
complete valid stream.

- [ ] **Step 5: Run full Phase 5 acceptance bundle**

```bash
UV_OFFLINE=1 uv run pytest -q \
  tests/runtime_risk \
  tests/domain/test_runtime_halt_contracts.py \
  tests/domain/test_runtime_risk_contracts.py \
  tests/domain/test_event_contracts.py \
  tests/domain/test_contract_generation.py \
  tests/event_ledger \
  tests/portfolio_reducer \
  tests/domain/test_account_portfolio_contracts.py \
  tests/portfolio_reducer/test_execution_accounting.py
UV_OFFLINE=1 uv run python scripts/generate_contracts.py --check
make check-contracts
make check-broad-handler-inventory
make check-secrets
python -m compileall -q packages/domain packages/runtime_risk
git diff --check
```

Expected: all pass; any PostgreSQL-gated skips must be pre-existing and reported
without adding new skip entries.

- [ ] **Step 6: Commit Task 4**

```bash
git add packages/runtime_risk/submit_authority.py \
  packages/runtime_risk/__init__.py \
  tests/runtime_risk/test_submit_authority.py \
  tests/runtime_risk/test_submit_authority_drills.py \
  tests/event_ledger/test_replay.py
git commit -m "feat: consume global halt submit authority"
```

Dispatch an independent Task 4 SPEC/QUALITY review.

---

## Whole-Branch Completion Gate

After all four task reviews pass:

1. Generate the SDD whole-branch immutable review package from base `dfbd925`
   through the exact candidate HEAD.
2. Dispatch a fresh, independent broad reviewer for SPEC and QUALITY. Require
   explicit findings by severity and exact file/line evidence.
3. Resolve every Critical and Important finding through the bounded SDD fix
   loop and scoped re-review. Record Minor findings for explicit final triage.
4. In a fresh standalone clone under `/home/thenam176/.cache/trading-agent/`
   with packet parent `0700`, process `umask 0022`, canonical offline uv/npm
   caches, and Linux `/home` temporary paths, run exactly once:

```bash
UV_OFFLINE=1 uv sync --frozen
(cd legacy/research-backend && UV_OFFLINE=1 uv sync --frozen --extra test)
(cd apps/dashboard && npm ci --offline)
UV_OFFLINE=1 uv run pytest -q \
  tests/runtime_risk \
  tests/domain/test_runtime_halt_contracts.py \
  tests/domain/test_runtime_risk_contracts.py \
  tests/domain/test_event_contracts.py \
  tests/domain/test_contract_generation.py \
  tests/event_ledger tests/portfolio_reducer
UV_OFFLINE=1 uv run python scripts/generate_contracts.py --check
make check-contracts
make check-broad-handler-inventory
make check-secrets
git diff --check
make test-all
git diff --check
git status --short
```

5. Require terminal exit zero and empty final status. Preserve private bounded
   logs; never alter the tested process umask or select an empty isolated cache.
6. Only a clean independent review plus the exact clean-candidate gates may
   authorize local `main` fast-forward. Do not push or activate runtime
   behavior.
