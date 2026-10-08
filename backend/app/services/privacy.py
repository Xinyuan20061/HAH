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
    AgentActionProposal,
    AgentMicroExperiment,
    ActionOutcome,
    ActionPolicyStat,
    ChatMessage,
    ChatSession,
    DietRecord,
    EvaluationBenchmark,
    EvaluationEvent,
    ExerciseRecord,
    FoodAnalysisCorrection,
    FoodClarificationQuestion,
    FoodAnalysisSession,
    HealthAgentRun,
    HealthAgentRunStage,
    HealthCheckIn,
    HealthGoalAdjustment,
    HealthGoalSetting,
    HealthPlan,
    HealthPlanItem,
    HealthProfile,
    HealthTimelineEvent,
    HealthStateFeature,
    HealthStateSnapshot,
    MediaAsset,
    MobileMediaUploadSession,
    MotionAnalysisJob,
    MotionAnalysisRun,
    MotionAnalysisFeedback,
    MotionEvidenceFrame,
    MotionPreviewObject,
    MotionGoldEvaluation,
    MotionStageTask,
    MotionUserFeedback,
    MotionScore,
    MotionEvent,
    MotionSemanticAnalysis,
    PlanTaskState,
    PrivacyAudit,
    ProviderConnectionCheck,
    ProviderInvocation,
    SafetyEvent,
    User,
    UserIdentity,
    UserIdentityLinkCode,
    UserAIConfig,
    UserFoodPrior,
    UserPreferenceMemory,
    VoiceUsageDaily,
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
    PolicyAcquisitionSession,
    PolicyAcquisitionQuestion,
    PolicyAcquisitionCommand,
    PolicyAcquisitionDailyUsage,
    PolicyAcquisitionEvent,
    PolicyDecisionCertificate,
    PolicyCertificateDependency,
    PolicyEvidenceRevision,
    PolicyAcquisitionFence,
    HarnessPluginInstallation,
    HarnessCapabilityAudit,
)
from app.services.storage import S3Storage, get_storage
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
    ("food_questions", FoodClarificationQuestion),
    ("action_audits", AgentActionAudit),
    ("evaluation_events", EvaluationEvent),
    ("evaluation_benchmarks", EvaluationBenchmark),
    ("safety_events", SafetyEvent),
    ("media_assets", MediaAsset),
    ("mobile_media_upload_sessions", MobileMediaUploadSession),
    ("ai_jobs", AIJob),
    ("motion_scores", MotionScore),
    ("motion_events", MotionEvent),
    ("motion_semantic_analyses", MotionSemanticAnalysis),
    ("motion_analysis_runs", MotionAnalysisRun),
    ("motion_analysis_feedback", MotionAnalysisFeedback),
    ("motion_user_feedback", MotionUserFeedback),
    ("motion_gold_evaluations", MotionGoldEvaluation),
    ("provider_invocations", ProviderInvocation),
    ("provider_connection_checks", ProviderConnectionCheck),
    ("voice_usage_daily", VoiceUsageDaily),
    ("training_intent", UserTrainingIntent),
    ("health_state_features", HealthStateFeature),
    ("health_state_snapshots", HealthStateSnapshot),
    ("action_policy_stats", ActionPolicyStat),
    ("action_outcomes", ActionOutcome),
    ("action_proposals", AgentActionProposal),
    ("user_food_priors", UserFoodPrior),
    ("user_preference_memory", UserPreferenceMemory),
    ("plan_task_states", PlanTaskState),
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
    ("policy_acquisition_sessions", PolicyAcquisitionSession),
    ("policy_acquisition_questions", PolicyAcquisitionQuestion),
    ("policy_acquisition_commands", PolicyAcquisitionCommand),
    ("policy_acquisition_daily_usage", PolicyAcquisitionDailyUsage),
    ("policy_acquisition_events", PolicyAcquisitionEvent),
    ("policy_decision_certificates", PolicyDecisionCertificate),
    ("policy_certificate_dependencies", PolicyCertificateDependency),
    ("policy_evidence_revisions", PolicyEvidenceRevision),
    ("policy_acquisition_fences", PolicyAcquisitionFence),
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
                if model is MobileMediaUploadSession:
                    item["staging_key"] = None
                    item["request_id"] = ""
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
    deleted = 0
    for asset in deletable:
        try:
            storage = S3Storage() if asset.storage_backend == "s3" else get_storage()
            storage.delete(asset.storage_key)
            deleted += 1
        except Exception:
            raise HTTPException(
                503, "非 CloudBase 媒体删除失败，账户数据已保留，请核实存储配置后重试"
            ) from None
    return deleted


