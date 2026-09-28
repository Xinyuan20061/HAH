from pydantic import BaseModel, Field, field_validator


class WorkerHeartbeatIn(BaseModel):
    worker_id: str = Field(min_length=3, max_length=120)
    name: str = Field(default="HealthMate Local Worker", max_length=120)
    version: str = Field(default="1.0.0", max_length=40)
    gpu_name: str = Field(default="", max_length=160)
    capabilities: list[str] = Field(default_factory=list, max_length=20)
    metadata: dict = Field(default_factory=dict)


class WorkerClaimIn(BaseModel):
    request_id: str | None = Field(default=None, min_length=8, max_length=80)
    worker_id: str = Field(min_length=3, max_length=120)
    capabilities: list[str] = Field(
        default_factory=lambda: ["motion_pose", "food_vision", "kinetics400"],
        max_length=20,
    )


class WorkerProgressIn(BaseModel):
    worker_id: str = Field(min_length=3, max_length=120)
    lease_token: str = Field(min_length=8, max_length=80)
    progress: int = Field(ge=0, le=99)
    stage: str = Field(default="", max_length=120)


class WorkerCompleteIn(BaseModel):
    worker_id: str = Field(min_length=3, max_length=120)
    lease_token: str = Field(min_length=8, max_length=80)
    result: dict
    metrics: dict = Field(default_factory=dict)

    @field_validator("metrics")
    @classmethod
    def valid_metrics(cls, value):
        import math

        latency = value.get("latency_ms", 0)
        if (
            type(latency) not in {float, int}
            or not math.isfinite(latency)
            or not 0 <= latency <= 86400000
        ):
            raise ValueError("latency_ms 无效")
        return value


class WorkerFailIn(BaseModel):
    worker_id: str = Field(min_length=3, max_length=120)
    lease_token: str = Field(min_length=8, max_length=80)
    error_code: str = Field(default="worker_error", max_length=80)
    error_message: str = Field(default="AI worker 执行失败", max_length=800)
    retryable: bool = True
