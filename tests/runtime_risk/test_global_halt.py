from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal
from decimal import localcontext
from pathlib import Path
from typing import Callable
from uuid import UUID

import pytest

from packages.domain import Currency, EventEnvelope, Money
from packages.domain.runtime_halt import (
    GlobalHaltReasonCode,
    GlobalHaltRecoveryAuthorization,
    GlobalHaltState,
    GlobalHaltStatus,
    GlobalHaltTransition,
    GlobalSafetyObservation,
    PreparedSubmitPermit,
    SubmitPermitConsumed,
    SubmitPermitPrepared,
)
from packages.event_ledger import AppendOutcome, InMemoryEventLedger, OutboxIntent
from packages.event_ledger.replay import deserialize_event, event_digest, serialize_event
from packages.runtime_risk import (
    FilesystemGlobalSafetyAuthority,
    GlobalHaltAuthorityError,
    GlobalHaltRecoveryError,
    audit_submit_authority_stream,
    canonical_model_digest,
    evaluate_global_breaker,
    global_safety_binding_digest,
    observe_global_safety,
    record_global_halt_observation,
    recover_global_halt,
    replay_global_halt_authority,
)
from packages.safety_evidence import CanonicalKillSwitchState, safety_source_fingerprint

from tests.runtime_risk.test_evaluator import NOW, evaluator_case, uid


STREAM_ID = uid(700)


def money(amount: str | Decimal, currency: Currency = Currency.USDT) -> Money:
    return Money(Decimal(amount), currency)


def _usdt_authority():
    case = evaluator_case(current_quantity="0")
    balance = case.observation.portfolio.balances[0]
    balance = balance.model_copy(
        update={
            name: money(getattr(balance, name).amount)
            for name in (
                "cash",
                "locked_funds",
                "margin_used",
                "realized_pnl",
                "unrealized_pnl",
                "fees",
                "funding",
            )
        }
        | {"currency": Currency.USDT}
    )
    exposure = case.observation.portfolio.total_exposure
    exposure = exposure.model_copy(
        update={
            "currency": Currency.USDT,
            "gross": money(exposure.gross.amount),
            "net": money(exposure.net.amount),
            "pending": money(exposure.pending.amount),
        }
    )
    portfolio = case.observation.portfolio.model_copy(
        update={
            "reporting_currency": Currency.USDT,
            "balances": (balance,),
            "total_exposure": exposure,
        }
    )
    spec = case.observation.instrument_specs[0]
    spec = spec.model_copy(
        update={
            "min_order_notional": money(spec.min_order_notional.amount),
            "max_order_notional": money(spec.max_order_notional.amount),
        }
    )
    observation = case.observation.model_copy(
        update={
            "portfolio": portfolio,
            "instrument_specs": (spec,),
            "daily_pnl": money(case.observation.daily_pnl.amount),
            "current_equity": money(case.observation.current_equity.amount),
            "peak_equity": money(case.observation.peak_equity.amount),
        }
    )
    policy = case.policy.model_copy(
        update={
            name: money(getattr(case.policy, name).amount)
            for name in (
                "max_pending_exposure",
                "max_gross_exposure",
                "max_abs_net_exposure",
                "max_strategy_exposure",
                "max_venue_exposure",
                "min_available_funds",
                "max_daily_loss",
                "max_drawdown",
            )
        }
    )
    return observation, policy


def runtime_observation(**changes: object):
    observation, _ = _usdt_authority()
    return observation.model_copy(update=changes)


def runtime_policy(**changes: object):
    _, policy = _usdt_authority()
    return policy.model_copy(update=changes)


def safety_observation(
    state: CanonicalKillSwitchState = CanonicalKillSwitchState.INACTIVE,
    *,
    observed_at: datetime = NOW,
) -> GlobalSafetyObservation:
    return GlobalSafetyObservation(
        source_fingerprint="a" * 64,
        kill_switch_state=state,
        observed_at=observed_at,
        schema_version="global-safety-observation-v1",
    )


class ExactSafetyVerifier:
    def verify(self, *, observation: GlobalSafetyObservation) -> GlobalSafetyObservation:
        return observation


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


@pytest.mark.parametrize(
    ("current_equity", "expected"),
    [
        ("901", ()),
        ("900", ()),
        ("899.999999", (GlobalHaltReasonCode.DRAWDOWN_LIMIT,)),
    ],
)
def test_global_breaker_drawdown_boundary(
    current_equity: str,
    expected: tuple[GlobalHaltReasonCode, ...],
) -> None:
    assert evaluate_global_breaker(
        observation=runtime_observation(
            daily_pnl=money("0"),
            current_equity=money(current_equity),
            peak_equity=money("1000"),
        ),
        policy=runtime_policy(max_daily_loss=money("1000"), max_drawdown=money("100")),
        safety=safety_observation(),
    ) == expected


def test_global_breaker_is_independent_of_hostile_decimal_context() -> None:
    observation = runtime_observation(
        daily_pnl=money("-100.000001"),
        current_equity=money("899.999999"),
        peak_equity=money("1000"),
    )
    policy = runtime_policy(max_daily_loss=money("100"), max_drawdown=money("100"))
    with localcontext() as context:
        context.prec = 1
        context.Emax = 1
        context.Emin = -1
        result = evaluate_global_breaker(
            observation=observation,
            policy=policy,
            safety=safety_observation(),
        )
    assert result == (
        GlobalHaltReasonCode.DAILY_LOSS_LIMIT,
        GlobalHaltReasonCode.DRAWDOWN_LIMIT,
    )


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (CanonicalKillSwitchState.INACTIVE, ()),
        (CanonicalKillSwitchState.ACTIVE, (GlobalHaltReasonCode.KILL_SWITCH_ACTIVE,)),
        (CanonicalKillSwitchState.UNKNOWN, (GlobalHaltReasonCode.SAFETY_AUTHORITY_UNKNOWN,)),
    ],
)
def test_global_breaker_kill_switch_states(
    state: CanonicalKillSwitchState,
    expected: tuple[GlobalHaltReasonCode, ...],
) -> None:
    assert evaluate_global_breaker(
        observation=runtime_observation(),
        policy=runtime_policy(),
        safety=safety_observation(state),
    ) == expected


def test_global_breaker_returns_every_cause_once_in_canonical_order() -> None:
    assert evaluate_global_breaker(
        observation=runtime_observation(
            daily_pnl=money("-1001"),
            current_equity=money("0"),
            peak_equity=money("1001"),
        ),
        policy=runtime_policy(max_daily_loss=money("1000"), max_drawdown=money("1000")),
        safety=safety_observation(CanonicalKillSwitchState.UNKNOWN),
    ) == (
        GlobalHaltReasonCode.SAFETY_AUTHORITY_UNKNOWN,
        GlobalHaltReasonCode.DAILY_LOSS_LIMIT,
        GlobalHaltReasonCode.DRAWDOWN_LIMIT,
    )


