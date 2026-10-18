from pathlib import Path

ROOT = Path(__file__).parents[2]
OPERATIONS = (
    "p3-integration-fixture-v1", "p3-baselines-v1", "p3-register-family-v1",
    "p3-oos-a0-v1", "p3-oos-a1-v1", "p3-oos-a2-v1", "p3-oos-a3-v1",
    "p3-select-primary-v1", "p3-holdout-primary-v1", "p3-native-parity-v1",
    "p3-phase-exit-v1",
)


def _source():
    return (ROOT/".github/workflows/p3-authority.yml").read_text()


def _job(source, name):
    start = source.index(f"\n  {name}:\n")
    following = [source.find(f"\n  {other}:\n", start+1) for other in ("p3", "attest-phase-exit") if other != name]
    ends = [index for index in following if index > start]
    return source[start:min(ends) if ends else len(source)]


def test_p3_workflow_has_only_fixed_dispatch_operations() -> None:
    source = _source()
    options = source[source.index("        options:\n"):source.index("\npermissions:")]
    assert tuple(line.strip()[2:] for line in options.splitlines()[1:] if line.strip()) == OPERATIONS
    assert "run-name: p3 ${{ inputs.operation }}" in source
    assert "github.event.inputs.operation }}" not in source
    assert '"${{ github.ref }}" != \'refs/heads/main\'' in source
    assert '"${{ github.repository }}" != "nam176hermes/Trading-Agent"' in source
    assert "${{ inputs.operation }}-${{ github.run_id }}-${{ github.run_attempt }}" in source
    assert "concurrency:\n  group: p3-authority\n  cancel-in-progress: false\n" in source


def test_p3_job_holds_no_secrets_or_authoritative_tooling() -> None:
    """Authority comes from the root controller's API attestation, never the runner job."""
    source = _source()
    job = _job(source, "p3")
    assert "secrets." not in source
    assert "uv run" not in job and "services.job_worker" not in job and "sudo" not in job
    assert "runs-on: [self-hosted, p3-authority, 'p3-run-${{ github.run_id }}-${{ github.run_attempt }}']" in job
    assert "permissions:\n  contents: read\n" in source and "permissions:" not in job
    assert '/srv/trading-agent-p3/outbox/${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}' in job
    assert "status.json|p3-phase-exit-v2.json|p3-phase-exit-v1.json)" in job


def test_p3_job_timeout_covers_the_hosted_session_bound() -> None:
    from services.job_worker.p3_session import HOSTED_SESSION_MINUTES
    job = _job(_source(), "p3")
    assert "timeout-minutes: ${{ inputs.operation == 'p3-holdout-primary-v1' && 180 || 60 }}" in job
    assert HOSTED_SESSION_MINUTES + 20 <= 180


def test_only_the_hosted_attestation_job_can_mint_identity_tokens() -> None:
    source = _source()
    attest = _job(source, "attest-phase-exit")
    assert source.count("id-token: write") == 1 and "id-token: write" in attest
    assert "    needs: p3\n    runs-on: ubuntu-24.04\n" in attest
    assert "if: inputs.operation == 'p3-holdout-primary-v1' && needs.p3.outputs.phase_exit == 'PASS'" in attest
    assert ("permissions:\n      contents: read\n      id-token: write\n      attestations: write\n"
            "      artifact-metadata: write\n") in attest
    assert attest.index("actions/download-artifact@v4") < attest.index("actions/attest@v4")
    assert "subject-path: ${{ runner.temp }}/p3/p3-phase-exit-v1.json" in attest
    assert "qualification_run_id=int(os.environ['GITHUB_RUN_ID'])" in attest
    assert "receipt['source']['commit_sha'] != os.environ['GITHUB_SHA']" in attest


def test_foundation_attests_exact_p3_promotion() -> None:
    source = (ROOT/".github/workflows/foundation.yml").read_text()
    assert "scripts/record_p3_promotion.py" in source
    assert "attest-p3-promotion" in source
    assert "p3-promotion-${{ github.run_id }}-${{ github.run_attempt }}" in source
    assert "docs/implementation/p3/receipts/p3-phase-exit-v1.json" in source


def test_no_pull_request_workflow_targets_a_self_hosted_runner() -> None:
    """Public repository: untrusted PR code must never reach a qualification host (F-02)."""
    untrusted = ("pull_request", "pull_request_target", "issue_comment", "workflow_run")
    for path in (ROOT/".github/workflows").glob("*.yml"):
        source = path.read_text()
        triggers = source[source.index("\non:"):source.index("\njobs:")]
        if "self-hosted" in source:
            assert not any(event in triggers for event in untrusted), path.name
