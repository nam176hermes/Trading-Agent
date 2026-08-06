from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = ROOT / "generated" / "domain" / "json-schema"
EXPECTED = {
    "MarketCandle.json",
    "MarketDataProvenance.json",
    "MarketSnapshot.json",
    "MarketContinuity.json",
    "EvidenceReference.json",
    "ResearchPacket.json",
    "SignalProposal.json",
    "TargetPosition.json",
    "TargetPortfolio.json",
    "PositionSnapshot.json",
    "PortfolioSnapshot.json",
    "RiskStateSnapshot.json",
    "RiskDecision.json",
    "OrderIntent.json",
    "OrderEvent.json",
    "OrderState.json",
    "FillEvent.json",
    "EventEnvelope_SignalProposal_.json",
    "EventEnvelope_TargetPortfolio_.json",
    "EventEnvelope_RiskDecision_.json",
    "EventEnvelope_OrderIntent_.json",
    "EventEnvelope_OrderEvent_.json",
    "EventEnvelope_FillEvent_.json",
    "StoredEvent.json",
    "ReplayIssue.json",
    "AppliedEvent.json",
    "EventTypeCount.json",
    "StreamProjection.json",
    "AggregateReplayState.json",
    "ReplayResult.json",
    "SnapshotRecord.json",
    "OutboxIntent.json",
    "AppendOutcome.json",
}


def test_contract_generation_is_deterministic_and_current() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/generate_contracts.py"], cwd=ROOT, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    after = {path.name: path.read_bytes() for path in SCHEMA_ROOT.glob("*.json")}
    assert set(after) == EXPECTED
    check = subprocess.run(
        [sys.executable, "scripts/generate_contracts.py", "--check"], cwd=ROOT, text=True, capture_output=True
    )
    assert check.returncode == 0, check.stdout + check.stderr


@pytest.mark.parametrize("filename", sorted(EXPECTED))
def test_domain_schema_is_valid_json_and_strict(filename: str) -> None:
    path = SCHEMA_ROOT / filename
    assert path.is_file()
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize(
    ("filename", "fields"),
    [
        ("SignalProposal.json", {"research_packet_id"}),
        ("TargetPortfolio.json", {"source_signal_ids"}),
        (
            "RiskStateSnapshot.json",
            {"state_id", "portfolio", "open_order_ids", "kill_switch_engaged", "observed_at", "schema_version"},
        ),
        ("OrderIntent.json", {"risk_decision_id"}),
    ],
)
def test_domain_provenance_and_risk_state_fields_are_required_in_schema(
    filename: str, fields: set[str]
) -> None:
    schema = json.loads((SCHEMA_ROOT / filename).read_text(encoding="utf-8"))
    assert fields <= set(schema["required"])


def test_order_contracts_publish_unsigned_identity_and_uppercase_lifecycle_data() -> None:
    intent = json.loads((SCHEMA_ROOT / "OrderIntent.json").read_text(encoding="utf-8"))
    assert intent["properties"]["quantity"]["$ref"] == "#/$defs/OrderQuantity"
    assert "Quantity" not in intent["$defs"]
    assert {
        "client_order_id",
        "strategy_id",
        "trader_id",
        "account_id",
        "execution_client_id",
    } <= set(intent["required"])

    event = json.loads((SCHEMA_ROOT / "OrderEvent.json").read_text(encoding="utf-8"))
    assert event["$defs"]["OrderStatus"]["enum"] == [
        "INITIALIZED",
        "SUBMITTED",
        "ACCEPTED",
        "PENDING_UPDATE",
        "PENDING_CANCEL",
        "TRIGGERED",
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCELED",
        "EXPIRED",
        "REJECTED",
        "DENIED",
    ]
    assert event["properties"]["event_fingerprint"]["pattern"] == "^[0-9a-f]{64}$"

    state = json.loads((SCHEMA_ROOT / "OrderState.json").read_text(encoding="utf-8"))
    assert state["properties"]["applied_events"]["items"]["$ref"] == "#/$defs/OrderEvent"


def test_risk_decision_schema_exposes_closed_reasons_and_outcome_invariants() -> None:
    schema = json.loads((SCHEMA_ROOT / "RiskDecision.json").read_text(encoding="utf-8"))
    reason_enum = schema["$defs"]["RiskReasonCode"]["enum"]
    assert reason_enum == [
        "WITHIN_LIMITS",
        "DATA_STALE",
        "SIGNAL_EXPIRED",
        "MODEL_NOT_APPROVED",
        "PRICE_OUTSIDE_COLLAR",
        "ORDER_NOTIONAL_LIMIT",
        "GROSS_EXPOSURE_LIMIT",
        "MARGIN_BUFFER_LIMIT",
        "DAILY_LOSS_LIMIT",
        "VENUE_DEGRADED",
        "DUPLICATE_COMMAND",
        "GLOBAL_HALT",
    ]
    assert schema["properties"]["approved_target"]["anyOf"][-1] == {"type": "null"}
    assert schema["x-risk-invariants"] == [
        "outcome == approved => approved_target == original_target",
        "outcome == modified => approved_target has a new identity and changed positions",
        "outcome == rejected => approved_target == null",
        "state_snapshot.observed_at <= decided_at",
        "original_target.effective_at <= decided_at",
        "kill_switch_engaged => outcome == rejected and GLOBAL_HALT in reason_codes",
    ]
    state_schema = json.loads(
        (SCHEMA_ROOT / "RiskStateSnapshot.json").read_text(encoding="utf-8")
    )
    assert state_schema["x-temporal-invariants"] == [
        "portfolio.observed_at <= observed_at",
        "portfolio.positions[*].observed_at <= portfolio.observed_at",
    ]


