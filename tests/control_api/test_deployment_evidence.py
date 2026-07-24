from __future__ import annotations

from copy import deepcopy
import importlib
import json
from pathlib import Path

from pydantic import ValidationError
import pytest


def test_promotion_decision_has_exact_values_and_defaults_to_no_go() -> None:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")

    assert [item.value for item in deployment_evidence.PromotionDecision] == [
        "NO_GO",
        "GO_PAPER_PRODUCTION",
        "GO_LIVE_LIMITED",
    ]
    assert (
        deployment_evidence.resolve_promotion_decision()
        is deployment_evidence.PromotionDecision.NO_GO
    )


COMMIT = "1" * 40
TREE = "2" * 40
OTHER_COMMIT = "3" * 40
OTHER_TREE = "4" * 40
MANIFEST_SHA256 = "a" * 64
OTHER_MANIFEST_SHA256 = "b" * 64
UNIT_SHA256 = "c" * 64
COMMAND_FINGERPRINT = "d" * 64
OTHER_COMMAND_FINGERPRINT = "e" * 64
ROOT = Path(__file__).resolve().parents[2]


def _valid_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "observed_at": "2026-07-16T12:00:00Z",
        "source": {
            "repository_root": "/home/operator/trading-agent",
            "commit": COMMIT,
            "tree": TREE,
        },
        "source_to_release": "VERIFIED",
        "release": {
            "release_root": f"/opt/trading-agent/releases/app-{COMMIT}",
            "manifest_path": f"/opt/trading-agent/manifests/app-{COMMIT}.manifest.json",
            "manifest_sha256": MANIFEST_SHA256,
            "source_commit": COMMIT,
            "source_tree": TREE,
        },
        "services": [
            {
                "service_id": "control-api",
                "release_to_unit": "VERIFIED",
                "unit": {
                    "unit_name": "trading-control-api.service",
                    "fragment_path": "/etc/systemd/system/trading-control-api.service",
                    "effective_unit_sha256": UNIT_SHA256,
                    "release_manifest_sha256": MANIFEST_SHA256,
                    "command_fingerprint": COMMAND_FINGERPRINT,
                },
                "unit_to_process": "VERIFIED",
                "process": {
                    "pid": 4123,
                    "start_ticks": 987654,
                    "command_fingerprint": COMMAND_FINGERPRINT,
                },
            }
        ],
    }


def _model() -> object:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")
    return deployment_evidence.DeploymentEvidence.model_validate(_valid_document())


def test_evidence_states_and_model_keys_are_exact_and_frozen() -> None:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")
    model = _model()

    assert [item.value for item in deployment_evidence.EvidenceState] == [
        "VERIFIED",
        "DRIFTED",
        "UNAVAILABLE",
    ]
    assert set(model.model_dump()) == {
        "schema_version",
        "observed_at",
        "source",
        "source_to_release",
        "release",
        "services",
    }
    assert set(model.source.model_dump()) == {"repository_root", "commit", "tree"}
    assert set(model.release.model_dump()) == {
        "release_root",
        "manifest_path",
        "manifest_sha256",
        "source_commit",
        "source_tree",
    }
    service = model.services[0]
    assert set(service.model_dump()) == {
        "service_id",
        "release_to_unit",
        "unit",
        "unit_to_process",
        "process",
    }
    assert set(service.unit.model_dump()) == {
        "unit_name",
        "fragment_path",
        "effective_unit_sha256",
        "release_manifest_sha256",
        "command_fingerprint",
    }
    assert set(service.process.model_dump()) == {
        "pid",
        "start_ticks",
        "command_fingerprint",
    }
    with pytest.raises(ValidationError, match="frozen"):
        model.schema_version = 2


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("source", "repository_root"), "relative/source"),
        (("source", "repository_root"), "/home/operator/../secrets"),
        (("release", "manifest_path"), "/opt/manifests/bad\nname.json"),
        (("services", 0, "unit", "fragment_path"), "etc/systemd/bad.service"),
        (("source", "commit"), "A" * 40),
        (("source", "tree"), "2" * 39),
        (("release", "manifest_sha256"), "g" * 64),
        (("services", 0, "unit", "effective_unit_sha256"), "c" * 63),
    ),
)
def test_paths_and_hashes_are_strict_and_publish_safe(
    path: tuple[str | int, ...], value: object
) -> None:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")
    document = _valid_document()
    target: object = document
    for component in path[:-1]:
        target = target[component]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]

    with pytest.raises(ValidationError):
        deployment_evidence.DeploymentEvidence.model_validate(document)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("schema_version", True),
        ("schema_version", 1.0),
        ("observed_at", 0),
    ),
)
def test_schema_version_and_observation_time_do_not_coerce(
    field: str, value: object
) -> None:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")
    document = _valid_document()
    document[field] = value

    with pytest.raises(ValidationError):
        deployment_evidence.DeploymentEvidence.model_validate(document)


