from packages.p3_status import derive_p3_status


def test_p3_status_is_held_without_external_phase_receipts(tmp_path) -> None:
    # Current repository has source authority but intentionally no official P3 outcome.
    status = derive_p3_status(__import__("pathlib").Path(__file__).parents[2])
    assert status.gates.phase_complete == "HELD"
    assert status.authority.live is False
    assert status.authority.broker is False


def test_structural_receipt_cannot_bypass_external_attestation(monkeypatch) -> None:
    monkeypatch.setattr("packages.p3_status._attested", lambda *args, **kwargs: False)
    status = derive_p3_status(__import__("pathlib").Path(__file__).parents[2])
    assert status.gates.pipeline_complete == "HELD"
    assert status.gates.phase_complete == "HELD"
