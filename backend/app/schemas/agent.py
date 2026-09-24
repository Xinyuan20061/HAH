from typing import Literal

from pydantic import BaseModel, Field


class AgentRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class AgentPlanItemUpdate(BaseModel):
    done: bool


class AgentInsightFeedback(BaseModel):
    verdict: Literal["helpful", "inaccurate", "resolved"]


class AgentExperimentStart(BaseModel):
    insight_code: str = Field(min_length=1, max_length=60)
    variant: Literal["gentle", "standard"] = "gentle"