def test_service_ids_must_be_unique() -> None:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")
    document = _valid_document()
    document["services"].append(deepcopy(document["services"][0]))  # type: ignore[union-attr,index]

    with pytest.raises(ValidationError, match="duplicate service_id"):
        deployment_evidence.DeploymentEvidence.model_validate(document)


def test_verified_links_require_matching_source_release_unit_and_process() -> None:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")

    for path, value in (
        (("release", "source_commit"), OTHER_COMMIT),
        (("release", "source_tree"), OTHER_TREE),
        (("services", 0, "unit", "release_manifest_sha256"), OTHER_MANIFEST_SHA256),
        (("services", 0, "process", "command_fingerprint"), OTHER_COMMAND_FINGERPRINT),
    ):
        document = _valid_document()
        target: object = document
        for component in path[:-1]:
            target = target[component]  # type: ignore[index]
        target[path[-1]] = value  # type: ignore[index]

        with pytest.raises(ValidationError, match="VERIFIED"):
            deployment_evidence.DeploymentEvidence.model_validate(document)


def test_drifted_links_require_and_preserve_observed_mismatches() -> None:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")

    source_drift = _valid_document()
    source_drift["source_to_release"] = "DRIFTED"
    source_drift["release"]["source_tree"] = OTHER_TREE  # type: ignore[index]

    unit_drift = _valid_document()
    unit_drift["services"][0]["release_to_unit"] = "DRIFTED"  # type: ignore[index]
    unit_drift["services"][0]["unit"]["release_manifest_sha256"] = (  # type: ignore[index]
        OTHER_MANIFEST_SHA256
    )

    process_drift = _valid_document()
    process_drift["services"][0]["unit_to_process"] = "DRIFTED"  # type: ignore[index]
    process_drift["services"][0]["process"]["command_fingerprint"] = (  # type: ignore[index]
        OTHER_COMMAND_FINGERPRINT
    )

    assert deployment_evidence.DeploymentEvidence.model_validate(source_drift)
    assert deployment_evidence.DeploymentEvidence.model_validate(unit_drift)
    assert deployment_evidence.DeploymentEvidence.model_validate(process_drift)


def test_unavailable_links_have_no_implied_target_identity() -> None:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")
    unavailable_release = _valid_document()
    unavailable_release.update(
        source_to_release="UNAVAILABLE",
        release=None,
        services=[],
    )
    model = deployment_evidence.DeploymentEvidence.model_validate(unavailable_release)

    assert model.source_to_release is deployment_evidence.EvidenceState.UNAVAILABLE
    assert model.release is None
    assert model.services == ()

    unavailable_process = _valid_document()
    unavailable_process["services"][0].update(  # type: ignore[index]
        unit_to_process="UNAVAILABLE",
        process=None,
    )
    model = deployment_evidence.DeploymentEvidence.model_validate(unavailable_process)
    assert model.services[0].process is None


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("pid", 0),
        ("pid", True),
        ("start_ticks", 0),
        ("start_ticks", True),
        ("command_fingerprint", "d" * 63),
        ("command_fingerprint", "D" * 64),
    ),
)
def test_process_identity_fences_pid_reuse(field: str, value: object) -> None:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")
    document = _valid_document()
    document["services"][0]["process"][field] = value  # type: ignore[index]

    with pytest.raises(ValidationError):
        deployment_evidence.DeploymentEvidence.model_validate(document)


def test_canonical_json_is_compact_sorted_ascii_and_round_trippable() -> None:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")
    model = _model()

    expected = json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    canonical = deployment_evidence.canonical_json(model)

    assert canonical == expected
    assert "\n" not in canonical
    assert deployment_evidence.DeploymentEvidence.model_validate_json(canonical) == model


