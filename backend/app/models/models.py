import json

from app.core.time import utc_now
from datetime import datetime
from sqlalchemy import (
    String,
    Integer,
    Float,
    DateTime,
    Date,
    ForeignKey,
    Index,
    Text,
    Boolean,
    CheckConstraint,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from sqlalchemy.dialects.mysql import MEDIUMTEXT


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    openid: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    nickname: Mapped[str] = mapped_column(String(64), default="")
    avatar_url: Mapped[str] = mapped_column(String(500), default="")
    profile: Mapped["HealthProfile|None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    ai_config: Mapped["UserAIConfig|None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class HealthProfile(Base, TimestampMixin):
    __tablename__ = "health_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), unique=True, index=True
    )
    gender: Mapped[str] = mapped_column(String(20), default="unspecified")
    age: Mapped[int] = mapped_column(Integer, default=20)
    height_cm: Mapped[float] = mapped_column(Float, default=170)
    weight_kg: Mapped[float] = mapped_column(Float, default=65)
    goal_type: Mapped[str] = mapped_column(String(30), default="maintain")
    activity_level: Mapped[str] = mapped_column(String(30), default="moderate")
    diet_preference: Mapped[str] = mapped_column(String(255), default="")
    allergies: Mapped[str] = mapped_column(String(255), default="")
    user: Mapped[User] = relationship(back_populates="profile")


class UserAIConfig(Base, TimestampMixin):
    __tablename__ = "user_ai_configs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), unique=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    provider: Mapped[str] = mapped_column(String(30), default="deepseek")
    base_url: Mapped[str] = mapped_column(
        String(500), default="https://api.deepseek.com"
    )
    model: Mapped[str] = mapped_column(String(120), default="deepseek-chat")
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    voice_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    voice_base_url: Mapped[str] = mapped_column(String(500), default="")
    voice_stt_model: Mapped[str] = mapped_column(String(120), default="whisper-1")
    voice_tts_model: Mapped[str] = mapped_column(String(120), default="tts-1")
    voice_name: Mapped[str] = mapped_column(String(80), default="alloy")
    voice_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # voice_provider: "off" | "openai_compatible" | "tencent_cloud" (spec section 5).
    voice_provider: Mapped[str] = mapped_column(String(30), default="off")
    voice_preferences_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    user: Mapped[User] = relationship(back_populates="ai_config")


class HealthGoalSetting(Base, TimestampMixin):
    __tablename__ = "health_goal_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), unique=True, index=True
    )
    water_target: Mapped[int] = mapped_column(Integer, default=1800)
    sleep_target: Mapped[float] = mapped_column(Float, default=8)
    exercise_target: Mapped[int] = mapped_column(Integer, default=30)
    steps_target: Mapped[int] = mapped_column(Integer, default=8000)
    protein_target: Mapped[float] = mapped_column(Float, default=90)
    calorie_target: Mapped[int] = mapped_column(Integer, default=2000)
    weekly_checkin_target: Mapped[int] = mapped_column(Integer, default=5)


