"""Retained seven-case PIT execution evidence; parsing never grants authority."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from packages.alpha_lifecycle.baseline_campaign import ArtifactStore, _read
from packages.alpha_lifecycle.contracts.base import DigestModel, SafeAuthority, Sha256, SourceIdentity, StrictModel, Text
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import CanonicalUtcDateTime, canonical_json_bytes


PIT_EVIDENCE_MEDIA = 'application/vnd.trading-agent.pit-evidence+json'
SUITE_PATH = Path(__file__).resolve().parents[2]/'docs/implementation/pre-p3/p2-pit-adversarial-suite-v1.json'


class PITCase(StrictModel):
    case_id: Text
    node_id: Text


class PITQualification(StrictModel):
    completed_at_utc: CanonicalUtcDateTime
    producer: Literal['scripts/qualify_pre_p3.py']
    run_id: Annotated[str, Field(pattern=r'^[1-9][0-9]*$')]
    run_attempt: Annotated[str, Field(pattern=r'^[1-9][0-9]*$')]


class PITAdversarialSuiteReceiptV1(DigestModel):
    schema_version: Literal['p3-pit-adversarial-suite-receipt-v1']
    source: SourceIdentity
    suite_manifest_ref: ArtifactRefV1
    collection_ref: ArtifactRefV1
    report_ref: ArtifactRefV1
    cases: Annotated[tuple[PITCase, ...], Field(min_length=7, max_length=7)]
    executed_node_ids: Annotated[tuple[Text, ...], Field(min_length=14, max_length=14)]
    logical_selector_count: Annotated[int, Field(strict=True, ge=7, le=7)]
    collected_node_count: Annotated[int, Field(strict=True, ge=14, le=14)]
    passed_node_count: Annotated[int, Field(strict=True, ge=14, le=14)]
    nonpass_count: Annotated[int, Field(strict=True, ge=0, le=0)]
    qualification: PITQualification
    authority: SafeAuthority


def pit_suite_cases(raw: bytes) -> tuple[PITCase, ...]:
    if raw != SUITE_PATH.read_bytes():
        raise ValueError('PIT suite differs from the current tracked manifest')
    suite = json.loads(raw)
    if (set(suite) != {'schema_version','required_cutoff_rule','cases'}
        or suite['schema_version'] != 'p2-pit-adversarial-suite-v1'):
        raise ValueError('PIT suite manifest contract differs')
    cases = tuple(PITCase.model_validate(case) for case in suite['cases'])
    if len(cases) != 7 or len({c.case_id for c in cases}) != 7 or len({c.node_id for c in cases}) != 7:
        raise ValueError('PIT suite requires seven unique cases and nodes')
    return cases


def _validate_observations(raw: bytes, cases: tuple[PITCase, ...], *, collection: bool) -> tuple[str, ...]:
    if len(raw) > 131072:
        raise ValueError('PIT observation report exceeds its bound')
    report = json.loads(raw)
    if (not isinstance(report, dict)
        or raw != (canonical_json_bytes(report) if collection else (json.dumps(report, indent=2, sort_keys=True)+'\n').encode())
        or set(report) != {'schema_version','component','collection_only','pytest_exit_status','summary','tests'}
        or type(report['schema_version']) is not int or report['schema_version'] != 1
        or report['component'] != 'root' or report['collection_only'] is not collection
        or type(report['pytest_exit_status']) is not int or report['pytest_exit_status'] != 0):
        raise ValueError('PIT observation report did not prove complete execution')
    rows = report['tests']
    if (not isinstance(rows, list) or len(rows) != 14
        or any(not isinstance(row, dict) or not isinstance(row.get('test_node_id'), str) for row in rows)):
        raise ValueError('PIT observation rows are invalid')
    nodes = tuple(row['test_node_id'] for row in rows)
    bases = {case.node_id for case in cases}
    if (nodes != tuple(sorted(set(nodes)))
        or any(sum(node == base or (node.startswith(base+'[') and node.endswith(']')) for base in bases) != 1 for node in nodes)
        or {node.split('[',1)[0] for node in nodes} != bases):
        raise ValueError('PIT observations omit or add a logical case')
    outcome, phase = ('collected','collection') if collection else ('passed','call')
    expected = [dict(test_node_id=node, component='root', outcome=outcome, reason='', phase=phase) for node in nodes]
    if (report['tests'] != expected or report['summary'] != {outcome:len(nodes)}
        or any(type(count) is not int for count in report['summary'].values())):
        raise ValueError('PIT observation inventory or call outcomes differ')
    return nodes


def _json_ref(ref: ArtifactRefV1, maximum: int, *, exact: bool = False, media: str = 'application/json') -> None:
    if (ref.media_type != media or type(ref.size_bytes) is not int
        or not 0 < ref.size_bytes <= maximum or (exact and ref.size_bytes != maximum)
        or ref.locator != ref.content_sha256+'.blob'):
        raise ValueError('PIT artifact reference has invalid media, size or locator')


def build_pit_suite_receipt(source, suite_manifest_ref, collection_ref, report_ref, qualification,
    *, store: ArtifactStore) -> PITAdversarialSuiteReceiptV1:
    _json_ref(suite_manifest_ref, len(SUITE_PATH.read_bytes()), exact=True, media=PIT_EVIDENCE_MEDIA)
    _json_ref(collection_ref, 131072)
    _json_ref(report_ref, 131072, media=PIT_EVIDENCE_MEDIA)
    cases = pit_suite_cases(store.read_bytes(suite_manifest_ref))
    collected = _validate_observations(store.read_bytes(collection_ref), cases, collection=True)
    executed = _validate_observations(store.read_bytes(report_ref), cases, collection=False)
    if collected != executed:
        raise ValueError('PIT execution differs from complete collected parameter inventory')
    value = dict(schema_version='p3-pit-adversarial-suite-receipt-v1', source=source,
        suite_manifest_ref=suite_manifest_ref, collection_ref=collection_ref, report_ref=report_ref, cases=cases,
        executed_node_ids=executed, logical_selector_count=7, collected_node_count=len(collected), passed_node_count=len(executed), nonpass_count=0,
        qualification=qualification, authority=dict(broker=False, live=False, network=False, production=False))
    value['digest'] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return PITAdversarialSuiteReceiptV1.model_validate_json(canonical_json_bytes(value))


def validate_pit_suite_receipt(ref: ArtifactRefV1, *, source: SourceIdentity,
    store: ArtifactStore) -> PITAdversarialSuiteReceiptV1:
    _json_ref(ref, 65536)
    receipt = _read(store, ref, PITAdversarialSuiteReceiptV1)
    if receipt.source != source:
        raise ValueError('PIT execution source differs from InputSet')
    expected = build_pit_suite_receipt(source, receipt.suite_manifest_ref, receipt.collection_ref, receipt.report_ref,
        receipt.qualification, store=store)
    if canonical_json_bytes(receipt) != canonical_json_bytes(expected):
        raise ValueError('PIT receipt differs from executed observations')
    return receipt


__all__ = ['PITAdversarialSuiteReceiptV1', 'build_pit_suite_receipt', 'pit_suite_cases', 'validate_pit_suite_receipt']
