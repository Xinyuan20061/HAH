from typing import Literal

from pydantic import BaseModel, Field


class AgentRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    agent_id: Literal["xiaojian", "xiaokang", "steward"] = "steward"
    channel: Literal["text", "voice"] = "text"


class AgentPlanItemUpdate(BaseModel):
    done: bool


class AgentInsightFeedback(BaseModel):
    verdict: Literal["helpful", "inaccurate", "resolved"]


class AgentExperimentStart(BaseModel):
    insight_code: str = Field(min_length=1, max_length=60)
    variant: Literal["gentle", "standard"] = "gentle"


class ActionConfirmIn(BaseModel):
    """Confirmation of a durable action proposal (spec §8.4)."""

    version: int = Field(default=1, ge=1)
    confirmation: bool = False
    # Required only for actions in ``TYPED_CONFIRMATION_REQUIRED`` (privacy).
    typed_confirmation: str | None = Field(default=None, max_length=60)


class ActionRejectIn(BaseModel):
    version: int = Field(default=1, ge=1)


class ActionProposalOut(BaseModel):
    proposal_id: str
    action_key: str
    title: str
    risk_level: str
    requires_confirmation: bool = True
    summary: str = ""
    expires_at: str | None = None
