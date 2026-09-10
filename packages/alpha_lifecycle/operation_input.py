"""Private, pre-review operation intents; parsing never grants execution authority."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from packages.alpha_lifecycle.contracts.base import (
    AlphaId,
    DigestModel,
    Sha256,
    StrictModel,
)
from packages.alpha_lifecycle.contracts.policy import _CANDIDATE_DIGESTS
from packages.data_contracts import ArtifactRefV1


FAMILY_IDS = tuple(sorted(_CANDIDATE_DIGESTS))


class BaselinesInput(StrictModel):
    baseline_manifest_ref: ArtifactRefV1


class RegisterFamilyInput(StrictModel):
    baseline_selection_ref: ArtifactRefV1
    candidate_spec_refs: Annotated[
        tuple[ArtifactRefV1, ...], Field(min_length=4, max_length=4)
    ]
    candidate_record_refs: Annotated[
        tuple[ArtifactRefV1, ...], Field(min_length=4, max_length=4)
    ]


class CandidateOOSInput(StrictModel):
    evaluation_manifest_ref: ArtifactRefV1


class SelectPrimaryInput(StrictModel):
    family_review_ref: ArtifactRefV1


class HoldoutInput(StrictModel):
    primary_selection_ref: ArtifactRefV1
    candidate_spec_ref: ArtifactRefV1
    registration_proof_ref: ArtifactRefV1
    custody_record_ref: ArtifactRefV1
    holdout_input_set_ref: ArtifactRefV1
    holdout_dataset_ref: ArtifactRefV1
    context_dataset_ref: ArtifactRefV1
    buffer_ref: ArtifactRefV1
    environment_ref: ArtifactRefV1
    policy_digest: Sha256


class NativeParityInput(StrictModel):
    holdout_manifest_ref: ArtifactRefV1
    primary_reference_ref: ArtifactRefV1
    baseline_reference_ref: ArtifactRefV1
    instrument_spec_ref: ArtifactRefV1
    native_request_ref: ArtifactRefV1


class PhaseExitInput(StrictModel):
    primary_selection_ref: ArtifactRefV1
    primary_qualification_ref: ArtifactRefV1
    baseline_selection_ref: ArtifactRefV1
    holdout_request_ref: ArtifactRefV1
    holdout_evaluation_ref: ArtifactRefV1
    holdout_replay_ref: ArtifactRefV1
    executable_ref: ArtifactRefV1
    baseline_executable_ref: ArtifactRefV1
    parity_ref: ArtifactRefV1
    current_primary_head_ref: ArtifactRefV1


_OPERATION_BODIES = {
    "p3-baselines-v1": ("BASELINES", BaselinesInput),
    "p3-register-family-v1": ("REGISTER_FAMILY", RegisterFamilyInput),
    **{f"p3-oos-a{index}-v1": ("OOS", CandidateOOSInput) for index in range(4)},
    "p3-select-primary-v1": ("OOS", SelectPrimaryInput),
    "p3-holdout-primary-v1": ("HOLDOUT", HoldoutInput),
    "p3-native-parity-v1": ("PARITY", NativeParityInput),
    "p3-phase-exit-v1": ("PHASE_EXIT", PhaseExitInput),
}


class P3OperationInput(DigestModel):
    schema_version: Literal["p3-operation-input-v1"]
    workflow_operation: Literal[
        "p3-baselines-v1",
        "p3-register-family-v1",
        "p3-oos-a0-v1",
        "p3-oos-a1-v1",
        "p3-oos-a2-v1",
        "p3-oos-a3-v1",
        "p3-select-primary-v1",
        "p3-holdout-primary-v1",
        "p3-native-parity-v1",
        "p3-phase-exit-v1",
    ]
    operation: Literal[
        "BASELINES", "REGISTER_FAMILY", "OOS", "HOLDOUT", "PARITY", "PHASE_EXIT"
    ]
    input_set_ref: ArtifactRefV1
    allowed_alpha_ids: Annotated[tuple[AlphaId, ...], Field(max_length=4)]
    body: (
        BaselinesInput
        | RegisterFamilyInput
        | CandidateOOSInput
        | SelectPrimaryInput
        | HoldoutInput
        | NativeParityInput
        | PhaseExitInput
    )

    @model_validator(mode="after")
    def _scope(self) -> "P3OperationInput":
        operation, body_type = _OPERATION_BODIES[self.workflow_operation]
        if self.operation != operation or not isinstance(self.body, body_type):
            raise ValueError(
                "operation intent body does not match its exact workflow operation"
            )
        ids = self.allowed_alpha_ids
        if tuple(sorted(set(ids))) != ids or any(
            alpha not in FAMILY_IDS for alpha in ids
        ):
            raise ValueError(
                "operation intent requires sorted unique frozen family IDs"
            )
        if isinstance(self.body, BaselinesInput):
            expected = ()
        elif isinstance(self.body, (RegisterFamilyInput, SelectPrimaryInput)):
            expected = FAMILY_IDS
        elif isinstance(self.body, CandidateOOSInput):
            expected = (
                FAMILY_IDS[int(self.workflow_operation.removeprefix("p3-oos-a")[0])],
            )
        else:
            if len(ids) != 1:
                raise ValueError("primary operation requires exactly one allowed alpha")
            expected = ids
        if ids != expected:
            raise ValueError("allowed alpha IDs do not match the workflow operation")
        return self
