"""Strict public request contracts for evidence acquisition."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class AcquisitionBudget(StrictModel):
    daily_prompt_limit: StrictInt = Field(default=2, ge=0, le=3)
    episode_prompt_limit: StrictInt = Field(default=14, ge=0, le=28)
    estimated_daily_seconds: StrictInt = Field(default=30, ge=0, le=120)


class StartAcquisitionRequest(StrictModel):
    expected_episode_version: StrictInt = Field(ge=1)
    consent_to_questions: StrictBool
    budget: AcquisitionBudget = Field(default_factory=AcquisitionBudget)

    @model_validator(mode="after")
    def require_consent(self):
        if not self.consent_to_questions:
            raise ValueError("EXPLICIT_ACQUISITION_CONSENT_REQUIRED")
        return self


class SessionVersionRequest(StrictModel):
    expected_session_version: StrictInt = Field(ge=1)


class NextQuestionRequest(SessionVersionRequest):
    expected_episode_version: StrictInt = Field(ge=1)


class SelectedSource(StrictModel):
    source_type: str = Field(min_length=1, max_length=48)
    source_id: str = Field(min_length=1, max_length=96)
    source_revision: StrictInt = Field(ge=1)
    metric_version: str = Field(min_length=1, max_length=40)


class AnswerRequest(NextQuestionRequest):
    response: Literal["answered", "unknown", "declined", "unavailable"]
    confirmation: StrictBool = False
    execution_value: Literal["completed", "explicitly_not_completed"] | None = None
    burden_value: float | None = Field(default=None, ge=0, le=10, strict=True)
    observed_at: AwareDatetime | None = None
    selected_source: SelectedSource | None = None
    client_elapsed_ms: StrictInt | None = Field(default=None, ge=0, le=600_000)

    @model_validator(mode="after")
    def validate_answer_shape(self):
        has_execution = self.execution_value is not None
        has_burden = self.burden_value is not None
        has_source = self.selected_source is not None
        if self.response == "answered":
            if not self.confirmation or sum((has_execution, has_burden, has_source)) != 1:
                raise ValueError("ANSWER_REQUIRES_CONFIRMATION_AND_ONE_VALUE")
            if has_burden != (self.observed_at is not None):
                raise ValueError("BURDEN_REQUIRES_AWARE_OBSERVATION_TIME")
        elif self.confirmation or has_execution or has_burden or has_source or self.observed_at is not None:
            raise ValueError("NON_ANSWER_CANNOT_CARRY_FACT")
        return self


class RepairRequest(SessionVersionRequest):
    expected_episode_version: StrictInt = Field(ge=1)
    client_elapsed_ms: StrictInt | None = Field(default=None, ge=0, le=600_000)


class RereviewRequest(StrictModel):
    expected_episode_version: StrictInt = Field(ge=1)
    expected_adjudication_revision: StrictInt = Field(ge=1)
    expected_evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ObservationRepairRequest(StrictModel):
    expected_episode_version: StrictInt = Field(ge=1)
    observation_ref_id: StrictInt = Field(ge=1)
    expected_observation_revision: StrictInt = Field(ge=1)
    expected_source_revision: StrictInt = Field(ge=1)
    expected_observation_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: StrictBool
    replacement_source: SelectedSource | None = None
    burden_value: float | None = Field(default=None, ge=0, le=10, strict=True)
    observed_at: AwareDatetime | None = None
    client_elapsed_ms: StrictInt | None = Field(default=None, ge=0, le=600_000)

    @model_validator(mode="after")
    def exactly_one_replacement(self):
        if not self.confirmation or (self.replacement_source is None) == (self.burden_value is None):
            raise ValueError("OBSERVATION_REPAIR_REQUIRES_ONE_CONFIRMED_REPLACEMENT")
        if self.burden_value is not None and self.observed_at is None:
            raise ValueError("BURDEN_REPAIR_REQUIRES_AWARE_TIME")
        if self.replacement_source is not None and self.observed_at is not None:
            raise ValueError("SOURCE_REPAIR_CANNOT_OVERRIDE_TIME")
        return self