class DietRecord(Base, TimestampMixin):
    __tablename__ = "diet_records"
    __table_args__ = (
        CheckConstraint(
            "meal_type IN ('breakfast','lunch','dinner','snack','other')",
            name="ck_diet_records_meal_type",
        ),
        Index(
            "ix_diet_records_user_recorded_id", "user_id", "recorded_at", "id"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    meal_type: Mapped[str] = mapped_column(String(20), default="other")
    calories: Mapped[float] = mapped_column(Float, default=0)
    protein: Mapped[float] = mapped_column(Float, default=0)
    carbs: Mapped[float] = mapped_column(Float, default=0)
    fat: Mapped[float] = mapped_column(Float, default=0)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    portion: Mapped[str] = mapped_column(String(120), default="")
    cooking_method: Mapped[str] = mapped_column(String(120), default="")
    weight_g: Mapped[float] = mapped_column(Float, default=0)
    fiber: Mapped[float] = mapped_column(Float, default=0)
    vision_analysis_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    items_json: Mapped[str] = mapped_column(Text, default="[]")
    # Optimistic concurrency counter (spec §5.4). Existing rows are 1.
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    @property
    def items(self) -> list[dict]:
        try:
            value = json.loads(self.items_json or "[]")
            return value if isinstance(value, list) else []
        except (TypeError, ValueError):
            return []


class ExerciseRecord(Base, TimestampMixin):
    __tablename__ = "exercise_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    duration_min: Mapped[int] = mapped_column(Integer, default=0)
    calories_burned: Mapped[float] = mapped_column(Float, default=0)
    intensity: Mapped[str] = mapped_column(String(20), default="medium")
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class HealthCheckIn(Base, TimestampMixin):
    __tablename__ = "health_checkins"
    __table_args__ = (
        UniqueConstraint("user_id", "record_date", name="uq_health_checkin_user_date"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    record_date: Mapped[str] = mapped_column(String(10), index=True)
    water_ml: Mapped[int] = mapped_column(Integer, default=0)
    sleep_hours: Mapped[float] = mapped_column(Float, default=0)
    weight_kg: Mapped[float] = mapped_column(Float, default=0)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    mood: Mapped[str] = mapped_column(String(20), default="normal")


class PlanTaskState(Base, TimestampMixin):
    __tablename__ = "plan_task_states"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "record_date", "task_key", name="uq_plan_state_user_date_task"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    record_date: Mapped[str] = mapped_column(String(10), index=True)
    task_key: Mapped[str] = mapped_column(String(80))
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    task_type: Mapped[str] = mapped_column(String(20), default="system")


class ChatSession(Base, TimestampMixin):
    __tablename__ = "chat_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(120), default="健康对话")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class HealthTimelineEvent(Base, TimestampMixin):
    __tablename__ = "health_timeline_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    source: Mapped[str] = mapped_column(String(40), default="manual")
    ref_type: Mapped[str] = mapped_column(String(40), default="")
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")


class MediaAsset(Base, TimestampMixin):
    __tablename__ = "media_assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(255), default="")
    # storage_key is the stable object identity. For CloudBase it is the cloud:// fileID.
    storage_key: Mapped[str] = mapped_column(String(700), unique=True, index=True)
    storage_backend: Mapped[str] = mapped_column(
        String(30), default="local", index=True
    )
    cloud_file_id: Mapped[str] = mapped_column(String(700), default="", index=True)
    # Private CloudBase files are accessed by a short-lived HTTPS URL supplied by the mini program.
    # The URL is never exposed in logs and can be refreshed without changing storage_key.
    source_url: Mapped[str] = mapped_column(Text, default="")
    source_url_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    media_type: Mapped[str] = mapped_column(String(20), index=True)
    content_type: Mapped[str] = mapped_column(String(120), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="ready")


class MotionAnalysisJob(Base, TimestampMixin):
    __tablename__ = "motion_analysis_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    media_asset_id: Mapped[int] = mapped_column(
        ForeignKey("media_assets.id"), index=True
    )
    exercise_type: Mapped[str] = mapped_column(String(40), default="squat")
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    result_json: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class HealthGoalAdjustment(Base, TimestampMixin):
    __tablename__ = "health_goal_adjustments"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    metric: Mapped[str] = mapped_column(String(40), index=True)
    window_days: Mapped[int] = mapped_column(Integer, default=14)
    observed_days: Mapped[int] = mapped_column(Integer, default=0)
    completion_rate: Mapped[float] = mapped_column(Float, default=0)
    previous_target: Mapped[float] = mapped_column(Float, default=0)
    recommended_target: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    facts_json: Mapped[str] = mapped_column(Text, default="{}")
    explanation: Mapped[str] = mapped_column(Text, default="")
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class HealthAgentRun(Base, TimestampMixin):
    __tablename__ = "health_agent_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    intent: Mapped[str] = mapped_column(String(60), default="general", index=True)
    user_message: Mapped[str] = mapped_column(Text)
    context_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    provider: Mapped[str] = mapped_column(String(40), default="demo")
    status: Mapped[str] = mapped_column(String(30), default="completed", index=True)


class AgentMicroExperiment(Base, TimestampMixin):
    """A user-confirmed, auditable N-of-1 behaviour experiment.

    The Agent may propose an experiment, but this row is only created after an
    explicit user action.  Baseline, protocol and outcome are frozen as JSON so
    a later report can be reproduced without pretending observational change is
    causal proof.
    """

    __tablename__ = "agent_micro_experiments"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # Stable ledger key: one decision_id joins signal -> proposal -> confirmed
    # action -> progress -> review for a single experiment (plan §5).
    decision_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, default="")
    insight_code: Mapped[str] = mapped_column(String(60), index=True)
    variant: Mapped[str] = mapped_column(String(20), default="gentle")
    title: Mapped[str] = mapped_column(String(180))
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    primary_metric: Mapped[str] = mapped_column(String(60), index=True)
    start_date: Mapped[str] = mapped_column(String(10), index=True)
    end_date: Mapped[str] = mapped_column(String(10), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    baseline_json: Mapped[str] = mapped_column(Text, default="{}")
    target_json: Mapped[str] = mapped_column(Text, default="{}")
    protocol_json: Mapped[str] = mapped_column(Text, default="{}")
    outcome_json: Mapped[str] = mapped_column(Text, default="{}")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class HealthPlan(Base, TimestampMixin):
    __tablename__ = "health_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(160), default="本周健康计划")
    period_start: Mapped[str] = mapped_column(String(10), index=True)
    period_end: Mapped[str] = mapped_column(String(10), index=True)
    source: Mapped[str] = mapped_column(String(30), default="agent")
    source_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("health_agent_runs.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)


class HealthPlanItem(Base, TimestampMixin):
    __tablename__ = "health_plan_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("health_plans.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    planned_date: Mapped[str] = mapped_column(String(10), index=True)
    category: Mapped[str] = mapped_column(String(30), default="habit")
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    target_json: Mapped[str] = mapped_column(Text, default="{}")
    done: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WeeklyReportSnapshot(Base, TimestampMixin):
    __tablename__ = "weekly_report_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "period_start", "period_end", name="uq_weekly_report_user_period"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    period_start: Mapped[str] = mapped_column(String(10), index=True)
    period_end: Mapped[str] = mapped_column(String(10), index=True)
    facts_version: Mapped[str] = mapped_column(String(20), default="1.0")
    facts_json: Mapped[str] = mapped_column(Text, default="{}")


class FoodAnalysisSession(Base, TimestampMixin):
    __tablename__ = "food_analysis_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    image_sha256: Mapped[str] = mapped_column(String(64), default="", index=True)
    provider: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0)
    initial_json: Mapped[str] = mapped_column(Text, default="{}")
    corrected_json: Mapped[str] = mapped_column(Text, default="")
    correction_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="analyzed", index=True)
    finalized_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("diet_records.id"), nullable=True, index=True
    )


class FoodAnalysisCorrection(Base, TimestampMixin):
    __tablename__ = "food_analysis_corrections"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("food_analysis_sessions.id"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    corrected_json: Mapped[str] = mapped_column(Text, default="{}")
    changed_fields_json: Mapped[str] = mapped_column(Text, default="[]")


class AgentActionAudit(Base, TimestampMixin):
    __tablename__ = "agent_action_audits"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("health_agent_runs.id"), nullable=True, index=True
    )
    action_key: Mapped[str] = mapped_column(String(80), index=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="low", index=True)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    input_json: Mapped[str] = mapped_column(Text, default="{}")
    output_json: Mapped[str] = mapped_column(Text, default="{}")
    block_reason: Mapped[str] = mapped_column(Text, default="")


class EvaluationEvent(Base, TimestampMixin):
    __tablename__ = "evaluation_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    metric_name: Mapped[str] = mapped_column(String(100), index=True)
    metric_value: Mapped[float] = mapped_column(Float, default=0)
    unit: Mapped[str] = mapped_column(String(30), default="count")
    source: Mapped[str] = mapped_column(String(60), default="runtime", index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class EvaluationBenchmark(Base, TimestampMixin):
    __tablename__ = "evaluation_benchmarks"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    task_type: Mapped[str] = mapped_column(String(60), index=True)
    metric_name: Mapped[str] = mapped_column(String(100), index=True)
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(30), default="%")
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")


class SafetyEvent(Base, TimestampMixin):
    __tablename__ = "safety_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    category: Mapped[str] = mapped_column(String(60), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="medium", index=True)
    action: Mapped[str] = mapped_column(String(40), default="warn", index=True)
    matched_rule: Mapped[str] = mapped_column(String(100), default="")
    message_hash: Mapped[str] = mapped_column(String(64), default="")
    excerpt_redacted: Mapped[str] = mapped_column(String(160), default="")


class PrivacyAudit(Base, TimestampMixin):
    __tablename__ = "privacy_audits"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(60), index=True)
    status: Mapped[str] = mapped_column(String(30), default="completed", index=True)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")


