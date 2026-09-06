from pathlib import Path


def test_p3_workflow_has_only_fixed_dispatch_operations() -> None:
    source = (Path(__file__).parents[2]/".github/workflows/p3-authority.yml").read_text()
    for operation in (
        "p3-integration-fixture-v1","p3-baselines-v1","p3-register-family-v1",
        "p3-oos-a0-v1","p3-oos-a1-v1","p3-oos-a2-v1","p3-oos-a3-v1",
        "p3-select-primary-v1","p3-holdout-primary-v1","p3-native-parity-v1",
        "p3-phase-exit-v1",
    ):
        assert operation in source
    assert "P3_AUTHORITY_REQUEST_FILE" in source
    assert "github.event.inputs.operation }}" not in source
    assert '"${{ github.ref }}" != \'refs/heads/main\'' in source
    assert '"${{ github.repository }}" != "nam176hermes/Trading-Agent"' in source
    assert "dispatch --request-file" in source
    assert "${{ inputs.operation }}-${{ github.run_id }}-${{ github.run_attempt }}" in source


def test_foundation_attests_exact_p3_promotion() -> None:
    source = (Path(__file__).parents[2]/".github/workflows/foundation.yml").read_text()
    assert "scripts/record_p3_promotion.py" in source
    assert "attest-p3-promotion" in source
    assert "p3-promotion-${{ github.run_id }}-${{ github.run_attempt }}" in source
    assert "docs/implementation/p3/receipts/p3-phase-exit-v1.json" in source
