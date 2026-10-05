"""Pydantic v2 request contracts to port into acquisition/contracts.py."""

from __future__ import annotations

from typing import Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class AcquisitionBudget(StrictModel):
    daily_prompt_limit: int = Field(default=2, ge=0, le=3, strict=True)
    episode_prompt_limit: int = Field(default=14, ge=0, le=28, strict=True)
    estimated_daily_seconds: int = Field(default=30, ge=0, le=120, strict=True)


class StartAcquisitionRequest(StrictModel):
    expected_episode_version: int = Field(ge=1, strict=True)
    consent_to_questions: StrictBool
    budget: AcquisitionBudget = Field(default_factory=AcquisitionBudget)

    @model_validator(mode="after")
    def require_consent(self):
        if not self.consent_to_questions:
            raise ValueError("ACQUISITION_CONSENT_REQUIRED")
        return self


class NextQuestionRequest(StrictModel):
    expected_session_version: int = Field(ge=1, strict=True)
    expected_episode_version: int = Field(ge=1, strict=True)


class SelectedSource(StrictModel):
    source_type: str = Field(min_length=1, max_length=48)
    source_id: str = Field(min_length=1, max_length=96)
    source_revision: int = Field(ge=1, strict=True)


class AnswerRequest(StrictModel):
    expected_session_version: int = Field(ge=1, strict=True)
    expected_episode_version: int = Field(ge=1, strict=True)
    response: Literal["answered", "unknown", "declined", "unavailable"]
    confirmation: StrictBool = False
    execution_value: Literal["completed", "explicitly_not_completed"] | None = None
    burden_value: float | None = Field(default=None, ge=0, le=10, strict=True)
    observed_at: AwareDatetime | None = None
    selected_source: SelectedSource | None = None
    client_elapsed_ms: int | None = Field(default=None, ge=0, le=600_000, strict=True)

    @model_validator(mode="after")
    def validate_payload(self):
        count = sum(value is not None for value in (
            self.execution_value, self.burden_value, self.selected_source,
        ))
        if self.response != "answered":
            if count or self.observed_at is not None or self.confirmation:
                raise ValueError("NON_ANSWER_MUST_NOT_WRITE_EVIDENCE")
            return self
        if count != 1 or not self.confirmation:
            raise ValueError("ANSWER_REQUIRES_ONE_CONFIRMED_VALUE")
        if self.burden_value is not None and self.observed_at is None:
            raise ValueError("SELF_REPORT_REQUIRES_OBSERVED_AT")
        if self.burden_value is None and self.observed_at is not None:
            raise ValueError("OBSERVED_AT_ONLY_FOR_SELF_REPORT")
        return self


class RepairRequest(StrictModel):
    expected_session_version: int = Field(ge=1, strict=True)
    expected_episode_version: int = Field(ge=1, strict=True)


class PauseAcquisitionRequest(StrictModel):
    expected_session_version: int = Field(ge=1, strict=True)


class RereviewRequest(StrictModel):
    expected_episode_version: int = Field(ge=1, strict=True)
    expected_adjudication_revision: int = Field(ge=1, strict=True)
    evidence_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ObservationRepairRequest(StrictModel):
    expected_episode_version: int = Field(ge=1, strict=True)
    observation_ref_id: int = Field(ge=1, strict=True)
    expected_observation_revision: int = Field(ge=1, strict=True)
    expected_source_revision: int = Field(ge=1, strict=True)
    expected_observation_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: StrictBool
    replacement_source: SelectedSource | None = None
    burden_value: float | None = Field(default=None, ge=0, le=10, strict=True)
    observed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def require_confirmation(self):
        if not self.confirmation:
            raise ValueError("OBSERVATION_REPAIR_REQUIRES_CONFIRMATION")
        if sum(value is not None for value in (
            self.replacement_source, self.burden_value,
        )) != 1:
            raise ValueError("OBSERVATION_REPAIR_REQUIRES_ONE_REPLACEMENT")
        if self.burden_value is not None and self.observed_at is None:
            raise ValueError("SELF_REPORT_REQUIRES_OBSERVED_AT")
        if self.burden_value is None and self.observed_at is not None:
            raise ValueError("OBSERVED_AT_ONLY_FOR_SELF_REPORT")
        return self