class AIJob(Base, TimestampMixin):
    """Database-backed job queue shared by WeChat Cloud Run and the local GPU worker."""

    __tablename__ = "ai_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    media_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_assets.id"), nullable=True, index=True
    )
    job_type: Mapped[str] = mapped_column(
        String(40), index=True
    )  # motion_pose | food_vision | kinetics400
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(
        Text().with_variant(MEDIUMTEXT(), "mysql"), default=""
    )
    dedupe_key: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    claim_request_id: Mapped[str | None] = mapped_column(
        String(80), nullable=True, unique=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_code: Mapped[str] = mapped_column(String(80), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    worker_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    lease_token: Mapped[str] = mapped_column(String(80), default="", index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AIWorkerNode(Base, TimestampMixin):
    __tablename__ = "ai_worker_nodes"
    id: Mapped[int] = mapped_column(primary_key=True)
    worker_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="HealthMate Local Worker")
    version: Mapped[str] = mapped_column(String(40), default="1.0.0")
    status: Mapped[str] = mapped_column(String(20), default="online", index=True)
    gpu_name: Mapped[str] = mapped_column(String(160), default="")
    capabilities_json: Mapped[str] = mapped_column(Text, default="[]")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, index=True
    )


class ExerciseResource(Base, TimestampMixin):
    """Curated links only; model output is never accepted as a resource URL."""

    __tablename__ = "exercise_resources"
    id: Mapped[int] = mapped_column(primary_key=True)
    exercise_type: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(200))
    platform: Mapped[str] = mapped_column(String(40), index=True)
    url: Mapped[str] = mapped_column(String(700), unique=True)
    difficulty: Mapped[str] = mapped_column(String(20), default="beginner", index=True)
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    quality_score: Mapped[float] = mapped_column(Float, default=0)
    summary: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class MotionScore(Base, TimestampMixin):
    __tablename__ = "motion_scores"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("ai_jobs.id"), unique=True, index=True
    )
    exercise_type: Mapped[str] = mapped_column(String(40), index=True)
    requested_exercise_type: Mapped[str] = mapped_column(String(40), default="")
    recognition_method: Mapped[str] = mapped_column(String(80), default="")
    recognition_confidence: Mapped[float] = mapped_column(Float, default=1)
    completeness: Mapped[float] = mapped_column(Float, default=0)
    stability: Mapped[float] = mapped_column(Float, default=0)
    rhythm_control: Mapped[float] = mapped_column(Float, default=0)
    risk_index: Mapped[float] = mapped_column(Float, default=0)
    overall: Mapped[float] = mapped_column(Float, default=0, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")


class MotionEvent(Base, TimestampMixin):
    __tablename__ = "motion_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("ai_jobs.id"), index=True)
    event_index: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    timestamp_seconds: Mapped[float] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(20), default="info", index=True)
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")


