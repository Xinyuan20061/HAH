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
    LargeBinary,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from sqlalchemy.dialects.mysql import MEDIUMTEXT, MEDIUMBLOB


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    # Nullable for accounts whose first identity is a mobile WeChat identity.
    # Verified provider identities live in UserIdentity; this legacy column is
    # retained for mini-program compatibility and the existing admin allowlist.
    openid: Mapped[str | None] = mapped_column(String(128), unique=True, index=True, nullable=True)
    nickname: Mapped[str] = mapped_column(String(64), default="")
    avatar_url: Mapped[str] = mapped_column(String(500), default="")
    profile: Mapped["HealthProfile|None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    ai_config: Mapped["UserAIConfig|None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class UserIdentity(Base):
    __tablename__ = "user_identities"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    issuer: Mapped[str] = mapped_column(String(191), nullable=False)
    subject: Mapped[str] = mapped_column(String(191), nullable=False)
    union_subject: Mapped[str | None] = mapped_column(String(191), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    __table_args__ = (
        UniqueConstraint("provider", "issuer", "subject", name="uq_user_identity_scope_subject"),
        Index("ix_user_identities_user_provider", "user_id", "provider"),
        Index("ix_user_identities_provider_subject", "provider", "subject"),
    )


class UserIdentityLinkCode(Base):
    __tablename__ = "user_identity_link_codes"
    id: Mapped[int] = mapped_column(primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    source_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


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
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


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
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


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
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


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


class MobileMediaUploadSession(Base, TimestampMixin):
    """Durable server-owned staging state for Android object uploads."""

    __tablename__ = "mobile_media_upload_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_mobile_upload_user_request"),
        UniqueConstraint("media_asset_id", name="uq_mobile_upload_media_asset"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    # Sessions survive account deletion only long enough to remove staging
    # objects after every previously issued PUT URL has expired.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    media_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    staging_key: Mapped[str | None] = mapped_column(String(700), nullable=True, unique=True)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), default="")
    media_type: Mapped[str] = mapped_column(String(20), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    cleanup_after: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    cleanup_completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cleanup_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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


class FoodReference(Base, TimestampMixin):
    """One audited entry of the local nutrition table (capability plan §6.4).

    Final nutrient values are computed from this table, never taken from a vision
    model's free-text numbers. ``reviewed_at``/``source_note`` exist so an
    unreviewed row is visibly unreviewed rather than looking authoritative.
    """

    __tablename__ = "food_references"
    id: Mapped[int] = mapped_column(primary_key=True)
    food_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name_zh: Mapped[str] = mapped_column(String(120))
    food_group: Mapped[str] = mapped_column(String(40), default="other", index=True)
    calories_per_100g: Mapped[float] = mapped_column(Float, default=0)
    protein_per_100g: Mapped[float] = mapped_column(Float, default=0)
    carbs_per_100g: Mapped[float] = mapped_column(Float, default=0)
    fat_per_100g: Mapped[float] = mapped_column(Float, default=0)
    fiber_per_100g: Mapped[float] = mapped_column(Float, default=0)
    density_g_per_ml: Mapped[float | None] = mapped_column(Float, nullable=True)
    aliases_json: Mapped[str] = mapped_column(Text, default="[]")
    cooking_adjustments_json: Mapped[str] = mapped_column(Text, default="{}")
    source_id: Mapped[str] = mapped_column(String(80), default="")
    source_note: Mapped[str] = mapped_column(String(300), default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    @property
    def aliases(self) -> list[str]:
        try:
            value = json.loads(self.aliases_json or "[]")
            return [str(item) for item in value] if isinstance(value, list) else []
        except (TypeError, ValueError):
            return []

    @property
    def cooking_adjustments(self) -> dict:
        try:
            value = json.loads(self.cooking_adjustments_json or "{}")
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}


class FoodClarificationQuestion(Base, TimestampMixin):
    """One question asked to shrink an estimate range (capability plan §6.3).

    ``expected_range_reduction`` is what makes question selection auditable: the
    system asks the question predicted to shrink the calorie interval the most,
    capped at two questions, instead of interrogating the user.
    """

    __tablename__ = "food_analysis_questions"
    __table_args__ = (
        UniqueConstraint(
            "analysis_id", "question_id", name="uq_food_analysis_question"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("food_analysis_sessions.id"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    question_id: Mapped[str] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(40))
    prompt: Mapped[str] = mapped_column(String(300))
    options_json: Mapped[str] = mapped_column(Text, default="[]")
    expected_range_reduction: Mapped[float] = mapped_column(Float, default=0)
    answer_option_key: Mapped[str] = mapped_column(String(60), default="")
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rank: Mapped[int] = mapped_column(Integer, default=1)

    @property
    def options(self) -> list[dict]:
        try:
            value = json.loads(self.options_json or "[]")
            return value if isinstance(value, list) else []
        except (TypeError, ValueError):
            return []


class UserFoodPrior(Base, TimestampMixin):
    """Personal portion prior (capability plan §6.5).

    Only affects the *initial suggestion* after at least three user confirmations,
    never skips this meal's confirmation, and can be viewed and cleared by the user.
    """

    __tablename__ = "user_food_priors"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "food_key", "context_key", name="uq_user_food_prior"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    food_key: Mapped[str] = mapped_column(String(80))
    context_key: Mapped[str] = mapped_column(String(60), default="default")
    median_mass_g: Mapped[float] = mapped_column(Float, default=0)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    dispersion: Mapped[float] = mapped_column(Float, default=0)
    last_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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
    # Spec §6 evaluation-page provenance: every externally shown score must
    # carry dataset, evidence level and model/retriever version.
    dataset: Mapped[str] = mapped_column(String(120), default="")
    evidence_level: Mapped[str] = mapped_column(String(40), default="")
    retriever_version: Mapped[str] = mapped_column(String(60), default="")


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
    """Database-backed job queue shared by WeChat Cloud Hosting and the local GPU worker."""

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
    # Spec B2 audit fields (migration 0039): population/exclusions/reviewer
    # per reviewed chunk; review_expires_at forces a re-review deadline;
    # content_sha256 anchors atomic-statement citation checks.
    population: Mapped[str] = mapped_column(String(240), default="")
    exclusions: Mapped[str] = mapped_column(String(240), default="")
    reviewer: Mapped[str] = mapped_column(String(120), default="")
    review_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), default="")


class KnowledgeClaim(Base, TimestampMixin):
    """Structured atomic claim from a reviewed knowledge chunk (spec B3)."""

    __tablename__ = "knowledge_claims"
    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[str] = mapped_column(String(64))
    source_key: Mapped[str] = mapped_column(String(120), index=True)
    subject_population: Mapped[str] = mapped_column(String(120), default="")
    condition: Mapped[str] = mapped_column(String(120), default="")
    behavior: Mapped[str] = mapped_column(String(160), default="")
    outcome: Mapped[str] = mapped_column(String(160), default="")
    direction: Mapped[str] = mapped_column(String(32), default="unspecified")
    strength: Mapped[str] = mapped_column(String(32), default="unspecified")
    qualifier: Mapped[str] = mapped_column(String(240), default="")
    source_location: Mapped[str] = mapped_column(String(240), default="")
    review_state: Mapped[str] = mapped_column(String(40), default="reviewed")
    reviewed_by: Mapped[str] = mapped_column(String(120), default="")
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    version_hash: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class KnowledgeConflictReview(Base, TimestampMixin):
    """Human-reviewed conflict verdict between two claims (spec B3)."""

    __tablename__ = "knowledge_conflict_reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    claim_a_id: Mapped[int] = mapped_column(Integer, index=True)
    claim_b_id: Mapped[int] = mapped_column(Integer, index=True)
    conflict_status: Mapped[str] = mapped_column(String(40))
    scope: Mapped[str] = mapped_column(String(240), default="")
    resolution: Mapped[str] = mapped_column(String(600), default="")
    reviewed_by: Mapped[str] = mapped_column(String(120), default="")
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class KnowledgeReviewEvent(Base, TimestampMixin):
    """Append-only audit trail of knowledge review lifecycle (spec B2)."""

    __tablename__ = "knowledge_review_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(String(120), index=True)
    event_type: Mapped[str] = mapped_column(String(40))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    reviewed_by: Mapped[str] = mapped_column(String(120), default="")
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


class MotionPreviewObject(Base):
    """Short-lived JPEG bytes shared by all API replicas, scoped to one run."""

    __tablename__ = "motion_preview_objects"
    __table_args__ = (UniqueConstraint("run_id", "asset_id", name="uq_motion_preview_run_asset"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("motion_analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    asset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    image_bytes: Mapped[bytes] = mapped_column(LargeBinary().with_variant(MEDIUMBLOB(), "mysql"), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


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
    __table_args__ = (
        Index(
            "uq_motion_user_feedback_request",
            "run_id", "user_id", "idempotency_key",
            unique=True,
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("motion_analysis_runs.id"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    frame_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    corrected_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class MotionGoldEvaluation(Base):
    """Gold-tier result for one motion run (capability plan §5.11 / §12).

    ``tier`` is persisted rather than inferred at read time, so a capability that
    failed its acceptance gate can never be presented as Gold merely because the
    code shipped. ``gate_json`` records which gate conditions passed, so the
    Silver downgrade is explainable.
    """

    __tablename__ = "motion_gold_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "exercise_id", "evaluator_version", name="uq_motion_gold_eval"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("motion_analysis_runs.id"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    exercise_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    evaluator_version: Mapped[str] = mapped_column(String(40), default="")
    tier: Mapped[str] = mapped_column(String(16), default="unknown")
    available: Mapped[bool] = mapped_column(Boolean, default=False)
    reason_unavailable: Mapped[str] = mapped_column(String(120), default="")
    reps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hold_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    view_bucket: Mapped[str] = mapped_column(String(30), default="")
    segments_json: Mapped[str] = mapped_column(Text, default="[]")
    findings_json: Mapped[str] = mapped_column(Text, default="[]")
    measurements_json: Mapped[str] = mapped_column(Text, default="{}")
    gate_json: Mapped[str] = mapped_column(Text, default="{}")
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
    A non-reversible key hash remains in the audit ledger. A separately encrypted
    provider reference exists only while retry is required and is cleared once
    the deletion has been verified.

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
    # Encrypted provider reference retained only while deletion needs retry.
    encrypted_storage_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    provider_receipt_json: Mapped[str] = mapped_column(Text, default="{}")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class HealthStateFeature(Base, TimestampMixin):
    """One derived health-state value, versioned and reproducible.

    Capability plan §4.2/§4.5. A derived fact is only useful if you can tell
    *how* it was produced and *what* it was produced from, so every row stores:

    * ``feature_key`` + ``feature_version`` — the definition that produced it, so
      two versions are never silently compared;
    * ``input_hash`` — a digest of the inputs it was computed from, which makes
      "the underlying records changed, recompute" a cheap equality check and
      makes a snapshot reproducible;
    * ``evidence_json`` — the concrete source references, never private reasoning;
    * ``observed_days`` / ``window_days`` — missing days are counted, not treated
      as zero (plan §4.3).

    ``UNIQUE(user_id, feature_key, window_days, feature_version)`` keeps exactly one
    live value per definition, so a recompute replaces rather than accumulates.
    """

    __tablename__ = "health_state_features"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "feature_key",
            "window_days",
            "feature_version",
            name="uq_health_state_feature",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    feature_key: Mapped[str] = mapped_column(String(80), index=True)
    feature_version: Mapped[str] = mapped_column(String(20), default="1.0.0")
    window_days: Mapped[int] = mapped_column(Integer, default=7)
    value_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_text: Mapped[str] = mapped_column(String(120), default="")
    unit: Mapped[str] = mapped_column(String(20), default="")
    evidence_type: Mapped[str] = mapped_column(String(20), default="observed")
    confidence_level: Mapped[str] = mapped_column(String(20), default="unavailable")
    observed_days: Mapped[int] = mapped_column(Integer, default=0)
    input_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    limitations_json: Mapped[str] = mapped_column(Text, default="[]")
    valid_from: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class HealthStateSnapshot(Base, TimestampMixin):
    """A frozen, versioned view of one user's health state (capability plan §4.2).

    ``snapshot_hash`` covers the ordered feature values, so the same inputs
    produce the same hash and a changed hash means a genuinely different state.
    """

    __tablename__ = "health_state_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    state_version: Mapped[str] = mapped_column(String(20), default="1.0.0")
    as_of: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    window_days: Mapped[int] = mapped_column(Integer, default=7)
    snapshot_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    values_json: Mapped[str] = mapped_column(Text, default="{}")
    constraints_json: Mapped[str] = mapped_column(Text, default="[]")
    missingness_json: Mapped[str] = mapped_column(Text, default="{}")
    active_actions_json: Mapped[str] = mapped_column(Text, default="[]")


class ActionPolicyStat(Base, TimestampMixin):
    """Explainable per-user policy statistics (capability plan §9.4).

    A Beta posterior over how often the user accepts / completes / values an action
    family. This is deliberately *not* reinforcement learning and never touches
    model weights: it only reorders which already-safe option is offered first, and
    a high-risk suggestion is never explored.

    ``UNIQUE(user_id, action_family, variant)`` keeps exactly one posterior per
    option; the raw counts are kept alongside ``alpha``/``beta`` so the posterior can
    be re-derived and shown to the user.
    """

    __tablename__ = "action_policy_stats"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "action_family", "variant", name="uq_action_policy_stat"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action_family: Mapped[str] = mapped_column(String(80), index=True)
    variant: Mapped[str] = mapped_column(String(40), default="default")
    offered: Mapped[int] = mapped_column(Integer, default=0)
    accepted: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    helpful: Mapped[int] = mapped_column(Integer, default=0)
    inaccurate: Mapped[int] = mapped_column(Integer, default=0)
    alpha: Mapped[float] = mapped_column(Float, default=1.0)
    beta: Mapped[float] = mapped_column(Float, default=1.0)


class ActionOutcome(Base):
    """One observed outcome of an executed action or experiment (plan §9.2/§9.5).

    Recording the *result* is what turns "the conversation ended" into "the system
    knows what happened next". ``conclusion`` distinguishes a real change from
    ``insufficient_data``, which is never counted as a positive signal.
    """

    __tablename__ = "action_outcomes"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action_key: Mapped[str] = mapped_column(String(80), index=True)
    # proposal / experiment / goal_adjustment / diet_finalize
    source: Mapped[str] = mapped_column(String(40), default="proposal", index=True)
    source_id: Mapped[str] = mapped_column(String(80), default="")
    decision_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    variant: Mapped[str] = mapped_column(String(40), default="default")
    result: Mapped[str] = mapped_column(String(40), default="unknown", index=True)
    conclusion: Mapped[str] = mapped_column(String(40), default="insufficient_data")
    user_feedback: Mapped[str] = mapped_column(String(20), default="")
    observed_json: Mapped[str] = mapped_column(Text, default="{}")
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class UserPreferenceMemory(Base, TimestampMixin):
    """Structured long-term preference memory (capability plan §8.5).

    Replaces "the last 3 conversation summaries" as the primary memory. A value is
    only stored when the user stated it explicitly or repeatedly chose it, so
    conversational text never silently becomes a long-term fact.

    ``UNIQUE(user_id, key)`` keeps one current value per preference; clearing it
    removes the row so it can no longer influence ranking.
    """

    __tablename__ = "user_preference_memory"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_user_preference_memory"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    key: Mapped[str] = mapped_column(String(80), index=True)
    value: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(30), default="explicit")
    evidence_count: Mapped[int] = mapped_column(Integer, default=1)
    confidence_level: Mapped[str] = mapped_column(String(20), default="high")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_confirmed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class PolicyTemplate(Base, TimestampMixin):
    """审核后的可验证策略模板（policy-learning spec §12）。"""

    __tablename__ = "policy_templates"
    __table_args__ = (
        UniqueConstraint("template_id", "template_version", name="uq_policy_template_version"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[str] = mapped_column(String(80), index=True)
    template_version: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="approved", index=True)
    protocol_json: Mapped[str] = mapped_column(Text, default="{}")
    source_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    template_hash: Mapped[str] = mapped_column(String(64), default="")


class PersonalStrategyUnit(Base, TimestampMixin):
    """冻结后的个人策略协议；创建它不等于启动行动。"""

    __tablename__ = "personal_strategy_units"
    __table_args__ = (
        Index("ix_personal_strategy_units_user_created", "user_id", "created_at", "id"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    template_id: Mapped[str] = mapped_column(String(80), index=True)
    template_version: Mapped[str] = mapped_column(String(40))
    strategy_id: Mapped[str] = mapped_column(String(100), index=True)
    protocol_version: Mapped[str] = mapped_column(String(40))
    metric_version: Mapped[str] = mapped_column(String(40))
    context_schema_version: Mapped[str] = mapped_column(String(40))
    context_key: Mapped[str] = mapped_column(String(64), index=True)
    protocol_json: Mapped[str] = mapped_column(Text, default="{}")
    context_json: Mapped[str] = mapped_column(Text, default="{}")
    state_snapshot_hash: Mapped[str] = mapped_column(String(64), default="")
    protocol_hash: Mapped[str] = mapped_column(String(64), index=True)
    baseline_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(20), default="compiled", index=True)
    # Indexed by the user+key unique migration; avoid a second unversioned
    # single-column index that would make ``alembic check`` report drift.
    compile_idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    compile_request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    compile_response_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)


class PolicyEpisode(Base, TimestampMixin):
    """一次用户确认的行动-观察周期。"""

    __tablename__ = "policy_episodes"
    __table_args__ = (
        Index("ix_policy_episodes_user_status", "user_id", "status"),
        UniqueConstraint("legacy_experiment_id", name="uq_policy_episode_legacy_experiment"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    unit_id: Mapped[str] = mapped_column(ForeignKey("personal_strategy_units.id"), index=True)
    legacy_experiment_id: Mapped[int | None] = mapped_column(nullable=True)
    decision_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    learning_epoch: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    version: Mapped[int] = mapped_column(Integer, default=1)
    review_revision: Mapped[int] = mapped_column(Integer, default=0)
    effective_adjudication_revision: Mapped[int | None] = mapped_column(nullable=True)
    protocol_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    execution_json: Mapped[str] = mapped_column(Text, default="[]")
    baseline_context_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    followup_context_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    baseline_state_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    followup_state_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    changed_variables_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    context_key: Mapped[str] = mapped_column(String(64), index=True)
    stop_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)


class PolicyExecutionOpportunity(Base):
    __tablename__ = "policy_execution_opportunities"
    __table_args__ = (
        UniqueConstraint("episode_id", "slot", name="uq_policy_opportunity_slot"),
        Index("ix_policy_opportunity_user", "user_id"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    episode_id: Mapped[str] = mapped_column(ForeignKey("policy_episodes.id"), index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    slot: Mapped[int] = mapped_column(Integer)
    frozen_action_json: Mapped[str] = mapped_column(Text, default="{}")
    current_report_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PolicyReport(Base):
    __tablename__ = "policy_reports"
    __table_args__ = (
        UniqueConstraint("user_id", "client_report_id", name="uq_policy_report_client_id"),
        UniqueConstraint("opportunity_id", "revision", name="uq_policy_report_opportunity_revision"),
        Index("ix_policy_reports_episode", "episode_id"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_report_id: Mapped[str] = mapped_column(String(96))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    episode_id: Mapped[str] = mapped_column(ForeignKey("policy_episodes.id"), index=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("policy_execution_opportunities.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    execution: Mapped[str] = mapped_column(String(32), default="unknown")
    burden: Mapped[float | None] = mapped_column(Float, nullable=True)
    confounders_json: Mapped[str] = mapped_column(Text, default="[]")
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    source_version: Mapped[int] = mapped_column(Integer, default=1)
    payload_hash: Mapped[str] = mapped_column(String(64), default="")


class PolicyObservationRef(Base):
    __tablename__ = "policy_observation_refs"
    __table_args__ = (
        UniqueConstraint("episode_id", "endpoint", "slot", name="uq_policy_observation_slot"),
        Index("ix_policy_observation_source", "user_id", "source_type", "source_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    episode_id: Mapped[str] = mapped_column(ForeignKey("policy_episodes.id"), index=True)
    endpoint: Mapped[str] = mapped_column(String(24))
    slot: Mapped[int] = mapped_column(Integer)
    source_type: Mapped[str] = mapped_column(String(48))
    source_id: Mapped[str] = mapped_column(String(96))
    source_revision: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    value_json: Mapped[str] = mapped_column(Text, default="null")
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    metric_version: Mapped[str] = mapped_column(String(40))
    confirmed: Mapped[bool] = mapped_column(Boolean, default=True)
    valid: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class PolicyAdjudication(Base):
    __tablename__ = "policy_adjudications"
    __table_args__ = (
        UniqueConstraint("episode_id", "revision", name="uq_policy_adjudication_revision"),
        Index("ix_policy_adjudication_user", "user_id"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    episode_id: Mapped[str] = mapped_column(ForeignKey("policy_episodes.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    learning_epoch: Mapped[str] = mapped_column(String(128), default="")
    execution_label: Mapped[int | None] = mapped_column(Integer, nullable=True)
    support_label: Mapped[int | None] = mapped_column(Integer, nullable=True)
    availability_label: Mapped[int | None] = mapped_column(Integer, nullable=True)
    conclusion: Mapped[str] = mapped_column(String(48))
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    source_hash: Mapped[str] = mapped_column(String(64), default="")
    algorithm_version: Mapped[str] = mapped_column(String(40))
    gate_version: Mapped[str] = mapped_column(String(40))
    valid: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    stale: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class PersonalPolicyBelief(Base, TimestampMixin):
    __tablename__ = "personal_policy_beliefs"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "strategy_id", "protocol_version", "metric_version",
            "context_key", "endpoint", "learning_epoch", name="uq_personal_policy_belief_key"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    strategy_id: Mapped[str] = mapped_column(String(100), index=True)
    protocol_version: Mapped[str] = mapped_column(String(40))
    metric_version: Mapped[str] = mapped_column(String(40))
    context_key: Mapped[str] = mapped_column(String(64), index=True)
    endpoint: Mapped[str] = mapped_column(String(24))
    learning_epoch: Mapped[str] = mapped_column(String(128), default="")
    alpha: Mapped[float] = mapped_column(Float, default=1.0)
    beta: Mapped[float] = mapped_column(Float, default=1.0)
    positive_count: Mapped[int] = mapped_column(Integer, default=0)
    negative_count: Mapped[int] = mapped_column(Integer, default=0)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PolicyDecision(Base):
    __tablename__ = "policy_decisions"
    __table_args__ = (
        Index("ix_policy_decisions_user_idempotency_key", "user_id", "idempotency_key", unique=True),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    state_hash: Mapped[str] = mapped_column(String(64), default="")
    belief_generation: Mapped[int] = mapped_column(Integer, default=0)
    candidate_json: Mapped[str] = mapped_column(Text, default="[]")
    selected_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    policy_mode: Mapped[str] = mapped_column(String(40), default="deterministic_heuristic")
    propensity_json: Mapped[str] = mapped_column(Text, default="{}")
    config_hash: Mapped[str] = mapped_column(String(64), default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    idempotency_request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    candidate_set_hash: Mapped[str] = mapped_column(String(64), default="")
    capability_snapshot_hash: Mapped[str] = mapped_column(String(64), default="")
    response_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class PolicyOutbox(Base):
    __tablename__ = "policy_outbox"
    __table_args__ = (
        UniqueConstraint("user_id", "event_type", "ref_id", "revision", name="uq_policy_outbox_event"),
        Index("ix_policy_outbox_status_retry", "status", "next_retry_at"),
    )
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    ref_id: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class PolicyActiveSlot(Base):
    __tablename__ = "policy_active_slots"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    episode_kind: Mapped[str] = mapped_column(String(40), default="policy")
    episode_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)


class PolicyDomainGeneration(Base):
    __tablename__ = "policy_domain_generations"
    __table_args__ = (UniqueConstraint("user_id", "domain", name="uq_policy_domain_generation"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    domain: Mapped[str] = mapped_column(String(64))
    source_generation: Mapped[int] = mapped_column(Integer, default=0)
    processed_generation: Mapped[int] = mapped_column(Integer, default=0)


class PolicyLearningControl(Base):
    __tablename__ = "policy_learning_controls"
    __table_args__ = (UniqueConstraint("user_id", "scope_key", name="uq_policy_learning_control"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    scope_key: Mapped[str] = mapped_column(String(160))
    epoch_counter: Mapped[int] = mapped_column(Integer, default=0)
    learning_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    reset_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PolicyAcquisitionSession(Base, TimestampMixin):
    __tablename__ = "policy_acquisition_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "episode_id", name="uq_acq_session_user_episode"),
        Index("ix_acq_sessions_user_status", "user_id", "status"),
        CheckConstraint("status IN ('active','paused','closed')", name="ck_acq_session_status"),
        CheckConstraint("decision_state IN ('needs_evidence','sufficient','waiting_window','deferred','needs_repair','blocked','stopped')", name="ck_acq_session_decision_state"),
        CheckConstraint("version > 0 AND episode_prompt_count >= 0", name="ck_acq_session_counters"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    episode_id: Mapped[str] = mapped_column(ForeignKey("policy_episodes.id"), nullable=False, index=True)
    contract_json: Mapped[str] = mapped_column(Text, nullable=False)
    contract_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    decision_state: Mapped[str] = mapped_column(String(32), default="needs_evidence", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    consented_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    budget_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    episode_prompt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active_question_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latest_certificate_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PolicyAcquisitionQuestion(Base):
    __tablename__ = "policy_acquisition_questions"
    __table_args__ = (
        Index("ix_acq_question_session_status", "session_id", "status"),
        Index("ix_acq_question_user_target", "user_id", "target_key"),
        CheckConstraint("status IN ('issued','answered','unknown','declined','unavailable','timed_out','obsolete','cancelled')", name="ck_acq_question_status"),
        CheckConstraint("slot >= 0 AND estimated_cost_ms >= 0", name="ck_acq_question_bounds"),
        CheckConstraint("expected_episode_version > 0 AND expected_session_version > 0", name="ck_acq_question_versions"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("policy_acquisition_sessions.id"), nullable=False, index=True)
    target_key: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(24), nullable=False)
    slot: Mapped[int] = mapped_column(Integer, nullable=False)
    target_opportunity_id: Mapped[str | None] = mapped_column(ForeignKey("policy_execution_opportunities.id"), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_episode_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_session_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="issued", nullable=False)
    prompt_json: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_cost_ms: Mapped[int] = mapped_column(Integer, default=3000, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    answer_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    resulting_report_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resulting_observation_ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PolicyAcquisitionCommand(Base):
    __tablename__ = "policy_acquisition_commands"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_acq_command_user_key"),
        CheckConstraint("method IN ('POST','PATCH','PUT','DELETE')", name="ck_acq_command_method"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    route_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class PolicyAcquisitionDailyUsage(Base):
    __tablename__ = "policy_acquisition_daily_usage"
    __table_args__ = (
        UniqueConstraint("user_id", "business_date", name="uq_acq_usage_user_date"),
        CheckConstraint("prompt_count >= 0 AND estimated_ms >= 0 AND measured_ms >= 0 AND version > 0", name="ck_acq_usage_bounds"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    business_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    prompt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    measured_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class PolicyAcquisitionEvent(Base):
    __tablename__ = "policy_acquisition_events"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_acq_event_sequence"),
        Index("ix_acq_event_user_created", "user_id", "created_at"),
        CheckConstraint("sequence > 0 AND (elapsed_ms IS NULL OR elapsed_ms >= 0)", name="ck_acq_event_bounds"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("policy_acquisition_sessions.id"), nullable=False, index=True)
    question_id: Mapped[str | None] = mapped_column(ForeignKey("policy_acquisition_questions.id"), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class PolicyDecisionCertificate(Base):
    __tablename__ = "policy_decision_certificates"
    __table_args__ = (
        UniqueConstraint("session_id", "revision", name="uq_acq_certificate_revision"),
        Index("ix_acq_certificate_user_episode", "user_id", "episode_id", "status"),
        CheckConstraint("revision > 0", name="ck_acq_certificate_revision"),
        CheckConstraint("purpose IN ('execution_progress','execution_endpoint')", name="ck_acq_certificate_purpose"),
        CheckConstraint("endpoint = 'execution'", name="ck_acq_certificate_endpoint"),
        CheckConstraint("status IN ('valid','stale','revoked')", name="ck_acq_certificate_status"),
        CheckConstraint("label IS NULL OR label IN (0,1)", name="ck_acq_certificate_label"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    episode_id: Mapped[str] = mapped_column(ForeignKey("policy_episodes.id"), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("policy_acquisition_sessions.id"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(24), nullable=False)
    label: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="valid", nullable=False)
    contract_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    binding_json: Mapped[str] = mapped_column(Text, nullable=False)
    proof_json: Mapped[str] = mapped_column(Text, nullable=False)
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    predecessor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class PolicyCertificateDependency(Base):
    __tablename__ = "policy_certificate_dependencies"
    __table_args__ = (
        UniqueConstraint("certificate_id", "dependency_kind", "source_type", "source_id", "metric_version", name="uq_acq_dependency_identity"),
        Index("ix_acq_dependency_source", "user_id", "source_type", "source_id"),
        CheckConstraint("source_revision >= 0", name="ck_acq_dependency_source_revision"),
        CheckConstraint("observation_revision IS NULL OR observation_revision > 0", name="ck_acq_dependency_observation_revision"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    certificate_id: Mapped[str] = mapped_column(ForeignKey("policy_decision_certificates.id"), nullable=False, index=True)
    dependency_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(48), default="", nullable=False)
    source_id: Mapped[str] = mapped_column(String(96), default="", nullable=False)
    source_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metric_version: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    value_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    observation_ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observation_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opportunity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_report_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PolicyEvidenceRevision(Base):
    __tablename__ = "policy_evidence_revisions"
    __table_args__ = (
        UniqueConstraint("user_id", "observation_ref_id", "observation_revision", name="uq_acq_evidence_revision"),
        Index("ix_acq_evidence_source", "user_id", "source_type", "source_id"),
        CheckConstraint("observation_revision > 0 AND source_revision > 0 AND slot >= 0", name="ck_acq_evidence_revision_bounds"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    observation_ref_id: Mapped[int] = mapped_column(ForeignKey("policy_observation_refs.id"), nullable=False, index=True)
    observation_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(String(48), nullable=False)
    source_id: Mapped[str] = mapped_column(String(96), nullable=False)
    source_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    metric_version: Mapped[str] = mapped_column(String(40), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(24), nullable=False)
    slot: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    value_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class PolicyAcquisitionFence(Base):
    __tablename__ = "policy_acquisition_fences"
    __table_args__ = (CheckConstraint("generation >= 0", name="ck_acq_fence_generation"),)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class HarnessPluginInstallation(Base, TimestampMixin):
    """User consent/configuration for a reviewed Harness capability."""

    __tablename__ = "harness_plugin_installations"
    __table_args__ = (
        UniqueConstraint("user_id", "plugin_id", name="uq_harness_plugin_installation"),
        Index("ix_harness_plugin_installations_user", "user_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plugin_id: Mapped[str] = mapped_column(String(80))
    plugin_version: Mapped[str] = mapped_column(String(40))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    reviewed_manifest_hash: Mapped[str] = mapped_column(String(64), default="")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(String(255), default="")
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class HarnessCapabilityAudit(Base, TimestampMixin):
    """Desensitized, user-visible history of capability configuration/use."""

    __tablename__ = "harness_capability_audits"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_harness_audit_idem"),
        Index("ix_harness_capability_audit_owner_plugin", "user_id", "plugin_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plugin_id: Mapped[str] = mapped_column(String(80), index=True)
    installation_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64), default="")
    config_version: Mapped[int] = mapped_column(Integer, default=0)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    response_json: Mapped[str] = mapped_column(Text, default="{}")
