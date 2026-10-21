"""Issuer-lane dispatch used by the root host controller."""
from pathlib import Path

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes


def _github_request(monkeypatch, *, operation='p3-baselines-v1'):
    from datetime import UTC, datetime, timedelta
    from tests.p3.github_lane import GithubLane
    from tests.p3.test_authority import _request
    from tests.p3.test_local_qualification import sealed
    from tests.p3.test_operation_input import intent
    import json
    request, source = _request('2026-01-01T01:00:00Z')
    now = datetime.now(UTC)
    request['authorization'] = sealed({**request['authorization'],
        'issued_at': (now - timedelta(minutes=1)).isoformat().replace('+00:00', 'Z'),
        'expires_at': (now + timedelta(minutes=30)).isoformat().replace('+00:00', 'Z')})
    request['operation_input'] = json.loads(intent(input_set_ref=request['authorization']['input_set_ref']))
    lane = GithubLane(monkeypatch, source=source)
    lane.admit_inputs(request['authorization']['input_set_ref'], operation=operation)
    return lane, lane.request(request), source


def test_github_operation_is_admitted_with_its_run_directory(monkeypatch):
    from scripts.p3_lane import admitted_operation, issuer_kind
    lane, path, source = _github_request(monkeypatch)
    request, identity, directory = admitted_operation(path, source=source)
    assert issuer_kind(identity) == 'GITHUB'
    assert (identity.run_id, identity.attempt) == (1, 1)
    assert directory == Path('/run/trading-agent-p3/1-1')
    assert request.authorization.issuer_run_id == 1


@pytest.mark.parametrize('drift', ['head', 'revoked', 'stale', 'operation', 'host'])
def test_github_operation_admission_follows_the_run_attestation(monkeypatch, drift):
    from packages.alpha_lifecycle.authority import AuthorityHeld
    from scripts.p3_lane import admitted_operation
    lane, path, source = _github_request(monkeypatch)
    lane.drift(drift, 'p3-baselines-v1')
    with pytest.raises((AuthorityHeld, ValueError)):
        admitted_operation(path, source=source)


def test_transport_identity_round_trips_and_lanes_use_separate_locks():
    from packages.alpha_lifecycle.contracts.authority import GithubRunIdentity
    from packages.local_authority import AuthorityStore, STORE_ROOT
    from scripts.p3_lane import GITHUB_AUTHORITY_ROOT, identity_from_transport, run_directory, transport_identity
    identity = GithubRunIdentity.model_validate_json(canonical_json_bytes(dict(issuer_kind='GITHUB',
        issuer_identity='p3-authority', repository='nam176hermes/Trading-Agent', workflow_ref='p3-authority.yml',
        run_id=9, attempt=2)))
    carried = transport_identity(identity, 'a' * 64, '00000000-0000-4000-8000-000000000001')
    assert carried['host_id'] == 'a' * 64 and identity_from_transport(carried) == identity
    assert run_directory(identity) == Path('/run/trading-agent-p3/9-2')
    assert AuthorityStore('p3-authority', root=GITHUB_AUTHORITY_ROOT).path != STORE_ROOT/'p3-authority'
    assert not GITHUB_AUTHORITY_ROOT.is_relative_to(STORE_ROOT)


def test_authorization_identity_resolves_github_run_authorization(monkeypatch):
    # The official transport compares this with its request identity; a GITHUB
    # RunAuthorization has no `.identity` (S7 run 900000000304 HELD on AttributeError).
    from scripts.p3_lane import admitted_operation, authorization_identity, identity_from_transport, transport_identity
    lane, path, source = _github_request(monkeypatch)
    request, identity, _ = admitted_operation(path, source=source)
    carried = transport_identity(identity, 'a' * 64, '00000000-0000-4000-8000-000000000001')
    assert authorization_identity(request.authorization) == identity == identity_from_transport(carried)


def test_transport_admission_compares_a_github_authorization_with_the_carried_identity(monkeypatch):
    """The exact check the official transport runs (S7 run 900000000304), with a real GITHUB authorization."""
    from scripts.p3_lane import admitted_operation, transport_identity
    from scripts.p3_local_transport import TransportHeld, _require_operation_identity
    lane, path, source = _github_request(monkeypatch)
    request, identity, _ = admitted_operation(path, source=source)
    carried = transport_identity(identity, 'a' * 64, '00000000-0000-4000-8000-000000000001')
    _require_operation_identity(request.authorization, carried)
    with pytest.raises(TransportHeld):
        _require_operation_identity(request.authorization, {**carried, 'run_id': carried['run_id'] + 1})