class KnowledgeDocument(Base, TimestampMixin):
    """Human-reviewed health knowledge chunk with traceable provenance."""

    __tablename__ = "knowledge_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(240))
    organization: Mapped[str] = mapped_column(String(120), index=True)
    source_url: Mapped[str] = mapped_column(String(700))
    source_published_at: Mapped[str] = mapped_column(String(20), default="")
    section: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text)
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class FitnessConcept(Base, TimestampMixin):
    """Versionable node in the exercise-effect ontology."""

    __tablename__ = "fitness_concepts"
    id: Mapped[int] = mapped_column(primary_key=True)
    concept_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    concept_type: Mapped[str] = mapped_column(String(40), index=True)
    name_zh: Mapped[str] = mapped_column(String(120), index=True)
    name_en: Mapped[str] = mapped_column(String(160), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    analyzer_support: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class FitnessRelation(Base, TimestampMixin):
    """Auditable directed edge; relation weights are ranking hints, not physiology."""

    __tablename__ = "fitness_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_key", "target_key", "relation_type", name="uq_fitness_relation"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(
        ForeignKey("fitness_concepts.concept_key"), index=True
    )
    target_key: Mapped[str] = mapped_column(
        ForeignKey("fitness_concepts.concept_key"), index=True
    )
    relation_type: Mapped[str] = mapped_column(String(40), index=True)
    role: Mapped[str] = mapped_column(String(30), default="")
    weight: Mapped[float] = mapped_column(Float, default=0)
    evidence: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(String(700), default="")
    review_status: Mapped[str] = mapped_column(
        String(30), default="seed", index=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class UserTrainingIntent(Base, TimestampMixin):
    __tablename__ = "user_training_intents"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), unique=True, index=True
    )
    target_body_parts_json: Mapped[str] = mapped_column(Text, default="[]")
    goals_json: Mapped[str] = mapped_column(Text, default="[]")
    constraints_json: Mapped[str] = mapped_column(Text, default="[]")
    preferred_equipment_json: Mapped[str] = mapped_column(Text, default="[]")
    notes: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(30), default="user_confirmed")
    confirmed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class MotionSemanticAnalysis(Base, TimestampMixin):
    __tablename__ = "motion_semantic_analyses"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("ai_jobs.id"), unique=True, index=True
    )
    exercise_key: Mapped[str] = mapped_column(String(120), default="", index=True)
    movement_patterns_json: Mapped[str] = mapped_column(Text, default="[]")
    target_body_parts_json: Mapped[str] = mapped_column(Text, default="[]")
    training_effects_json: Mapped[str] = mapped_column(Text, default="[]")
    goal_alignment_json: Mapped[str] = mapped_column(Text, default="{}")
    observed_semantics_json: Mapped[str] = mapped_column(Text, default="{}")
    method: Mapped[str] = mapped_column(String(80), default="knowledge_graph_v1")
    confidence: Mapped[float] = mapped_column(Float, default=0)