@pytest.mark.parametrize(
    "tamper",
    (
        "source_to_release",
        "release_to_unit",
        "unit_to_process",
        "duplicate_services",
        "unsafe_path",
    ),
)
def test_canonical_json_revalidates_model_copy_tampering(tamper: str) -> None:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")
    model = _model()
    service = model.services[0]

    if tamper == "source_to_release":
        release = model.release.model_copy(update={"source_tree": OTHER_TREE})
        tampered = model.model_copy(update={"release": release})
    elif tamper == "release_to_unit":
        unit = service.unit.model_copy(
            update={"release_manifest_sha256": OTHER_MANIFEST_SHA256}
        )
        tampered_service = service.model_copy(update={"unit": unit})
        tampered = model.model_copy(update={"services": (tampered_service,)})
    elif tamper == "unit_to_process":
        process = service.process.model_copy(
            update={"command_fingerprint": OTHER_COMMAND_FINGERPRINT}
        )
        tampered_service = service.model_copy(update={"process": process})
        tampered = model.model_copy(update={"services": (tampered_service,)})
    elif tamper == "duplicate_services":
        tampered = model.model_copy(update={"services": (service, service)})
    else:
        source = model.source.model_copy(
            update={"repository_root": "/home/operator/../unsafe"}
        )
        tampered = model.model_copy(update={"source": source})

    with pytest.raises(ValidationError):
        deployment_evidence.canonical_json(tampered)


def test_canonical_json_requires_the_normative_deployment_model() -> None:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")

    with pytest.raises(TypeError, match="DeploymentEvidence"):
        deployment_evidence.canonical_json(_model().source)


@pytest.mark.parametrize(
    "key",
    (
        "api_token",
        "credential",
        "password",
        "database_dsn",
        "database_url",
        "private_key",
        "env",
        "environment",
    ),
)
def test_secret_like_keys_are_rejected_before_serialization(key: str) -> None:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")
    document = _valid_document()
    document["services"][0]["process"][key] = "must-not-be-accepted"  # type: ignore[index]

    with pytest.raises(ValidationError, match="secret-like key"):
        deployment_evidence.DeploymentEvidence.model_validate(document)


@pytest.mark.parametrize(
    "evidence",
    (
        None,
        {},
        {"schema_version": 1, "api_token": "must-not-be-accepted"},
    ),
)
def test_absent_or_malformed_evidence_resolves_to_no_go(evidence: object) -> None:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")

    assert deployment_evidence.resolve_promotion_decision(
        deployment_evidence.PromotionDecision.GO_PAPER_PRODUCTION,
        evidence,
    ) is deployment_evidence.PromotionDecision.NO_GO


@pytest.mark.parametrize(
    ("link_path", "identity_path", "identity_value"),
    (
        (("source_to_release",), ("release", "source_tree"), OTHER_TREE),
        (
            ("services", 0, "release_to_unit"),
            ("services", 0, "unit", "release_manifest_sha256"),
            OTHER_MANIFEST_SHA256,
        ),
        (
            ("services", 0, "unit_to_process"),
            ("services", 0, "process", "command_fingerprint"),
            OTHER_COMMAND_FINGERPRINT,
        ),
    ),
)
def test_drifted_evidence_resolves_to_no_go(
    link_path: tuple[str | int, ...],
    identity_path: tuple[str | int, ...],
    identity_value: str,
) -> None:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")
    document = _valid_document()
    link_target: object = document
    for component in link_path[:-1]:
        link_target = link_target[component]  # type: ignore[index]
    link_target[link_path[-1]] = "DRIFTED"  # type: ignore[index]
    identity_target: object = document
    for component in identity_path[:-1]:
        identity_target = identity_target[component]  # type: ignore[index]
    identity_target[identity_path[-1]] = identity_value  # type: ignore[index]

    assert deployment_evidence.resolve_promotion_decision(
        deployment_evidence.PromotionDecision.GO_PAPER_PRODUCTION,
        document,
    ) is deployment_evidence.PromotionDecision.NO_GO


