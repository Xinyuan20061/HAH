from __future__ import annotations
from app.core.time import utc_now, utc_iso

import io
import json
import zipfile
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    AIJob,
    AgentActionAudit,
    AgentMicroExperiment,
    ChatMessage,
    ChatSession,
    DietRecord,
    EvaluationBenchmark,
    EvaluationEvent,
    ExerciseRecord,
    FoodAnalysisCorrection,
    FoodAnalysisSession,
    HealthAgentRun,
    HealthCheckIn,
    HealthGoalAdjustment,
    HealthGoalSetting,
    HealthPlan,
    HealthPlanItem,
    HealthProfile,
    HealthTimelineEvent,
    MediaAsset,
    MotionAnalysisJob,
    MotionScore,
    MotionEvent,
    MotionSemanticAnalysis,
    PlanTaskState,
    PrivacyAudit,
    SafetyEvent,
    User,
    UserAIConfig,
    UserTrainingIntent,
    WeeklyReportSnapshot,
    PolicyTemplate,
    PersonalStrategyUnit,
    PolicyEpisode,
    PolicyExecutionOpportunity,
    PolicyReport,
    PolicyObservationRef,
    PolicyAdjudication,
    PersonalPolicyBelief,
    PolicyDecision,
    PolicyOutbox,
    PolicyActiveSlot,
    PolicyDomainGeneration,
    PolicyLearningControl,
    HarnessPluginInstallation,
    HarnessCapabilityAudit,
)
from app.services.storage import get_storage
from fastapi import HTTPException


TABLES = [
    ("health_profile", HealthProfile),
    ("health_goals", HealthGoalSetting),
    ("diet_records", DietRecord),
    ("exercise_records", ExerciseRecord),
    ("checkins", HealthCheckIn),
    ("timeline", HealthTimelineEvent),
    ("agent_runs", HealthAgentRun),
    ("agent_micro_experiments", AgentMicroExperiment),
    ("plans", HealthPlan),
    ("plan_items", HealthPlanItem),
    ("weekly_reports", WeeklyReportSnapshot),
    ("goal_adjustments", HealthGoalAdjustment),
    ("food_analyses", FoodAnalysisSession),
    ("food_corrections", FoodAnalysisCorrection),
    ("action_audits", AgentActionAudit),
    ("evaluation_events", EvaluationEvent),
    ("evaluation_benchmarks", EvaluationBenchmark),
    ("safety_events", SafetyEvent),
    ("media_assets", MediaAsset),
    ("ai_jobs", AIJob),
    ("motion_scores", MotionScore),
    ("motion_events", MotionEvent),
    ("motion_semantic_analyses", MotionSemanticAnalysis),
    ("training_intent", UserTrainingIntent),
    ("policy_templates", PolicyTemplate),
    ("policy_strategy_units", PersonalStrategyUnit),
    ("policy_episodes", PolicyEpisode),
    ("policy_execution_opportunities", PolicyExecutionOpportunity),
    ("policy_reports", PolicyReport),
    ("policy_observations", PolicyObservationRef),
    ("policy_adjudications", PolicyAdjudication),
    ("policy_beliefs", PersonalPolicyBelief),
    ("policy_decisions", PolicyDecision),
    ("policy_outbox", PolicyOutbox),
    ("policy_active_slots", PolicyActiveSlot),
    ("policy_domain_generations", PolicyDomainGeneration),
    ("policy_learning_controls", PolicyLearningControl),
    ("harness_plugin_installations", HarnessPluginInstallation),
    ("harness_capability_audits", HarnessCapabilityAudit),
    # Kept only so users upgrading from v0.7 can still export/delete legacy rows.
    ("legacy_motion_jobs", MotionAnalysisJob),
]


def _row(obj):
    out = {}
    for c in obj.__table__.columns:
        v = getattr(obj, c.name)
        if isinstance(v, datetime):
            v = utc_iso(v)
        out[c.name] = v
    return out


def cloud_file_ids(db: Session, user_id: int) -> list[str]:
    assets = db.scalars(select(MediaAsset).where(MediaAsset.user_id == user_id)).all()
    return sorted(
        {
            a.cloud_file_id
            for a in assets
            if a.cloud_file_id and a.storage_backend == "cloudbase"
        }
    )


def export_preview(db: Session, user_id: int):
    counts = {}
    for name, model in TABLES:
        if hasattr(model, "user_id"):
            counts[name] = len(
                db.scalars(select(model).where(model.user_id == user_id)).all()
            )
    sessions = db.scalars(
        select(ChatSession).where(ChatSession.user_id == user_id)
    ).all()
    counts["chat_sessions"] = len(sessions)
    counts["chat_messages"] = sum(
        len(db.scalars(select(ChatMessage).where(ChatMessage.session_id == s.id)).all())
        for s in sessions
    )
    return {
        "format": "zip/json",
        "includes": [
            "profile",
            "goals",
            "diet",
            "exercise",
            "checkins",
            "timeline",
            "agent plans",
            "weekly facts",
            "AI food correction history",
            "AI job history",
            "evaluation metrics",
            "safety audit",
            "media metadata",
            "chat history",
            "personal policy protocol, evidence, adjudication and learning audit",
            "Harness capability configuration and desensitized usage audit",
        ],
        "excludes": [
            "API Key 明文",
            "云存储文件二进制",
            "服务端密钥",
            "媒体临时下载 URL",
        ],
        "counts": counts,
    }