class DatasetRegistry(Base, TimestampMixin):
    """Audited metadata only; raw third-party datasets are never bundled here."""

    __tablename__ = "dataset_registry"
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    homepage_url: Mapped[str] = mapped_column(String(700), default="")
    paper_url: Mapped[str] = mapped_column(String(700), default="")
    primary_use: Mapped[str] = mapped_column(String(300), default="")
    modalities_json: Mapped[str] = mapped_column(Text, default="[]")
    access_mode: Mapped[str] = mapped_column(String(50), index=True)
    license_summary: Mapped[str] = mapped_column(Text, default="")
    redistribution_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    adoption_status: Mapped[str] = mapped_column(String(40), index=True)
    verified_on: Mapped[str] = mapped_column(String(10), default="")
    notes: Mapped[str] = mapped_column(Text, default="")


class ModelRegistry(Base, TimestampMixin):
    """Versioned capability record; registration is not an accuracy claim."""

    __tablename__ = "model_registry"
    __table_args__ = (
        UniqueConstraint("model_key", "version", name="uq_model_registry_version"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    model_key: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(40))
    task: Mapped[str] = mapped_column(String(100), index=True)
    implementation_type: Mapped[str] = mapped_column(String(30), index=True)
    release_status: Mapped[str] = mapped_column(String(40), index=True)
    artifact_uri: Mapped[str] = mapped_column(String(700), default="")
    training_dataset_keys_json: Mapped[str] = mapped_column(Text, default="[]")
    label_schema_version: Mapped[str] = mapped_column(String(80), default="")
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    thresholds_json: Mapped[str] = mapped_column(Text, default="{}")
    code_revision: Mapped[str] = mapped_column(String(80), default="")
    claims_scope: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class ModelEvaluation(Base, TimestampMixin):
    __tablename__ = "model_evaluations"
    id: Mapped[int] = mapped_column(primary_key=True)
    model_registry_id: Mapped[int] = mapped_column(
        ForeignKey("model_registry.id"), index=True
    )
    dataset_key: Mapped[str] = mapped_column(String(80), index=True)
    split_name: Mapped[str] = mapped_column(String(80), default="test")
    report_sha256: Mapped[str] = mapped_column(String(64), index=True)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    gate_status: Mapped[str] = mapped_column(String(30), index=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class MotionAnalysisRun(Base):
    """Unified motion analysis task (one click -> one run -> one result contract).

    Spec 8.1. `dedupe_key` is the same-request unique key: the same idempotency
    key plus the same asset returns the same task instead of creating a duplicate.
    Migration 0024 creates created_at + finished_at (no updated_at);
    do not add TimestampMixin.
    """

    __tablename__ = "motion_analysis_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    media_asset_id: Mapped[int] = mapped_column(
        ForeignKey("media_assets.id"), index=True
    )
    ai_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_jobs.id"), nullable=True, index=True
    )
    parent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("motion_analysis_runs.id"), nullable=True, index=True
    )
    dedupe_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True
    )
    requested_type: Mapped[str] = mapped_column(String(30), default="auto")
    pipeline_version: Mapped[str] = mapped_column(
        String(60), default="motion-unified-v1"
    )
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    consent_version: Mapped[str] = mapped_column(String(40), default="")
    model_versions_json: Mapped[str] = mapped_column(Text, default="{}")
    # V2 (migration 0025): idempotency fingerprint, result version, effective
    # pipeline chosen by the server, cloud review mode and terminal error code.
    request_fingerprint: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    result_version: Mapped[int] = mapped_column(Integer, default=1)
    effective_pipeline_version: Mapped[str | None] = mapped_column(
        String(60), nullable=True
    )
    cloud_review_mode: Mapped[str] = mapped_column(String(30), default="off")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def model_versions(self) -> dict:
        try:
            value = json.loads(self.model_versions_json or "{}")
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}