def test_unavailable_evidence_resolves_to_no_go() -> None:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")
    document = _valid_document()
    document.update(source_to_release="UNAVAILABLE", release=None, services=[])

    assert deployment_evidence.resolve_promotion_decision(
        deployment_evidence.PromotionDecision.GO_PAPER_PRODUCTION,
        document,
    ) is deployment_evidence.PromotionDecision.NO_GO


@pytest.mark.parametrize(
    "decision",
    (
        "GO_PAPER_PRODUCTION",
        "GO_LIVE_LIMITED",
    ),
)
def test_observational_evidence_never_authorizes_a_go_decision(decision: str) -> None:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")

    assert deployment_evidence.resolve_promotion_decision(
        decision,
        _valid_document(),
    ) is deployment_evidence.PromotionDecision.NO_GO


@pytest.mark.parametrize(
    "link",
    ("source_to_release", "release_to_unit", "unit_to_process"),
)
def test_model_copy_cannot_bypass_cross_link_validation(link: str) -> None:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")
    model = _model()

    if link == "source_to_release":
        release = model.release.model_copy(update={"source_tree": OTHER_TREE})
        tampered = model.model_copy(update={"release": release})
    elif link == "release_to_unit":
        service = model.services[0]
        unit = service.unit.model_copy(
            update={"release_manifest_sha256": OTHER_MANIFEST_SHA256}
        )
        tampered_service = service.model_copy(update={"unit": unit})
        tampered = model.model_copy(update={"services": (tampered_service,)})
    else:
        service = model.services[0]
        process = service.process.model_copy(
            update={"command_fingerprint": OTHER_COMMAND_FINGERPRINT}
        )
        tampered_service = service.model_copy(update={"process": process})
        tampered = model.model_copy(update={"services": (tampered_service,)})

    assert tampered.is_fully_verified is False
    with pytest.raises(ValidationError, match="VERIFIED"):
        deployment_evidence.DeploymentEvidence.model_validate(tampered)
    assert deployment_evidence.resolve_promotion_decision(
        deployment_evidence.PromotionDecision.GO_PAPER_PRODUCTION,
        tampered,
    ) is deployment_evidence.PromotionDecision.NO_GO


@pytest.mark.parametrize(
    "observed_at",
    (
        "0",
        "2026-07-16 12:00:00Z",
        "2026-07-16T12:00:00z",
        "2026-07-16T12:00:00-00:00",
        "٢٠٢٦-٠٧-١٦T١٢:٠٠:٠٠Z",
    ),
)
def test_observed_at_requires_exact_rfc3339_utc_before_parsing(
    observed_at: str,
) -> None:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")
    document = _valid_document()
    document["observed_at"] = observed_at

    with pytest.raises(ValidationError, match="RFC 3339 UTC"):
        deployment_evidence.DeploymentEvidence.model_validate(document)


def test_checked_json_schema_is_generated_from_the_strict_model() -> None:
    deployment_evidence = importlib.import_module("packages.deployment_evidence")
    schema_path = ROOT / "ops/evidence/source-release-unit-pid.schema.json"

    expected = deployment_evidence.deployment_evidence_json_schema()
    actual = json.loads(schema_path.read_text(encoding="utf-8"))

    assert actual == expected
    assert actual["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert actual["additionalProperties"] is False
    assert actual["x-semantic-validation"] == {
        "constraints": [
            "normalized_absolute_paths",
            "exact_rfc3339_utc",
            "unique_service_ids",
            "identity_link_consistency",
            "secret_like_key_rejection",
        ],
        "promotion_authority": False,
        "required": True,
        "schema_only_authoritative": False,
        "validator": "packages.deployment_evidence.DeploymentEvidence",
    }
    assert actual["required"] == [
        "schema_version",
        "observed_at",
        "source",
        "source_to_release",
        "release",
        "services",
    ]
    assert actual["$defs"]["EvidenceState"]["enum"] == [
        "VERIFIED",
        "DRIFTED",
        "UNAVAILABLE",
    ]
    model_definitions = {
        "ProcessIdentity",
        "ReleaseIdentity",
        "ServiceDeploymentEvidence",
        "SourceIdentity",
        "UnitIdentity",
    }
    assert all(
        actual["$defs"][name]["additionalProperties"] is False
        for name in model_definitions
    )