def test_global_breaker_rejects_non_reporting_policy_currency() -> None:
    with pytest.raises(GlobalHaltAuthorityError, match="accounting authority"):
        evaluate_global_breaker(
            observation=runtime_observation(),
            policy=evaluator_case().policy,
            safety=safety_observation(),
        )


def test_global_safety_binding_excludes_observation_time() -> None:
    first = safety_observation(observed_at=NOW)
    later = safety_observation(observed_at=NOW + timedelta(seconds=1))
    expected = hashlib.sha256(
        json.dumps(
            {"kill_switch_state": "INACTIVE", "source_fingerprint": "a" * 64},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()
    assert global_safety_binding_digest(first) == expected
    assert global_safety_binding_digest(later) == expected


@pytest.mark.parametrize(
    ("sentinel", "mode", "expected"),
    [
        (None, None, CanonicalKillSwitchState.INACTIVE),
        ("2026-08-10T12:00:00Z: operator halt\n", 0o600, CanonicalKillSwitchState.ACTIVE),
        ("2026-08-10T12:00:00Z: operator halt\n", 0o644, CanonicalKillSwitchState.UNKNOWN),
        ("malformed\n", 0o600, CanonicalKillSwitchState.UNKNOWN),
    ],
)
def test_observe_global_safety_resolves_sentinel_without_mutation(
    tmp_path: Path,
    sentinel: str | None,
    mode: int | None,
    expected: CanonicalKillSwitchState,
) -> None:
    del tmp_path
    root = Path(tempfile.mkdtemp(prefix="global-halt-safety-", dir="/tmp"))
    try:
        path = root / ".kill_switch"
        if sentinel is not None:
            path.write_text(sentinel, encoding="utf-8")
            path.chmod(mode)
        before = tuple((item.name, item.lstat().st_mode, item.read_bytes()) for item in root.iterdir())
        observed = observe_global_safety(source_root=root, observed_at=NOW)
        after = tuple((item.name, item.lstat().st_mode, item.read_bytes()) for item in root.iterdir())
        assert observed == GlobalSafetyObservation(
            source_fingerprint=safety_source_fingerprint(root),
            kill_switch_state=expected,
            observed_at=NOW,
            schema_version="global-safety-observation-v1",
        )
        assert after == before
    finally:
        shutil.rmtree(root)


def test_observe_global_safety_treats_symlink_as_unknown_without_following_it(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.write_text("2026-08-10T12:00:00Z: operator halt\n", encoding="utf-8")
    target.chmod(0o600)
    sentinel = tmp_path / ".kill_switch"
    sentinel.symlink_to(target)
    observed = observe_global_safety(source_root=tmp_path, observed_at=NOW)
    assert observed.kill_switch_state is CanonicalKillSwitchState.UNKNOWN
    assert sentinel.is_symlink()
    assert target.read_text(encoding="utf-8") == "2026-08-10T12:00:00Z: operator halt\n"


def initialize(
    ledger,
    *,
    observation=None,
    policy=None,
    safety=None,
    transition_id: UUID = uid(701),
    event_id: UUID = uid(702),
    decided_at: datetime = NOW,
    safety_verifier: object | None = None,
) -> GlobalHaltState:
    return record_global_halt_observation(
        repository=ledger,
        stream_id=STREAM_ID,
        observation=observation or runtime_observation(),
        policy=policy or runtime_policy(),
        safety=safety or safety_observation(),
        safety_verifier=safety_verifier or ExactSafetyVerifier(),
        transition_id=transition_id,
        event_id=event_id,
        decided_at=decided_at,
    )


def _private_active_safety_root(root: Path) -> None:
    root.mkdir()
    sentinel = root / ".kill_switch"
    sentinel.write_text(
        "2026-08-10T12:00:00Z: final-wave safety test\n",
        encoding="utf-8",
    )
    sentinel.chmod(0o600)


@pytest.mark.parametrize("evidence", ("alternate-root", "fabricated"))
def test_global_halt_initialization_rejects_untrusted_inactive_safety(
    tmp_path: Path,
    evidence: str,
) -> None:
    canonical_root = tmp_path / "canonical"
    alternate_root = tmp_path / "alternate"
    _private_active_safety_root(canonical_root)
    alternate_root.mkdir()
    if evidence == "alternate-root":
        supplied = observe_global_safety(
            source_root=alternate_root,
            observed_at=NOW,
        )
    else:
        supplied = GlobalSafetyObservation(
            source_fingerprint=safety_source_fingerprint(canonical_root),
            kill_switch_state=CanonicalKillSwitchState.INACTIVE,
            observed_at=NOW,
            schema_version="global-safety-observation-v1",
        )

    ledger = InMemoryEventLedger()
    with pytest.raises(GlobalHaltAuthorityError):
        initialize(
            ledger,
            safety=supplied,
            safety_verifier=FilesystemGlobalSafetyAuthority(canonical_root),
        )
    assert ledger.load_events() == ()


def _freshness_observation(
    authority: str,
    *,
    authority_at: datetime,
    observation_at: datetime = NOW,
):
    observation = evaluator_case(current_quantity="1").observation
    portfolio = observation.portfolio
    balances = portfolio.balances
    positions = portfolio.positions
    if authority in {"observation", "portfolio"}:
        balances = tuple(
            balance.model_copy(update={"observed_at": authority_at})
            for balance in balances
        )
        positions = tuple(
            position.model_copy(
                update={
                    "observed_at": authority_at,
                    "mark": position.mark.model_copy(
                        update={"marked_at": authority_at}
                    )
                    if position.mark is not None
                    else None,
                }
            )
            for position in positions
        )
        portfolio = portfolio.model_copy(
            update={
                "balances": balances,
                "positions": positions,
                "observed_at": authority_at,
            }
        )
    elif authority == "balance":
        balances = (
            balances[0].model_copy(update={"observed_at": authority_at}),
            *balances[1:],
        )
        portfolio = portfolio.model_copy(update={"balances": balances})
    elif authority == "position":
        position = positions[0]
        positions = (
            position.model_copy(
                update={
                    "observed_at": authority_at,
                    "mark": position.mark.model_copy(
                        update={"marked_at": authority_at}
                    )
                    if position.mark is not None
                    else None,
                }
            ),
            *positions[1:],
        )
        portfolio = portfolio.model_copy(update={"positions": positions})
    elif authority == "mark":
        position = positions[0]
        assert position.mark is not None
        positions = (
            position.model_copy(
                update={
                    "mark": position.mark.model_copy(
                        update={"marked_at": authority_at}
                    )
                }
            ),
            *positions[1:],
        )
        portfolio = portfolio.model_copy(update={"positions": positions})
    if authority == "observation":
        observation_at = authority_at
        market_snapshots = tuple(
            item.model_copy(update={"observed_at": authority_at})
            for item in observation.market_snapshots
        )
        conversion_rates = tuple(
            item.model_copy(update={"observed_at": authority_at})
            for item in observation.conversion_rates
        )
        venue_health = tuple(
            item.model_copy(update={"observed_at": authority_at})
            for item in observation.venue_health
        )
    else:
        market_snapshots = observation.market_snapshots
        conversion_rates = observation.conversion_rates
        venue_health = observation.venue_health
    return observation.model_copy(
        update={
            "portfolio": portfolio,
            "market_snapshots": market_snapshots,
            "conversion_rates": conversion_rates,
            "venue_health": venue_health,
            "command_window_started_at": min(
                observation.command_window_started_at,
                observation_at,
            ),
            "observed_at": observation_at,
        }
    )


def test_safe_global_halt_initialization_accepts_exact_freshness_boundary() -> None:
    ledger = InMemoryEventLedger()
    observation = _freshness_observation("mark", authority_at=NOW)
    state = initialize(
        ledger,
        observation=observation,
        policy=evaluator_case().policy.model_copy(
            update={"portfolio_max_age_seconds": 10}
        ),
        safety=safety_observation(observed_at=NOW),
        decided_at=NOW + timedelta(seconds=10),
    )
    assert state.status is GlobalHaltStatus.ACTIVE


def test_safe_global_halt_initialization_accepts_maximum_freshness_policy() -> None:
    state = initialize(
        InMemoryEventLedger(),
        policy=runtime_policy(
            portfolio_max_age_seconds=86_399_999_999_999,
        ),
    )
    assert state.status is GlobalHaltStatus.ACTIVE


@pytest.mark.parametrize(
    "authority",
    ("observation", "portfolio", "balance", "position", "mark", "safety"),
)
def test_safe_global_halt_initialization_rejects_one_microsecond_stale_authority(
    authority: str,
) -> None:
    stale_at = NOW - timedelta(microseconds=1)
    observation = _freshness_observation(
        authority if authority != "safety" else "mark",
        authority_at=stale_at if authority != "safety" else NOW,
    )
    selected_safety = safety_observation(
        observed_at=stale_at if authority == "safety" else NOW
    )
    ledger = InMemoryEventLedger()
    with pytest.raises(GlobalHaltAuthorityError):
        initialize(
            ledger,
            observation=observation,
            policy=evaluator_case().policy.model_copy(
                update={"portfolio_max_age_seconds": 10}
            ),
            safety=selected_safety,
            decided_at=NOW + timedelta(seconds=10),
        )
    assert ledger.load_events() == ()


@pytest.mark.parametrize("authority", ("observation", "safety"))
def test_safe_global_halt_initialization_rejects_future_authority(
    authority: str,
) -> None:
    future = NOW + timedelta(microseconds=1)
    observation = runtime_observation(
        observed_at=future if authority == "observation" else NOW
    )
    selected_safety = safety_observation(
        observed_at=future if authority == "safety" else NOW
    )
    with pytest.raises(GlobalHaltAuthorityError):
        initialize(
            InMemoryEventLedger(),
            observation=observation,
            safety=selected_safety,
            decided_at=NOW,
        )


def test_stale_unsafe_global_authority_still_initializes_halted() -> None:
    stale_at = NOW - timedelta(seconds=60)
    observation = _freshness_observation(
        "observation",
        authority_at=stale_at,
    ).model_copy(
        update={
            "daily_pnl": Money(
                Decimal("-1001"),
                evaluator_case().policy.max_daily_loss.currency,
            )
        }
    )
    state = initialize(
        InMemoryEventLedger(),
        observation=observation,
        policy=evaluator_case().policy.model_copy(
            update={"portfolio_max_age_seconds": 10}
        ),
        safety=safety_observation(observed_at=stale_at),
        decided_at=NOW,
    )
    assert state.status is GlobalHaltStatus.HALTED


def test_global_halt_safe_generation_one_is_durable_and_read_back_exactly() -> None:
    ledger = InMemoryEventLedger()
    state = initialize(ledger)
    event = ledger.load_events()[0]
    assert state.status is GlobalHaltStatus.ACTIVE
    assert state.generation == 1
    assert state.reason_codes == (GlobalHaltReasonCode.INITIALIZED_SAFE,)
    assert state.transition_event_id == event.event_id
    assert state.transition_digest == event_digest(serialize_event(event))
    assert ledger.load_outbox() == (
        OutboxIntent(
            event_id=event.event_id,
            topic="runtime-risk.global-halt-transitions",
            payload_json='{"transition_id":"00000000-0000-0000-0000-0000000002bd"}',
        ),
    )


def test_global_halt_unsafe_generation_one_is_halted() -> None:
    ledger = InMemoryEventLedger()
    state = initialize(ledger, observation=runtime_observation(daily_pnl=money("-1001")))
    assert state.status is GlobalHaltStatus.HALTED
    assert state.generation == 1
    assert state.reason_codes == (GlobalHaltReasonCode.DAILY_LOSS_LIMIT,)


def test_active_safe_observation_does_not_append_or_replace_transition_evidence() -> None:
    ledger = InMemoryEventLedger()
    first = initialize(ledger)
    changed = runtime_observation(observation_id=uid(711), state_version=2, daily_pnl=money("50"))
    retained = initialize(
        ledger,
        observation=changed,
        transition_id=uid(712),
        event_id=uid(713),
        decided_at=NOW + timedelta(seconds=1),
    )
    assert retained == first
    assert len(ledger.load_events()) == 1
    assert retained.runtime_observation_digest != canonical_model_digest(changed)


def test_active_breach_rotates_once_and_repeated_halt_does_not_append() -> None:
    ledger = InMemoryEventLedger()
    first = initialize(ledger)
    breach = runtime_observation(daily_pnl=money("-1001"))
    halted = initialize(
        ledger,
        observation=breach,
        transition_id=uid(714),
        event_id=uid(715),
        decided_at=NOW + timedelta(seconds=1),
    )
    repeated = initialize(
        ledger,
        observation=breach.model_copy(update={"state_version": 2}),
        transition_id=uid(716),
        event_id=uid(717),
        decided_at=NOW + timedelta(seconds=2),
    )
    assert halted.generation == 2
    assert halted.status is GlobalHaltStatus.HALTED
    assert halted.prior_transition_event_id == first.transition_event_id
    assert halted.prior_transition_digest == first.transition_digest
    assert repeated == halted
    assert len(ledger.load_events()) == 2


def transition_event(
    payload: GlobalHaltTransition,
    *,
    event_id: UUID,
    stream_id: UUID = STREAM_ID,
    sequence: int,
) -> EventEnvelope[GlobalHaltTransition]:
    return EventEnvelope[GlobalHaltTransition](
        event_id=event_id,
        event_type="GlobalHaltTransition",
        schema_version="global-halt-transition-event-v1",
        source="runtime-risk",
        stream_id=stream_id,
        sequence=sequence,
        observed_at=payload.decided_at,
        ingested_at=payload.decided_at,
        produced_at=payload.decided_at,
        effective_at=payload.decided_at,
        expires_at=payload.decided_at + timedelta(minutes=5),
        correlation_id=payload.transition_id,
        causation_id=payload.transition_id,
        trace_id=payload.transition_id,
        payload=payload,
    )


def transition_payload(
    *,
    transition_id: UUID = uid(720),
    prior_generation: int = 0,
    prior_transition_digest: str | None = None,
    next_generation: int = 1,
    next_status: GlobalHaltStatus = GlobalHaltStatus.ACTIVE,
    reason_codes: tuple[GlobalHaltReasonCode, ...] = (GlobalHaltReasonCode.INITIALIZED_SAFE,),
    recovery_authorization_digest: str | None = None,
    decided_at: datetime = NOW,
) -> GlobalHaltTransition:
    observation = runtime_observation()
    return GlobalHaltTransition(
        transition_id=transition_id,
        prior_generation=prior_generation,
        prior_transition_digest=prior_transition_digest,
        next_generation=next_generation,
        next_status=next_status,
        reason_codes=reason_codes,
        runtime_policy_digest=canonical_model_digest(runtime_policy()),
        runtime_observation_digest=canonical_model_digest(observation),
        portfolio_digest=canonical_model_digest(observation.portfolio),
        safety_observation_digest=canonical_model_digest(safety_observation()),
        recovery_authorization_digest=recovery_authorization_digest,
        decided_at=decided_at,
        schema_version="global-halt-transition-v1",
    )


def test_global_halt_replay_restart_is_equal_and_filters_other_streams() -> None:
    ledger = InMemoryEventLedger()
    expected = initialize(ledger)
    foreign = transition_event(
        transition_payload(transition_id=uid(721)),
        event_id=uid(722),
        stream_id=uid(799),
        sequence=1,
    )
    events = (foreign, *ledger.load_events())
    first = replay_global_halt_authority(events=events, stream_id=STREAM_ID)
    restarted = replay_global_halt_authority(
        events=tuple(
            deserialize_event(serialize_event(event))
            for event in events
        ),
        stream_id=STREAM_ID,
    )
    assert first == restarted
    assert first.state == expected
    assert first.head_sequence == 1
    assert first.head_event_id == expected.transition_event_id
    assert first.head_event_digest == expected.transition_digest


@pytest.mark.parametrize("kind", ("gap", "duplicate", "prior-digest", "foreign", "impossible"))
def test_global_halt_replay_rejects_stream_contradictions(kind: str) -> None:
    first = transition_event(transition_payload(), event_id=uid(730), sequence=1)
    first_digest = event_digest(serialize_event(first))
    halted_payload = transition_payload(
        transition_id=uid(731),
        prior_generation=1,
        prior_transition_digest=("f" * 64 if kind == "prior-digest" else first_digest),
        next_generation=2,
        next_status=GlobalHaltStatus.HALTED,
        reason_codes=(GlobalHaltReasonCode.DAILY_LOSS_LIMIT,),
        decided_at=NOW + timedelta(seconds=1),
    )
    second = transition_event(halted_payload, event_id=uid(732), sequence=3 if kind == "gap" else 2)
    events: tuple[EventEnvelope[object], ...] = (first, second)
    if kind == "duplicate":
        events = (first, second.model_copy(update={"sequence": 1}))
    elif kind == "foreign":
        from tests.runtime_risk.test_approval import runtime_risk_event

        events = (first, runtime_risk_event(event_id=uid(733), stream_id=STREAM_ID, sequence=2))
    elif kind == "impossible":
        impossible = halted_payload.model_copy(
            update={
                "next_status": GlobalHaltStatus.ACTIVE,
                "reason_codes": (GlobalHaltReasonCode.RECOVERY_AUTHORIZED,),
                "recovery_authorization_digest": "e" * 64,
            }
        )
        events = (first, transition_event(impossible, event_id=uid(734), sequence=2))
    with pytest.raises(GlobalHaltAuthorityError):
        replay_global_halt_authority(events=events, stream_id=STREAM_ID)


def prepared_event(
    state: GlobalHaltState,
    *,
    event_id: UUID = uid(735),
    sequence: int = 2,
    prepared_at: datetime = NOW + timedelta(seconds=1),
) -> EventEnvelope[SubmitPermitPrepared]:
    payload = SubmitPermitPrepared(
        permit_id=uid(736),
        approval_event_id=uid(737),
        approval_reference_digest="0" * 64,
        intent_digest="1" * 64,
        policy_risk_decision_digest="2" * 64,
        runtime_risk_decision_digest="3" * 64,
        runtime_policy_digest="4" * 64,
        runtime_observation_digest="5" * 64,
        portfolio_digest="6" * 64,
        safety_binding_digest="7" * 64,
        halt_stream_id=STREAM_ID,
        halt_generation=state.generation,
        halt_transition_event_id=state.transition_event_id,
        halt_transition_digest=state.transition_digest,
        prepared_at=prepared_at,
        expires_at=prepared_at + timedelta(seconds=5),
        schema_version="submit-permit-prepared-v1",
    )
    return EventEnvelope[SubmitPermitPrepared](
        event_id=event_id,
        event_type="SubmitPermitPrepared",
        schema_version="submit-permit-prepared-event-v1",
        source="runtime-risk",
        stream_id=STREAM_ID,
        sequence=sequence,
        observed_at=payload.prepared_at,
        ingested_at=payload.prepared_at,
        produced_at=payload.prepared_at,
        effective_at=payload.prepared_at,
        expires_at=payload.expires_at,
        correlation_id=payload.permit_id,
        causation_id=payload.approval_event_id,
        trace_id=payload.permit_id,
        payload=payload,
    )


@pytest.mark.parametrize(
    ("prepared_delta", "accepted"),
    (
        (timedelta(0), True),
        (timedelta(microseconds=-1), False),
    ),
    ids=("equal-active-transition", "one-microsecond-before-active"),
)
def test_global_halt_replay_requires_prepare_after_active_transition(
    prepared_delta: timedelta,
    accepted: bool,
) -> None:
    active_at = NOW + timedelta(seconds=2)
    first = transition_event(
        transition_payload(decided_at=active_at),
        event_id=uid(760),
        sequence=1,
    )
    state = replay_global_halt_authority(events=(first,), stream_id=STREAM_ID).state
    assert state is not None
    permit = prepared_event(
        state,
        event_id=uid(761),
        prepared_at=active_at + prepared_delta,
    )
    if accepted:
        assert replay_global_halt_authority(
            events=(first, permit),
            stream_id=STREAM_ID,
        ).prepared
    else:
        with pytest.raises(GlobalHaltAuthorityError):
            replay_global_halt_authority(
                events=(first, permit),
                stream_id=STREAM_ID,
            )


def test_global_halt_replay_rejects_backward_transition_authority_time() -> None:
    first = transition_event(
        transition_payload(decided_at=NOW + timedelta(seconds=2)),
        event_id=uid(762),
        sequence=1,
    )
    first_digest = event_digest(serialize_event(first))
    second = transition_event(
        transition_payload(
            transition_id=uid(763),
            prior_generation=1,
            prior_transition_digest=first_digest,
            next_generation=2,
            next_status=GlobalHaltStatus.HALTED,
            reason_codes=(GlobalHaltReasonCode.DAILY_LOSS_LIMIT,),
            decided_at=NOW + timedelta(seconds=1),
        ),
        event_id=uid(764),
        sequence=2,
    )
    with pytest.raises(GlobalHaltAuthorityError):
        replay_global_halt_authority(events=(first, second), stream_id=STREAM_ID)


def test_record_global_halt_rejects_transition_behind_current_stream_head() -> None:
    ledger = InMemoryEventLedger()
    active = initialize(
        ledger,
        transition_id=uid(765),
        event_id=uid(766),
        decided_at=NOW,
    )
    prepared = prepared_event(
        active,
        event_id=uid(767),
        prepared_at=NOW + timedelta(seconds=2),
    )
    ledger.append(
        prepared,
        OutboxIntent(event_id=prepared.event_id, topic="test.prepared"),
    )
    before = ledger.load_events()
    with pytest.raises(GlobalHaltAuthorityError):
        initialize(
            ledger,
            observation=runtime_observation(daily_pnl=money("-1001")),
            safety=safety_observation(observed_at=NOW + timedelta(seconds=1)),
            transition_id=uid(768),
            event_id=uid(769),
            decided_at=NOW + timedelta(seconds=1),
        )
    assert ledger.load_events() == before


def consumed_event(
    prepared: PreparedSubmitPermit,
    *,
    event_id: UUID = uid(738),
    sequence: int = 3,
    consumed_at: datetime = NOW + timedelta(seconds=2),
) -> EventEnvelope[SubmitPermitConsumed]:
    payload = SubmitPermitConsumed(
        permit_id=prepared.permit_id,
        prepared_event_digest=prepared.prepared_event_digest,
        halt_stream_id=prepared.halt_stream_id,
        halt_generation=prepared.halt_generation,
        halt_transition_digest=prepared.halt_transition_digest,
        consumed_at=consumed_at,
        schema_version="submit-permit-consumed-v1",
    )
    return EventEnvelope[SubmitPermitConsumed](
        event_id=event_id,
        event_type="SubmitPermitConsumed",
        schema_version="submit-permit-consumed-event-v1",
        source="runtime-risk",
        stream_id=STREAM_ID,
        sequence=sequence,
        observed_at=payload.consumed_at,
        ingested_at=payload.consumed_at,
        produced_at=payload.consumed_at,
        effective_at=payload.consumed_at,
        expires_at=payload.consumed_at + timedelta(minutes=5),
        correlation_id=payload.permit_id,
        causation_id=payload.permit_id,
        trace_id=payload.permit_id,
        payload=payload,
    )


def test_global_halt_replay_derives_prepared_identity_and_consumes_once() -> None:
    ledger = InMemoryEventLedger()
    state = initialize(ledger)
    first = ledger.load_events()[0]
    prepared_envelope = prepared_event(state)
    prepared_replay = replay_global_halt_authority(
        events=(first, prepared_envelope), stream_id=STREAM_ID
    )
    prepared = prepared_replay.prepared[0]
    assert prepared.prepared_event_id == prepared_envelope.event_id
    assert prepared.prepared_event_digest == event_digest(serialize_event(prepared_envelope))
    consumed_envelope = consumed_event(prepared)
    consumed_replay = replay_global_halt_authority(
        events=(first, prepared_envelope, consumed_envelope), stream_id=STREAM_ID
    )
    assert consumed_replay.prepared == ()
    assert consumed_replay.consumed_permit_ids == (prepared.permit_id,)


def _permit_rotated_through_recovery() -> tuple[
    tuple[EventEnvelope[object], ...],
    PreparedSubmitPermit,
    GlobalHaltState,
]:
    first = transition_event(
        transition_payload(),
        event_id=uid(770),
        sequence=1,
    )
    active = replay_global_halt_authority(events=(first,), stream_id=STREAM_ID).state
    assert active is not None
    prepared_envelope = prepared_event(active, event_id=uid(771), sequence=2)
    prepared = replay_global_halt_authority(
        events=(first, prepared_envelope),
        stream_id=STREAM_ID,
    ).prepared[0]
    halted_envelope = transition_event(
        transition_payload(
            transition_id=uid(772),
            prior_generation=1,
            prior_transition_digest=active.transition_digest,
            next_generation=2,
            next_status=GlobalHaltStatus.HALTED,
            reason_codes=(GlobalHaltReasonCode.KILL_SWITCH_ACTIVE,),
            decided_at=NOW + timedelta(seconds=2),
        ),
        event_id=uid(773),
        sequence=3,
    )
    halted = replay_global_halt_authority(
        events=(first, prepared_envelope, halted_envelope),
        stream_id=STREAM_ID,
    ).state
    assert halted is not None
    recovered_envelope = transition_event(
        transition_payload(
            transition_id=uid(774),
            prior_generation=2,
            prior_transition_digest=halted.transition_digest,
            next_generation=3,
            next_status=GlobalHaltStatus.ACTIVE,
            reason_codes=(GlobalHaltReasonCode.RECOVERY_AUTHORIZED,),
            recovery_authorization_digest="e" * 64,
            decided_at=NOW + timedelta(seconds=3),
        ),
        event_id=uid(775),
        sequence=4,
    )
    events: tuple[EventEnvelope[object], ...] = (
        first,
        prepared_envelope,
        halted_envelope,
        recovered_envelope,
    )
    recovered = replay_global_halt_authority(
        events=events,
        stream_id=STREAM_ID,
    ).state
    assert recovered is not None
    return events, prepared, recovered


def test_global_halt_replay_retires_pre_rotation_permits_without_id_reuse() -> None:
    events, prepared, _ = _permit_rotated_through_recovery()
    replay = replay_global_halt_authority(events=events, stream_id=STREAM_ID)
    assert replay.prepared == ()
    assert replay.retired_permit_ids == (prepared.permit_id,)


def test_global_halt_replay_rejects_pre_rotation_permit_rebound_to_recovery() -> None:
    events, prepared, recovered = _permit_rotated_through_recovery()
    consumed_at = NOW + timedelta(seconds=4)
    forged_payload = SubmitPermitConsumed(
        permit_id=prepared.permit_id,
        prepared_event_digest=prepared.prepared_event_digest,
        halt_stream_id=STREAM_ID,
        halt_generation=recovered.generation,
        halt_transition_digest=recovered.transition_digest,
        consumed_at=consumed_at,
        schema_version="submit-permit-consumed-v1",
    )
    forged = EventEnvelope[SubmitPermitConsumed](
        event_id=uid(776),
        event_type="SubmitPermitConsumed",
        schema_version="submit-permit-consumed-event-v1",
        source="runtime-risk",
        stream_id=STREAM_ID,
        sequence=5,
        observed_at=consumed_at,
        ingested_at=consumed_at,
        produced_at=consumed_at,
        effective_at=consumed_at,
        expires_at=consumed_at + timedelta(minutes=5),
        correlation_id=prepared.permit_id,
        causation_id=prepared.permit_id,
        trace_id=prepared.permit_id,
        payload=forged_payload,
    )
    with pytest.raises(GlobalHaltAuthorityError):
        replay_global_halt_authority(
            events=(*events, forged),
            stream_id=STREAM_ID,
        )
    ledger = InMemoryEventLedger()
    for event in (*events, forged):
        ledger.append(
            event,
            OutboxIntent(event_id=event.event_id, topic="test.restart-forgery"),
        )
    with pytest.raises(GlobalHaltAuthorityError):
        audit_submit_authority_stream(repository=ledger, stream_id=STREAM_ID)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("prepared_event_digest", "f" * 64),
        ("halt_stream_id", uid(777)),
        ("halt_generation", 2),
        ("halt_transition_digest", "d" * 64),
    ),
)
def test_global_halt_replay_rejects_each_consumed_prepared_binding_mismatch(
    field: str,
    value: object,
) -> None:
    first = transition_event(transition_payload(), event_id=uid(778), sequence=1)
    active = replay_global_halt_authority(events=(first,), stream_id=STREAM_ID).state
    assert active is not None
    prepared_envelope = prepared_event(active, event_id=uid(779), sequence=2)
    prepared = replay_global_halt_authority(
        events=(first, prepared_envelope), stream_id=STREAM_ID
    ).prepared[0]
    valid = consumed_event(prepared, event_id=uid(780), sequence=3)
    payload = SubmitPermitConsumed.model_construct(
        **{**valid.payload.model_dump(mode="python"), field: value}
    )
    forged = valid.model_copy(update={"payload": payload})
    with pytest.raises(GlobalHaltAuthorityError):
        replay_global_halt_authority(
            events=(first, prepared_envelope, forged),
            stream_id=STREAM_ID,
        )


def test_global_halt_replay_rejects_consume_before_prepare() -> None:
    ledger = InMemoryEventLedger()
    state = initialize(ledger)
    first = ledger.load_events()[0]
    prepared_envelope = prepared_event(state)
    prepared = replay_global_halt_authority(
        events=(first, prepared_envelope), stream_id=STREAM_ID
    ).prepared[0]
    with pytest.raises(GlobalHaltAuthorityError):
        replay_global_halt_authority(
            events=(first, consumed_event(prepared, sequence=2)),
            stream_id=STREAM_ID,
        )


@pytest.mark.parametrize(
    ("consumed_at", "accepted"),
    (
        (NOW + timedelta(microseconds=999_999), False),
        (NOW + timedelta(seconds=1), True),
        (NOW + timedelta(seconds=6), True),
        (NOW + timedelta(seconds=6, microseconds=1), False),
    ),
    ids=(
        "before-prepared-at",
        "exact-prepared-at",
        "exact-expiry",
        "after-expiry",
    ),
)
def test_global_halt_replay_enforces_submit_permit_consumption_window(
    consumed_at: datetime,
    accepted: bool,
) -> None:
    ledger = InMemoryEventLedger()
    state = initialize(ledger)
    first = ledger.load_events()[0]
    prepared_envelope = prepared_event(state)
    prepared = replay_global_halt_authority(
        events=(first, prepared_envelope), stream_id=STREAM_ID
    ).prepared[0]
    events = (
        first,
        prepared_envelope,
        consumed_event(prepared, consumed_at=consumed_at),
    )
    if accepted:
        assert replay_global_halt_authority(
            events=events, stream_id=STREAM_ID
        ).consumed_permit_ids == (prepared.permit_id,)
    else:
        with pytest.raises(GlobalHaltAuthorityError):
            replay_global_halt_authority(events=events, stream_id=STREAM_ID)


@pytest.mark.parametrize("second_kind", ("transition", "permit"))
def test_global_halt_replay_rejects_reused_envelope_event_id(
    second_kind: str,
) -> None:
    ledger = InMemoryEventLedger()
    active = initialize(ledger)
    first = ledger.load_events()[0]
    if second_kind == "permit":
        second: EventEnvelope[object] = prepared_event(
            active,
            event_id=first.event_id,
        )
    else:
        breach = runtime_observation(daily_pnl=money("-1001"))
        payload = GlobalHaltTransition(
            transition_id=uid(739),
            prior_generation=active.generation,
            prior_transition_digest=active.transition_digest,
            next_generation=active.generation + 1,
            next_status=GlobalHaltStatus.HALTED,
            reason_codes=(GlobalHaltReasonCode.DAILY_LOSS_LIMIT,),
            runtime_policy_digest=canonical_model_digest(runtime_policy()),
            runtime_observation_digest=canonical_model_digest(breach),
            portfolio_digest=canonical_model_digest(breach.portfolio),
            safety_observation_digest=canonical_model_digest(safety_observation()),
            recovery_authorization_digest=None,
            decided_at=NOW + timedelta(seconds=1),
            schema_version="global-halt-transition-v1",
        )
        second = transition_event(
            payload,
            event_id=first.event_id,
            sequence=2,
        )
    with pytest.raises(GlobalHaltAuthorityError):
        replay_global_halt_authority(
            events=(first, second),
            stream_id=STREAM_ID,
        )


def recovery_authorization(
    state: GlobalHaltState,
    *,
    observation=None,
    policy=None,
    safety=None,
    **changes: object,
) -> GlobalHaltRecoveryAuthorization:
    selected_observation = observation or runtime_observation()
    selected_policy = policy or runtime_policy()
    selected_safety = safety or safety_observation(observed_at=NOW + timedelta(seconds=2))
    values: dict[str, object] = {
        "authorization_id": uid(740),
        "authorization_digest": "b" * 64,
        "halted_generation": state.generation,
        "halted_transition_digest": state.transition_digest,
        "runtime_policy_digest": canonical_model_digest(selected_policy),
        "runtime_observation_digest": canonical_model_digest(selected_observation),
        "portfolio_digest": canonical_model_digest(selected_observation.portfolio),
        "safety_binding_digest": global_safety_binding_digest(selected_safety),
        "issued_at": NOW + timedelta(seconds=1),
        "expires_at": NOW + timedelta(minutes=1),
        "operator_authority_digest": "c" * 64,
        "schema_version": "global-halt-recovery-authorization-v1",
    }
    values.update(changes)
    return GlobalHaltRecoveryAuthorization(**values)


class ExactVerifier:
    def verify(self, **kwargs: object) -> GlobalHaltRecoveryAuthorization:
        authorization = kwargs["authorization"]
        assert type(authorization) is GlobalHaltRecoveryAuthorization
        return authorization


def halted_ledger() -> tuple[InMemoryEventLedger, GlobalHaltState]:
    ledger = InMemoryEventLedger()
    state = initialize(ledger, observation=runtime_observation(daily_pnl=money("-1001")))
    return ledger, state


def recover(
    ledger: InMemoryEventLedger,
    authorization: GlobalHaltRecoveryAuthorization,
    *,
    observation=None,
    policy=None,
    safety=None,
    verifier: object | None = None,
    transition_id: UUID = uid(741),
    event_id: UUID = uid(742),
    decided_at: datetime = NOW + timedelta(seconds=3),
    safety_verifier: object | None = None,
) -> GlobalHaltState:
    return recover_global_halt(
        repository=ledger,
        stream_id=STREAM_ID,
        observation=observation or runtime_observation(),
        policy=policy or runtime_policy(),
        safety=safety or safety_observation(observed_at=NOW + timedelta(seconds=2)),
        safety_verifier=safety_verifier or ExactSafetyVerifier(),
        authorization=authorization,
        verifier=verifier or ExactVerifier(),
        transition_id=transition_id,
        event_id=event_id,
        decided_at=decided_at,
    )


@pytest.mark.parametrize("evidence", ("alternate-root", "fabricated"))
def test_global_halt_recovery_rejects_untrusted_inactive_safety(
    tmp_path: Path,
    evidence: str,
) -> None:
    canonical_root = tmp_path / "canonical"
    alternate_root = tmp_path / "alternate"
    _private_active_safety_root(canonical_root)
    alternate_root.mkdir()
    observed_at = NOW + timedelta(seconds=2)
    if evidence == "alternate-root":
        supplied = observe_global_safety(
            source_root=alternate_root,
            observed_at=observed_at,
        )
    else:
        supplied = GlobalSafetyObservation(
            source_fingerprint=safety_source_fingerprint(canonical_root),
            kill_switch_state=CanonicalKillSwitchState.INACTIVE,
            observed_at=observed_at,
            schema_version="global-safety-observation-v1",
        )
    ledger, halted = halted_ledger()
    authorization = recovery_authorization(halted, safety=supplied)

    with pytest.raises(GlobalHaltRecoveryError):
        recover(
            ledger,
            authorization,
            safety=supplied,
            safety_verifier=FilesystemGlobalSafetyAuthority(canonical_root),
        )
    assert len(ledger.load_events()) == 1


def _recovery_freshness_case(
    authority: str,
    *,
    authority_at: datetime,
) -> tuple[
    InMemoryEventLedger,
    GlobalHaltRecoveryAuthorization,
    object,
    object,
    GlobalSafetyObservation,
]:
    policy = evaluator_case().policy.model_copy(
        update={"portfolio_max_age_seconds": 10}
    )
    breached = evaluator_case(current_quantity="1").observation.model_copy(
        update={
            "daily_pnl": Money(
                Decimal("-1001"),
                policy.max_daily_loss.currency,
            )
        }
    )
    ledger = InMemoryEventLedger()
    halted = initialize(
        ledger,
        observation=breached,
        policy=policy,
        safety=safety_observation(observed_at=NOW),
        decided_at=NOW,
    )
    safe = _freshness_observation(
        authority,
        authority_at=authority_at,
        observation_at=NOW + timedelta(seconds=7),
    )
    selected_safety = safety_observation(observed_at=NOW + timedelta(seconds=8))
    authorization = recovery_authorization(
        halted,
        observation=safe,
        policy=policy,
        safety=selected_safety,
        issued_at=NOW + timedelta(seconds=7),
        expires_at=NOW + timedelta(seconds=20),
    )
    return ledger, authorization, safe, policy, selected_safety


@pytest.mark.parametrize("authority", ("portfolio", "balance", "position", "mark"))
def test_global_halt_recovery_accepts_exact_nested_freshness_boundary(
    authority: str,
) -> None:
    ledger, authorization, observation, policy, selected_safety = (
        _recovery_freshness_case(authority, authority_at=NOW)
    )
    state = recover(
        ledger,
        authorization,
        observation=observation,
        policy=policy,
        safety=selected_safety,
        decided_at=NOW + timedelta(seconds=10),
    )
    assert state.status is GlobalHaltStatus.ACTIVE


@pytest.mark.parametrize("authority", ("portfolio", "balance", "position", "mark"))
def test_global_halt_recovery_rejects_one_microsecond_stale_nested_authority(
    authority: str,
) -> None:
    ledger, authorization, observation, policy, selected_safety = (
        _recovery_freshness_case(
            authority,
            authority_at=NOW - timedelta(microseconds=1),
        )
    )
    with pytest.raises(GlobalHaltRecoveryError):
        recover(
            ledger,
            authorization,
            observation=observation,
            policy=policy,
            safety=selected_safety,
            decided_at=NOW + timedelta(seconds=10),
        )
    assert len(ledger.load_events()) == 1


def test_safe_observation_cannot_recover_a_halted_stream_without_authority() -> None:
    ledger, halted = halted_ledger()
    retained = initialize(
        ledger,
        transition_id=uid(743),
        event_id=uid(744),
        decided_at=NOW + timedelta(seconds=3),
    )
    assert retained == halted
    assert len(ledger.load_events()) == 1


@pytest.mark.parametrize(
    "change",
    (
        {"expires_at": NOW + timedelta(seconds=2)},
        {"halted_generation": 2},
        {"halted_transition_digest": "0" * 64},
        {"runtime_policy_digest": "1" * 64},
        {"runtime_observation_digest": "2" * 64},
        {"portfolio_digest": "3" * 64},
        {"safety_binding_digest": "4" * 64},
    ),
)
def test_global_halt_recovery_rejects_expired_stale_or_forged_authority(
    change: dict[str, object],
) -> None:
    ledger, halted = halted_ledger()
    authorization = recovery_authorization(halted, **change)
    with pytest.raises(GlobalHaltRecoveryError):
        recover(ledger, authorization)
    assert replay_global_halt_authority(events=ledger.load_events(), stream_id=STREAM_ID).state == halted


def test_exact_verified_global_halt_recovery_rotates_generation() -> None:
    ledger, halted = halted_ledger()
    safe = runtime_observation()
    safety = safety_observation(observed_at=NOW + timedelta(seconds=2))
    authorization = recovery_authorization(halted, observation=safe, safety=safety)
    recovered = recover(ledger, authorization, observation=safe, safety=safety)
    assert recovered.status is GlobalHaltStatus.ACTIVE
    assert recovered.generation == halted.generation + 1
    assert recovered.reason_codes == (GlobalHaltReasonCode.RECOVERY_AUTHORIZED,)
    assert recovered.prior_transition_event_id == halted.transition_event_id
    assert recovered.prior_transition_digest == halted.transition_digest
    assert len(ledger.load_events()) == 2


def test_global_halt_recovery_rejects_runtime_observation_after_decision() -> None:
    ledger, halted = halted_ledger()
    future = runtime_observation(observed_at=NOW + timedelta(seconds=4))
    authorization = recovery_authorization(halted, observation=future)
    with pytest.raises(GlobalHaltRecoveryError):
        recover(ledger, authorization, observation=future)


def test_global_halt_recovery_rejects_stale_runtime_observation() -> None:
    ledger, halted = halted_ledger()
    authorization = recovery_authorization(halted)
    with pytest.raises(GlobalHaltRecoveryError):
        recover(
            ledger,
            authorization,
            decided_at=NOW + timedelta(seconds=11),
        )


def test_global_halt_recovery_rejects_authorization_issued_before_halt() -> None:
    ledger, halted = halted_ledger()
    authorization = recovery_authorization(
        halted,
        issued_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=1),
    )
    with pytest.raises(GlobalHaltRecoveryError):
        recover(ledger, authorization)