class MotionAnalysisFeedback(Base):
    """One structured result per run; preview images live in short-term storage.

    Spec 8.1: no large base64 images inside the long JSON text.
    Migration 0024 creates only created_at (no updated_at).
    """

    __tablename__ = "motion_analysis_feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("motion_analysis_runs.id"), unique=True, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    schema_version: Mapped[str] = mapped_column(String(30), default="motion-unified-v1")
    result_json: Mapped[str] = mapped_column(
        Text().with_variant(MEDIUMTEXT(), "mysql"), default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)

    @property
    def result(self) -> dict:
        try:
            value = json.loads(self.result_json or "{}")
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}


class ProviderInvocation(Base):
    """Desensitized per-call ledger for DeepSeek / Tencent voice (spec 8.2/8.3).

    Never stores raw audio, images, full prompts or keys. Migration 0024 creates
    only created_at (no updated_at); do not add TimestampMixin.
    """

    __tablename__ = "provider_invocations"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("motion_analysis_runs.id"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), index=True)
    operation: Mapped[str] = mapped_column(String(60))
    request_fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    # V2 (migration 0025): atomic pre-request reservation. reservation_key is the
    # sha256 of (user_id, evidence_hash, operation, model, prompt_version,
    # policy_version, consent_mode); the unique constraint lets the DB block a
    # double-spend race before the external call.
    reservation_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True
    )
    evidence_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    consent_mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    token_or_char_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    cost_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class ProviderConnectionCheck(Base):
    """'Verify once, then stop' record for real external calls (spec 7.3).

    The (config_fingerprint, direction, status) unique constraint makes a
    successful check deduplicable: same fingerprint + direction already verified
    returns the cached record instead of calling the external service again.
    Migration 0024 creates only created_at (no updated_at).
    """

    __tablename__ = "provider_connection_checks"
    __table_args__ = (
        UniqueConstraint(
            "config_fingerprint",
            "direction",
            "status",
            name="uq_provider_conn_check",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    config_fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    direction: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(20), default="success")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class VoiceUsageDaily(Base):
    """Local daily voice budget counters (spec 7.1); not Tencent console quota.

    Migration 0024 creates updated_at but not created_at for this table.
    """

    __tablename__ = "voice_usage_daily"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "usage_date", "provider", name="uq_voice_usage_daily"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    usage_date: Mapped[datetime] = mapped_column(Date, default=utc_now, index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    asr_count: Mapped[int] = mapped_column(Integer, default=0)
    tts_chars: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )


class MotionEvidenceFrame(Base):
    """Per-run generic evidence pool (V2, migration 0025).

    One row per (run, frame_id). Receipt carries only references
    (preview_asset_id), never image bytes; rows expire via expires_at and are
    purged by the short-term storage worker. UNIQUE(run_id, frame_id) makes a
    re-ingestion idempotent.
    """

    __tablename__ = "motion_evidence_frames"
    __table_args__ = (
        UniqueConstraint("run_id", "frame_id", name="uq_motion_evidence_frame"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("motion_analysis_runs.id"), index=True
    )
    frame_id: Mapped[str] = mapped_column(String(80))
    timestamp_ms: Mapped[int] = mapped_column(Integer, default=0)
    preview_asset_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    observation_json: Mapped[str] = mapped_column(Text, default="{}")
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class MotionStageTask(Base):
    """Persistent post-processing stage task (V2, migration 0025).

    The worker receipt transaction only validates + stores local evidence, then
    enqueues these rows. A background consumer claims and advances them,
    committing at each status transition so polling sees real progress.
    UNIQUE(run_id, stage, version) gives compare-and-set: a consumer holding an
    older lease cannot complete a stage that has been re-enqueued at a newer
    version. status: queued | processing | done | failed.
    """

    __tablename__ = "motion_stage_tasks"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "stage", "version", name="uq_motion_stage_task"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("motion_analysis_runs.id"), index=True
    )
    stage: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    lease_token: Mapped[str] = mapped_column(String(80), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )


class MotionUserFeedback(Base):
    """Per-feedback user annotations (V2, migration 0025).

    Written separately from the computed result snapshot so a re-analysis never
    overwrites user corrections. kind: useful | wrong_label | wrong_frame |
    unhelpful_advice.
    """

    __tablename__ = "motion_user_feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("motion_analysis_runs.id"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    frame_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    corrected_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class HealthAgentRunStage(Base, TimestampMixin):
    """Durable per-stage ledger for one agent run (spec §8.2/§8.8).

    The run row is created *before* the first provider call, so a crashed or
    cancelled turn leaves an inspectable ``failed``/``cancelled`` record instead
    of no record at all. ``stage_key`` is ``router`` / ``worker:<id>`` /
    ``decision`` / ``action``; ``trace_json`` holds desensitized tool names,
    statuses and timings — never private reasoning.
    """

    __tablename__ = "health_agent_run_stages"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "stage_key", "attempt", name="uq_agent_run_stage_attempt"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("health_agent_runs.id"), index=True
    )
    stage_key: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    provider: Mapped[str] = mapped_column(String(60), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    trace_json: Mapped[str] = mapped_column(Text, default="{}")


class AgentActionProposal(Base, TimestampMixin):
    """A persisted, confirmable write proposal (spec §8.2/§8.3/§8.4).

    The model may only *propose*: the registry validates the action-specific
    arguments, hashes the canonical payload and writes this row. Execution
    happens only through ``POST /agent/actions/{proposal_id}/confirm`` which
    re-verifies the hash, so a mutated payload can never be executed.
    """

    __tablename__ = "agent_action_proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    # Random public id; never an enumerable autoincrement.
    proposal_id: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("health_agent_runs.id"), nullable=True, index=True
    )
    action_key: Mapped[str] = mapped_column(String(80), index=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="low")
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    payload_hash: Mapped[str] = mapped_column(String(64))
    display_json: Mapped[str] = mapped_column(Text, default="{}")
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    audit_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_action_audits.id"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class MediaDeletionTask(Base, TimestampMixin):
    """Server-verifiable media deletion ledger (spec §10.1/§10.2).

    A client receipt is supporting evidence only: ``verified`` requires the
    server to observe the object missing or to hold a platform success receipt.
    Only a hash of the storage key is kept, so the ledger can reconcile an
    orphaned object after account deletion without retaining a usable reference.

    ``user_id`` is deliberately NOT a foreign key: the ledger must outlive the
    account row it refers to (that is the "minimal audit reference" the spec
    requires), and an FK would make account deletion fail.
    """

    __tablename__ = "media_deletion_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    media_asset_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    storage_backend: Mapped[str] = mapped_column(String(30), default="")
    storage_key_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    provider_receipt_json: Mapped[str] = mapped_column(Text, default="{}")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