def delete_account_data(
    db: Session, user_id: int, *, media_objects_already_handled: bool = False
):
    assets = db.scalars(select(MediaAsset).where(MediaAsset.user_id == user_id)).all()
    upload_sessions = db.scalars(
        select(MobileMediaUploadSession).where(
            MobileMediaUploadSession.user_id == user_id,
            MobileMediaUploadSession.staging_key.is_not(None),
            MobileMediaUploadSession.cleanup_completed_at.is_(None),
        )
    ).all()
    if not media_objects_already_handled and upload_sessions:
        from app.services.media_reconciliation import enqueue_deletion

        for session in upload_sessions:
            session.status = "cancelled"
            task = enqueue_deletion(
                db,
                user_id=user_id,
                media_asset_id=session.media_asset_id,
                storage_backend="s3",
                storage_key=session.staging_key,
                reason="account_deletion_upload_staging",
            )
            task.next_attempt_at = session.cleanup_after
            db.add(task)
        db.commit()
    cloud_files = [
        a.cloud_file_id
        for a in assets
        if a.storage_backend == "cloudbase" and a.cloud_file_id
    ]
    deleted_non_cloud = (
        0 if media_objects_already_handled else _delete_non_cloud_media(assets)
    )

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

    run_ids = list(db.scalars(select(HealthAgentRun.id).where(
        HealthAgentRun.user_id == user_id)).all())
    if run_ids:
        db.execute(delete(HealthAgentRunStage).where(HealthAgentRunStage.run_id.in_(run_ids)))

    motion_run_ids = list(db.scalars(select(MotionAnalysisRun.id).where(
        MotionAnalysisRun.user_id == user_id)).all())
    if motion_run_ids:
        # These short-lived rows intentionally have no owner column of their
        # own; remove them through the user's run IDs before deleting the run.
        db.execute(delete(MotionPreviewObject).where(MotionPreviewObject.run_id.in_(motion_run_ids)))
        db.execute(delete(MotionEvidenceFrame).where(MotionEvidenceFrame.run_id.in_(motion_run_ids)))
        db.execute(delete(MotionStageTask).where(MotionStageTask.run_id.in_(motion_run_ids)))
        # Re-analysis runs form a self-FK chain. Break only the links inside
        # this account's deletion set so the child-first deletion is portable.
        from sqlalchemy import update
        db.execute(update(MotionAnalysisRun).where(
            MotionAnalysisRun.user_id == user_id).values(parent_run_id=None))

    # Children before parents. Remote media deletion is handled before this row set is removed.
    order = [
        FoodAnalysisCorrection,
        FoodClarificationQuestion,
        PolicyAcquisitionEvent,
        PolicyAcquisitionQuestion,
        PolicyCertificateDependency,
        PolicyDecisionCertificate,
        PolicyEvidenceRevision,
        PolicyAcquisitionCommand,
        PolicyAcquisitionDailyUsage,
        PolicyAcquisitionSession,
        PolicyAcquisitionFence,
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
        AgentActionProposal,
        MotionAnalysisFeedback,
        MotionGoldEvaluation,
        MotionUserFeedback,
        ProviderInvocation,
        MotionAnalysisRun,
        HealthAgentRunStage,
        AgentActionAudit,
        AgentMicroExperiment,
        ActionOutcome,
        ActionPolicyStat,
        EvaluationEvent,
        EvaluationBenchmark,
        SafetyEvent,
        MotionEvent,
        MotionSemanticAnalysis,
        MotionScore,
        AIJob,
        MotionAnalysisJob,
        MobileMediaUploadSession,
        FoodAnalysisSession,
        MediaAsset,
        WeeklyReportSnapshot,
        HealthPlanItem,
        HealthPlan,
        HealthGoalAdjustment,
        HealthAgentRun,
        HealthStateFeature,
        HealthStateSnapshot,
        HealthTimelineEvent,
        PlanTaskState,
        HealthCheckIn,
        ExerciseRecord,
        DietRecord,
        HealthGoalSetting,
        UserTrainingIntent,
        UserFoodPrior,
        UserPreferenceMemory,
        ProviderConnectionCheck,
        VoiceUsageDaily,
        UserAIConfig,
        HealthProfile,
        ChatSession,
        PrivacyAudit,
    ]
    for model in order:
        if hasattr(model, "user_id"):
            db.execute(delete(model).where(model.user_id == user_id))

    db.execute(
        delete(UserIdentityLinkCode).where(
            UserIdentityLinkCode.source_user_id == user_id
        )
    )
    db.execute(delete(UserIdentity).where(UserIdentity.user_id == user_id))
    user = db.get(User, user_id)
    if user:
        db.delete(user)
    db.commit()
    return {
        "deleted_non_cloud_media_objects": deleted_non_cloud,
        "cloud_media_objects_expected_deleted": len(cloud_files),
    }