def build_export_zip(db: Session, user_id: int) -> bytes:
    user = db.get(User, user_id)
    data = {
        "exported_at": utc_iso(utc_now()),
        "user": {
            "id": user.id if user else None,
            "nickname": user.nickname if user else "",
            "created_at": utc_iso(user.created_at)
            if user and user.created_at
            else None,
        },
        "data": {},
    }
    for name, model in TABLES:
        if hasattr(model, "user_id"):
            rows = []
            for obj in db.scalars(select(model).where(model.user_id == user_id)).all():
                item = _row(obj)
                # Download URLs are short-lived credentials and are not part of a portable export.
                if model is MediaAsset:
                    item["source_url"] = ""
                rows.append(item)
            data["data"][name] = rows
    sessions = db.scalars(
        select(ChatSession).where(ChatSession.user_id == user_id)
    ).all()
    data["data"]["chats"] = [
        {
            "session": _row(s),
            "messages": [
                _row(m)
                for m in db.scalars(
                    select(ChatMessage).where(ChatMessage.session_id == s.id)
                ).all()
            ],
        }
        for s in sessions
    ]
    cfg = db.scalar(select(UserAIConfig).where(UserAIConfig.user_id == user_id))
    data["data"]["ai_config"] = (
        {
            "enabled": cfg.enabled,
            "provider": cfg.provider,
            "base_url": cfg.base_url,
            "model": cfg.model,
            "has_api_key": bool(cfg.api_key_encrypted),
            "voice_enabled": cfg.voice_enabled,
            "voice_base_url": cfg.voice_base_url,
            "voice_stt_model": cfg.voice_stt_model,
            "voice_tts_model": cfg.voice_tts_model,
            "voice_name": cfg.voice_name,
            "has_voice_api_key": bool(cfg.voice_api_key_encrypted),
        }
        if cfg
        else None
    )
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "README.txt",
            "HealthMate personal data export. API keys, passwords, server secrets, temporary media URLs and media binaries are intentionally excluded.\n",
        )
        z.writestr(
            "healthmate-export.json",
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
        )
    return mem.getvalue()


def _delete_non_cloud_media(assets: list[MediaAsset]) -> int:
    deletable = [a for a in assets if a.storage_backend != "cloudbase"]
    if not deletable:
        return 0
    storage = get_storage()
    deleted = 0
    for asset in deletable:
        try:
            storage.delete(asset.storage_key)
            deleted += 1
        except Exception:
            raise HTTPException(
                503, "非 CloudBase 媒体删除失败，账户数据已保留，请核实存储配置后重试"
            ) from None
    return deleted


def delete_account_data(db: Session, user_id: int):
    assets = db.scalars(select(MediaAsset).where(MediaAsset.user_id == user_id)).all()
    cloud_files = [
        a.cloud_file_id
        for a in assets
        if a.storage_backend == "cloudbase" and a.cloud_file_id
    ]
    deleted_non_cloud = _delete_non_cloud_media(assets)

    session_ids = [
        x.id
        for x in db.scalars(
            select(ChatSession).where(ChatSession.user_id == user_id)
        ).all()
    ]
    if session_ids:
        db.execute(delete(ChatMessage).where(ChatMessage.session_id.in_(session_ids)))

    plan_ids = [
        x.id
        for x in db.scalars(
            select(HealthPlan).where(HealthPlan.user_id == user_id)
        ).all()
    ]
    if plan_ids:
        db.execute(delete(HealthPlanItem).where(HealthPlanItem.plan_id.in_(plan_ids)))

    # Children before parents. CloudBase binaries must already have been removed by wx.cloud.deleteFile.
    order = [
        FoodAnalysisCorrection,
        PolicyOutbox,
        PolicyAdjudication,
        PolicyObservationRef,
        PolicyReport,
        PolicyExecutionOpportunity,
        PolicyEpisode,
        PolicyDecision,
        PersonalPolicyBelief,
        PolicyActiveSlot,
        PolicyDomainGeneration,
        PolicyLearningControl,
        HarnessCapabilityAudit,
        HarnessPluginInstallation,
        PersonalStrategyUnit,
        AgentActionAudit,
        AgentMicroExperiment,
        EvaluationEvent,
        EvaluationBenchmark,
        SafetyEvent,
        MotionEvent,
        MotionSemanticAnalysis,
        MotionScore,
        AIJob,
        MotionAnalysisJob,
        FoodAnalysisSession,
        MediaAsset,
        WeeklyReportSnapshot,
        HealthPlanItem,
        HealthPlan,
        HealthGoalAdjustment,
        HealthAgentRun,
        HealthTimelineEvent,
        PlanTaskState,
        HealthCheckIn,
        ExerciseRecord,
        DietRecord,
        HealthGoalSetting,
        UserTrainingIntent,
        UserAIConfig,
        HealthProfile,
        ChatSession,
        PrivacyAudit,
    ]
    for model in order:
        if hasattr(model, "user_id"):
            db.execute(delete(model).where(model.user_id == user_id))

    user = db.get(User, user_id)
    if user:
        db.delete(user)
    db.commit()
    return {
        "deleted_non_cloud_media_objects": deleted_non_cloud,
        "cloud_media_objects_expected_deleted_by_client": len(cloud_files),
    }
