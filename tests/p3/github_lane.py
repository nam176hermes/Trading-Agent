"""Test double for the protected GITHUB lane: root-published documents and run attestation.

Serves canonical documents through `runtime_release.config._read_protected_file` exactly
as root custody would, pins host identity, and publishes a fresh API attestation for one
run. Nothing here sets GITHUB_* variables; the lane must not need them.
"""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path
from uuid import UUID

from packages.engine_contracts.serialization import canonical_json_bytes

HOST = ('b' * 64, UUID('00000000-0000-4000-8000-000000000009'))
AUTHORITY = dict(broker=False, live=False, network=False, production=False)


def _sealed(value):
    value = {k: v for k, v in value.items() if k != 'digest'}
    return {**value, 'digest': hashlib.sha256(canonical_json_bytes(value)).hexdigest()}


def _z(value):
    return value.isoformat().replace('+00:00', 'Z')


def _real_host_identity():
    from packages.alpha_lifecycle import local_qualification
    return local_qualification._host_identity


# Captured at import, before any test replaces it, for tests that need the real host.
REAL_HOST_IDENTITY = _real_host_identity()


class GithubLane:
    def __init__(self, monkeypatch, *, source, run_id=1, attempt=1, operation='p3-baselines-v1'):
        from packages.alpha_lifecycle import local_qualification
        from packages.runtime_release import config
        self.documents: dict[Path, object] = {}
        self.source = source
        self.run_id, self.attempt = run_id, attempt
        self.directory = Path(f'/run/trading-agent-p3/{run_id}-{attempt}')
        fallback = config._read_protected_file

        def read(path, **options):
            if path in self.documents:
                document = self.documents[path]
                raw = document if isinstance(document, bytes) else canonical_json_bytes(document) + b'\n'
                return raw, (1, 2)
            if path.is_relative_to('/run/trading-agent-p3') or path.is_relative_to('/etc/trading-agent-p3'):
                raise FileNotFoundError(path)
            return fallback(path, **options)
        monkeypatch.setattr(config, '_read_protected_file', read)
        monkeypatch.setattr(local_qualification, '_host_identity', lambda: HOST)
        self.research_inputs_sha256 = '0' * 64
        self.attest(operation)
        # The controller's source-ready record (D-S7-02); consumers compare it with their own source.
        from packages.alpha_lifecycle import github_run
        monkeypatch.setattr(github_run, '_current_source', lambda root: self._source_dict())
        self.publish_source_ready()

    def _source_dict(self):
        return self.source if isinstance(self.source, dict) else self.source.model_dump(mode='json')

    def publish_source_ready(self, **changes):
        value = dict(schema_version='p3-github-source-ready-v1', mode='GITHUB',
            identity=dict(issuer_kind='GITHUB', issuer_identity='p3-authority', repository='nam176hermes/Trading-Agent',
                          workflow_ref='p3-authority.yml', run_id=self.run_id, attempt=self.attempt),
            source=self._source_dict(), project_status_sha256='9' * 64, observed_at=_z(datetime.now(UTC)),
            authority=AUTHORITY)
        value.update(changes)
        self.documents[self.directory/'source-ready.json'] = _sealed(value)

    def revoke_source_ready(self):
        self.documents.pop(self.directory/'source-ready.json', None)

    def attest(self, operation, **changes):
        value = dict(schema_version='p3-github-run-attestation-v1', mode='GITHUB',
            identity=dict(issuer_kind='GITHUB', issuer_identity='p3-authority', repository='nam176hermes/Trading-Agent',
                          workflow_ref='p3-authority.yml', run_id=self.run_id, attempt=self.attempt),
            head_sha=_commit(self.source), operation=operation, runner_name=f'p3-jit-{self.run_id}-{self.attempt}',
            observed_at=_z(datetime.now(UTC)), response_sha256=dict(run='1' * 64, jobs='2' * 64, branch='3' * 64),
            host_id=HOST[0], boot_id=str(HOST[1]), research_inputs_sha256=self.research_inputs_sha256,
            authority=AUTHORITY)
        value.update(changes)
        self.documents[self.directory/'github-run.json'] = _sealed(value)

    def admit_inputs(self, input_set_ref, *, campaign='REHEARSAL', operation='p3-baselines-v1'):
        """Publish a reviewed research-input receipt for this InputSet and pin it in the run."""
        from datetime import timedelta
        from packages.alpha_lifecycle.github_run import GithubResearchInputs
        now = datetime.now(UTC)
        source = self.source if isinstance(self.source, dict) else self.source.model_dump(mode='json')
        reference = input_set_ref if isinstance(input_set_ref, dict) else input_set_ref.model_dump(mode='json')
        def ref(digest):
            return dict(content_sha256=digest, size_bytes=1, media_type='application/json', locator=f'{digest}.blob')
        body = dict(schema_version='p3-github-research-inputs-v1', campaign=campaign, source=source,
                    policy_digest='1' * 64, input_ref=reference, environment_ref=ref('3' * 64),
                    integration_receipt_ref=ref('4' * 64), review_ref=ref('5' * 64),
                    issued_at=_z(now - timedelta(minutes=5)), expires_at=_z(now + timedelta(hours=6)),
                    authority=AUTHORITY)
        binding = GithubResearchInputs.model_validate(_sealed(body)).binding_digest
        review = _sealed(dict(schema_version='p3-review-approval-v1', source=source,
            subject_digests=[reference['content_sha256'], binding], operator_identity='nam176hermes',
            reviewer_identity='claude-agent-reviewer', review_execution_id='inputs-review', verdict='APPROVED',
            issued_at=_z(now - timedelta(minutes=10)), expires_at=_z(now + timedelta(hours=8)),
            evidence_ref=ref('6' * 64), authority=AUTHORITY))
        raw = canonical_json_bytes(review)
        body['review_ref'] = dict(content_sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw),
                                  media_type='application/json', locator=hashlib.sha256(raw).hexdigest()+'.blob')
        inputs = _sealed(body)
        self.documents[Path('/etc/trading-agent-p3/github/research-inputs.json')] = inputs
        self.documents[Path('/etc/trading-agent-p3/github/research-inputs-review.json')] = review
        self.research_inputs_sha256 = hashlib.sha256(canonical_json_bytes(inputs) + b'\n').hexdigest()
        self.attest(operation)

    DRIFTS = ('head', 'revoked', 'stale', 'operation', 'host')

    def drift(self, kind, operation):
        """Make the run attestation disagree in one way that GitHub context used to cover."""
        if kind == 'revoked':
            self.documents.pop(self.directory/'github-run.json')
        elif kind == 'head':
            self.attest(operation, head_sha='0' * 40)
        elif kind == 'stale':
            self.attest(operation, observed_at='2020-01-01T00:00:00Z')
        elif kind == 'operation':
            self.attest('p3-oos-a0-v1' if operation != 'p3-oos-a0-v1' else 'p3-baselines-v1')
        elif kind == 'host':
            self.attest(operation, host_id='0' * 64)
        else:
            raise AssertionError(kind)

    def request(self, document) -> Path:
        """Publish a request at its run-derived protected path and return that path."""
        nonce = document['authorization']['nonce']
        path = self.directory/'operations'/f'{nonce}.json'
        self.documents[path] = document
        return path


def _commit(source):
    return source['commit_sha'] if isinstance(source, dict) else source.commit_sha