def test_decimal_wire_schemas_use_bounded_canonical_strings() -> None:
    signal_schema = json.loads(
        (SCHEMA_ROOT / "SignalProposal.json").read_text(encoding="utf-8")
    )
    for field in ("score", "confidence"):
        property_schema = signal_schema["properties"][field]
        assert property_schema["type"] == "string"
        assert property_schema["pattern"]
        assert "ge" not in property_schema
        assert "le" not in property_schema
        assert "minimum" not in property_schema
        assert "maximum" not in property_schema

    position_schema = json.loads(
        (SCHEMA_ROOT / "TargetPosition.json").read_text(encoding="utf-8")
    )
    weight_schema = position_schema["properties"]["target_weight"]
    assert weight_schema["type"] == "string"
    assert weight_schema["pattern"]
    assert "ge" not in weight_schema
    assert "le" not in weight_schema


def test_outbox_payload_schema_declares_canonical_json_transport_metadata() -> None:
    schema = json.loads((SCHEMA_ROOT / "OutboxIntent.json").read_text(encoding="utf-8"))
    payload = schema["properties"]["payload_json"]

    assert payload["contentMediaType"] == "application/json"
    assert payload["x-canonical-json"] is True
    assert payload["minLength"] == 2
    assert payload["maxLength"] == 65536


def test_nested_d01_decimal_wire_schemas_are_canonical_strings() -> None:
    schema = json.loads(
        (SCHEMA_ROOT / "EventEnvelope_FillEvent_.json").read_text(encoding="utf-8")
    )
    for definition, field in (("Money", "amount"), ("Price", "amount"), ("Quantity", "value")):
        property_schema = schema["$defs"][definition]["properties"][field]
        assert property_schema["type"] == "string"
        assert property_schema["pattern"]
        assert "anyOf" not in property_schema


def test_signal_evidence_schema_is_typed_bounded_and_point_in_time_explicit() -> None:
    evidence_schema = json.loads(
        (SCHEMA_ROOT / "EvidenceReference.json").read_text(encoding="utf-8")
    )
    assert evidence_schema["properties"]["source"]["$ref"] == "#/$defs/EvidenceSource"
    assert evidence_schema["properties"]["locator"]["$ref"] == "#/$defs/EvidenceLocator"
    locator = evidence_schema["$defs"]["EvidenceLocator"]
    assert locator["additionalProperties"] is False
    assert locator["properties"]["authority"]["maxLength"] == 128
    assert locator["properties"]["path"]["maxItems"] == 16
    assert locator["properties"]["path"]["items"]["maxLength"] == 64
    assert locator["x-prohibited-content"] == [
        "credentials",
        "account-routing",
        "order-type",
        "execution-text",
    ]

    packet_schema = json.loads(
        (SCHEMA_ROOT / "ResearchPacket.json").read_text(encoding="utf-8")
    )
    assert packet_schema["x-temporal-invariants"] == [
        "evidence.observed_at <= cutoff_at"
    ]

    signal_schema = json.loads(
        (SCHEMA_ROOT / "SignalProposal.json").read_text(encoding="utf-8")
    )
    assert "research_packet_cutoff_at" in signal_schema["required"]
    assert signal_schema["x-temporal-invariants"] == [
        "research_packet_cutoff_at == cutoff_at",
        "evidence.observed_at <= cutoff_at",
        "cutoff_at < expires_at",
    ]


@pytest.mark.parametrize(
    "filename",
    [
        "EventEnvelope_SignalProposal_.json",
        "EventEnvelope_TargetPortfolio_.json",
        "EventEnvelope_RiskDecision_.json",
        "EventEnvelope_OrderIntent_.json",
        "EventEnvelope_OrderEvent_.json",
        "EventEnvelope_FillEvent_.json",
    ],
)
def test_typed_event_envelope_schemas_constrain_event_type_to_payload_name(
    filename: str,
) -> None:
    schema = json.loads((SCHEMA_ROOT / filename).read_text(encoding="utf-8"))
    expected = filename.removeprefix("EventEnvelope_").removesuffix("_.json")
    assert schema["properties"]["event_type"] == {"const": expected, "type": "string"}


@pytest.mark.parametrize("filename", ("ReplayResult.json", "SnapshotRecord.json"))
def test_replay_public_contracts_require_versioned_canonical_hash_fields(filename: str) -> None:
    schema = json.loads((SCHEMA_ROOT / filename).read_text(encoding="utf-8"))
    assert {"schema_version", "reducer_version", "canonical_state_json", "state_hash"} <= set(schema["required"])
    assert schema["properties"]["state_hash"]["pattern"] == "^[0-9a-f]{64}$"