def test_exact_global_halt_recovery_retry_returns_same_durable_state() -> None:
    ledger, halted = halted_ledger()
    authorization = recovery_authorization(halted)
    first = recover(ledger, authorization)
    second = recover(ledger, authorization)
    assert second == first
    assert len(ledger.load_events()) == 2


def test_global_halt_recovery_rejects_conflicting_same_event_id_retry() -> None:
    ledger, halted = halted_ledger()
    authorization = recovery_authorization(halted)
    recovered = recover(ledger, authorization)
    with pytest.raises(GlobalHaltRecoveryError):
        recover(
            ledger,
            authorization,
            transition_id=uid(745),
            event_id=recovered.transition_event_id,
        )


class PostCommitReadFailureLedger(InMemoryEventLedger):
    def __init__(self) -> None:
        super().__init__()
        self.fail_read_after_next_append = False
        self._fail_next_read = False

    def append(
        self, event: EventEnvelope[object], outbox: OutboxIntent
    ) -> AppendOutcome:
        outcome = super().append(event, outbox)
        if self.fail_read_after_next_append:
            self.fail_read_after_next_append = False
            self._fail_next_read = True
        return outcome

    def load_events(self) -> tuple[EventEnvelope[object], ...]:
        if self._fail_next_read:
            self._fail_next_read = False
            raise RuntimeError("ambiguous post-commit read failure")
        return super().load_events()


