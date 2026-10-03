from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SourceRef(StrictModel):
    source_type: str = Field(min_length=1, max_length=48)
    source_id: str = Field(min_length=1, max_length=96)
    source_revision: int = Field(ge=1)
    observed_at: datetime
    metric_version: str = Field(min_length=1, max_length=40)
    evidence_type: Literal["observed", "user_confirmed", "derived"]
    trace_id: str | None = None


class StrategyCompileRequest(StrictModel):
    template_id: str = Field(min_length=1, max_length=80)
    template_version: str | None = None
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)
    goal_key: str = Field(default="make_plan_sustainable", min_length=1, max_length=80)


class SourcePoint(StrictModel):
    slot: int = Field(ge=0, le=28)
    # Deprecated compatibility field. Trusted source values are resolved by the
    # server and this value is never used as evidence.
    value: float | None = None
    source_type: str = Field(min_length=1, max_length=48)
    source_id: str = Field(min_length=1, max_length=96)
    source_revision: int = Field(ge=1)
    observed_at: datetime
    metric_version: str = Field(min_length=1, max_length=40)
    confirmed: bool = True
    endpoint: Literal["baseline", "followup"] = "followup"


class ObservationRefInput(StrictModel):
    """Client may select an owned source, but cannot submit its value."""

    endpoint: Literal["baseline", "followup"] = "followup"
    slot: int = Field(ge=0, le=28)
    source_type: str = Field(min_length=1, max_length=48)
    source_id: str = Field(min_length=1, max_length=96)
    source_revision: int = Field(ge=1)
    metric_version: str = Field(min_length=1, max_length=40)


class SelfReportInput(StrictModel):
    """Explicitly self-reported measurement, never represented as a source record."""

    endpoint: Literal["baseline", "followup"] = "followup"
    slot: int = Field(ge=0, le=28)
    source_id: str = Field(min_length=1, max_length=96)
    observed_at: datetime
    metric_version: str = Field(min_length=1, max_length=40)
    value: float


class ExecutionReportRequest(StrictModel):
    episode_version: int = Field(ge=1)
    report_id: str = Field(min_length=1, max_length=96)
    opportunity_id: str = Field(min_length=1, max_length=64)
    execution: Literal["completed", "explicitly_not_completed", "unknown"]
    perceived_burden: float | None = Field(default=None, ge=0, le=10)
    confounder_codes: list[str] = Field(default_factory=list, max_length=12)


class ObservationRequest(StrictModel):
    episode_version: int = Field(ge=1)
    points: list[SourcePoint] = Field(default_factory=list, max_length=56)

    # New contract: the client submits only source references. The server fills
    # observed_at, metric version and value from the owned source row.
    refs: list[ObservationRefInput] = Field(default_factory=list, max_length=56)
    self_reports: list[SelfReportInput] = Field(default_factory=list, max_length=56)


class EpisodeStartRequest(StrictModel):
    strategy_unit_id: str = Field(min_length=1, max_length=64)
    protocol_hash: str = Field(min_length=64, max_length=64)
    version: int = Field(default=1, ge=1)
    decision_id: str | None = Field(default=None, max_length=64)


class EpisodeFinishRequest(StrictModel):
    episode_version: int = Field(ge=1)


class StopRequest(StrictModel):
    episode_version: int = Field(ge=1)
    reason_code: str = Field(min_length=1, max_length=120)


class DecisionRequest(StrictModel):
    context_key: str | None = None
    exploration_consented: bool = False


class ResetRequest(StrictModel):
    strategy_id: str | None = Field(default=None, max_length=100)
    scope: str = Field(default="strategy", pattern="^(strategy|all)$")
    version: int = Field(default=1, ge=1)