def test_global_halt_recovery_retry_resolves_ambiguous_post_commit_failure() -> None:
    ledger = PostCommitReadFailureLedger()
    halted = initialize(
        ledger,
        observation=runtime_observation(daily_pnl=money("-1001")),
    )
    authorization = recovery_authorization(halted)
    ledger.fail_read_after_next_append = True
    with pytest.raises(GlobalHaltRecoveryError):
        recover(ledger, authorization)
    recovered = recover(ledger, authorization)
    assert recovered.status is GlobalHaltStatus.ACTIVE
    assert recovered.generation == halted.generation + 1
    assert len(ledger.load_events()) == 2


@pytest.mark.parametrize("behavior", ("raise", "wrong-type", "altered"))
def test_global_halt_recovery_requires_exact_verifier_result(behavior: str) -> None:
    ledger, halted = halted_ledger()
    authorization = recovery_authorization(halted)

    class Verifier:
        def verify(self, **kwargs: object) -> object:
            if behavior == "raise":
                raise RuntimeError("private verifier detail")
            if behavior == "wrong-type":
                return object()
            return authorization.model_copy(update={"authorization_digest": "d" * 64})

    with pytest.raises(GlobalHaltRecoveryError) as caught:
        recover(ledger, authorization, verifier=Verifier())
    assert "private verifier detail" not in str(caught.value)


class BoundaryRepository:
    def __init__(
        self,
        *,
        append_error: Exception | None = None,
        mutate_read_back: Callable[[EventEnvelope[object]], EventEnvelope[object]] | None = None,
    ) -> None:
        self.events: list[EventEnvelope[object]] = []
        self.append_error = append_error
        self.mutate_read_back = mutate_read_back
        self.outbox: OutboxIntent | None = None

    def append(self, event: EventEnvelope[object], outbox: OutboxIntent) -> AppendOutcome:
        if self.append_error is not None:
            raise self.append_error
        self.events.append(event)
        self.outbox = outbox
        return AppendOutcome(event_id=event.event_id, inserted=True)

    def load_events(self) -> tuple[EventEnvelope[object], ...]:
        if self.mutate_read_back is not None and self.events:
            return (self.mutate_read_back(self.events[0]),)
        return tuple(self.events)


class MalformedReadBackRecord:
    @property
    def event_id(self) -> UUID:
        raise RuntimeError("private malformed record detail")


@pytest.mark.parametrize(
    "repository",
    (
        BoundaryRepository(append_error=RuntimeError("private append detail")),
        BoundaryRepository(mutate_read_back=lambda event: event.model_copy(update={"source": "mutated"})),
    ),
)
def test_global_halt_durable_boundary_rejects_append_or_byte_mismatch(
    repository: BoundaryRepository,
) -> None:
    with pytest.raises(GlobalHaltAuthorityError) as caught:
        initialize(repository, transition_id=uid(750), event_id=uid(751))
    assert "private append detail" not in str(caught.value)


def test_global_halt_read_back_accessor_failure_is_bounded() -> None:
    repository = BoundaryRepository(
        mutate_read_back=lambda event: MalformedReadBackRecord()  # type: ignore[arg-type,return-value]
    )
    with pytest.raises(GlobalHaltAuthorityError) as caught:
        initialize(repository, transition_id=uid(752), event_id=uid(753))
    assert "private malformed record detail" not in str(caught.value)


def test_global_halt_trusted_byte_identical_replica_replays_same_state() -> None:
    origin = InMemoryEventLedger()
    expected = initialize(origin)
    replica = InMemoryEventLedger()
    event = origin.load_events()[0]
    replica.append(event, OutboxIntent(event_id=event.event_id, topic="trusted-replica.audit"))
    actual = replay_global_halt_authority(events=replica.load_events(), stream_id=STREAM_ID)
    assert replica.load_outbox() != origin.load_outbox()
    assert serialize_event(replica.load_events()[0]) == serialize_event(event)
    assert actual.state == expected
    audit_submit_authority_stream,
